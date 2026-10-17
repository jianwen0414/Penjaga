"""Guided-check measurements on controlled signals: each verdict, and refusal to guess."""

from __future__ import annotations

import numpy as np
import pytest

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


def _window(values: list[float], period_s: float = 1.0, t0: int = 0) -> checks.Window:
    ts = (t0 + (np.arange(len(values)) + 1) * period_s * US).astype(np.int64)
    return checks.Window.of(
        Series(ts, np.array(values, dtype=np.float64), np.zeros(len(values), np.uint8)),
        t0,
        int(ts[-1]),
        period_s,
    )


def test_residual_drop_when_the_leaking_circuit_is_off() -> None:
    r = np.random.default_rng(1)
    before = _window(list(3.0 + r.normal(0, 0.05, 120)), 1.0)
    after = _window(list(1.5 + r.normal(0, 0.05, 120)), 1.0)
    idle, off = _window([0.02] * 120, 1.0), _window([0.0] * 120, 1.0)
    outcome = checks.residual_drop(before, after, (idle, off), 0.1)
    assert outcome.verdict == "dropped"
    names = {m["name"]: m["value"] for m in outcome.measurements}
    assert {"residual_before", "residual_after", "residual_change_se"} <= set(names)
    assert names["circuit_before"] == pytest.approx(0.02)
    same = checks.residual_drop(
        before, _window(list(3.0 + r.normal(0, 0.05, 120)), 1.0), (idle, off), 0.1
    )
    assert same.verdict == "unchanged"


def test_a_drop_smaller_than_the_finding_saw_is_only_reduced() -> None:
    r = np.random.default_rng(2)
    before = _window(list(3.0 + r.normal(0, 0.05, 120)), 1.0)
    after = _window(list(2.6 + r.normal(0, 0.05, 120)), 1.0)
    off = _window([0.0] * 120, 1.0)
    assert checks.residual_drop(before, after, (None, off), 0.1).verdict == "dropped"
    small = checks.residual_drop(before, after, (None, off), 0.1, expect_drop_ma=1.2)
    assert small.verdict == "reduced"
    assert "circuit_before" not in {m["name"] for m in small.measurements}
    big = checks.residual_drop(before, after, (None, off), 0.1, expect_drop_ma=0.6)
    assert big.verdict == "dropped"


def test_residual_drop_needs_the_circuit_really_off() -> None:
    before = _window([3.0] * 600)
    after = _window([1.5] * 600)
    on = _window([2.0] * 600)
    outcome = checks.residual_drop(before, after, (None, on), 0.1)
    assert outcome.status == "unavailable"
    assert outcome.reason is not None
    assert "not switched off" in outcome.reason


def test_tank_is_full() -> None:
    assert checks.tank_is_full(_window([98.0, 99.5, 100.0], 60.0)) is True
    assert checks.tank_is_full(_window([96.0, 99.5, 100.0], 60.0)) is False
    assert checks.tank_is_full(_window([float("nan")] * 3, 60.0)) is None
