"""Finding lifecycle: one condition (key) keeps one id across revisions (PRD §10.4, D-006).

Detectors report *observations* of a condition; the tracker turns them into Finding revisions:
- the first observation opens a finding (revision 1) with ``id`` derived from the key and
  ``first_seen``, so replays produce identical ids;
- later observations update it, and a new revision is emitted only when something a person
  would care about changes (severity, type, candidates, a confidence move of at least
  ``CONFIDENCE_STEP``) or after ``HEARTBEAT_US`` of house time;
- ``clear`` closes it (status cleared). A later observation of the same key opens a new finding.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from penjaga_contract.core import Finding
from penjaga_contract.validate import load_context, validate_finding_document

US = 1_000_000
CONFIDENCE_STEP = 0.05
HEARTBEAT_US = 86_400 * US
SEVERITY_ORDER = ["info", "heads_up", "act_soon", "urgent"]


def finding_id(key: str, first_seen_us: int) -> str:
    digest = hashlib.sha256(f"{key}|{first_seen_us}".encode()).hexdigest()
    return f"fnd_{digest[:20]}"


@dataclass(frozen=True)
class Observation:
    """What a detector concluded about one condition at house time ``at_us``."""

    key: str
    type: str
    severity: str
    confidence: float
    evidence: Sequence[Mapping[str, Any]]
    at_us: int
    severity_floor: bool = False
    confidence_kind: str = "deterministic_rule"
    object_ids: Sequence[str] = ()
    candidates: Sequence[Mapping[str, Any]] = ()
    candidates_other_p: float = 0.0
    quantities: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    series_refs: Sequence[Mapping[str, Any]] = ()
    onset_us: int | None = None
    safety_guidance_key: str | None = None

    @property
    def layer(self) -> str:
        return self.key.split(":", 1)[0]


@dataclass
class _Open:
    id: str
    first_seen_us: int
    revision: int
    last: Finding
    emitted_at_us: int


class FindingTracker:
    def __init__(self, house_id: str, source: Mapping[str, str], zone: ZoneInfo) -> None:
        self.house_id = house_id
        self.source = dict(source)
        self.zone = zone
        self.open: dict[str, _Open] = {}

    def iso(self, us: int) -> str:
        return datetime.fromtimestamp(us / US, tz=self.zone).isoformat()

    def _document(self, obs: Observation, fid: str, revision: int, first_us: int) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "schema_version": "1.0",
            "id": fid,
            "key": obs.key,
            "revision": revision,
            "status": "active",
            "house_id": self.house_id,
            "layer": obs.layer,
            "type": obs.type,
            "severity": obs.severity,
            "severity_floor": obs.severity_floor,
            "confidence": round(float(obs.confidence), 4),
            "confidence_kind": obs.confidence_kind,
            "object_ids": list(obs.object_ids),
            "candidates": [dict(c) for c in obs.candidates],
            "candidates_other_p": round(float(obs.candidates_other_p), 4),
            "quantities": {k: dict(v) for k, v in obs.quantities.items()},
            "evidence": [dict(e) for e in obs.evidence],
            "series_refs": [dict(r) for r in obs.series_refs],
            "first_seen": self.iso(first_us),
            "last_seen": self.iso(obs.at_us),
            "updated_at": self.iso(obs.at_us),
            "source": self.source,
        }
        if obs.onset_us is not None:
            doc["onset_estimate"] = self.iso(min(obs.onset_us, obs.at_us))
        if obs.safety_guidance_key is not None:
            doc["safety_guidance_key"] = obs.safety_guidance_key
        return doc

    def observe(self, obs: Observation) -> Finding | None:
        """Record an observation; returns a new revision when one is due, else None."""
        current = self.open.get(obs.key)
        if current is None:
            fid = finding_id(obs.key, obs.at_us)
            finding = _checked(self._document(obs, fid, 1, obs.at_us))
            self.open[obs.key] = _Open(fid, obs.at_us, 1, finding, obs.at_us)
            return finding
        if obs.at_us < current.emitted_at_us:
            raise ValueError(f"{obs.key}: observations must move forward in house time")
        candidate = _checked(
            self._document(obs, current.id, current.revision + 1, current.first_seen_us)
        )
        if not _material(current.last, candidate, obs.at_us - current.emitted_at_us):
            return None
        current.revision += 1
        current.last = candidate
        current.emitted_at_us = obs.at_us
        return candidate

    def clear(self, key: str, at_us: int) -> Finding | None:
        """Close the finding for ``key`` (if open); returns its final revision."""
        current = self.open.pop(key, None)
        if current is None:
            return None
        doc = current.last.model_dump(mode="json", by_alias=True, exclude_none=True)
        doc.update(
            revision=current.revision + 1,
            status="cleared",
            cleared_at=self.iso(at_us),
            updated_at=self.iso(at_us),
        )
        return _checked(doc)

    def is_open(self, key: str) -> bool:
        return key in self.open


def _checked(doc: dict[str, Any]) -> Finding:
    """Full contract validation (the generated model alone skips conditional rules such as
    'urgent needs safety guidance' and 'deterministic rules have confidence 1')."""
    validate_finding_document(doc, load_context()).raise_for_errors(f"finding {doc['key']}")
    return Finding.model_validate(doc)


def _material(previous: Finding, new: Finding, since_us: int) -> bool:
    if since_us >= HEARTBEAT_US:
        return True
    if (previous.severity, previous.type) != (new.severity, new.type):
        return True
    if [c.object_id for c in previous.candidates] != [c.object_id for c in new.candidates]:
        return True
    return abs(previous.confidence - new.confidence) >= CONFIDENCE_STEP


def severity_rank(severity: str) -> int:
    return SEVERITY_ORDER.index(severity)
