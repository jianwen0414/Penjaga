"""L5 management notices (PRD §6.5 clue-linking, §17 management portal).

Township management posts notices of planned water or power works. Penjaga shows the ones that
reach this house, says what each means for it (the roof tank, the taps fed from the mains, the
appliances on the board), and links a supply drop to a notice that explains it, so planned works
aren't mistaken for faults. A drop no notice explains says so, and suggests asking management.

Matching rule: the drop must begin between an hour before the notice's start and the expected
recovery (or ``RECOVERY_GRACE`` after the end when the notice gives no recovery time). When the
notice was posted doesn't matter: a notice posted after the drop still explains it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from penjaga_api.cases import epoch_ms

NOTICE_KIND = "notice"
PRESETS = Path(__file__).resolve().parent / "data" / "notice_presets.json"
LEAD = timedelta(hours=1)  # a drop up to an hour before the start still counts
RECOVERY_GRACE = timedelta(hours=6)
# This house's locality and street, from the house record's location. A notice reaches the house
# when one of its areas names one of these, or when it names no areas and covers the district.
AREA_KEYWORDS = ("elmina", "u17", "cassia")
DISTRICT_KEYWORDS = ("shah alam", "petaling")

NoticeType = Literal["water_disruption", "planned_power_outage", "works", "general"]
Utility = Literal["water", "electricity", "other"]

SUPPLY_FINDINGS: dict[str, tuple[str, frozenset[str]]] = {
    "water_supply_interruption": ("water", frozenset({"water_disruption", "works"})),
    "power_supply_interruption": ("electricity", frozenset({"planned_power_outage", "works"})),
}


class NoticeDraft(BaseModel):
    """A notice as staff confirm it, whether typed in or read from pasted text."""

    model_config = ConfigDict(extra="forbid")

    type: NoticeType
    utility: Utility
    scheduled: bool = True
    title: str = Field(min_length=3, max_length=140)
    reason: str = Field(default="", max_length=400)
    areas: list[str] = Field(default_factory=list, max_length=20)
    district: str = Field(default="", max_length=120)
    starts_at: datetime
    ends_at: datetime
    expected_recovery: datetime | None = None
    recovery_note: str = Field(default="", max_length=400)
    advice: list[str] = Field(default_factory=list, max_length=8)
    alternative_supply: str | None = Field(default=None, max_length=300)
    contact: str | None = Field(default=None, max_length=200)
    reference: str | None = Field(default=None, max_length=60)

    @field_validator("starts_at", "ends_at", "expected_recovery")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("times need a UTC offset (house time is +08:00)")
        return value

    @model_validator(mode="after")
    def _order(self) -> NoticeDraft:
        if self.ends_at <= self.starts_at:
            raise ValueError("the end must be after the start")
        if self.expected_recovery is not None and self.expected_recovery < self.starts_at:
            raise ValueError("supply can't be expected back before the works start")
        return self


def reaches_house(areas: list[str], district: str) -> bool:
    text = " ".join(areas).lower()
    if any(k in text for k in AREA_KEYWORDS):
        return True
    return not areas and any(k in district.lower() for k in DISTRICT_KEYWORDS)


def draft_issues(draft: NoticeDraft, now: str) -> list[str]:
    """Things staff should look at before publishing (none of them stop publishing)."""
    issues: list[str] = []
    if not reaches_house(draft.areas, draft.district):
        issues.append(
            "The areas don't name Elmina Green, Seksyen U17 or Jalan Cassia, so this notice won't "
            "reach the homes Penjaga watches."
        )
    if draft.ends_at < datetime.fromisoformat(now):
        issues.append("These works have already ended.")
    if draft.utility == "water" and draft.expected_recovery is None:
        issues.append("No time for supply to be fully back: homeowners will see the end time only.")
    return issues


@dataclass(frozen=True)
class NoticeMatch:
    """A notice that explains a case's supply drop, or None-like absence handled by callers."""

    notice: dict[str, Any]
    issuer: str
    late: bool  # the case is still open after the notice's expected recovery


def window_end(notice: dict[str, Any]) -> datetime:
    """Until when a drop beginning can still be explained by the notice."""
    if notice.get("expected_recovery"):
        return datetime.fromisoformat(notice["expected_recovery"])
    return datetime.fromisoformat(notice["ends_at"]) + RECOVERY_GRACE


def back_by(notice: dict[str, Any]) -> datetime:
    """When the notice said supply would be back."""
    return datetime.fromisoformat(notice.get("expected_recovery") or notice["ends_at"])


def explains(notice: dict[str, Any], finding_type: str, began: str) -> bool:
    """Does a published notice that reaches the house explain a supply finding begun at
    ``began``?"""
    wanted = SUPPLY_FINDINGS.get(finding_type)
    if wanted is None or notice.get("status") != "published" or not notice.get("reaches_house"):
        return False
    utility, types = wanted
    if notice["utility"] != utility or notice["type"] not in types:
        return False
    t = datetime.fromisoformat(began)
    return datetime.fromisoformat(notice["starts_at"]) - LEAD <= t <= window_end(notice)


def during_recovery(notice: dict[str, Any], began: str) -> bool:
    """Did something begin while water was off or coming back after a notice's works?"""
    if notice.get("status") != "published" or not notice.get("reaches_house"):
        return False
    if notice["utility"] != "water":
        return False
    t = datetime.fromisoformat(began)
    return datetime.fromisoformat(notice["starts_at"]) <= t <= window_end(notice) + RECOVERY_GRACE


def body(notice: dict[str, Any]) -> str:
    parts = [notice.get("reason", ""), notice.get("recovery_note", ""), *notice.get("advice", [])]
    parts += [notice.get("alternative_supply") or "", notice.get("contact") or ""]
    return " ".join(f"{p.strip().rstrip('.')}." for p in parts if p and p.strip())


def to_view(notice: dict[str, Any], issuer: str, impact: str | None) -> dict[str, Any]:
    """The app's ``Notice``, with the structured fields alongside."""
    recovery = notice.get("expected_recovery")
    out: dict[str, Any] = {
        "id": notice["id"],
        "type": notice["type"],
        "title": notice["title"],
        "issuer": issuer,
        "area": ", ".join(notice["areas"]) or notice.get("district", ""),
        "starts_at": epoch_ms(notice["starts_at"]),
        "ends_at": epoch_ms(notice["ends_at"]),
        "body": body(notice),
        "utility": notice["utility"],
        "expected_recovery": None if not recovery else epoch_ms(recovery),
        "advice": list(notice.get("advice", [])),
        "reference": notice.get("reference"),
        "contact": notice.get("contact"),
        "status": notice["status"],
    }
    if impact:
        out["impact"] = impact
    return out


@cache
def _presets() -> dict[str, Any]:
    doc: dict[str, Any] = json.loads(PRESETS.read_text(encoding="utf-8"))
    return doc


def preset_names() -> tuple[str, ...]:
    return tuple(_presets()["presets"])


def from_preset(name: str, now: str, starts_in_h: float | None = None) -> tuple[NoticeDraft, str]:
    """A seed notice placed relative to house time ``now``, and its source note."""
    doc = _presets()
    p = doc["presets"][name]
    start = datetime.fromisoformat(now) + timedelta(
        hours=p["starts_in_h"] if starts_in_h is None else starts_in_h
    )
    start = start.replace(second=0, microsecond=0)
    end = start + timedelta(hours=p["duration_h"])
    recovery = end + timedelta(hours=p["recovery_after_h"]) if p["recovery_after_h"] else None
    draft = NoticeDraft(
        type=p["type"],
        utility=p["utility"],
        scheduled=p["scheduled"],
        title=p["title"],
        reason=p["reason"],
        areas=p["areas"],
        district=p["district"],
        starts_at=start,
        ends_at=end,
        expected_recovery=recovery,
        recovery_note=p["recovery_note"],
        advice=p["advice"],
        alternative_supply=p["alternative_supply"],
        contact=p["contact"],
        reference=p["reference"],
    )
    return draft, f"{doc['label']}: {p['adapted_from']}"
