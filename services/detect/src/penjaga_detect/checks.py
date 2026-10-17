"""Guided checks: measure the effect of something the household just did.

The agent runs the conversation ("close the angle valve under the Bathroom 2 cistern"). These
functions measure what changed in the readings (``docs/design/interfaces.md``, M5).

**Significance.** A change counts only if it exceeds both:
- twice its standard error;
- the stream's resolution.

Samples of one stream are autocorrelated, so the standard error comes from block means of
``BLOCK_S`` seconds (``RESIDUAL_BLOCK_S`` for the 1 s residual current, whose windows are short
so that other circuits' appliances have little time to switch), not from raw samples. A window
with less than ``MIN_COVERAGE`` of its expected samples present is "unavailable", never guessed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt

from penjaga_detect.chunk import US, Series

BLOCK_S = 60.0
RESIDUAL_BLOCK_S = 10.0
MIN_COVERAGE = 0.5
MIN_BLOCKS = 3

FLOW_STREAM = "water.main.flow_lpm"
TANK_STREAM = "water.tank.level_pct"
CIRCUIT_OFF_BELOW_A = 0.1  # a circuit switched off at its MCB draws (almost) nothing
TANK_FULL_PCT = 97.0  # the float valve has (nearly) closed: refilling no longer shows as flow


def circuit_stream(circuit_id: str) -> str:
    return f"elec.circuit.{circuit_id}.current_a"


def residual_stream(rccb_id: str) -> str:
    return f"elec.rcd.{rccb_id}.residual_ma"


@dataclass(frozen=True)
class Window:
    """Present samples of one stream in (t0, t1], with the coverage they give."""

    ts_us: npt.NDArray[np.int64]
    values: npt.NDArray[np.float64]
    coverage: float

    @staticmethod
    def of(series: Series, t0_us: int, t1_us: int, period_s: float) -> Window:
        part = series.between(t0_us, t1_us)
        ok = ~np.isnan(part.values)
        expected = max(1, round((t1_us - t0_us) / (period_s * US)))
        return Window(part.ts_us[ok], part.values[ok], min(1.0, int(ok.sum()) / expected))

    @property
    def usable(self) -> bool:
        return self.coverage >= MIN_COVERAGE and self.values.size > 0


@dataclass(frozen=True)
class Outcome:
    status: str  # complete or unavailable
    measurements: list[dict[str, Any]]
    verdict: str | None
    reason: str | None = None


def unavailable(reason: str) -> Outcome:
    return Outcome("unavailable", [], None, reason)


def _blocks(w: Window, block_s: float = BLOCK_S) -> npt.NDArray[np.float64]:
    """Means of consecutive ``block_s`` blocks (from the first sample)."""
    index = (w.ts_us - w.ts_us[0]) // round(block_s * US)
    sums = np.bincount(index, weights=w.values)
    counts = np.bincount(index)
    return np.asarray(sums[counts > 0] / counts[counts > 0], dtype=np.float64)


def _mean_se(w: Window, block_s: float = BLOCK_S) -> tuple[float, float]:
    blocks = _blocks(w, block_s)
    mean = float(np.mean(w.values))
    if blocks.size < MIN_BLOCKS:
        return mean, math.inf  # too short to judge a change
    return mean, float(np.std(blocks, ddof=1) / math.sqrt(blocks.size))


def _change(
    before: Window, after: Window, resolution: float, block_s: float = BLOCK_S
) -> tuple[float, float, int]:
    """(after - before, its standard error, sign of a significant change or 0)."""
    mb, sb = _mean_se(before, block_s)
    ma, sa = _mean_se(after, block_s)
    delta = ma - mb
    se = math.hypot(sb, sa)
    significant = abs(delta) > 2 * se and abs(delta) > resolution
    return delta, se, (int(math.copysign(1, delta)) if significant else 0)


def _m(name: str, value: float, unit: str, **extra: float) -> dict[str, Any]:
    item: dict[str, Any] = {"name": name, "value": round(value, 4), "unit": unit}
    item.update({k: round(v, 4) for k, v in extra.items()})
    return item


def _se(name: str, se: float, unit: str) -> list[dict[str, Any]]:
    """The standard error, when the windows were long enough to estimate one."""
    return [_m(name, se, unit)] if math.isfinite(se) else []


def flow_stop(before: Window, after: Window, expect_below_lpm: float, resolution: float) -> Outcome:
    if not (before.usable and after.usable):
        return unavailable("too few water meter readings in the measurement windows")
    delta, se, sign = _change(before, after, resolution)
    mb, ma = float(np.mean(before.values)), float(np.mean(after.values))
    if ma < expect_below_lpm:
        verdict = "stopped"
    elif sign < 0:
        verdict = "reduced"
    else:
        verdict = "unchanged"
    return Outcome(
        "complete",
        [
            _m("flow_before", mb, "L/min"),
            _m("flow_after", ma, "L/min", threshold=expect_below_lpm),
            _m("flow_change", delta, "L/min"),
            *_se("flow_change_se", se, "L/min"),
        ],
        verdict,
    )


def circuit_load(before: Window, after: Window, resolution: float) -> Outcome:
    if not (before.usable and after.usable):
        return unavailable("too few circuit current readings in the measurement windows")
    delta, se, sign = _change(before, after, resolution)
    verdict = {-1: "dropped", 0: "unchanged", 1: "rose"}[sign]
    return Outcome(
        "complete",
        [
            _m("current_before", float(np.mean(before.values)), "A"),
            _m("current_after", float(np.mean(after.values)), "A"),
            _m("current_change", delta, "A"),
            *_se("current_change_se", se, "A"),
        ],
        verdict,
    )


def tank_level_trend(window: Window, resolution: float) -> Outcome:
    if not window.usable or window.values.size < MIN_BLOCKS:
        return unavailable("too few tank level readings in the measurement window")
    hours = (window.ts_us - window.ts_us[0]) / (3600 * US)
    span = float(hours[-1] - hours[0])
    if span <= 0:
        return unavailable("the tank level readings span no time")
    blocks_t = _block_times(window)
    blocks_v = _blocks(window)
    if blocks_v.size < MIN_BLOCKS:
        return unavailable("the measurement window is too short for a trend")
    slope, se = _ols_slope(blocks_t, blocks_v)
    change = slope * span
    if slope + 2 * se < 0 and abs(change) > resolution:
        verdict = "falling"
    elif slope - 2 * se > 0 and abs(change) > resolution:
        verdict = "rising"
    else:
        verdict = "steady"
    return Outcome(
        "complete",
        [
            _m("level_start", float(window.values[0]), "%"),
            _m("level_end", float(window.values[-1]), "%"),
            _m("level_slope", slope, "%/h"),
            _m("level_slope_se", se, "%/h"),
        ],
        verdict,
    )


def _block_times(w: Window) -> npt.NDArray[np.float64]:
    index = (w.ts_us - w.ts_us[0]) // round(BLOCK_S * US)
    hours = (w.ts_us - w.ts_us[0]) / (3600 * US)
    sums = np.bincount(index, weights=hours)
    counts = np.bincount(index)
    return np.asarray(sums[counts > 0] / counts[counts > 0], dtype=np.float64)


def _ols_slope(x: npt.NDArray[np.float64], y: npt.NDArray[np.float64]) -> tuple[float, float]:
    """Least-squares slope of y on x and its standard error."""
    xc = x - x.mean()
    sxx = float(np.sum(xc * xc))
    slope = float(np.sum(xc * (y - y.mean())) / sxx)
    residuals = y - y.mean() - slope * xc
    dof = max(1, x.size - 2)
    se = math.sqrt(float(np.sum(residuals * residuals)) / dof / sxx)
    return slope, se


def residual_drop(
    before: Window,
    after: Window,
    circuit: tuple[Window | None, Window | None],
    resolution: float,
    *,
    expect_drop_ma: float | None = None,
) -> Outcome:
    """Did the RCCB's residual current fall after one circuit was switched off at its MCB?

    ``circuit`` is the circuit's current in the before and after windows. If it still draws
    current after, the circuit was not off and the check cannot say anything; what it drew
    before is reported (``circuit_before``), as the leakage of its running appliances falls with
    it. Every circuit leaks a little, so switching off a healthy one also lowers the residual
    current: with ``expect_drop_ma`` (the rise the finding saw), a fall of at least half of it is
    ``dropped`` and a smaller significant fall ``reduced``."""
    circuit_before, circuit_after = circuit
    if not (before.usable and after.usable):
        return unavailable("too few residual current readings in the measurement windows")
    if circuit_after is not None and circuit_after.usable:
        drawn = float(np.mean(circuit_after.values))
        if drawn >= CIRCUIT_OFF_BELOW_A:
            return unavailable(f"the circuit still drew {drawn:.2f} A: it was not switched off")
    delta, se, sign = _change(before, after, resolution, RESIDUAL_BLOCK_S)
    verdict = {-1: "dropped", 0: "unchanged", 1: "rose"}[sign]
    if verdict == "dropped" and expect_drop_ma is not None and -delta < 0.5 * expect_drop_ma:
        verdict = "reduced"
    load = (
        [_m("circuit_before", float(np.mean(circuit_before.values)), "A")]
        if circuit_before is not None and circuit_before.usable
        else []
    )
    return Outcome(
        "complete",
        [
            _m("residual_before", float(np.mean(before.values)), "mA"),
            _m("residual_after", float(np.mean(after.values)), "mA"),
            _m("residual_change", delta, "mA"),
            *_se("residual_change_se", se, "mA"),
            *load,
        ],
        verdict,
    )


def tank_is_full(window: Window, full_pct: float = TANK_FULL_PCT) -> bool | None:
    """Whether the roof tank has been at full through the window (None without readings).
    Flow checks wait for it: while the tank refills, closing a valve barely changes the flow."""
    if not window.usable:
        return None
    return bool(np.min(window.values) >= full_pct)
