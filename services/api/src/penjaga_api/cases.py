"""Findings → cases: grouping, status, severity and timeline (PRD §18.2.1, §19).

A **case** is one underlying condition as the owner sees it. It is derived, deterministically, from
every finding revision of the run plus what people did on the case (``case_actions``), so a restart
or a replay rebuilds exactly the same cases. Wording lives in ``templates``; this module decides
only *which* findings form a case and *what state* it is in.

Grouping (one case per underlying element):
- L1: all water findings at the house's single meter form one case (a burst and the night flow
  it causes are the same event);
- L2: by circuit (an MCB maps to the circuit it protects), or by the RCCB for RCCB-level findings;
- L3: by appliance; L5: by supply (water or power).
Within a group, findings chain into the same case while it is active or until ``REOPEN_WINDOW``
after it resolved (so "it tripped again two days later" reopens the same case); later findings
open a new case.

Severity: the highest severity among the active findings. A finding with ``severity_floor`` keeps
the case at the highest severity it ever had while it is active, whatever a later revision says
(PRD §15.3 🔒). A resolved case shows its peak.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from penjaga_api.labels import SEVERITY_WORDS, finding_label
from penjaga_api.store import CaseAction
from penjaga_contract.house import ElementKind, HouseIndex

SEVERITY_RANK = {"info": 0, "heads_up": 1, "act_soon": 2, "urgent": 3}

# A finding that starts within this house time after its case resolved reopens the case.
REOPEN_WINDOW = timedelta(days=7)

# Calibrated model findings below this confidence wait in ``monitoring`` instead of opening.
MONITOR_BELOW_CONFIDENCE = 0.5

# Appliance-health findings costing less than this much extra energy a day wait in ``monitoring``
# (0.1 kWh/day is about 3 kWh a month: calm by default, PRD §6.1). Step 4's cost calculator
# replaces this with the cost of waiting.
NEGLIGIBLE_KWH_PER_DAY = 0.1

# What people did on a case (``case_actions``). The timeline texts for bookings, checks and
# tickets match the ones the app's conversation view recognises.
DISMISS = "dismiss"
TICKET = "ticket"
TICKET_STATUS = "ticket_status"
TICKET_REPLY = "ticket_reply"
BOOKING = "booking"
BOOKING_ACK = "booking_ack"  # derived: the (illustrative) vendor confirms after a short while
BOOKING_COMPLETE = "booking_complete"
CHECK_RESULT = "check_result"
BOOKING_ACK_AFTER = timedelta(minutes=30)  # house time
TICKET_STATUS_WORDS = {
    "received": "received your report",
    "scheduled": "scheduled a visit",
    "in_progress": "is working on it",
    "resolved": "marked it resolved",
}


def house_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def epoch_ms(value: str) -> int:
    return int(house_dt(value).timestamp() * 1000)


@dataclass
class Track:
    """One finding (stable ``id``) and every revision of it seen in the run, in order."""

    id: str
    revisions: list[dict[str, Any]] = field(default_factory=list)

    @property
    def latest(self) -> dict[str, Any]:
        return self.revisions[-1]

    @property
    def first(self) -> dict[str, Any]:
        return self.revisions[0]

    @property
    def active(self) -> bool:
        return bool(self.latest["status"] == "active")

    @property
    def ended_at(self) -> str | None:
        doc = self.latest
        if doc["status"] == "active":
            return None
        return str(doc.get("cleared_at") or doc["updated_at"])

    @property
    def peak(self) -> str:
        return str(max((r["severity"] for r in self.revisions), key=SEVERITY_RANK.__getitem__))


@dataclass(frozen=True)
class TimelineEntry:
    t: str  # house time
    actor: str
    text: str


@dataclass
class CaseState:
    id: str
    group: str
    layer: str
    tracks: list[Track]
    status: str
    severity: str
    floor: bool
    opened_at: str
    resolved_at: str | None
    timeline: list[TimelineEntry]
    dismissal: CaseAction | None
    pending: bool = False  # a ticket or booking is still open
    outcome: str | None = None

    @property
    def active_tracks(self) -> list[Track]:
        return [t for t in self.tracks if t.active]

    @property
    def lead(self) -> dict[str, Any]:
        """The finding revision the case is written around: the most severe active finding (the
        most recently updated on a tie), or the most severe one ever if the case is resolved."""
        pool = self.active_tracks or self.tracks
        best = max(
            pool,
            key=lambda t: (
                SEVERITY_RANK[t.latest["severity"] if t.active else t.peak],
                t.latest["updated_at"],
            ),
        )
        return best.latest

    @property
    def related(self) -> list[dict[str, Any]]:
        """Latest revisions of the case's other findings, active first."""
        lead_id = self.lead["id"]
        others = [t for t in self.tracks if t.id != lead_id]
        others.sort(key=lambda t: (not t.active, t.first["first_seen"]))
        return [t.latest for t in others]

    def object_ids(self) -> list[str]:
        """Every element the case's findings name with certainty, lead first."""
        seen: dict[str, None] = {}
        for doc in [self.lead, *self.related]:
            for object_id in doc["object_ids"]:
                seen.setdefault(object_id, None)
        return list(seen)


# ---------------------------------------------------------------------- grouping


def group_of(doc: dict[str, Any], index: HouseIndex) -> str:
    layer = str(doc["layer"])
    if layer == "L1":
        return f"L1:{index.meter_id}"
    if layer == "L5":
        return f"L5:{doc['type']}"
    target = _target(doc)
    if layer == "L2" and target is not None and index.exists(target):
        if index.kind_of(target) is ElementKind.mcb:
            circuits = index.circuits_under(target)
            if len(circuits) == 1:
                return f"L2:{circuits[0]}"
        return f"L2:{target}"
    return f"{layer}:{target or doc['type']}"


def _target(doc: dict[str, Any]) -> str | None:
    """The element a finding is about: a circuit before its breaker, else the first object ID,
    else the last segment of its key."""
    ids: list[str] = list(doc["object_ids"])
    for prefix in ("circ_", "app_", "rcd_", "mcb_"):
        for object_id in ids:
            if object_id.startswith(prefix):
                return object_id
    if ids:
        return ids[0]
    parts = str(doc["key"]).split(":")
    return parts[2] if len(parts) > 2 else None


def _tracks(revisions: Iterable[dict[str, Any]]) -> list[Track]:
    by_id: dict[str, Track] = {}
    for doc in revisions:
        by_id.setdefault(doc["id"], Track(doc["id"])).revisions.append(doc)
    for track in by_id.values():
        track.revisions.sort(key=lambda d: int(d["revision"]))
    return sorted(by_id.values(), key=lambda t: (t.first["first_seen"], t.id))


def _chain(tracks: list[Track]) -> list[list[Track]]:
    """Split one group's findings into cases: a finding joins the current case unless every
    finding in it had ended more than ``REOPEN_WINDOW`` before it started."""
    cases: list[list[Track]] = []
    for track in tracks:
        if cases:
            current = cases[-1]
            ends = [t.ended_at for t in current]
            if any(e is None for e in ends):
                current.append(track)
                continue
            last_end = max(house_dt(e) for e in ends if e is not None)
            if house_dt(track.first["first_seen"]) <= last_end + REOPEN_WINDOW:
                current.append(track)
                continue
        cases.append([track])
    return cases


def case_id(run: str, group: str, first_finding_id: str) -> str:
    digest = hashlib.sha256(f"{run}|{group}|{first_finding_id}".encode()).hexdigest()
    return f"case_{digest[:20]}"


# ---------------------------------------------------------------------- state


def build_cases(
    run: str,
    revisions: Iterable[dict[str, Any]],
    actions: Iterable[CaseAction],
    index: HouseIndex,
    now: str | None = None,
) -> list[CaseState]:
    """Every case of ``run``: most severe open ones first, resolved and dismissed last.
    ``now`` (house time) adds the events that follow from time alone (a vendor confirming)."""
    actions = _with_derived(list(actions), now)
    groups: dict[str, list[Track]] = {}
    for track in _tracks(revisions):
        groups.setdefault(group_of(track.first, index), []).append(track)
    by_case: dict[str, list[CaseAction]] = {}
    for action in actions:
        by_case.setdefault(action.case_id, []).append(action)
    cases = []
    for group, tracks in groups.items():
        for members in _chain(tracks):
            cid = case_id(run, group, members[0].id)
            cases.append(_state(cid, group, members, by_case.get(cid, [])))
    cases.sort(key=_display_order)
    return cases


def _with_derived(actions: list[CaseAction], now: str | None) -> list[CaseAction]:
    if now is None:
        return actions
    acked = {a.detail.get("booking_id") for a in actions if a.kind == BOOKING_ACK}
    out = list(actions)
    for a in actions:
        if a.kind != BOOKING or a.detail.get("booking_id") in acked:
            continue
        at = house_dt(a.house_time) + BOOKING_ACK_AFTER
        if at <= house_dt(now):
            out.append(CaseAction(0, a.case_id, BOOKING_ACK, "vendor", at.isoformat(), a.detail))
    return out


def _display_order(case: CaseState) -> tuple[int, int, str]:
    settled = case.status in ("resolved", "dismissed")
    rank = SEVERITY_RANK[case.severity]
    newest = -epoch_ms(case.timeline[-1].t) if case.timeline else 0
    return (1 if settled else 0, -rank, str(newest).zfill(20))


def _severity(latest: dict[str, dict[str, Any]], floors: dict[str, str]) -> tuple[str, bool]:
    """Case severity from each active finding's latest revision, held up by safety floors."""
    rank = -1
    floor = False
    for finding_id, doc in latest.items():
        if doc["status"] != "active":
            continue
        rank = max(rank, SEVERITY_RANK[doc["severity"]])
        if finding_id in floors:
            floor = True
            rank = max(rank, SEVERITY_RANK[floors[finding_id]])
    if rank < 0:
        return "info", floor
    return next(s for s, r in SEVERITY_RANK.items() if r == rank), floor


class _Replay:
    """Walks a case's finding revisions and owner actions in house-time order, keeping the
    case's severity, safety floors, resolution, dismissal and timeline as they were then."""

    def __init__(self) -> None:
        self.latest: dict[str, dict[str, Any]] = {}
        self.floors: dict[str, str] = {}  # finding id → highest severity it had under a floor
        self.peak = "info"
        self.timeline: list[TimelineEntry] = []
        self.shown: str | None = None  # severity the case was last shown at
        self.resolved_at: str | None = None
        self.dismissal: CaseAction | None = None
        self.dismissed_rank = -1
        self.open_tickets: set[str] = set()
        self.open_bookings: set[str] = set()
        self.outcome: str | None = None

    def note(self, t: str, text: str, actor: str = "penjaga") -> None:
        self.timeline.append(TimelineEntry(t, actor, text))

    def action(self, t: str, action: CaseAction) -> None:
        d = action.detail
        if action.kind == DISMISS:
            self.dismissal = action
            self.dismissed_rank = SEVERITY_RANK[self.shown or "info"]
            reason = str(d.get("reason") or "").strip()
            self.note(t, "Marked as not an issue" + (f": {reason}" if reason else ""), action.actor)
        elif action.kind == TICKET:
            self.open_tickets.add(str(d["ticket_id"]))
            self.note(t, "Sent to management", action.actor)
        elif action.kind == TICKET_STATUS:
            if d["status"] == "resolved":
                self.open_tickets.discard(str(d["ticket_id"]))
            words = TICKET_STATUS_WORDS.get(str(d["status"]), str(d["status"]))
            self.note(t, f"Management {words}", "management")
        elif action.kind == TICKET_REPLY:
            self.note(t, f"Management: {d['text']}", "management")
        elif action.kind == BOOKING:
            self.open_bookings.add(str(d["booking_id"]))
            self.note(t, f"Booked {d['vendor_name']} for {d['slot_text']}", action.actor)
        elif action.kind == BOOKING_ACK:
            self.note(t, f"{d['vendor_name']} confirmed the visit", "vendor")
        elif action.kind == BOOKING_COMPLETE:
            self.open_bookings.discard(str(d["booking_id"]))
            self.outcome = f"Repaired by {d['vendor_name']}"
            self.note(t, "Job marked complete", "vendor")
        elif action.kind == CHECK_RESULT:
            if d.get("check") == "breaker":
                result = "dropped" if d["result"] == "stopped" else "unchanged"
                self.note(
                    t, f"Switched off the {d['label']} circuit: leakage {result}", action.actor
                )
            else:
                result = "stopped" if d["result"] == "stopped" else "continued"
                self.note(t, f"Checked {d['label']}: flow {result}", action.actor)

    def revision(self, t: str, doc: dict[str, Any]) -> None:
        previous = self.latest.get(doc["id"])
        self.latest[doc["id"]] = doc
        active = doc["status"] == "active"
        if doc["severity_floor"] and active:
            held = self.floors.get(doc["id"], "info")
            self.floors[doc["id"]] = max(held, doc["severity"], key=SEVERITY_RANK.__getitem__)
        self.peak = max(self.peak, doc["severity"], key=SEVERITY_RANK.__getitem__)
        self._describe(t, doc, previous)
        if not any(d["status"] == "active" for d in self.latest.values()):
            if self.resolved_at is None:
                self.resolved_at = str(doc.get("cleared_at") or t)
                self.note(t, "Readings back to normal")
            return
        severity, _ = _severity(self.latest, self.floors)
        if self.shown is not None and severity != self.shown:
            verb = "Raised" if SEVERITY_RANK[severity] > SEVERITY_RANK[self.shown] else "Lowered"
            self.note(t, f"{verb} to {SEVERITY_WORDS[severity]}")
        if self.dismissal is not None:
            if SEVERITY_RANK[severity] > self.dismissed_rank:
                self.note(t, "Reopened: this has become more serious")
                self.dismissal = None
            elif previous is None:
                self.note(t, "Reopened: seen again")
                self.dismissal = None
        self.shown = severity

    def _describe(self, t: str, doc: dict[str, Any], previous: dict[str, Any] | None) -> None:
        label = finding_label(doc["type"])
        if previous is None and doc["status"] == "active":
            if self.resolved_at is not None:
                self.note(t, f"Seen again: {label}")
                self.resolved_at = None
            else:
                self.note(t, f"{'Also noticed' if self.timeline else 'Noticed'} {label}")
        elif doc["status"] != "active" and (previous is None or previous["status"] == "active"):
            if previous is None:  # first seen already over (e.g. backfilled after a restart)
                self.note(str(doc["first_seen"]), f"Noticed {label}")
            ended = "Replaced by a newer finding" if doc["status"] == "superseded" else "Stopped"
            self.note(t, f"{ended}: {label}")


def _state(
    cid: str,
    group: str,
    tracks: list[Track],
    actions: list[CaseAction],
) -> CaseState:
    events: list[tuple[str, int, Any]] = [
        (str(doc["updated_at"]), 0, doc) for track in tracks for doc in track.revisions
    ]
    events += [(a.house_time, 1, a) for a in actions]
    events.sort(key=lambda e: (house_dt(e[0]), e[1]))
    replay = _Replay()
    for t, _, item in events:
        if isinstance(item, CaseAction):
            replay.action(t, item)
        else:
            replay.revision(t, item)

    severity, floor = _severity(replay.latest, replay.floors)
    active = [t for t in tracks if t.active]
    pending = bool(replay.open_tickets or replay.open_bookings)
    if not active:
        severity = replay.peak
    if pending:
        status = "action_pending"  # a cleared finding waits for the open ticket or booking
    elif not active:
        status = "resolved"
    elif replay.dismissal is not None:
        status = "dismissed"
    elif _monitor_only(active, severity, floor):
        status = "monitoring"
    else:
        status = "open"
    return CaseState(
        id=cid,
        group=group,
        layer=str(tracks[0].first["layer"]),
        tracks=tracks,
        status=status,
        severity=severity,
        floor=floor,
        opened_at=str(tracks[0].first["updated_at"]),
        resolved_at=None if active or pending else replay.resolved_at,
        timeline=sorted(replay.timeline, key=lambda e: house_dt(e.t)),
        dismissal=replay.dismissal if active else None,
        pending=pending,
        outcome=replay.outcome,
    )


def _monitor_only(active: list[Track], severity: str, floor: bool) -> bool:
    """Calm by default: low-stakes or low-confidence findings are watched, not raised."""
    if floor or severity in ("act_soon", "urgent"):
        return False
    if severity == "info":
        return True
    docs = [t.latest for t in active]
    if all(
        d["confidence_kind"] == "calibrated_probability"
        and float(d["confidence"]) < MONITOR_BELOW_CONFIDENCE
        for d in docs
    ):
        return True
    return all(_negligible(d) for d in docs)


def _negligible(doc: dict[str, Any]) -> bool:
    if doc["layer"] != "L3":
        return False
    extra = doc["quantities"].get("extra_energy_kwh_per_day")
    return extra is not None and float(extra["value"]) < NEGLIGIBLE_KWH_PER_DAY


def dismissable(case: CaseState) -> bool:
    """An owner may dismiss an active case unless it is urgent or under a safety floor
    (PRD §18.4 🔒: never downplay a safety-floor finding)."""
    return case.status in ("open", "monitoring") and not case.floor and case.severity != "urgent"
