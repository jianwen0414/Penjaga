"""Actions on a case (PRD §19): management tickets, illustrative vendor bookings and guided checks.

Every external action needs the owner's confirmation (🔒 §18.4): ``preview`` shows exactly what will
be sent and to whom and returns a single-use token, bound to that case, action and parameters;
only that token makes the action run. The agent can only propose; the owner's tap confirms. Each
action writes a timeline event (``case_actions``), and everything that leaves the house's data
space (a ticket, a booking) is a logged disclosure with the minimum fields (PDPA).

"Management" is the township's management office (in an SDP township, its Township & Community
Services team, which already takes defect reports): during the 24-month defect liability period a
ticket is effectively a pre-filled defect report for that existing channel. The recipient is
configurable (``PENJAGA_MANAGEMENT_NAME``) and nothing here claims to act for SDP.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
from datetime import date, datetime, timedelta, timezone
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from penjaga_api.cases import (
    BOOKING,
    BOOKING_COMPLETE,
    CHECK_RESULT,
    TICKET,
    TICKET_REPLY,
    TICKET_STATUS,
    CaseState,
)
from penjaga_api.templates import CaseExtras, guided_targets
from penjaga_api.upstream import UpstreamUnavailableError
from penjaga_api.vendors import directory, list_vendors

if TYPE_CHECKING:
    from penjaga_api.service import ApiService

log = logging.getLogger("penjaga_api.actions")

CONFIRM_FOR = timedelta(minutes=30)  # house time a preview stays confirmable
DEFECT_LIABILITY = 24  # months from handover (landed homes, statutory)
TICKET_STATUSES = ("received", "scheduled", "in_progress", "resolved")
CHECK_SETTLE_S = 120.0
CHECK_WINDOW_S = 600.0
CHECK_STOPPED_BELOW_LPM = 0.02
CONTACT = ("in_app", "phone_call", "whatsapp")


class ActionKind(StrEnum):
    ticket = "ticket"
    booking = "booking"
    check = "check"


class ActionRefusedError(ValueError):
    """The action can't run: not confirmed, already used, expired, or not offered on this case."""


def management_name() -> str:
    return os.environ.get("PENJAGA_MANAGEMENT_NAME", "Township management office")


def _params_key(params: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()


HOUSE_ZONE = timezone(timedelta(hours=8))  # Asia/Kuala_Lumpur, no daylight saving


def _slot_text(slot_ms: int) -> str:
    dt = datetime.fromtimestamp(slot_ms / 1000, tz=HOUSE_ZONE)
    hour = dt.hour % 12 or 12
    minutes = f":{dt.minute:02d}" if dt.minute else ""
    return f"{dt:%a} {hour}{minutes}{'am' if dt.hour < 12 else 'pm'}"


class Actions:
    def __init__(self, service: ApiService) -> None:
        self.service = service

    # ------------------------------------------------------------------ helpers

    @property
    def store(self) -> Any:
        return self.service.store

    def _run(self) -> str:
        return self.service._run()[0]

    async def _case(self, case_id: str) -> tuple[CaseState, dict[str, Any]]:
        consent = await self.service._consent_map()
        state = self.service._find(case_id, consent)
        return state, await self.service.case(case_id)

    def _offered(self, case: dict[str, Any], kind: str) -> None:
        if case["status"] in ("resolved", "dismissed"):
            raise ActionRefusedError(f"case is {case['status']}")
        wanted = {"ticket": "send_management", "booking": "book_vendor", "check": "guided_check"}
        if kind != "ticket" and not any(a["kind"] == wanted[kind] for a in case["actions"]):
            raise ActionRefusedError(f"this case doesn't offer {wanted[kind].replace('_', ' ')}")

    def extras(self, case_id: str) -> CaseExtras:
        """The booking and completed checks a case's rendering needs."""
        run = self._run()
        bookings = self.store.records("booking", run=run, case_id=case_id)
        checks = [
            c for c in self.store.records("check", run=run, case_id=case_id) if c.get("result")
        ]
        return CaseExtras(booking=bookings[-1] if bookings else None, checks=tuple(checks))

    # ------------------------------------------------------------------ confirmation

    async def preview(
        self, case_id: str, kind: ActionKind, params: dict[str, Any]
    ) -> dict[str, Any]:
        """What the action would send, and to whom, plus the token that confirms it."""
        state, case = await self._case(case_id)
        self._offered(case, kind.value)
        now = await self.service.house_time()
        if kind is ActionKind.ticket:
            shown = self._ticket_content(state, case, params, now)
            to, fields = management_name(), sorted(shown)
        elif kind is ActionKind.booking:
            vendor = self._vendor(case, params, now)
            shown = self._booking_content(case, vendor, int(params["slot"]))
            to, fields = vendor["name"], sorted(shown)
        else:
            fixture, valve = self._check_target(state, params)
            names = self.service.names
            shown = {
                "instruction": f"Close the {names.of(valve)}, then confirm.",
                "fixture": names.of(fixture),
            }
            to, fields = "nobody: Penjaga only reads your water meter", []
        token = secrets.token_urlsafe(18)
        expires = (datetime.fromisoformat(now) + CONFIRM_FOR).isoformat()
        self.store.add_confirmation(
            token,
            case_id=case_id,
            kind=kind.value,
            params=_params_key(params),
            created=now,
            expires=expires,
        )
        return {
            "confirmation": token,
            "expires": expires,
            "to": to,
            "fields": fields,
            "sends": shown,
        }

    async def _redeem(
        self, token: str, case_id: str, kind: ActionKind, params: dict[str, Any]
    ) -> str:
        record = self.store.use_confirmation(token)
        if record is None:
            raise ActionRefusedError("not confirmed: preview the action and confirm it first")
        now = await self.service.house_time()
        if (record["case_id"], record["kind"], record["params"]) != (
            case_id,
            kind.value,
            _params_key(params),
        ):
            raise ActionRefusedError("this confirmation is for a different action")
        if datetime.fromisoformat(now) > datetime.fromisoformat(record["expires"]):
            raise ActionRefusedError("the confirmation has expired: preview it again")
        return now

    # ------------------------------------------------------------------ tickets

    def _ticket_content(
        self, state: CaseState, case: dict[str, Any], params: dict[str, Any], now: str
    ) -> dict[str, Any]:
        """The minimum a ticket carries (PDPA Disclosure principle): no readings, no history."""
        contact = str(params.get("contact", "in_app"))
        if contact not in CONTACT:
            raise ActionRefusedError(f"contact must be one of {', '.join(CONTACT)}")
        content: dict[str, Any] = {
            "title": case["title"],
            "summary": case["summary"],
            "location": case["location"],
            "object_ids": case["focus"]["object_ids"],
            "evidence": case["evidence"]["stats"],
            "severity": case["severity"],
            "note": str(params.get("note", ""))[:1000],
            "contact": contact,
        }
        if state.layer == "L1" and self._within_defect_liability(now):
            content["may_be_defect"] = (
                "Detected within the 24-month defect liability period after handover; "
                "this may be a defect for the developer to assess."
            )
        return content

    def _within_defect_liability(self, now: str) -> bool:
        handover = date.fromisoformat(str(self.service.house_document()["handover_date"]))
        months = DEFECT_LIABILITY
        end = handover.replace(year=handover.year + months // 12)
        return datetime.fromisoformat(now).date() < end

    async def send_ticket(
        self, case_id: str, params: dict[str, Any], confirmation: str
    ) -> dict[str, Any]:
        state, case = await self._case(case_id)
        self._offered(case, "ticket")
        now = await self._redeem(confirmation, case_id, ActionKind.ticket, params)
        content = self._ticket_content(state, case, params, now)
        ticket_id = f"tkt_{secrets.token_hex(6)}"
        run = self._run()
        doc = {**content, "recipient": management_name(), "messages": []}
        self.store.put_record(
            "ticket",
            ticket_id,
            run=run,
            case_id=case_id,
            status="received",
            house_time=now,
            doc=doc,
        )
        self.store.add_case_action(
            run,
            case_id,
            kind=TICKET,
            actor="owner",
            house_time=now,
            detail={"ticket_id": ticket_id},
        )
        self.store.log_disclosure(
            house_time=now,
            actor="owner",
            recipient=management_name(),
            purpose="report a problem (management ticket)",
            fields=sorted(content),
            basis="owner's confirmed request",
            ref=ticket_id,
        )
        self._changed(now, "ticket", {"id": ticket_id, "case_id": case_id, "status": "received"})
        return self.ticket(ticket_id)

    def ticket(self, ticket_id: str) -> dict[str, Any]:
        record = self.store.record(ticket_id)
        if record is None or record["kind"] != "ticket":
            raise KeyError(ticket_id)
        return dict(record)

    def tickets(self, status: str | None = None) -> list[dict[str, Any]]:
        return list(reversed(self.store.records("ticket", status=status)))

    async def set_ticket_status(self, ticket_id: str, status: str) -> dict[str, Any]:
        if status not in TICKET_STATUSES:
            raise ActionRefusedError(f"status must be one of {', '.join(TICKET_STATUSES)}")
        ticket = self.ticket(ticket_id)
        if TICKET_STATUSES.index(status) <= TICKET_STATUSES.index(ticket["status"]):
            raise ActionRefusedError(f"ticket is already {ticket['status']}")
        now = await self.service.house_time()
        ticket["history"] = [*ticket.get("history", []), {"status": status, "t": now}]
        self._save_ticket(ticket, status, now)
        self.store.add_case_action(
            ticket["run"],
            ticket["case_id"],
            kind=TICKET_STATUS,
            actor="management",
            house_time=now,
            detail={"ticket_id": ticket_id, "status": status},
        )
        self._changed(
            now, "ticket", {"id": ticket_id, "case_id": ticket["case_id"], "status": status}
        )
        return self.ticket(ticket_id)

    async def reply(self, ticket_id: str, text: str) -> dict[str, Any]:
        ticket = self.ticket(ticket_id)
        now = await self.service.house_time()
        ticket["messages"] = [*ticket["messages"], {"actor": "management", "text": text, "t": now}]
        self._save_ticket(ticket, ticket["status"], now)
        self.store.add_case_action(
            ticket["run"],
            ticket["case_id"],
            kind=TICKET_REPLY,
            actor="management",
            house_time=now,
            detail={"ticket_id": ticket_id, "text": text},
        )
        self._changed(
            now,
            "ticket",
            {"id": ticket_id, "case_id": ticket["case_id"], "status": ticket["status"]},
        )
        return self.ticket(ticket_id)

    def _save_ticket(self, ticket: dict[str, Any], status: str, now: str) -> None:
        base = ("kind", "id", "run", "case_id", "status", "created", "updated")
        doc = {k: v for k, v in ticket.items() if k not in base}
        self.store.put_record(
            "ticket",
            ticket["id"],
            run=ticket["run"],
            case_id=ticket["case_id"],
            status=status,
            house_time=now,
            doc=doc,
        )

    # ------------------------------------------------------------------ bookings

    def _vendor(self, case: dict[str, Any], params: dict[str, Any], now: str) -> dict[str, Any]:
        book = next((a for a in case["actions"] if a["kind"] == "book_vendor"), None)
        if book is None:
            raise ActionRefusedError("this case doesn't offer a booking")
        category = str(book.get("vendor_category", "plumber"))
        vendors = {v["id"]: v for v in list_vendors(category, now)}
        vendor = vendors.get(str(params.get("vendor_id")))
        if vendor is None:
            raise ActionRefusedError(f"pick one of the {category} vendors: {', '.join(vendors)}")
        if int(params.get("slot", 0)) not in vendor["next_slots"]:
            raise ActionRefusedError("pick one of the vendor's offered slots")
        return vendor

    @staticmethod
    def _booking_content(case: dict[str, Any], vendor: dict[str, Any], slot: int) -> dict[str, Any]:
        return {
            "problem": case["title"],
            "location": case["location"],
            "visit_slot": _slot_text(slot),
            "vendor": vendor["name"],
        }

    async def book(self, case_id: str, params: dict[str, Any], confirmation: str) -> dict[str, Any]:
        _, case = await self._case(case_id)
        self._offered(case, "booking")
        now = await self.service.house_time()
        vendor = self._vendor(case, params, now)
        now = await self._redeem(confirmation, case_id, ActionKind.booking, params)
        slot = int(params["slot"])
        content = self._booking_content(case, vendor, slot)
        booking_id = f"bkg_{secrets.token_hex(6)}"
        run = self._run()
        doc = {"vendor_id": vendor["id"], "vendor_name": vendor["name"], "slot": slot, **content}
        self.store.put_record(
            "booking",
            booking_id,
            run=run,
            case_id=case_id,
            status="requested",
            house_time=now,
            doc=doc,
        )
        self.store.add_case_action(
            run,
            case_id,
            kind=BOOKING,
            actor="owner",
            house_time=now,
            detail={
                "booking_id": booking_id,
                "vendor_name": vendor["name"],
                "slot_text": content["visit_slot"],
            },
        )
        self.store.log_disclosure(
            house_time=now,
            actor="owner",
            recipient=vendor["name"],
            purpose="book a repair visit",
            fields=sorted(content),
            basis="owner's confirmed request",
            ref=booking_id,
        )
        self._changed(now, "case", {"id": case_id, "booking": booking_id})
        record = self.store.record(booking_id)
        return dict(record or {})

    async def complete_booking(self, booking_id: str) -> dict[str, Any]:
        """Operator (the Lab) marks the illustrative vendor's job done; never automatic."""
        record = self.store.record(booking_id)
        if record is None or record["kind"] != "booking":
            raise KeyError(booking_id)
        if record["status"] == "completed":
            raise ActionRefusedError("booking is already completed")
        now = await self.service.house_time()
        base = ("kind", "id", "run", "case_id", "status", "created", "updated")
        doc = {k: v for k, v in record.items() if k not in base}
        self.store.put_record(
            "booking",
            booking_id,
            run=record["run"],
            case_id=record["case_id"],
            status="completed",
            house_time=now,
            doc={**doc, "done_at": now},
        )
        self.store.add_case_action(
            record["run"],
            record["case_id"],
            kind=BOOKING_COMPLETE,
            actor="vendor",
            house_time=now,
            detail={"booking_id": booking_id, "vendor_name": record["vendor_name"]},
        )
        self._changed(now, "case", {"id": record["case_id"], "booking": booking_id})
        return dict(self.store.record(booking_id) or {})

    def vendors(self, category: str, now: str) -> dict[str, Any]:
        return {"label": directory()["label"], "vendors": list_vendors(category, now)}

    # ------------------------------------------------------------------ guided checks

    def _check_target(self, state: CaseState, params: dict[str, Any]) -> tuple[str, str]:
        fixture = str(params.get("fixture_id"))
        for target, valve in guided_targets(state, self.service.index):
            if target == fixture:
                return target, valve
        raise ActionRefusedError("that fixture isn't one of this case's guided check steps")

    async def start_check(
        self, case_id: str, params: dict[str, Any], confirmation: str
    ) -> dict[str, Any]:
        state, case = await self._case(case_id)
        self._offered(case, "check")
        fixture, valve = self._check_target(state, params)
        now = await self._redeem(confirmation, case_id, ActionKind.check, params)
        check = await self.service.upstream.start_check(
            {
                "kind": "flow_stop",
                "since": now,
                "settle_s": CHECK_SETTLE_S,
                "window_s": CHECK_WINDOW_S,
                "expect_below_lpm": CHECK_STOPPED_BELOW_LPM,
            }
        )
        doc = {
            "fixture_id": fixture,
            "valve_id": valve,
            "ready_at": check["ready_at"],
            "result": None,
        }
        self.store.put_record(
            "check",
            check["check_id"],
            run=self._run(),
            case_id=case_id,
            status="pending",
            house_time=now,
            doc=doc,
        )
        return dict(self.store.record(check["check_id"]) or {})

    async def poll_checks(self) -> int:
        """Collect finished guided checks; how many completed."""
        done = 0
        for record in self.store.records("check", status="pending"):
            try:
                check = await self.service.upstream.get_check(record["id"])
            except UpstreamUnavailableError as exc:
                log.warning("check %s unavailable: %s", record["id"], exc)
                return done
            if check["status"] == "pending":
                continue
            now = await self.service.house_time()
            measured = {m["name"]: m.get("value") for m in check.get("measurements", [])}
            result = "stopped" if check.get("verdict") == "stopped" else "continued"
            if check["status"] == "unavailable":
                result = "unavailable"
            base = ("kind", "id", "run", "case_id", "status", "created", "updated")
            doc = {k: v for k, v in record.items() if k not in base}
            doc.update(
                result=None if result == "unavailable" else result,
                verdict=check.get("verdict"),
                flow_before=measured.get("flow_before"),
                flow_after=measured.get("flow_after"),
            )
            self.store.put_record(
                "check", record["id"], run=record["run"], case_id=record["case_id"],
                status=check["status"], house_time=now, doc=doc,
            )  # fmt: skip
            if result != "unavailable":
                names = self.service.names
                label = names.of(doc["fixture_id"])
                self.store.add_case_action(
                    record["run"],
                    record["case_id"],
                    kind=CHECK_RESULT,
                    actor="owner",
                    house_time=now,
                    detail={"label": f"{label[:1].lower()}{label[1:]}", "result": result},
                )
            self._changed(now, "case", {"id": record["case_id"], "check": record["id"]})
            done += 1
        return done

    # ------------------------------------------------------------------ events

    def _changed(self, house_time: str, kind: str, payload: dict[str, Any]) -> None:
        self.store.publish(kind, house_time, payload)
        self.service.publish_case_changes()
        self.service.notify()
