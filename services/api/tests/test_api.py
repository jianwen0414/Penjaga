"""Routes, roles, consent and privacy controls of ``/api/v1`` (the follower is driven by hand)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from penjaga_api.api import create_app
from penjaga_api.service import CONSENT_TEXTS, ApiService, AppLayer, Role
from penjaga_api.store import ApiStore
from penjaga_api.testing import FakeUpstream, load_findings

TOKENS = {
    Role.homeowner: "homeowner-token-0123456789",
    Role.management: "management-token-0123456789",
    Role.operator: "operator-token-0123456789",
}


def _h(role: Role) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[role]}"}


class Harness:
    def __init__(self, tmp_path: Path) -> None:
        self.up = FakeUpstream(episode_start="2025-11-26T00:00:00+08:00")
        self.store = ApiStore(tmp_path / "api.sqlite")
        self.service = ApiService(self.store, self.up)
        self.client = TestClient(create_app(self.service, tokens=TOKENS, autostart=False))

    def follow_once(self) -> None:
        """One follower sync, ended as soon as the backfill is filed."""

        async def run() -> None:
            async def end_soon() -> None:
                await asyncio.sleep(0.05)
                self.up.end_streams()

            await asyncio.gather(self.service.follower.sync(), end_soon())

        asyncio.run(run())


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    harness = Harness(tmp_path)
    yield harness
    harness.store.close()


def test_health_is_public_and_says_nothing_personal(h: Harness) -> None:
    r = h.client.get("/api/v1/health")
    assert r.status_code == 200
    assert set(r.json()) == {"status", "detection_connected"}


def test_routes_need_a_token_and_the_right_role(h: Harness) -> None:
    assert h.client.get("/api/v1/house").status_code == 401
    bad = {"Authorization": "Bearer not-a-real-token-at-all"}
    assert h.client.get("/api/v1/house", headers=bad).status_code == 401
    for role in Role:
        assert h.client.get("/api/v1/house", headers=_h(role)).status_code == 200
    assert h.client.get("/api/v1/findings", headers=_h(Role.homeowner)).status_code == 403
    assert h.client.get("/api/v1/consent", headers=_h(Role.management)).status_code == 403
    assert h.client.get("/api/v1/diagnostics", headers=_h(Role.homeowner)).status_code == 403


def test_tokens_must_be_long_and_distinct(h: Harness) -> None:
    with pytest.raises(ValueError, match="16 characters"):
        create_app(h.service, tokens={Role.homeowner: "short"}, autostart=False)
    same = "the-same-token-0123456789"
    with pytest.raises(ValueError, match="its own token"):
        create_app(h.service, tokens={Role.homeowner: same, Role.operator: same}, autostart=False)


def test_the_house_record_and_id_validation(h: Harness) -> None:
    house = h.client.get("/api/v1/house", headers=_h(Role.homeowner)).json()
    assert house["house_id"] == "elmina_g6_demo"
    ok = h.client.post(
        "/api/v1/house/validate-ids",
        json={"ids": ["db_main", "db_main"]},
        headers=_h(Role.operator),
    )
    assert ok.json() == {"ids": ["db_main"]}
    bad = h.client.post(
        "/api/v1/house/validate-ids",
        json={"ids": ["wall_GF_kitchen_N"]},
        headers=_h(Role.operator),
    )
    assert bad.status_code == 422
    assert "wall_GF_kitchen_N" in bad.json()["detail"]


def test_findings_need_an_episode_then_list_the_latest(h: Harness) -> None:
    assert h.client.get("/api/v1/findings", headers=_h(Role.operator)).status_code == 503
    for doc in load_findings("l2_elderly"):
        h.up.emit(doc)
    h.follow_once()
    found = h.client.get("/api/v1/findings", headers=_h(Role.operator)).json()
    assert {f["type"] for f in found} == {
        "circuit_overload",
        "protective_device_trip",
        "repeated_trips",
    }
    only_l5 = h.client.get("/api/v1/findings?layer=L5", headers=_h(Role.operator)).json()
    assert only_l5 == []


def test_consent_lists_every_layer_with_its_text(h: Harness) -> None:
    layers = h.client.get("/api/v1/consent", headers=_h(Role.homeowner)).json()
    assert [layer["layer"] for layer in layers] == ["L1", "L2", "L3", "L4", "L5", "AI"]
    assert all(layer["text"] and layer["enabled"] for layer in layers)
    assert all(layer["decided_at"] is None for layer in layers)


def test_switching_a_detection_layer_off_stops_it_there_and_is_recorded(h: Harness) -> None:
    version = CONSENT_TEXTS[AppLayer.L1].version
    r = h.client.put(
        "/api/v1/consent/L1",
        json={"enabled": False, "text_version": version},
        headers=_h(Role.homeowner),
    )
    assert r.status_code == 200
    assert r.json()["decided_at"] == h.up.house_time
    assert h.up.layer_calls == [("L1", False)]
    (decision,) = h.client.get("/api/v1/consent/history", headers=_h(Role.homeowner)).json()
    assert decision == {
        "layer": "L1",
        "enabled": False,
        "text_version": version,
        "actor": "homeowner",
        "decided_at": h.up.house_time,
    }
    l1 = h.client.get("/api/v1/consent", headers=_h(Role.homeowner)).json()[0]
    assert l1["enabled"] is False


def test_l4_consent_is_kept_here_because_detection_does_not_run_it(h: Harness) -> None:
    r = h.client.put(
        "/api/v1/consent/L4",
        json={"enabled": False, "text_version": "v1"},
        headers=_h(Role.homeowner),
    )
    assert r.status_code == 200
    assert h.up.layer_calls == []


def test_a_decision_on_an_outdated_text_is_refused(h: Harness) -> None:
    r = h.client.put(
        "/api/v1/consent/L2",
        json={"enabled": True, "text_version": "v0"},
        headers=_h(Role.homeowner),
    )
    assert r.status_code == 409
    assert h.store.consent_history() == []


def test_no_consent_is_recorded_when_detection_cannot_apply_it(h: Harness) -> None:
    h.up.down = True
    r = h.client.put(
        "/api/v1/consent/L1",
        json={"enabled": False, "text_version": "v1"},
        headers=_h(Role.homeowner),
    )
    assert r.status_code == 502
    assert h.store.consent_history() == []


def test_retention_and_disclosures_are_the_owners_to_see_and_set(h: Harness) -> None:
    assert h.client.get("/api/v1/disclosures", headers=_h(Role.homeowner)).json() == []
    r = h.client.put("/api/v1/retention/events", json={"days": 7}, headers=_h(Role.homeowner))
    assert r.json()["events"] == 7
    unknown = h.client.put("/api/v1/retention/photos", json={"days": 7}, headers=_h(Role.homeowner))
    assert unknown.status_code == 404
    zero = h.client.put("/api/v1/retention/events", json={"days": 0}, headers=_h(Role.homeowner))
    assert zero.status_code == 422


def test_closing_the_account_erases_personal_records(h: Harness) -> None:
    for doc in load_findings("l1_leak"):
        h.up.emit(doc)
    h.follow_once()
    h.client.put(
        "/api/v1/consent/L4",
        json={"enabled": True, "text_version": "v1"},
        headers=_h(Role.homeowner),
    )
    assert h.client.delete("/api/v1/account", headers=_h(Role.homeowner)).status_code == 204
    assert h.store.consent_history() == []
    assert h.store.events_after(0) == []
    assert h.client.get("/api/v1/findings", headers=_h(Role.operator)).json() == []


def test_live_events_wake_waiters_and_respect_the_audience(h: Harness) -> None:
    async def scenario() -> list[str]:
        waiter = asyncio.create_task(h.service.wait_for_events(0, timeout_s=5))
        await asyncio.sleep(0.02)
        h.store.publish("consent", "2025-11-26T10:00:00+08:00", {"layer": "L1", "enabled": False})
        h.service.notify()
        return [e.kind for e in await waiter]

    assert asyncio.run(scenario()) == ["consent"]
    from penjaga_api.service import EVENT_AUDIENCE

    assert Role.homeowner not in EVENT_AUDIENCE["finding"]
    assert Role.management not in EVENT_AUDIENCE["consent"]
