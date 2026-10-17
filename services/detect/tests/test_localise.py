"""Guided-check localisation (D-029): hypotheses, check options, Bayesian update, next check."""

from __future__ import annotations

import pytest

from penjaga_contract.core import Finding
from penjaga_detect.detector import DetectorInfo, HouseContext
from penjaga_detect.findings import FindingTracker, Observation
from penjaga_detect.localise import (
    VERDICTS,
    Likelihood,
    Localiser,
    check_options,
    evidence_verdict,
    expected_gain,
    hypotheses,
    prior,
    update,
)

US = 1_000_000
HOUSE = HouseContext.canonical()
INDEX = HOUSE.index
PERFECT = Likelihood(
    {
        "flow_stop": {
            "behind": {
                "stopped": 1.0,
                "reduced": 0.0,
                "unchanged": 0.0,
                "idle": 0.0,
                "unavailable": 0.0,
            },
            "other": {
                "stopped": 0.0,
                "reduced": 0.0,
                "unchanged": 1.0,
                "idle": 0.0,
                "unavailable": 0.0,
            },
        },
        "tank_level_trend": {
            "behind": {"falling": 1.0, "steady": 0.0, "rising": 0.0, "unavailable": 0.0},
            "other": {"falling": 0.0, "steady": 1.0, "rising": 0.0, "unavailable": 0.0},
        },
    }
)


def _options() -> dict[str, frozenset[str]]:
    return {o.key: o.behind for o in check_options(INDEX)}


def test_hypotheses_are_the_places_a_leak_can_be() -> None:
    hyps = set(hypotheses(INDEX))
    assert {"fix_bath2_wc_cistern", "fix_porch_garden_tap", "valve_roof_tank_float"} <= hyps
    assert {"pipe_bath2_cold_supply", "pipe_supply_main", "pipe_rising_main"} <= hyps
    assert "pipe_roof_tank_overflow" not in hyps
    assert "fix_kitchen_ro_purifier" not in hyps  # no fault location in the catalogue


def test_checks_use_only_valves_the_owner_may_operate() -> None:
    options = _options()
    assert "flow_stop:valve_bath2_wc_angle" in options
    assert "flow_stop:valve_bath2_branch" in options
    assert "tank_level_trend:valve_main_stop" in options
    assert not any("float" in k or "downfeed" in k for k in options)
    assert "flow_stop:valve_main_stop" not in options  # closing it stops every flow: no answer


def test_what_each_check_isolates() -> None:
    options = _options()
    assert options["flow_stop:valve_bath2_wc_angle"] == {"fix_bath2_wc_cistern"}
    assert options["flow_stop:valve_bath2_branch"] == {
        "pipe_bath2_cold_supply",
        "fix_bath2_wc_cistern",
        "fix_bath2_basin_tap",
        "fix_bath2_shower",
        "fix_bath2_bidet_spray",
    }
    tank = options["tank_level_trend:valve_main_stop"]
    assert {"fix_yard_laundry_tap", "pipe_gf_downfeed", "pipe_downfeed_main"} <= tank
    assert not tank & {
        "fix_kitchen_sink_tap",
        "fix_porch_garden_tap",
        "pipe_rising_main",
        "pipe_supply_main",
        "valve_roof_tank_float",
    }


def _finding(candidates: list[dict[str, object]], other: float) -> Finding:
    tracker = FindingTracker(
        HOUSE.house_id,
        DetectorInfo(
            "l1_test", "L1", "model", "1.0.0", model="test", model_card="docs/models/test.md"
        ).source(),
        HOUSE.zone,
    )
    found = tracker.observe(
        Observation(
            key=f"L1:water_leak:{INDEX.meter_id}",
            type="running_cistern_suspected",
            severity="heads_up",
            confidence=0.9,
            confidence_kind="calibrated_probability",
            evidence=[{"name": "leak_probability", "value": 0.9}],
            at_us=1_790_000_000 * US,
            candidates=candidates,
            candidates_other_p=other,
        )
    )
    assert found is not None
    return found


def test_prior_follows_the_findings_candidates() -> None:
    hyps = hypotheses(INDEX)
    cisterns = [h for h in hyps if h.endswith("_wc_cistern")]
    finding = _finding(
        [{"object_id": c, "p": 0.2, "basis": "leak_class_w2+contract_kind"} for c in cisterns],
        0.2,
    )
    p = prior(finding, hyps)
    assert sum(p.values()) == pytest.approx(1.0)
    assert all(p[c] == pytest.approx(0.2) for c in cisterns)
    others = [h for h in hyps if h not in cisterns]
    assert sum(p[h] for h in others) == pytest.approx(0.2)


def test_a_finding_without_locations_gives_a_uniform_prior() -> None:
    hyps = hypotheses(INDEX)
    p = prior(None, hyps)
    assert all(v == pytest.approx(1 / len(hyps)) for v in p.values())


def test_update_keeps_only_what_a_certain_verdict_allows() -> None:
    hyps = hypotheses(INDEX)
    option = next(o for o in check_options(INDEX) if o.valve_id == "valve_bath2_branch")
    stopped = update(prior(None, hyps), option, "stopped", PERFECT)
    assert {h for h, p in stopped.items() if p > 0} == option.behind
    unchanged = update(prior(None, hyps), option, "unchanged", PERFECT)
    assert not {h for h, p in unchanged.items() if p > 0} & option.behind
    with pytest.raises(ValueError, match="verdict"):
        update(stopped, option, "falling", PERFECT)


def test_a_check_that_cannot_change_the_belief_gains_nothing() -> None:
    hyps = hypotheses(INDEX)
    option = next(o for o in check_options(INDEX) if o.valve_id == "valve_bath2_wc_angle")
    belief = dict.fromkeys(hyps, 0.0)
    belief["fix_bath3_wc_cistern"] = 1.0
    assert expected_gain(belief, option, PERFECT) == pytest.approx(0.0)
    assert expected_gain(prior(None, hyps), option, PERFECT) > 0


@pytest.mark.parametrize(
    "truth", ["fix_bath2_wc_cistern", "pipe_bath3_cold_supply", "fix_yard_laundry_tap"]
)
def test_perfect_checks_narrow_a_leak_to_what_the_valves_can_separate(truth: str) -> None:
    loc = Localiser(INDEX, PERFECT)
    loc.start_from(None)
    while (option := loc.next_check()) is not None:
        positive = VERDICTS[option.kind][0]
        negative = VERDICTS[option.kind][2 if option.kind == "flow_stop" else 1]
        loc.observe(option, positive if truth in option.behind else negative)
    assert truth in {h for h, p in loc.posterior.items() if p > 0}
    assert loc.posterior[truth] == max(loc.posterior.values())
    assert len(loc.done) <= loc.params.max_checks


def test_counts_estimate_the_likelihood_with_smoothing() -> None:
    lik = Likelihood.from_counts({"flow_stop": {"behind": {"stopped": 8}, "other": {}}})
    assert lik.p("flow_stop", True, "stopped") == pytest.approx(9 / 13)  # 5 verdicts
    assert lik.p("flow_stop", True, "reduced") == pytest.approx(1 / 13)
    default = Likelihood.default()
    assert lik.p("flow_stop", False, "unchanged") == default.p("flow_stop", False, "unchanged")
    for kind, verdicts in VERDICTS.items():
        for side in (True, False):
            assert sum(default.p(kind, side, v) for v in verdicts) == pytest.approx(1.0)


def test_a_still_meter_before_the_check_is_no_evidence() -> None:
    assert evidence_verdict("flow_stop", "stopped", 0.0, 0.02) == "idle"
    assert evidence_verdict("flow_stop", "stopped", 0.3, 0.02) == "stopped"
    assert evidence_verdict("flow_stop", "unchanged", None, 0.02) == "unchanged"
    assert evidence_verdict("tank_level_trend", "steady", 0.0, 0.02) == "steady"
    assert evidence_verdict("flow_stop", None, 0.3, 0.02) == "unavailable"
    hyps = hypotheses(INDEX)
    option = next(o for o in check_options(INDEX) if o.valve_id == "valve_bath2_branch")
    lik = Likelihood.default()
    before = prior(None, hyps)
    after = update(before, option, "idle", lik)
    assert after == pytest.approx(before)  # idle is equally likely either way
