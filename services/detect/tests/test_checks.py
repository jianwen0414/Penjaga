"""Guided-check measurements on controlled signals: each verdict, and refusal to guess."""

from __future__ import annotations

import numpy as np

from penjaga_detect import checks
from penjaga_detect.chunk import Series

US = 1_000_000
RNG = np.random.default_rng(3)


def series(values: np.ndarray, period_s: float, t0_s: float = 0.0) -> Series:
    ts = np.round((t0_s + period_s * (1 + np.arange(values.size))) * US).astype(np.int64)
    return Series(ts, values.astype(np.float64), np.zeros(values.size, dtype=np.uint8))


def window(values: np.ndarray, period_s: float, t0_s: float = 0.0) -> checks.Window:
    s = series(values, period_s, t0_s)
    end = t0_s + period_s * values.size
    return checks.Window.of(s, round(t0_s * US), round(end * US), period_s)


def noisy(level: float, n: int, sd: float = 0.05) -> np.ndarray:
    return level + RNG.normal(0, sd, n)


def test_flow_stop_verdicts() -> None:
    before = window(noisy(0.30, 60), 10)
    stopped = checks.flow_stop(before, window(noisy(0.0, 60, 0.005), 10), 0.05, 0.06)
    assert stopped.verdict == "stopped"
    assert stopped.status == "complete"
    reduced = checks.flow_stop(before, window(noisy(0.15, 60), 10), 0.05, 0.06)
    assert reduced.verdict == "reduced"
    same = checks.flow_stop(before, window(noisy(0.30, 60), 10), 0.05, 0.06)
    assert same.verdict == "unchanged"
    names = [m["name"] for m in stopped.measurements]
    assert names == ["flow_before", "flow_after", "flow_change", "flow_change_se"]


def test_a_change_below_the_resolution_is_not_significant() -> None:
    before = window(np.full(60, 0.30), 10)
    after = window(np.full(60, 0.27), 10)  # 0.03 L/min: below the meter's 0.06
    assert checks.flow_stop(before, after, 0.01, 0.06).verdict == "unchanged"


def test_circuit_load_verdicts() -> None:
    before = window(noisy(4.0, 300, 0.2), 1)
    assert checks.circuit_load(before, window(noisy(0.5, 300, 0.2), 1), 0.01).verdict == "dropped"
    assert checks.circuit_load(before, window(noisy(8.0, 300, 0.2), 1), 0.01).verdict == "rose"
    assert checks.circuit_load(before, window(noisy(4.0, 300, 0.2), 1), 0.01).verdict == "unchanged"


def test_tank_trend_verdicts() -> None:
    minutes = np.arange(60, dtype=np.float64)
    falling = checks.tank_level_trend(window(80 - 0.2 * minutes, 60), 1.0)
    assert falling.verdict == "falling"
    assert falling.measurements[2]["name"] == "level_slope"
    assert falling.measurements[2]["value"] < 0
    rising = checks.tank_level_trend(window(40 + 0.3 * minutes, 60), 1.0)
    assert rising.verdict == "rising"
    steady = checks.tank_level_trend(window(noisy(70.0, 60, 0.3), 60), 1.0)
    assert steady.verdict == "steady"


def test_missing_readings_make_a_check_unavailable() -> None:
    good = window(noisy(0.3, 60), 10)
    gaps = np.full(60, np.nan)
    gaps[:10] = 0.3  # 1/6 of the window present
    sparse = window(gaps, 10)
    out = checks.flow_stop(good, sparse, 0.05, 0.06)
    assert out.status == "unavailable"
    assert out.verdict is None
    assert out.reason
    assert checks.tank_level_trend(window(np.array([50.0, 50.0]), 60), 1.0).status == (
        "unavailable"
    )


def test_too_short_windows_report_no_standard_error() -> None:
    before = window(np.array([0.3, 0.3]), 10)  # 20 s: fewer than three 60 s blocks
    after = window(np.array([0.0, 0.0]), 10)
    out = checks.flow_stop(before, after, 0.05, 0.06)
    assert out.verdict == "stopped"  # below the expected flow, which needs no error bar
    assert "flow_change_se" not in [m["name"] for m in out.measurements]
