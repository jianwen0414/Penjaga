"""``/api/v1`` case routes: the app's view, cases, dismiss, the tour flag, live case events and the
home summary (the follower is driven by hand over the in-memory detection)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from penjaga_api.api import create_app
from penjaga_api.service import CONSENT_TEXTS, ApiService, AppLayer, Role
from penjaga_api.store import ApiStore
from penjaga_api.testing import FakeUpstream, finding, revision
from penjaga_api.upstream import UpstreamUnavailableError

TOKENS = {
    Role.homeowner: "homeowner-token-0123456789",
    Role.management: "management-token-0123456789",
    Role.operator: "operator-token-0123456789",
}
VIEW_KEYS = {
    "cases", "notices", "wellbeing", "home", "maintenance", "passport", "consent", "toured",
}  # fmt: skip


def _h(role: Role) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[role]}"}


class Harness:
    def __init__(self, tmp_path: Path) -> None:
        self.up = FakeUpstream(episode_start="2025-11-01T00:00:00+08:00")
        self.up.house_time = "2025-11-03T12:00:00+08:00"
        self.store = ApiStore(tmp_path / "api.sqlite")
        self.service = ApiService(self.store, self.up)
        self.client = TestClient(create_app(self.service, tokens=TOKENS, autostart=False))

    def follow(self, *docs: dict[str, Any]) -> None:
        """Emit ``docs`` from detection and run one follower sync over them."""
        for doc in docs:
            self.up.emit(doc)

        async def run() -> None:
            async def end_soon() -> None:
                await asyncio.sleep(0.05)
                self.up.end_streams()

            await asyncio.gather(self.service.follower.sync(), end_soon())

        asyncio.run(run())

    def view(self) -> dict[str, Any]:
        r = self.client.get("/api/v1/view", headers=_h(Role.homeowner))
        assert r.status_code == 200, r.text
        doc: dict[str, Any] = r.json()
        return doc

    def case_by_layer(self, layer: str) -> dict[str, Any]:
        (case,) = [c for c in self.view()["cases"] if c["layer"] == layer]
        found: dict[str, Any] = case
        return found


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    harness = Harness(tmp_path)
    yield harness
    harness.store.close()


def test_the_view_has_the_app_shape(h: Harness) -> None:
    h.follow(finding("water_supply_interruption"), finding("circuit_overload"))
    view = h.view()
    assert set(view) == VIEW_KEYS
    assert view["notices"] == []
    assert len(view["maintenance"]) == 6  # the soonest reminders (test_notices_maintenance)
    assert view["wellbeing"] is None
    assert view["toured"] is False
    assert view["consent"] == {
        "L1": True, "L2": True, "L3": True, "L4": True, "L5": True, "AI": True,
    }  # fmt: skip
    assert [c["severity"] for c in view["cases"]] == ["urgent", "heads_up"]
    assert view["home"]["status"] == "urgent"


def test_cases_and_one_case_by_id(h: Harness) -> None:
    h.follow(finding("water_supply_interruption"))
    (listed,) = h.client.get("/api/v1/cases", headers=_h(Role.homeowner)).json()
    one = h.client.get(f"/api/v1/cases/{listed['id']}", headers=_h(Role.operator)).json()
    assert one == listed
    missing = h.client.get("/api/v1/cases/case_nope", headers=_h(Role.homeowner))
    assert missing.status_code == 404
    assert h.client.get("/api/v1/cases", headers=_h(Role.management)).status_code == 403


def test_the_evidence_chart_comes_from_stream_summaries(h: Harness) -> None:
    doc = finding("water_supply_interruption", first_seen="2025-11-03T10:00:00+08:00")
    start = datetime.fromisoformat("2025-11-03T07:30:00+08:00")
    h.up.readings["water.main.pressure_bar"] = [
        ((start + timedelta(minutes=10 * i)).isoformat(), 1.8 if i < 15 else 0.1) for i in range(25)
    ]
    h.follow(doc)
    series = h.case_by_layer("L5")["evidence"]["series"]
    assert series["unit"] == "bar"
    assert series["points"][0]["v"] == pytest.approx(1.8)
    assert series["points"][-1]["v"] == pytest.approx(0.1)
    assert series["lines"] == [{"v": 0.5, "label": "Low-pressure limit", "tone": "warn"}]
    (call,) = [c for c in h.up.summary_calls if c[0] == "water.main.pressure_bar"]
    assert call[1] == "2025-11-03T07:00:00+08:00"


def test_a_failing_stream_service_leaves_an_empty_chart_not_an_error(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h.follow(finding("water_supply_interruption"))

    async def down(*_: Any) -> dict[str, Any]:
        raise UpstreamUnavailableError("summary down")

    monkeypatch.setattr(h.up, "readings_summary", down)
    case = h.case_by_layer("L5")
    assert case["evidence"]["series"]["points"] == []
    assert h.view()["home"]["today"]["water_typical_l"] is None


def test_the_owner_can_dismiss_an_ordinary_case(h: Harness) -> None:
    h.follow(finding("water_supply_interruption"))
    case = h.case_by_layer("L5")
    assert case["actions"][-1]["kind"] == "dismiss"
    r = h.client.post(
        f"/api/v1/cases/{case['id']}/dismiss",
        json={"reason": "planned works"},
        headers=_h(Role.homeowner),
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "dismissed"
    assert r.json()["timeline"][-1] == {
        "t": r.json()["timeline"][-1]["t"],
        "actor": "owner",
        "text": "Marked as not an issue: planned works",
    }
    again = h.client.post(
        f"/api/v1/cases/{case['id']}/dismiss", json={}, headers=_h(Role.homeowner)
    )
    assert again.status_code == 409


def test_a_safety_floor_case_cannot_be_dismissed(h: Harness) -> None:
    h.follow(finding("protective_device_trip"))
    case = h.case_by_layer("L2")
    assert all(a["kind"] != "dismiss" for a in case["actions"])
    r = h.client.post(f"/api/v1/cases/{case['id']}/dismiss", json={}, headers=_h(Role.homeowner))
    assert r.status_code == 409
    assert "safety-floor" in r.json()["detail"]
    operator = h.client.post(
        f"/api/v1/cases/{case['id']}/dismiss", json={}, headers=_h(Role.operator)
    )
    assert operator.status_code == 403


def test_switching_a_layer_off_hides_its_cases(h: Harness) -> None:
    h.follow(finding("circuit_overload"), finding("water_supply_interruption"))
    r = h.client.put(
        "/api/v1/consent/L2",
        json={"enabled": False, "text_version": CONSENT_TEXTS[AppLayer.L2].version},
        headers=_h(Role.homeowner),
    )
    assert r.status_code == 200
    view = h.view()
    assert [c["layer"] for c in view["cases"]] == ["L5"]
    assert view["consent"]["L2"] is False
    assert h.up.layer_calls == [("L2", False)]


def test_the_tour_flag_is_stored(h: Harness) -> None:
    h.follow()
    assert h.client.post("/api/v1/toured", headers=_h(Role.homeowner)).status_code == 204
    assert h.view()["toured"] is True


def test_resolved_cases_go_to_the_passport(h: Harness) -> None:
    first = finding("power_supply_interruption", first_seen="2025-11-03T09:00:00+08:00")
    back = revision(
        {**first, "status": "cleared", "cleared_at": "2025-11-03T10:00:00+08:00"},
        2,
        "2025-11-03T10:00:00+08:00",
    )
    h.follow(first, back)
    view = h.view()
    (case,) = view["cases"]
    assert case["status"] == "resolved"
    assert view["passport"][0] == {
        "t": case["resolved_at"],
        "title": case["title"],
        "detail": "Readings back to normal",
        "case_id": case["id"],
    }
    assert view["passport"][-1]["title"] == "Home handed over"
    assert view["home"]["status"] == "all_quiet"


def test_case_changes_are_published_to_the_homeowner(h: Harness) -> None:
    doc = finding("protective_device_trip", first_seen="2025-11-03T10:00:00+08:00")
    h.follow(doc)
    events = [e for e in h.store.events_after(0) if e.kind == "case"]
    assert len(events) == 1
    assert events[0].payload["severity"] == "act_soon"

    h.follow()  # nothing new: no new case event
    assert len([e for e in h.store.events_after(0) if e.kind == "case"]) == 1

    h.follow(finding("repeated_trips", first_seen="2025-11-03T11:00:00+08:00"))
    (_, second) = [e for e in h.store.events_after(0) if e.kind == "case"]
    assert second.payload["id"] == events[0].payload["id"]
    assert second.payload["severity"] == "urgent"


def test_today_is_compared_with_the_house_own_history(h: Harness) -> None:
    h.up.house_time = "2025-11-03T03:30:00+08:00"
    readings = []
    for day in ("2025-11-01", "2025-11-02", "2025-11-03"):
        for hour in range(4):
            t = datetime.fromisoformat(f"{day}T{hour:02d}:00:00+08:00")
            # 1 L/min on the earlier days, 2 L/min today
            readings.append((t.isoformat(), 2.0 if day == "2025-11-03" else 1.0))
    h.up.readings["water.main.flow_lpm"] = readings
    h.follow()
    today = h.view()["home"]["today"]
    # 3.5 hours so far: 3 full hours + half an hour, at 2 L/min = 420 L
    assert today["water_l"] == 420
    assert today["water_series"] == [120.0, 120.0, 120.0, 60.0]
    assert today["water_typical_l"] == 210
    assert today["elec_typical_kwh"] is None  # no power readings: no made-up typical


def test_the_view_needs_an_episode(h: Harness) -> None:
    r = h.client.get("/api/v1/view", headers=_h(Role.homeowner))
    assert r.status_code == 503


def test_a_leak_case_in_the_view_shows_its_monthly_cost(h: Harness) -> None:
    start = datetime.fromisoformat("2025-11-01T00:00:00+08:00")
    usual = [(start + timedelta(minutes=10 * i)).isoformat() for i in range(6 * 52)]
    h.up.readings["water.main.flow_lpm"] = [(t, 0.3) for t in usual]  # 12.96 m3 a month
    h.up.readings["elec.main.power_w"] = [(t, 500.0) for t in usual]  # 360 kWh a month
    h.follow(finding("running_cistern_suspected", first_seen="2025-11-03T04:00:00+08:00"))
    case = h.case_by_layer("L1")
    assert " a month" in case["impact"]["headline"]
    assert "RM" in case["impact"]["headline"]
    assert "12.96" not in case["impact"]["how"]  # rounded for people
    assert "13.0 m³" in case["impact"]["how"]
    (book,) = [a for a in case["actions"] if a["kind"] == "book_vendor"]
    assert "typically RM" in book["detail"]


def test_without_a_day_of_history_there_is_no_ringgit_figure(h: Harness) -> None:
    h.follow(finding("running_cistern_suspected", first_seen="2025-11-01T10:00:00+08:00"))
    case = h.case_by_layer("L1")
    assert "RM" not in case["impact"]["headline"]
    assert "at least a day" in case["impact"]["how"]


def test_the_vendor_directory_route(h: Harness) -> None:
    h.follow()
    r = h.client.get("/api/v1/vendors", params={"category": "plumber"}, headers=_h(Role.homeowner))
    assert r.status_code == 200
    doc = r.json()
    assert "written quote" in doc["label"]
    assert {v["category"] for v in doc["vendors"]} == {"plumber"}
    bad = h.client.get("/api/v1/vendors", params={"category": "roofer"}, headers=_h(Role.homeowner))
    assert bad.status_code == 422
    portal = h.client.get(
        "/api/v1/vendors", params={"category": "plumber"}, headers=_h(Role.management)
    )
    assert portal.status_code == 403


def test_live_readings_for_the_listening_line(h: Harness) -> None:
    h.up.house_time = "2025-11-03T12:00:00+08:00"
    h.up.readings["water.main.flow_lpm"] = [
        ("2025-11-03T11:59:10+08:00", 0.2),
        ("2025-11-03T11:59:50+08:00", 0.4),
    ]
    h.up.readings["elec.main.power_w"] = [("2025-11-03T11:59:30+08:00", 1250.0)]
    h.follow()
    r = h.client.get("/api/v1/live", headers=_h(Role.homeowner))
    assert r.status_code == 200
    assert r.json()["water_lpm"] == 0.4
    assert r.json()["power_kw"] == 1.25
    h.up.readings = {}
    assert h.client.get("/api/v1/live", headers=_h(Role.homeowner)).json()["water_lpm"] is None
