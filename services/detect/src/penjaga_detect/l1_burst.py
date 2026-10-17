"""L1 burst rule (W5): high flow that never stops, at any hour. Deterministic.

A burst finding opens when the mean *unexplained* meter flow over the window exceeds
``burst_lpm`` and no sample in the window falls below half of it. Unexplained flow is the meter
flow minus what the roof tank gained over the window (its level rise, from the tank level
sensor): water that went into the tank is not lost (v1.1.0, D-030). Without a tank level reading
the whole flow counts, as in v1.0.0.

Why the tank matters: normal heavy use (showers, a washer) pauses or tails off within the hour,
and at normal pressure a full tank refill takes about an hour at most (1,364 L at about
20 L/min). Where the supply pressure is low by day, the tank refills slowly at night, for hours
and without a pause, which v1.0.0 took for a burst (3.4 urgent false alarms per house-month on
the low-pressure validation houses). Only the tank's rise is subtracted, never its fall: a
draining tank already shows in the meter as the float valve opens.

Urgent, with safety guidance (shut the main stop valve). Clears after an hour without the
condition. The tank's rise is measured from its readings inside each window, all at or
before the moment being decided, so the decision never depends on how house time is chunked.
Right after data starts, while the tank readings cover under half a window, no decision is made
(a burst is then found at most one window later).

``BurstWatch`` does the work for any detector; ``L1BurstRule`` runs it on its own, beside the
minimum-night-flow baseline (D-022).
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import Any

import numpy as np

from penjaga_contract.core import Finding
from penjaga_detect.chunk import Chunk
from penjaga_detect.detector import DetectorInfo, HouseContext
from penjaga_detect.findings import FindingTracker, Observation
from penjaga_detect.signals import Carry, Floats, Ints, TierState, rolling_mean, track_tiers

US = 1_000_000
FLOW = "water.main.flow_lpm"
TANK = "water.tank.level_pct"
FLOW_PERIOD_S = 10.0
TANK_MARGIN_S = 180.0  # tank history kept beyond the window


@dataclass(frozen=True)
class BurstParams:
    burst_lpm: float = 6.0
    burst_window_s: float = 3600.0


def rise_rate(tank_ts: Ints, tank_level: Floats, at_us: Ints, window_us: int) -> Floats:
    """Rate of tank level change (%/min) over (t - window, t] from the first and last valid
    readings inside it; NaN when they span less than half the window."""
    ok = ~np.isnan(tank_level)
    ts, level = tank_ts[ok], tank_level[ok]
    out = np.full(at_us.size, np.nan)
    if ts.size == 0:
        return out
    last = np.searchsorted(ts, at_us, side="right") - 1
    first = np.searchsorted(ts, at_us - window_us, side="right")
    span = np.where(last >= first, ts[np.maximum(last, 0)] - ts[np.minimum(first, ts.size - 1)], 0)
    usable = (last >= first) & (span >= window_us // 2)
    lo, hi = first[usable], last[usable]
    out[usable] = (level[hi] - level[lo]) / (span[usable] / US / 60.0)
    return out


class BurstWatch:
    def __init__(self, house: HouseContext, tracker: FindingTracker, params: BurstParams) -> None:
        self.p = params
        self.tracker = tracker
        self.meter = house.index.meter_id
        tanks = house.index.house.water.tanks
        self.tank_l = float(tanks[0].capacity_l) if tanks else 0.0
        self.key = f"L1:burst_pipe:{self.meter}"
        self.state = TierState()
        self.out: list[Finding] = []

    def update(
        self,
        chunk: Chunk,
        ts: Ints,
        values: Floats,
        first_new: int,
        tank: tuple[Ints, Floats] | None = None,
    ) -> list[Finding]:
        """Findings from the new samples ``ts[first_new:]`` (earlier ones are history).

        ``tank`` is the tank level history (times, %) covering at least the window before the
        new samples; None when the house has no tank level sensor."""
        self.out = []
        clear = functools.partial(self._clear)
        if ts.size == first_new:
            empty = np.empty(0, np.int64)
            track_tiers(self.state, empty, empty, chunk_end_us=chunk.t1_us,
                        clear_after_us=3600 * US, on_raise=_never, on_clear=clear)  # fmt: skip
            return self.out
        window_us = round(self.p.burst_window_s * US)
        expected = self.p.burst_window_s / FLOW_PERIOD_S
        mean = rolling_mean(ts, values, window_us, expected, 0.8)[first_new:]
        stored = self._stored_lpm(ts[first_new:], window_us, tank)
        unexplained = mean - np.nan_to_num(stored)
        low = np.where(
            np.isnan(values), np.nan, (values < 0.5 * self.p.burst_lpm).astype(np.float64)
        )
        low_share = rolling_mean(ts, low, window_us, expected, 0.8)[first_new:]
        # with a tank sensor but too little of its history, make no decision yet
        high = (np.nan_to_num(unexplained) > self.p.burst_lpm) & ~np.isnan(stored)
        unbroken = np.nan_to_num(low_share, nan=1.0) == 0.0
        tiers = (high & unbroken).astype(np.int64)
        raise_ = functools.partial(self._raise, unexplained, stored)
        track_tiers(self.state, ts[first_new:], tiers, chunk_end_us=chunk.t1_us,
                    clear_after_us=3600 * US, on_raise=raise_, on_clear=clear)  # fmt: skip
        return self.out

    def _stored_lpm(self, at_us: Ints, window_us: int, tank: tuple[Ints, Floats] | None) -> Floats:
        """Rate at which the tank gained water over each window (L/min): 0 without a tank
        sensor, NaN while its readings cover too little of the window."""
        if tank is None or self.tank_l <= 0:
            return np.zeros(at_us.size)
        rate = rise_rate(tank[0], tank[1], at_us, window_us) / 100.0 * self.tank_l
        return np.asarray(np.where(np.isnan(rate), np.nan, np.maximum(rate, 0.0)))

    def _raise(self, mean: Any, stored: Any, j: int, at_us: int, tier: int) -> None:
        flow = round(float(mean[j]), 2)
        finding = self.tracker.observe(
            Observation(
                key=self.key,
                type="burst_pipe_suspected",
                severity="urgent",
                confidence=1.0,
                at_us=at_us,
                object_ids=[self.meter],
                evidence=[
                    {"name": "mean_flow", "value": flow, "unit": "L/min"},
                    {
                        "name": "tank_fill",
                        "value": round(float(np.nan_to_num(stored[j])), 2),
                        "unit": "L/min",
                    },
                    {"name": "window", "value": round(self.p.burst_window_s / 60), "unit": "min"},
                    {"name": "rule", "value": "continuous_high_flow"},
                ],
                quantities={"water_l_per_day": {"value": round(flow * 1440), "unit": "L/day"}},
                safety_guidance_key="sg_burst_pipe",
            )
        )
        if finding is not None:
            self.out.append(finding)

    def _clear(self, at_us: int) -> None:
        finding = self.tracker.clear(self.key, at_us)
        if finding is not None:
            self.out.append(finding)


class L1BurstRule:
    """The burst rule as a detector of its own."""

    def __init__(self, house: HouseContext, params: BurstParams | None = None) -> None:
        p = params or BurstParams()
        self.info = DetectorInfo(
            "l1_burst_rule", "L1", "rule", "1.1.0", streams=frozenset({FLOW, TANK})
        )
        self.tracker = FindingTracker(house.house_id, self.info.source(), house.zone)
        self.watch = BurstWatch(house, self.tracker, p)
        self.carry = Carry(round(p.burst_window_s * US))
        # the tank's level at the start of a window is its last sample before it
        self.tank = Carry(round((p.burst_window_s + TANK_MARGIN_S) * US))
        self.has_tank = bool(house.index.house.water.tanks)

    def update(self, chunk: Chunk) -> list[Finding]:
        ts, values, first_new = self.carry.extend(chunk.get(FLOW))
        tank_ts, tank_values, _ = self.tank.extend(chunk.get(TANK))
        tank = (tank_ts, tank_values) if self.has_tank and tank_ts.size else None
        return self.watch.update(chunk, ts, values, first_new, tank)


def _never(index: int, at_us: int, tier: int) -> None:
    raise AssertionError("no samples, so nothing can be raised")
