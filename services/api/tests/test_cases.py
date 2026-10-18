"""Findings → cases: grouping, lifecycle, severity floors, templates and wording rules (step 3)."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

import pytest

from penjaga_api.cases import CaseState, build_cases, dismissable
from penjaga_api.names import Names
from penjaga_api.safety import guidance_keys
from penjaga_api.store import CaseAction
from penjaga_api.templates import TEMPLATES, render
from penjaga_api.testing import FINDING_TYPES, finding, load_findings, revision
from penjaga_contract.resources import schema_path
from penjaga_contract.validate import load_context, validate_finding_document

CTX = load_context()
INDEX = CTX.index()
NAMES = Names(INDEX)
ROOT = Path(__file__).resolve().parents[3]
API_SRC = ROOT / "services" / "api" / "src" / "penjaga_api"
FIXTURES = ["l1_leak", "l1_burst", "l2_elderly", "l2_l5_wet", "l3", "l5_water"]
NOW = "2025-11-10T12:00:00+08:00"

# The app's Case shape (src/api/types.ts on the app branch).
CASE_KEYS = {
    "id", "status", "layer", "severity", "title", "location", "summary", "confidence",
    "confidence_note", "impact", "actions", "evidence", "timeline", "focus", "opened_at",
}  # fmt: skip
OPTIONAL_KEYS = {"safety", "guided", "booking", "resolved_at", "outcome"}
STATUSES = {"open", "monitoring", "action_pending", "resolved", "dismissed"}
SEVERITIES = ["info", "heads_up", "act_soon", "urgent"]
ACTION_KINDS = {"guided_check", "book_vendor", "send_management", "dismiss", "show_in_3d"}
TIMELINE_ACTORS = {"penjaga", "owner", "vendor", "management"}
BANNED_TRADE_TERMS = re.compile(r"licensed (electrician|plumber)|wireman", re.IGNORECASE)
ID_TOKEN = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")


def cases_of(
    docs: list[dict[str, Any]], actions: list[CaseAction] | None = None
) -> list[CaseState]:
    return build_cases("run_test", docs, actions or [], INDEX)


def rendered(state: CaseState, now: str = NOW) -> dict[str, Any]:
    return render(state, INDEX, NAMES, now).case


def texts(case: dict[str, Any]) -> list[str]:
    """Every owner-facing string in a case."""
    out = [case["title"], case["location"], case["summary"], case["confidence_note"]]
    out += [case["impact"]["headline"], *case["impact"]["lines"], case["impact"].get("how", "")]
    out += case.get("safety", [])
    for action in case["actions"]:
        out += [action["label"], action.get("detail", "")]
        if "focus" in action:
            out.append(action["focus"].get("label", ""))
    ev = case["evidence"]
    out += [ev["title"], ev["explanation"]]
    out += [f"{s['label']} {s['value']}" for s in ev["stats"]]
    out += [e["text"] for e in case["timeline"]]
    out.append(case["focus"].get("label", ""))
    return [t for t in out if t]


def assert_valid_case(case: dict[str, Any]) -> None:
    assert CASE_KEYS <= set(case) <= CASE_KEYS | OPTIONAL_KEYS
    assert case["status"] in STATUSES
    assert case["severity"] in SEVERITIES
    assert 0 <= case["confidence"] <= 1
    assert case["title"]
    assert case["summary"]
    assert case["location"]
    assert case["impact"]["headline"]
    assert sum(bool(a.get("primary")) for a in case["actions"]) <= 1
    for action in case["actions"]:
        assert action["kind"] in ACTION_KINDS
        if action["kind"] == "book_vendor":
            assert action["vendor_category"] in ("plumber", "electrician", "aircon_service")
    assert all(e["actor"] in TIMELINE_ACTORS for e in case["timeline"])
    assert case["timeline"] == sorted(case["timeline"], key=lambda e: e["t"])
    assert case["focus"]["layer"] in ("water", "electrical", "structure")
    assert case["focus"]["severity"] == case["severity"]
    focus_ids = list(case["focus"]["object_ids"])
    focus_ids += [i for a in case["actions"] if "focus" in a for i in a["focus"]["object_ids"]]
    assert focus_ids
    assert all(INDEX.exists(i) for i in focus_ids)
    if case["severity"] == "urgent" and case["status"] != "resolved":
        assert case["safety"], "urgent cases show their safety guidance"


def all_fixture_cases() -> list[dict[str, Any]]:
    out = []
    for name in FIXTURES:
        docs = load_findings(name)
        now = max(d["updated_at"] for d in docs)
        out += [rendered(s, now) for s in cases_of(docs)]
    return out


# ---------------------------------------------------------------------- real findings


@pytest.mark.parametrize("fixture", FIXTURES)
def test_every_captured_finding_becomes_a_valid_case(fixture: str) -> None:
    docs = load_findings(fixture)
    states = cases_of(docs)
    assert states
    now = max(d["updated_at"] for d in docs)
    for state in states:
        case = rendered(state, now)
        assert_valid_case(case)
        peak = max(SEVERITIES.index(d["severity"]) for d in [state.lead, *state.related])
        if case["status"] != "resolved":
            active = [t.latest for t in state.active_tracks]
            assert SEVERITIES.index(case["severity"]) == max(
                SEVERITIES.index(d["severity"]) for d in active
            )
        else:
            assert SEVERITIES.index(case["severity"]) == peak


def test_a_burst_and_its_night_flow_are_one_urgent_case() -> None:
    (state,) = cases_of(load_findings("l1_burst"))
    assert {t.latest["type"] for t in state.tracks} == {
        "burst_pipe_suspected",
        "night_flow_unexplained",
    }
    case = rendered(state)
    assert case["severity"] == "urgent"
    assert case["title"].startswith("Water has been flowing nonstop")
    assert "main stop valve" in case["safety"][0]


def test_overload_and_its_trips_are_one_case_on_the_circuit() -> None:
    (state,) = cases_of(load_findings("l2_elderly"))
    assert state.group == "L2:circ_sockets_bed4"
    assert state.lead["type"] == "repeated_trips"
    assert state.floor


def test_the_night_flow_case_is_honest_that_the_meter_cannot_locate_it() -> None:
    (state,) = cases_of(load_findings("l1_leak"))
    case = rendered(state)
    assert "can't tell which fixture or pipe" in case["summary"]
    assert case["focus"]["object_ids"] == ["meter_water_main"]
    assert case["actions"][0]["kind"] == "guided_check"


def test_a_negligible_fridge_rise_is_monitored_not_raised() -> None:
    (state,) = cases_of(load_findings("l3"))
    assert state.lead["quantities"]["extra_energy_kwh_per_day"]["value"] < 0.1
    assert state.status == "monitoring"


# ---------------------------------------------------------------------- every finding type


def test_the_synthetic_findings_are_valid_contract_documents() -> None:
    for finding_type in FINDING_TYPES:
        for severity in (None, "urgent"):
            extra: dict[str, Any] = {} if severity is None else {"severity": severity}
            report = validate_finding_document(finding(finding_type, **extra), CTX)
            assert not report.errors, (finding_type, report.errors)


def test_every_finding_type_in_the_contract_has_a_template() -> None:
    schema = json.loads(schema_path("core", "finding").read_text(encoding="utf-8"))
    types = set(schema["$defs"]["FindingType"]["enum"])
    assert len(types) == 16
    assert set(TEMPLATES) == types == set(FINDING_TYPES)


@pytest.mark.parametrize("finding_type", FINDING_TYPES)
@pytest.mark.parametrize("severity", [None, "urgent"])
def test_every_finding_type_renders_a_valid_case(finding_type: str, severity: str | None) -> None:
    extra: dict[str, Any] = {} if severity is None else {"severity": severity}
    (state,) = cases_of([finding(finding_type, **extra)])
    case = rendered(state)
    assert_valid_case(case)
    assert case["layer"] == state.lead["layer"]


@pytest.mark.parametrize("finding_type", FINDING_TYPES)
def test_a_cleared_finding_of_every_type_renders_a_resolved_case(finding_type: str) -> None:
    first = finding(finding_type)
    done = finding(
        finding_type,
        revision=2,
        status="cleared",
        updated_at="2025-11-03T09:00:00+08:00",
    )
    (state,) = cases_of([first, done])
    case = rendered(state)
    assert case["status"] == "resolved"
    assert case["actions"] == []
    assert case["resolved_at"] > case["opened_at"]
    assert_valid_case(case)


def test_safety_texts_exist_for_every_guidance_key_in_the_contract() -> None:
    schema = json.loads(schema_path("core", "finding").read_text(encoding="utf-8"))
    assert guidance_keys() == set(schema["$defs"]["SafetyGuidanceKey"]["enum"])


def test_a_located_model_candidate_list_is_named_not_invented() -> None:
    (state,) = cases_of([finding("running_cistern_suspected")])
    case = rendered(state)
    assert "equally likely" in case["summary"]
    assert "Master bathroom toilet cistern" in case["summary"]
    assert case["focus"]["object_ids"] == ["fix_bath_master_wc_cistern", "fix_bath2_wc_cistern"]
    assert case["confidence_note"].startswith("Suspected")


# ---------------------------------------------------------------------- lifecycle


def test_a_safety_floor_case_is_never_downgraded_by_a_later_revision() -> None:
    urgent = finding("circuit_overload")
    lower = revision(
        {**urgent, "severity": "heads_up", "safety_guidance_key": None},
        2,
        "2025-11-03T05:00:00+08:00",
    )
    del lower["safety_guidance_key"]
    (state,) = cases_of([urgent, lower])
    assert state.severity == "urgent"
    case = rendered(state)
    assert case["severity"] == "urgent"
    assert case["safety"]
    assert not dismissable(state)


def test_without_a_floor_a_lower_revision_lowers_the_case() -> None:
    first = finding("water_supply_interruption", severity="act_soon")
    lower = revision({**first, "severity": "heads_up"}, 2, "2025-11-03T05:00:00+08:00")
    (state,) = cases_of([first, lower])
    assert state.severity == "heads_up"
    assert "Lowered to heads-up" in [e.text for e in state.timeline]


def test_a_trip_escalating_to_repeated_trips_is_one_case_with_its_timeline() -> None:
    trip = finding("protective_device_trip", first_seen="2025-11-03T10:00:00+08:00")
    again = finding("repeated_trips", first_seen="2025-11-03T13:00:00+08:00")
    (state,) = cases_of([trip, again])
    assert state.severity == "urgent"
    assert state.lead["type"] == "repeated_trips"
    texts_ = [e.text for e in state.timeline]
    assert texts_[0].startswith("Noticed a breaker")
    assert "Raised to urgent" in texts_
    case = rendered(state)
    assert "keeps tripping" in case["title"]


def test_cleared_resolves_and_a_return_reopens_the_same_case() -> None:
    first = finding("protective_device_trip", first_seen="2025-11-03T10:00:00+08:00")
    cleared = revision(
        {**first, "status": "cleared", "cleared_at": "2025-11-04T10:00:00+08:00"},
        2,
        "2025-11-04T10:00:00+08:00",
    )
    (resolved,) = cases_of([first, cleared])
    assert resolved.status == "resolved"

    again = finding("protective_device_trip", first_seen="2025-11-06T09:00:00+08:00")
    (reopened,) = cases_of([first, cleared, again])
    assert reopened.id == resolved.id
    assert reopened.status == "open"
    assert "Seen again: a breaker or safety switch tripped" in [e.text for e in reopened.timeline]
    assert "not the first time" in rendered(reopened)["summary"]


def test_a_return_long_after_resolution_opens_a_new_case() -> None:
    first = finding("protective_device_trip", first_seen="2025-11-03T10:00:00+08:00")
    cleared = revision(
        {**first, "status": "cleared", "cleared_at": "2025-11-04T10:00:00+08:00"},
        2,
        "2025-11-04T10:00:00+08:00",
    )
    later = finding("protective_device_trip", first_seen="2025-11-20T09:00:00+08:00")
    states = cases_of([first, cleared, later])
    assert [s.status for s in states] == ["open", "resolved"]
    assert len({s.id for s in states}) == 2


def test_different_circuits_are_different_cases() -> None:
    kitchen = finding("protective_device_trip")
    yard = finding("protective_device_trip", target="mcb_sockets_yard")
    assert len(cases_of([kitchen, yard])) == 2


def test_a_dismissed_case_reopens_when_it_becomes_more_serious() -> None:
    first = finding("water_supply_interruption")
    (state,) = cases_of([first])
    dismiss = CaseAction(1, state.id, "dismiss", "owner", "2025-11-03T06:00:00+08:00", {})
    (dismissed,) = cases_of([first], [dismiss])
    assert dismissed.status == "dismissed"
    assert dismissed.timeline[-1].actor == "owner"

    worse = revision({**first, "severity": "act_soon"}, 2, "2025-11-03T08:00:00+08:00")
    (back,) = cases_of([first, worse], [dismiss])
    assert back.status == "open"
    assert "Reopened: this has become more serious" in [e.text for e in back.timeline]


def test_case_ids_are_stable_across_rebuilds() -> None:
    docs = load_findings("l2_l5_wet")
    assert [s.id for s in cases_of(docs)] == [s.id for s in cases_of(list(reversed(docs)))]


# ---------------------------------------------------------------------- wording rules


def test_no_case_text_contains_a_raw_contract_id() -> None:
    cases = all_fixture_cases()
    for finding_type in FINDING_TYPES:
        for severity in (None, "urgent"):
            extra: dict[str, Any] = {} if severity is None else {"severity": severity}
            cases += [rendered(s) for s in cases_of([finding(finding_type, **extra)])]
    for case in cases:
        for text in texts(case):
            leaked = [t for t in ID_TOKEN.findall(text) if INDEX.exists(t)]
            assert not leaked, (case["title"], text)


def test_no_case_text_or_code_uses_banned_trade_terms() -> None:
    for case in all_fixture_cases():
        for text in texts(case):
            assert not BANNED_TRADE_TERMS.search(text), text
    ban = API_SRC / "agent" / "guardrails.py"  # where the ban itself is defined
    for path in [*API_SRC.rglob("*.py"), *API_SRC.rglob("*.json")]:
        if path != ban:
            assert not BANNED_TRADE_TERMS.search(path.read_text(encoding="utf-8")), path


def test_no_scenario_names_in_case_code() -> None:
    """🔒 Nothing is scripted to a scenario: no preset name appears in the Core API."""
    toml = ROOT / "services" / "sim" / "src" / "penjaga_sim" / "scenarios.toml"
    scenarios = set(tomllib.loads(toml.read_text(encoding="utf-8")))
    assert {"leaky_bathroom", "burst_while_away"} <= scenarios
    for path in API_SRC.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for name in scenarios:
            assert name not in source, (path.name, name)
