"""Step 7: confirmed actions on a case: management tickets and the portal API, illustrative vendor
bookings and guided checks on live readings."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from penjaga_api.api import create_app
from penjaga_api.service import ApiService, Role
from penjaga_api.store import ApiStore
from penjaga_api.testing import FakeUpstream, finding

TOKENS = {
    Role.homeowner: "homeowner-token-0123456789",
    Role.management: "management-token-0123456789",
    Role.operator: "operator-token-0123456789",
}
OWNER = {"Authorization": f"Bearer {TOKENS[Role.homeowner]}"}
STAFF = {"Authorization": f"Bearer {TOKENS[Role.management]}"}
LAB = {"Authorization": f"Bearer {TOKENS[Role.operator]}"}


class Harness:
    def __init__(self, tmp_path: Path) -> None:
        self.up = FakeUpstream(episode_start="2025-11-01T00:00:00+08:00")
        self.up.house_time = "2025-11-03T12:00:00+08:00"
        self.store = ApiStore(tmp_path / "api.sqlite")
        self.service = ApiService(self.store, self.up)
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

    def later(self, **delta: float) -> None:
        t = datetime.fromisoformat(self.up.house_time) + timedelta(**delta)
        self.up.house_time = t.isoformat()

    def case(self) -> dict[str, Any]:
        (case,) = self.client.get("/api/v1/cases", headers=OWNER).json()
        found: dict[str, Any] = case
        return found

    def preview(self, case_id: str, kind: str, params: dict[str, Any]) -> dict[str, Any]:
        r = self.client.post(
            f"/api/v1/cases/{case_id}/actions/{kind}/preview",
            json={"params": params},
            headers=OWNER,
        )
        assert r.status_code == 200, r.text
        doc: dict[str, Any] = r.json()
        return doc

    def ticket(self, case_id: str, note: str = "Please check") -> dict[str, Any]:
        params = {"note": note, "contact": "in_app"}
        token = self.preview(case_id, "ticket", params)["confirmation"]
        r = self.client.post(
            f"/api/v1/cases/{case_id}/tickets",
            json={**params, "confirmation": token},
            headers=OWNER,
        )
        assert r.status_code == 201, r.text
        doc: dict[str, Any] = r.json()
        return doc


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    harness = Harness(tmp_path)
    yield harness
    harness.store.close()


def texts(case: dict[str, Any]) -> list[str]:
    return [e["text"] for e in case["timeline"]]


# ---------------------------------------------------------------------- confirmation


def test_no_action_runs_without_a_valid_confirmation(h: Harness) -> None:
    h.follow(finding("water_supply_interruption"))
    case_id = h.case()["id"]
    body = {"note": "x", "contact": "in_app"}
    bogus = h.client.post(
        f"/api/v1/cases/{case_id}/tickets",
        json={**body, "confirmation": "not-a-real-token"},
        headers=OWNER,
    )
    assert bogus.status_code == 409
    assert "not confirmed" in bogus.json()["detail"]

    token = h.preview(case_id, "ticket", body)["confirmation"]
    other = h.client.post(
        f"/api/v1/cases/{case_id}/tickets",
        json={"note": "a different note", "contact": "in_app", "confirmation": token},
        headers=OWNER,
    )
    assert other.status_code == 409  # bound to the previewed content, and now used up
    reused = h.client.post(
        f"/api/v1/cases/{case_id}/tickets", json={**body, "confirmation": token}, headers=OWNER
    )
    assert reused.status_code == 409

    stale = h.preview(case_id, "ticket", body)["confirmation"]
    h.later(hours=1)
    expired = h.client.post(
        f"/api/v1/cases/{case_id}/tickets", json={**body, "confirmation": stale}, headers=OWNER
    )
    assert expired.status_code == 409
    assert "expired" in expired.json()["detail"]
    assert h.store.records("ticket") == []
    assert h.store.disclosures() == []


def test_the_preview_says_what_is_sent_and_to_whom(h: Harness) -> None:
    h.follow(finding("running_cistern_suspected"))
    case_id = h.case()["id"]
    preview = h.preview(case_id, "ticket", {"note": "Leaking?", "contact": "whatsapp"})
    assert preview["to"] == "Township management office"
    assert "evidence" in preview["fields"]
    assert preview["sends"]["note"] == "Leaking?"
    assert "may_be_defect" in preview["sends"]  # leak within 24 months of handover (Jun 2024)


# ---------------------------------------------------------------------- tickets and the portal


def test_a_ticket_reaches_the_portal_and_its_status_syncs_back(h: Harness) -> None:
    h.follow(finding("water_supply_interruption"))
    case_id = h.case()["id"]
    sent = h.ticket(case_id)
    assert sent["status"] == "received"
    owner_case = h.case()
    assert owner_case["status"] == "action_pending"
    assert "Sent to management" in texts(owner_case)

    inbox = h.client.get("/api/v1/manage/tickets", headers=STAFF).json()
    assert [t["id"] for t in inbox] == [sent["id"]]
    assert h.client.get("/api/v1/manage/tickets", headers=OWNER).status_code == 403

    r = h.client.post(
        f"/api/v1/manage/tickets/{sent['id']}/status", json={"status": "scheduled"}, headers=STAFF
    )
    assert r.status_code == 200
    assert [s["status"] for s in r.json()["history"]] == ["scheduled"]  # for the portal's activity
    back = h.client.post(
        f"/api/v1/manage/tickets/{sent['id']}/status", json={"status": "received"}, headers=STAFF
    )
    assert back.status_code == 409
    h.client.post(
        f"/api/v1/manage/tickets/{sent['id']}/replies",
        json={"text": "Technician on Thursday."},
        headers=STAFF,
    )
    owner_case = h.case()
    assert "Management scheduled a visit" in texts(owner_case)
    assert "Management: Technician on Thursday." in texts(owner_case)
    events = [e.kind for e in h.store.events_after(0)]
    assert events.count("ticket") == 3

    h.client.post(
        f"/api/v1/manage/tickets/{sent['id']}/status", json={"status": "resolved"}, headers=STAFF
    )
    assert h.case()["status"] == "open"  # the finding is still active; nothing pending


def test_a_ticket_is_a_logged_disclosure_with_minimum_fields(h: Harness) -> None:
    h.follow(finding("water_supply_interruption"))
    sent = h.ticket(h.case()["id"])
    (logged,) = h.store.disclosures()
    assert logged["recipient"] == "Township management office"
    assert logged["ref"] == sent["id"]
    assert set(logged["fields"]) <= {
        "title", "summary", "location", "object_ids", "evidence", "severity", "note", "contact",
        "may_be_defect",
    }  # fmt: skip
    assert "series" not in str(sent)  # no raw readings leave


def test_an_unknown_ticket(h: Harness) -> None:
    h.follow()
    assert h.client.get("/api/v1/manage/tickets/tkt_nope", headers=STAFF).status_code == 404


# ---------------------------------------------------------------------- bookings


def test_booking_an_illustrative_vendor_end_to_end(h: Harness) -> None:
    h.follow(finding("protective_device_trip"))
    case = h.case()
    vendors = h.client.get(
        "/api/v1/vendors", params={"category": "electrician"}, headers=OWNER
    ).json()
    vendor = vendors["vendors"][0]
    params = {"vendor_id": vendor["id"], "slot": vendor["next_slots"][0]}
    preview = h.preview(case["id"], "booking", params)
    assert "illustrative" not in preview["to"]
    r = h.client.post(
        f"/api/v1/cases/{case['id']}/bookings",
        json={**params, "confirmation": preview["confirmation"]},
        headers=OWNER,
    )
    assert r.status_code == 201, r.text
    booked = h.case()
    assert booked["booking"]["status"] == "requested"
    assert booked["booking"]["id"] == r.json()["id"]  # the Lab completes jobs by it
    assert booked["status"] == "action_pending"
    assert all(a["kind"] != "book_vendor" for a in booked["actions"])
    assert any(t.startswith(f"Booked {vendor['name']} for ") for t in texts(booked))

    h.later(minutes=31)
    acked = h.case()
    assert acked["booking"]["status"] == "acknowledged"
    assert f"{vendor['name']} confirmed the visit" in texts(acked)

    booking_id = r.json()["id"]
    assert (
        h.client.post(f"/api/v1/bookings/{booking_id}/complete", headers=OWNER).status_code == 403
    )
    done = h.client.post(f"/api/v1/bookings/{booking_id}/complete", headers=LAB)
    assert done.status_code == 200
    finished = h.case()
    assert finished["booking"]["status"] == "completed"
    assert "Job marked complete" in texts(finished)
    (logged,) = h.store.disclosures()
    assert logged["recipient"] == preview["to"]


def test_a_booking_needs_an_offered_vendor_and_slot(h: Harness) -> None:
    h.follow(finding("protective_device_trip"))
    case_id = h.case()["id"]
    r = h.client.post(
        f"/api/v1/cases/{case_id}/actions/booking/preview",
        json={"params": {"vendor_id": "v_plumb_1", "slot": 1}},
        headers=OWNER,
    )
    assert r.status_code == 409  # a plumber on an electrical case, and a slot never offered


# ---------------------------------------------------------------------- guided checks


def test_a_guided_check_runs_on_live_readings_and_updates_the_case(h: Harness) -> None:
    h.follow(finding("night_flow_unexplained"))
    case = h.case()
    steps = case["guided"]["steps"]
    assert steps[0]["fixture_label"] == "Bathroom 4 toilet cistern"  # ground floor first
    assert steps[0]["valve_id"] == "valve_bath4_wc_angle"  # the valve the owner closes
    assert "WC angle valve" in steps[0]["instruction"]

    first = steps[0]["fixture_id"]
    token = h.preview(case["id"], "check", {"fixture_id": first})["confirmation"]
    started = h.client.post(
        f"/api/v1/cases/{case['id']}/checks",
        json={"fixture_id": first, "confirmation": token},
        headers=OWNER,
    )
    assert started.status_code == 201, started.text
    (request,) = [c["request"] for c in h.up.checks.values()]
    assert request["kind"] == "flow_stop"
    assert request["since"] == h.up.house_time

    assert asyncio.run(h.service.actions.poll_checks()) == 0  # still measuring
    h.later(minutes=13)
    assert asyncio.run(h.service.actions.poll_checks()) == 1
    checked = h.case()
    assert checked["guided"]["steps"][0]["result"] == "continued"
    assert checked["guided"]["steps"][0]["flow_after"] == 0.944
    assert "Checked bathroom 4 toilet cistern: flow continued" in texts(checked)

    second = steps[1]["fixture_id"]
    h.up.check_verdict, h.up.check_flow = "stopped", (0.7, 0.0)
    token = h.preview(case["id"], "check", {"fixture_id": second})["confirmation"]
    h.client.post(
        f"/api/v1/cases/{case['id']}/checks",
        json={"fixture_id": second, "confirmation": token},
        headers=OWNER,
    )
    h.later(minutes=13)
    asyncio.run(h.service.actions.poll_checks())
    found = h.case()
    assert found["guided"]["done"] is True
    assert found["guided"]["located"] == second
    assert found["title"] == "The Master bathroom toilet cistern is leaking"
    assert found["focus"]["object_ids"] == [second, "valve_bath_master_wc_angle"]
    assert all(a["kind"] != "guided_check" for a in found["actions"])
    assert h.store.disclosures() == []  # a check sends nothing anywhere


def test_a_check_only_runs_on_the_case_s_own_steps(h: Harness) -> None:
    h.follow(finding("night_flow_unexplained"))
    case_id = h.case()["id"]
    r = h.client.post(
        f"/api/v1/cases/{case_id}/actions/check/preview",
        json={"params": {"fixture_id": "fix_kitchen_sink_tap"}},
        headers=OWNER,
    )
    assert r.status_code == 409


def test_cases_without_a_guided_check_refuse_one(h: Harness) -> None:
    h.follow(finding("water_supply_interruption"))
    case_id = h.case()["id"]
    r = h.client.post(
        f"/api/v1/cases/{case_id}/actions/check/preview",
        json={"params": {"fixture_id": "fix_bath4_wc_cistern"}},
        headers=OWNER,
    )
    assert r.status_code == 409
