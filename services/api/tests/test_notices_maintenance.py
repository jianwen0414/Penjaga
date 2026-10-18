"""Step 8: management notices (L5 clue-linking) and maintenance reminders (L4)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from penjaga_api import maintenance
from penjaga_api.agent.tools import ToolBox
from penjaga_api.api import create_app
from penjaga_api.names import Names
from penjaga_api.notices import NoticeDraft, explains, reaches_house
from penjaga_api.service import ApiService, Role
from penjaga_api.store import ApiStore
from penjaga_api.testing import FakeLLM, FakeUpstream, finding, reply
from penjaga_contract.validate import load_context

TOKENS = {
    Role.homeowner: "homeowner-token-0123456789",
    Role.management: "management-token-0123456789",
    Role.operator: "operator-token-0123456789",
}
INDEX = load_context().index()
DROP = "2025-11-03T04:00:00+08:00"  # when the synthetic supply findings begin


def _h(role: Role) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[role]}"}


class Harness:
    def __init__(self, tmp_path: Path, llm: FakeLLM | None = None) -> None:
        self.up = FakeUpstream(episode_start="2025-11-01T00:00:00+08:00")
        self.up.house_time = "2025-11-03T12:00:00+08:00"
        self.store = ApiStore(tmp_path / "api.sqlite")
        self.service = ApiService(self.store, self.up, llm=llm)
        self.client = TestClient(create_app(self.service, tokens=TOKENS, autostart=False))

    def follow(self, *docs: dict[str, Any]) -> None:
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

    def seed(self, preset: str = "water_works", starts_in_h: float = -8.5) -> dict[str, Any]:
        r = self.client.post(
            "/api/v1/notices/seed",
            json={"preset": preset, "starts_in_h": starts_in_h},
            headers=_h(Role.operator),
        )
        assert r.status_code == 201, r.text
        doc: dict[str, Any] = r.json()
        return doc

    def switch(self, layer: str, enabled: bool) -> None:
        r = self.client.put(
            f"/api/v1/consent/{layer}",
            json={"enabled": enabled, "text_version": "v1"},
            headers=_h(Role.homeowner),
        )
        assert r.status_code == 200, r.text


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    harness = Harness(tmp_path)
    yield harness
    harness.store.close()


def _notice(**changes: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "id": "ntc_test",
        "status": "published",
        "reaches_house": True,
        "utility": "water",
        "type": "water_disruption",
        "title": "Scheduled water supply disruption",
        "starts_at": "2025-11-03T09:00:00+08:00",
        "ends_at": "2025-11-03T17:00:00+08:00",
        "expected_recovery": "2025-11-04T01:00:00+08:00",
    }
    doc.update(changes)
    return doc


# ---------------------------------------------------------------------- notices: rules


def test_a_notice_draft_needs_an_offset_and_an_end_after_the_start() -> None:
    base = {
        "type": "water_disruption",
        "utility": "water",
        "title": "Water works",
        "starts_at": "2025-11-03T09:00:00+08:00",
        "ends_at": "2025-11-03T17:00:00+08:00",
    }
    NoticeDraft.model_validate(base)
    with pytest.raises(ValidationError, match="end must be after"):
        NoticeDraft.model_validate({**base, "ends_at": "2025-11-03T08:00:00+08:00"})
    with pytest.raises(ValidationError, match="UTC offset"):
        NoticeDraft.model_validate({**base, "starts_at": "2025-11-03T09:00:00"})
    with pytest.raises(ValidationError, match="before the works start"):
        NoticeDraft.model_validate({**base, "expected_recovery": "2025-11-03T07:00:00+08:00"})


def test_a_notice_reaches_the_house_only_when_it_names_its_area() -> None:
    assert reaches_house(["Elmina Green (Seksyen U17)"], "Petaling")
    assert reaches_house(["Jalan Cassia U17/75"], "")
    assert not reaches_house(["Seksyen 15", "Seksyen 16"], "Petaling (Shah Alam)")
    assert reaches_house([], "Daerah Petaling")  # a district-wide notice
    assert not reaches_house([], "Klang")


@pytest.mark.parametrize(
    ("began", "expected"),
    [
        ("2025-11-03T08:30:00+08:00", True),  # within the hour before the start
        ("2025-11-03T07:30:00+08:00", False),  # earlier than that
        ("2025-11-03T12:00:00+08:00", True),  # during the works
        ("2025-11-03T20:00:00+08:00", True),  # partial overlap: after the end, before recovery
        ("2025-11-04T02:00:00+08:00", False),  # after the expected recovery
    ],
)
def test_a_supply_drop_matches_a_notice_inside_its_window(began: str, expected: bool) -> None:
    assert explains(_notice(), "water_supply_interruption", began) is expected


def test_without_a_recovery_time_the_window_is_six_hours_after_the_end() -> None:
    notice = _notice(expected_recovery=None)
    assert explains(notice, "water_supply_interruption", "2025-11-03T22:30:00+08:00")
    assert not explains(notice, "water_supply_interruption", "2025-11-03T23:30:00+08:00")


def test_only_a_live_notice_for_the_same_supply_matches() -> None:
    began = "2025-11-03T10:00:00+08:00"
    assert not explains(_notice(status="withdrawn"), "water_supply_interruption", began)
    assert not explains(_notice(reaches_house=False), "water_supply_interruption", began)
    assert not explains(_notice(), "power_supply_interruption", began)
    outage = _notice(utility="electricity", type="planned_power_outage")
    assert explains(outage, "power_supply_interruption", began)
    assert not explains(outage, "night_flow_unexplained", began)


# ---------------------------------------------------------------------- notices: the app


def test_a_matched_supply_drop_is_explained_not_alarming(h: Harness) -> None:
    notice = h.seed()
    assert notice["reaches_house"] is True
    assert notice["provenance"].startswith("Adapted from")
    h.follow(finding("water_supply_interruption"))
    view = h.view()
    (case,) = view["cases"]
    assert case["title"] == "Water is off for the scheduled works"
    assert (case["status"], case["severity"]) == ("monitoring", "info")
    assert "isn't a fault in your home" in case["summary"]
    assert notice["title"] in case["confidence_note"]
    assert any(e["text"] == f"Matched to the notice: {notice['title']}" for e in case["timeline"])
    assert "send_management" not in [a["kind"] for a in case["actions"]]
    assert any("expects supply back by" in line for line in case["impact"]["lines"])
    assert view["home"]["status"] == "all_quiet"
    (shown,) = view["notices"]
    assert shown["issuer"] == "Township management office"
    assert shown["impact"].startswith("Water pressure at your inlet dropped")
    assert "1,364 L" in shown["impact"]
    assert shown["body"].startswith("Pipe diversion")


def test_a_notice_posted_after_the_drop_still_explains_it(h: Harness) -> None:
    h.follow(finding("water_supply_interruption"))
    (before,) = h.view()["cases"]
    assert any("No notice from management" in line for line in before["impact"]["lines"])
    assert "send_management" in [a["kind"] for a in before["actions"]]
    h.seed()
    (after,) = h.view()["cases"]
    assert after["title"] == "Water is off for the scheduled works"


def test_supply_back_later_than_the_notice_said_is_flagged(h: Harness) -> None:
    h.seed()  # works 03:30 to 17:30, supply expected back by 03:30 the next day
    h.follow(finding("water_supply_interruption"))
    h.up.house_time = "2025-11-04T05:00:00+08:00"
    (case,) = h.view()["cases"]
    assert case["status"] == "open"
    assert case["severity"] == "heads_up"
    assert any("That time has passed" in line for line in case["impact"]["lines"])
    assert "send_management" in [a["kind"] for a in case["actions"]]


def test_a_notice_for_another_area_is_not_shown_or_matched(h: Harness) -> None:
    draft = {
        "type": "water_disruption",
        "utility": "water",
        "title": "Water disruption in Seksyen 15",
        "areas": ["Seksyen 15"],
        "district": "Petaling",
        "starts_at": "2025-11-03T03:00:00+08:00",
        "ends_at": "2025-11-03T18:00:00+08:00",
    }
    r = h.client.post("/api/v1/manage/notices", json=draft, headers=_h(Role.management))
    assert r.status_code == 201, r.text
    assert r.json()["reaches_house"] is False
    assert "won't reach" in r.json()["issues"][0]
    h.follow(finding("water_supply_interruption"))
    view = h.view()
    assert view["notices"] == []
    assert view["cases"][0]["title"] == "Water supply to your house has stopped"


def test_management_publishes_lists_and_withdraws_notices(h: Harness) -> None:
    draft = {
        "type": "planned_power_outage",
        "utility": "electricity",
        "title": "Planned power outage",
        "areas": ["Jalan Cassia U17/75"],
        "starts_at": "2025-11-03T14:00:00+08:00",
        "ends_at": "2025-11-03T18:00:00+08:00",
    }
    r = h.client.post("/api/v1/manage/notices", json=draft, headers=_h(Role.homeowner))
    assert r.status_code == 403
    r = h.client.post("/api/v1/manage/notices", json=draft, headers=_h(Role.management))
    assert r.status_code == 201, r.text
    notice_id = r.json()["id"]
    listed = h.client.get("/api/v1/manage/notices", headers=_h(Role.management)).json()
    assert [n["id"] for n in listed] == [notice_id]
    assert [e.kind for e in h.store.events_after(0)].count("notice") == 1
    h.follow()
    (shown,) = h.view()["notices"]
    assert "Refrigerator" in shown["impact"]
    assert "Wi-Fi router" in shown["impact"]
    assert "up to 4 hours" in shown["impact"]
    r = h.client.post(f"/api/v1/manage/notices/{notice_id}/withdraw", headers=_h(Role.management))
    assert r.json()["status"] == "withdrawn"
    assert h.view()["notices"] == []
    missing = h.client.post("/api/v1/manage/notices/ntc_x/withdraw", headers=_h(Role.management))
    assert missing.status_code == 404


def test_with_notices_switched_off_nothing_is_shown_or_linked(h: Harness) -> None:
    h.seed()
    h.follow(finding("water_supply_interruption"))
    h.switch("L5", False)
    view = h.view()
    assert view["notices"] == []
    assert view["cases"] == []  # the L5 supply case is hidden too


def test_night_flow_while_water_comes_back_is_explained(h: Harness) -> None:
    h.seed()
    h.follow(finding("night_flow_unexplained", first_seen="2025-11-03T20:00:00+08:00"))
    (case,) = h.view()["cases"]
    assert "roof tank refilling" in case["summary"]


def test_an_unknown_preset_is_refused(h: Harness) -> None:
    r = h.client.post("/api/v1/notices/seed", json={"preset": "flood"}, headers=_h(Role.operator))
    assert r.status_code == 422


# ---------------------------------------------------------------------- notices: the AI reader

PARSED = {
    "type": "water_disruption",
    "utility": "water",
    "scheduled": True,
    "title": "Scheduled water supply disruption",
    "reason": "Pipe connection works",
    "areas": ["Elmina Green U17"],
    "district": "Petaling",
    "starts_at": "2025-11-05T09:00:00+08:00",
    "ends_at": "2025-11-05T21:00:00+08:00",
    "expected_recovery": "",
    "recovery_note": "",
    "advice": ["Store water."],
    "alternative_supply": "",
    "contact": None,
    "reference": None,
}
NOTICE_TEXT = "Gangguan bekalan air berjadual di Elmina Green U17 pada 5 November, 9 pagi."


def test_the_ai_reads_a_pasted_notice_for_staff_to_check(tmp_path: Path) -> None:
    llm = FakeLLM([reply("not json"), reply(json.dumps(PARSED))])
    h = Harness(tmp_path, llm)
    r = h.client.post(
        "/api/v1/manage/notices/parse", json={"text": NOTICE_TEXT}, headers=_h(Role.management)
    )
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["draft"]["areas"] == ["Elmina Green U17"]
    assert doc["draft"]["expected_recovery"] is None
    assert doc["draft"]["alternative_supply"] is None
    assert doc["issues"] == [
        "No time for supply to be fully back: homeowners will see the end time only."
    ]
    assert len(llm.calls) == 2  # one correction after the malformed reply
    assert "can't be used" in llm.calls[1]["messages"][-1]["content"]
    assert h.service.notice_docs() == []  # reading publishes nothing
    h.store.close()


def test_an_unreadable_notice_or_no_ai_is_reported(tmp_path: Path) -> None:
    bad = {**PARSED, "ends_at": "2025-11-05T08:00:00+08:00"}
    h = Harness(tmp_path, FakeLLM([reply(json.dumps(bad)), reply(json.dumps(bad))]))
    r = h.client.post(
        "/api/v1/manage/notices/parse", json={"text": NOTICE_TEXT}, headers=_h(Role.management)
    )
    assert r.status_code == 422
    h.store.close()
    off = Harness(tmp_path / "off")
    r = off.client.post(
        "/api/v1/manage/notices/parse", json={"text": NOTICE_TEXT}, headers=_h(Role.management)
    )
    assert r.status_code == 503
    off.store.close()


# ---------------------------------------------------------------------- maintenance


def test_every_rule_is_sourced_and_applies_to_this_house() -> None:
    for rule in maintenance.rules():
        assert rule.confidence in {"official", "secondary", "unconfirmed"}, rule.id
        assert rule.url.startswith("https://"), rule.id
        assert rule.source, rule.id
        assert rule.basis, rule.id
        assert maintenance.targets(rule, INDEX), rule.id


def test_reminders_count_from_install_dates() -> None:
    items = {i.rule.id: i for i in maintenance.schedule(INDEX, "2025-03-01T10:00:00+08:00")}
    service = items["aircon_service"]  # all five aircons were installed on 15 Jul 2024
    assert service.due == date(2025, 7, 15)
    assert len(service.object_ids) == 5
    assert not service.overdue
    assert maintenance.title(service, Names(INDEX)) == "Have all 5 aircons serviced"
    rcd = items["rcd_test"]  # monthly: rolls forward from handover, never "overdue"
    assert date(2025, 3, 1) <= rcd.due < date(2025, 3, 31)
    assert not rcd.overdue


def test_a_long_interval_missed_shows_as_overdue() -> None:
    items = {i.rule.id: i for i in maintenance.schedule(INDEX, "2025-11-03T12:00:00+08:00")}
    tank = items["tank_inspect"]  # every 182 days from handover (1 Jun 2024)
    assert tank.overdue
    assert tank.due == date(2025, 5, 31)


def test_marking_a_reminder_done_moves_it_and_goes_to_the_passport(h: Harness) -> None:
    h.follow()
    items = h.client.get("/api/v1/maintenance", headers=_h(Role.homeowner)).json()
    tank = next(i for i in items if i["id"].startswith("tank_inspect@"))
    assert tank["overdue"] is True
    assert tank["confidence"] == "unconfirmed"
    r = h.client.post(f"/api/v1/maintenance/{tank['id']}/done", headers=_h(Role.homeowner))
    assert r.status_code == 201, r.text
    items = h.client.get("/api/v1/maintenance", headers=_h(Role.homeowner)).json()
    again = next(i for i in items if i["id"].startswith("tank_inspect@"))
    assert again["id"] == "tank_inspect@2026-05-04"  # 182 days from today
    assert again["overdue"] is False
    assert again["last_done"] is not None
    passport = h.view()["passport"]
    assert passport[0]["title"] == "Look inside the roof water tank, and have it flushed if dirty"
    assert passport[0]["detail"].startswith("Done")
    gone = h.client.post(f"/api/v1/maintenance/{tank['id']}/done", headers=_h(Role.homeowner))
    assert gone.status_code == 404  # that reminder no longer exists


def test_the_app_lists_what_comes_up_next_before_what_is_overdue(h: Harness) -> None:
    h.follow()
    shown = h.view()["maintenance"]
    assert len(shown) == 6
    assert [m["overdue"] for m in shown[:3]] == [False, False, False]
    assert any(m["overdue"] for m in shown[3:])
    assert shown[0]["due"] <= shown[1]["due"] <= shown[2]["due"]


def test_due_reminders_are_published_once(h: Harness) -> None:
    h.follow()
    first = asyncio.run(h.service.publish_maintenance_due())
    assert first >= 1
    assert asyncio.run(h.service.publish_maintenance_due()) == 0
    kinds = [e.kind for e in h.store.events_after(0)]
    assert kinds.count("maintenance") == first


def test_with_the_schedule_switched_off_there_are_no_reminders(h: Harness) -> None:
    h.follow()
    h.switch("L4", False)
    view = h.view()
    assert view["maintenance"] == []
    assert [p["title"] for p in view["passport"]] == ["Home handed over"]
    assert h.client.get("/api/v1/maintenance", headers=_h(Role.homeowner)).json() == []
    assert asyncio.run(h.service.publish_maintenance_due()) == 0
    r = h.client.post("/api/v1/maintenance/rcd_test@2025-11-30/done", headers=_h(Role.homeowner))
    assert r.status_code == 409


def test_the_agent_can_read_notices_and_reminders(h: Harness) -> None:
    h.seed()
    h.follow()
    now = h.up.house_time
    tb = ToolBox(h.service, now, [], {})
    notices = json.loads(asyncio.run(tb.run("get_notices", "{}")))["notices"]
    assert notices[0]["supply_back_by"] == "3:30 am, Tue 04 Nov"
    assert notices[0]["for_this_home"]
    items = json.loads(asyncio.run(tb.run("get_maintenance", "{}")))["items"]
    assert items[0]["due"].endswith("2025") or items[0]["overdue"]
    assert {"task", "due", "how_often", "source", "confidence"} <= set(items[0])
    # tool output gives days as words, never raw epoch numbers
    assert all(not isinstance(v, int) or isinstance(v, bool) for v in items[0].values())
