"""L4 maintenance reminders (PRD §16 maintenance rules, M10 Home Passport).

Rules are structured data (``data/maintenance_rules.json``), each with its source, a confidence
(official, secondary, unconfirmed) and any conflicting advice. A reminder counts from the last
time the owner marked it done, else from the element's install date in the house record, else
from the handover date. Short intervals (``ROLLING_DAYS`` or less, such as a monthly RCD test)
roll forward to the next date from today, because the house predates Penjaga; longer ones show
as overdue from the most recent date that was missed. Items of one rule due on the same day
show as one reminder.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import cache
from pathlib import Path
from typing import Any

from penjaga_api.cases import epoch_ms
from penjaga_api.names import Names
from penjaga_contract.house import HouseIndex

DATA = Path(__file__).resolve().parent / "data" / "maintenance_rules.json"
ROLLING_DAYS = 90
DONE_KIND = "maintenance_done"


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    group_title: str | None
    element_ids: tuple[str, ...]
    appliance_kinds: tuple[str, ...]
    interval_days: int
    interval: str
    source: str
    basis: str
    url: str
    confidence: str
    conflict: str | None


@dataclass(frozen=True)
class Item:
    """One reminder: a rule, the elements it covers, and when it is next due (house date)."""

    rule: Rule
    object_ids: tuple[str, ...]
    due: date
    overdue: bool
    last_done: str | None

    @property
    def id(self) -> str:
        return f"{self.rule.id}@{self.due.isoformat()}"


@cache
def rules() -> tuple[Rule, ...]:
    doc: dict[str, Any] = json.loads(DATA.read_text(encoding="utf-8"))
    return tuple(
        Rule(
            id=r["id"],
            title=r["title"],
            group_title=r.get("group_title"),
            element_ids=tuple(r["applies_to"].get("element_ids", ())),
            appliance_kinds=tuple(r["applies_to"].get("appliance_kinds", ())),
            interval_days=int(r["interval_days"]),
            interval=r["interval"],
            source=r["source"],
            basis=r["basis"],
            url=r["url"],
            confidence=r["confidence"],
            conflict=r.get("conflict"),
        )
        for r in doc["rules"]
    )


def targets(rule: Rule, index: HouseIndex) -> list[str]:
    """The elements of this house a rule applies to, in house record order."""
    out = [i for i in rule.element_ids if index.exists(i)]
    kinds = set(rule.appliance_kinds)
    out += [a.id for a in index.house.appliances if str(a.kind) in kinds or a.kind in kinds]
    return out


def handover(index: HouseIndex) -> date:
    return date.fromisoformat(str(index.house.handover_date))


def installed(index: HouseIndex, object_id: str) -> date:
    value = getattr(index.element(object_id).model, "install_date", None)
    return date.fromisoformat(str(value)) if value else handover(index)


def _next_due(anchor: date, days: int, today: date) -> tuple[date, bool]:
    due = anchor + timedelta(days=days)
    if due >= today:
        return due, False
    missed = (today - anchor).days // days  # whole intervals gone by since the anchor
    if days <= ROLLING_DAYS:
        due = anchor + timedelta(days=days * missed)
        return (due if due >= today else due + timedelta(days=days)), False
    return anchor + timedelta(days=days * missed), True


def schedule(
    index: HouseIndex, now: str, done: Mapping[tuple[str, str], str] | None = None
) -> list[Item]:
    """Every reminder for the house at house time ``now``, soonest first. ``done`` holds the
    house time each (rule, element) was last marked done."""
    done = done or {}
    today = datetime.fromisoformat(now).date()
    groups: dict[tuple[str, date], list[tuple[str, bool, str | None]]] = {}
    by_id = {r.id: r for r in rules()}
    for rule in rules():
        for object_id in targets(rule, index):
            last = done.get((rule.id, object_id))
            anchor = datetime.fromisoformat(last).date() if last else installed(index, object_id)
            due, overdue = _next_due(anchor, rule.interval_days, today)
            groups.setdefault((rule.id, due), []).append((object_id, overdue, last))
    items = [
        Item(
            rule=by_id[rule_id],
            object_ids=tuple(o for o, _, _ in members),
            due=due,
            overdue=any(over for _, over, _ in members),
            last_done=max((last for _, _, last in members if last), default=None),
        )
        for (rule_id, due), members in groups.items()
    ]
    items.sort(key=lambda i: (i.due, i.rule.id))
    return items


def last_done(records: Iterable[dict[str, Any]]) -> dict[tuple[str, str], str]:
    """(rule, element) → house time last marked done, from ``maintenance_done`` records."""
    out: dict[tuple[str, str], str] = {}
    for record in records:
        for object_id in record["object_ids"]:
            key = (record["rule"], object_id)
            if key not in out or out[key] < record["done_at"]:
                out[key] = record["done_at"]
    return out


def title(item: Item, names: Names) -> str:
    rule = item.rule
    if len(item.object_ids) > 1:
        if rule.group_title:
            return rule.group_title.replace("{count}", str(len(item.object_ids)))
        return rule.title.replace("{name}", names.listing(item.object_ids, limit=2))
    return rule.title.replace("{name}", names.of(item.object_ids[0]))


def due_ms(item: Item, now: str) -> int:
    """The start of the due day in house time, as the app's epoch milliseconds."""
    tz = datetime.fromisoformat(now).tzinfo
    return epoch_ms(datetime(item.due.year, item.due.month, item.due.day, tzinfo=tz).isoformat())


def to_view(item: Item, names: Names, now: str) -> dict[str, Any]:
    """The app's ``MaintenanceItem``, with the rule's sourcing alongside."""
    rule = item.rule
    return {
        "id": item.id,
        "title": title(item, names),
        "object_id": item.object_ids[0],
        "object_ids": list(item.object_ids),
        "due": due_ms(item, now),
        "overdue": item.overdue,
        "interval": rule.interval,
        "source": rule.source,
        "basis": rule.basis,
        "url": rule.url,
        "confidence": rule.confidence,
        "conflict": rule.conflict,
        "last_done": None if item.last_done is None else epoch_ms(item.last_done),
    }
