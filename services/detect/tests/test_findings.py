from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest

from penjaga_contract.validate import ContractError, load_context, validate_finding_document
from penjaga_detect.findings import HEARTBEAT_US, FindingTracker, Observation, finding_id

US = 1_000_000
T0 = 1_790_784_000 * US  # 2026-10-01T00:00:00+08:00
ZONE = ZoneInfo("Asia/Kuala_Lumpur")
SOURCE = {"detector": "test_detector", "kind": "rule", "version": "1.0.0"}


def obs(at_s: float, **kw: object) -> Observation:
    base: dict[str, object] = {
        "key": "L2:circuit_overload:circ_sockets_kitchen",
        "type": "circuit_overload",
        "severity": "act_soon",
        "confidence": 1.0,
        "evidence": [{"name": "current", "value": 20.5, "unit": "A"}],
        "at_us": T0 + round(at_s * US),
        "object_ids": ["circ_sockets_kitchen"],
    }
    base.update(kw)
    return Observation(**base)  # type: ignore[arg-type]


@pytest.fixture
def tracker() -> FindingTracker:
    return FindingTracker(load_context().house_id, SOURCE, ZONE)


def valid(finding: object) -> None:
    doc = finding.model_dump(mode="json", by_alias=True, exclude_none=True)  # type: ignore[attr-defined]
    report = validate_finding_document(doc, load_context())
    assert report.ok, [str(i) for i in report.errors]


def test_first_observation_opens_a_finding_with_a_deterministic_id(tracker: FindingTracker) -> None:
    f = tracker.observe(obs(10))
    assert f is not None
    assert f.revision == 1
    assert f.id == finding_id("L2:circuit_overload:circ_sockets_kitchen", T0 + 10 * US)
    assert f.status.value == "active"
    valid(f)
    again = FindingTracker(load_context().house_id, SOURCE, ZONE).observe(obs(10))
    assert again == f  # replays give identical findings


MODEL = {"confidence_kind": "calibrated_probability"}
URGENT = {"severity": "urgent", "safety_guidance_key": "sg_circuit_overload"}


def test_revisions_only_for_material_changes(tracker: FindingTracker) -> None:
    tracker.observe(obs(0, confidence=0.8, **MODEL))
    assert tracker.observe(obs(60, confidence=0.82, **MODEL)) is None
    raised = tracker.observe(obs(120, confidence=0.82, **MODEL, **URGENT))
    assert raised is not None
    assert raised.revision == 2
    assert raised.first_seen < raised.last_seen
    assert tracker.observe(obs(180, confidence=0.9, **MODEL, **URGENT)) is not None
    later = obs(180 + HEARTBEAT_US / US, confidence=0.9, **MODEL, **URGENT)
    heartbeat = tracker.observe(later)
    assert heartbeat is not None
    assert heartbeat.revision == 4
    valid(heartbeat)


def test_invalid_findings_are_refused(tracker: FindingTracker) -> None:
    with pytest.raises(ContractError, match="safety"):
        tracker.observe(obs(0, severity="urgent"))
    with pytest.raises(ContractError):
        tracker.observe(obs(0, confidence=0.5))  # a deterministic rule is certain
    with pytest.raises(ContractError):
        tracker.observe(obs(0, object_ids=["not_in_the_house"]))


def test_clear_and_reopen(tracker: FindingTracker) -> None:
    first = tracker.observe(obs(0))
    assert first is not None
    cleared = tracker.clear(first.key, T0 + 3600 * US)
    assert cleared is not None
    assert cleared.status.value == "cleared"
    assert cleared.id == first.id
    assert cleared.revision == 2
    valid(cleared)
    assert tracker.clear(first.key, T0 + 3601 * US) is None
    reopened = tracker.observe(obs(7200))
    assert reopened is not None
    assert reopened.id != first.id
    assert reopened.revision == 1


def test_time_must_move_forward(tracker: FindingTracker) -> None:
    tracker.observe(obs(100))
    tracker.observe(obs(200, **URGENT))
    with pytest.raises(ValueError, match="forward"):
        tracker.observe(obs(150))
