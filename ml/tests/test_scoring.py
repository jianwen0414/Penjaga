"""The scorer on hand-made truth and findings, so its rules are pinned down exactly."""

from __future__ import annotations

import pytest

from penjaga_contract.validate import load_index
from penjaga_eval.scoring import (
    DAY_US,
    EXPECTED,
    GRACE_US,
    EpisodeResult,
    FindingSummary,
    TruthAnomaly,
    TruthFault,
    score_episodes,
)

H = 3600 * 1_000_000
T0 = 1_700_000_000 * 1_000_000


def fault(
    ep: str,
    kind: str = "W2",
    *,
    start_h: float = 24,
    end_h: float | None = None,
    objects: tuple[str, ...] = ("fix_bath2_wc_cistern",),
    magnitude: float = 0.3,
) -> TruthFault:
    end = T0 + round((end_h if end_h is not None else 24 * 7) * H)
    layer = EXPECTED[kind][0]
    return TruthFault(f"tr_{ep}", kind, layer, T0 + round(start_h * H), end, objects, magnitude)


def finding(
    fid: str, at_h: float, kind: str = "night_flow_unexplained", ranked: tuple[str, ...] = ()
) -> FindingSummary:
    return FindingSummary(fid, "L1", kind, T0 + round(at_h * H), ranked)


def result(
    ep: str,
    faults: list[TruthFault],
    findings: list[FindingSummary],
    anomalies: list[TruthAnomaly] | None = None,
) -> EpisodeResult:
    return EpisodeResult(ep, 7.0, faults, anomalies or [], findings)


def l1(results: list[EpisodeResult]) -> dict:  # type: ignore[type-arg]
    out = score_episodes(results, ["L1"], load_index())["L1"]
    assert isinstance(out, dict)
    return out


CISTERNS = ("fix_bath2_wc_cistern", "fix_bath3_wc_cistern")


def test_detection_time_to_detect_and_localisation() -> None:
    m = l1(
        [
            result("e1", [fault("e1")], [finding("f1", 52, ranked=CISTERNS)]),
            result(
                "e2",
                [fault("e2")],
                [finding("f2", 76, "running_cistern_suspected", ranked=CISTERNS[::-1])],
            ),
        ]
    )
    t = m["by_fault_type"]["W2"]
    assert (t["n"], t["detected"], t["recall"]) == (2, 2, 1.0)
    assert t["ttd_hours_median"] == pytest.approx(40.0)
    assert t["specific_type_rate"] == 0.5
    assert t["localisation_top1"] == 0.5
    assert t["localisation_top3"] == 1.0
    assert m["precision"] == 1.0
    assert m["false_alarms_per_house_month"] == 0


def test_findings_before_the_fault_or_after_the_grace_are_false_alarms() -> None:
    end_h = 48.0
    late = end_h + GRACE_US["L1"] / H + 1
    m = l1(
        [result("e1", [fault("e1", end_h=end_h)], [finding("early", 10), finding("late", late)])]
    )
    assert m["detected"] == 0
    assert m["false_alarms"] == 2
    assert m["precision"] == 0.0
    assert m["false_alarms_per_house_month"] == pytest.approx(2 / (7 / 30.4375), rel=1e-3)


def test_wrong_type_does_not_count() -> None:
    m = l1(
        [
            result(
                "e1", [fault("e1", kind="W5")], [finding("f", 30, kind="running_cistern_suspected")]
            )
        ]
    )
    assert m["detected"] == 0
    assert m["false_alarms"] == 1


def test_one_finding_detects_one_fault() -> None:
    faults = [fault("e1", start_h=24), fault("e1", start_h=30)]
    faults[1] = TruthFault(
        "tr_b", "W2", "L1", T0 + 30 * H, T0 + 100 * H, ("fix_bath3_wc_cistern",), 0.3
    )
    m = l1([result("e1", faults, [finding("f", 40)])])
    assert m["detected"] == 1
    assert m["recall"] == 0.5


def test_false_alarms_are_attributed_to_legitimate_anomalies() -> None:
    bucket = TruthAnomaly("night_bucket_fill", T0 + 23 * H, T0 + 23 * H + 900 * 1_000_000)
    m = l1([result("e1", [], [finding("f", 23.5), finding("g", 100)], [bucket])])
    assert m["false_alarms_by_cause"] == {"night_bucket_fill": 1, "none": 1}
    assert m["recall"] is None


def test_recall_by_magnitude_terciles() -> None:
    results = []
    for i in range(6):
        hits = [finding(f"f{i}", 30)] if i >= 3 else []
        results.append(result(f"e{i}", [fault(f"e{i}", magnitude=0.1 * (i + 1))], hits))
    bins = l1(results)["by_fault_type"]["W2"]["recall_by_magnitude"]
    assert [b["recall"] for b in bins] == [0.0, 0.5, 1.0]
    assert DAY_US > 0


def test_magnitude_bins_count_each_fault_once() -> None:
    results = [result(f"e{i}", [fault(f"e{i}", magnitude=0.1)], []) for i in range(6)]
    bins = l1(results)["by_fault_type"]["W2"]["recall_by_magnitude"]
    assert sum(b["n"] for b in bins) == 6
