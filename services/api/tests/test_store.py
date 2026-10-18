from __future__ import annotations

from datetime import datetime

import pytest

from penjaga_api.store import DEFAULT_RETENTION_DAYS, ApiStore
from penjaga_api.testing import load_findings, revision


def test_findings_are_stored_once_per_revision(store: ApiStore) -> None:
    docs = load_findings("l2_elderly")
    assert store.add_findings("epi_a", docs) == docs
    assert store.add_findings("epi_a", docs) == []
    assert store.conflicts == 0


def test_a_different_document_under_a_stored_revision_is_counted_and_ignored(
    store: ApiStore,
) -> None:
    (doc,) = load_findings("l1_leak")
    store.add_findings("epi_a", [doc])
    changed = {**doc, "severity": "urgent"}
    assert store.add_findings("epi_a", [changed]) == []
    assert store.conflicts == 1
    assert store.latest_findings("epi_a")[0]["severity"] == doc["severity"]


def test_latest_keeps_the_highest_revision_and_runs_stay_apart(store: ApiStore) -> None:
    (doc,) = load_findings("l1_leak")
    older = revision(doc, 1, "2026-10-19T04:00:00+08:00")
    store.add_findings("epi_a", [older, doc])
    store.add_findings("epi_b", load_findings("l5_water"))
    (latest,) = store.latest_findings("epi_a")
    assert latest["revision"] == doc["revision"]
    assert [d["revision"] for d in store.finding_history("epi_a", doc["id"])] == [1, 7]
    assert [d["layer"] for d in store.latest_findings("epi_b")] == ["L5"]
    assert store.latest_findings("epi_a", layer="L5") == []


def test_consent_keeps_every_decision_and_reports_the_latest(store: ApiStore) -> None:
    store.record_consent("L1", False, "v1", "homeowner", "2025-11-05T10:00:00+08:00")
    store.record_consent("L1", True, "v1", "homeowner", "2025-11-06T10:00:00+08:00")
    store.record_consent("L2", False, "v1", "homeowner", "2025-11-06T11:00:00+08:00")
    state = store.consent_state()
    assert state["L1"]["enabled"] is True
    assert state["L1"]["decided_at"] == "2025-11-06T10:00:00+08:00"
    assert state["L2"]["enabled"] is False
    assert [h["enabled"] for h in store.consent_history()] == [False, True, False]


def test_disclosures_record_recipient_fields_and_basis(store: ApiStore) -> None:
    seq = store.log_disclosure(
        house_time="2025-11-05T10:00:00+08:00",
        actor="homeowner",
        recipient="management",
        purpose="ticket",
        fields=["case_summary", "location"],
        basis="owner confirmation",
        ref="tkt_1",
    )
    (entry,) = store.disclosures()
    assert entry["seq"] == seq
    assert entry["fields"] == ["case_summary", "location"]
    assert entry["recipient"] == "management"


def test_retention_defaults_updates_and_rejects(store: ApiStore) -> None:
    assert store.retention() == DEFAULT_RETENTION_DAYS
    store.set_retention("events", 7)
    assert store.retention()["events"] == 7
    with pytest.raises(KeyError):
        store.set_retention("photos", 7)
    with pytest.raises(ValueError, match="at least one day"):
        store.set_retention("events", 0)


def test_purge_deletes_only_what_is_older_than_its_retention(store: ApiStore) -> None:
    (doc,) = load_findings("l1_leak")  # updated 2026-10-25
    store.add_findings("epi_a", [doc])
    store.publish("finding", "2026-10-01T00:00:00+08:00", {"id": "old"})
    store.publish("finding", "2026-10-30T00:00:00+08:00", {"id": "new"})
    deleted = store.purge(datetime.fromisoformat("2026-11-15T00:00:00+08:00"))
    assert deleted == {"findings": 0, "disclosures": 0, "events": 1, "chat": 0}
    assert [e.payload["id"] for e in store.events_after(0)] == ["new"]
    store.set_retention("findings", 1)
    assert store.purge(datetime.fromisoformat("2026-11-15T00:00:00+08:00"))["findings"] == 1
    assert store.latest_findings("epi_a") == []


def test_erase_all_removes_every_personal_record(store: ApiStore) -> None:
    store.add_findings("epi_a", load_findings("l1_leak"))
    store.note_episode("epi_a", "2026-10-17T00:00:00+08:00")
    store.record_consent("L1", True, "v1", "homeowner", "2025-11-05T10:00:00+08:00")
    store.log_disclosure(
        house_time="2025-11-05T10:00:00+08:00",
        actor="homeowner",
        recipient="vendor",
        purpose="booking",
        fields=["slot"],
        basis="consent",
    )
    store.publish("finding", "2025-11-05T10:00:00+08:00", {"id": "x"})
    store.set_value("detect_cursor", "12")
    store.add_case_action(
        "epi_a",
        "case_x",
        kind="dismiss",
        actor="owner",
        house_time="2025-11-05T10:00:00+08:00",
        detail={"reason": "x"},
    )
    store.erase_all()
    assert store.case_actions("epi_a") == []
    assert store.latest_findings("epi_a") == []
    assert store.current_episode() is None
    assert store.consent_history() == []
    assert store.disclosures() == []
    assert store.events_after(0) == []
    assert store.get_value("detect_cursor") is None


def test_case_actions_are_kept_per_run_and_follow_findings_retention(store: ApiStore) -> None:
    for run, t in (("epi_a", "2025-01-01T10:00:00+08:00"), ("epi_b", "2025-11-05T10:00:00+08:00")):
        store.add_case_action(
            run, "case_x", kind="dismiss", actor="owner", house_time=t, detail={"reason": run}
        )
    (action,) = store.case_actions("epi_b")
    assert (action.case_id, action.kind, action.actor) == ("case_x", "dismiss", "owner")
    assert action.detail == {"reason": "epi_b"}
    store.set_retention("findings", 30)
    deleted = store.purge(datetime.fromisoformat("2025-11-06T00:00:00+08:00"))
    assert deleted["findings"] == 1
    assert store.case_actions("epi_a") == []
    assert len(store.case_actions("epi_b")) == 1
