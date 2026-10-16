"""L1 burst rule (W5): high flow that never stops, at any hour. Deterministic.

A burst finding opens when the mean meter flow over the window exceeds ``burst_lpm`` and no
sample in the window falls below half of it. Normal heavy use (showers, a washer, the roof tank
refilling after the morning) pauses or tails off within the hour; a full tank refill cannot
last much more than an hour (1,364 L at about 20 L/min), so the tuned window is 60 minutes
(ml/params, D-022). Urgent, with safety guidance (shut the main stop valve). Clears after an
hour without the condition.

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
FLOW_PERIOD_S = 10.0


@dataclass(frozen=True)
class BurstParams:
    burst_lpm: float = 6.0
    burst_window_s: float = 3600.0


class BurstWatch:
    def __init__(self, house: HouseContext, tracker: FindingTracker, params: BurstParams) -> None:
        self.p = params
        self.tracker = tracker
        self.meter = house.index.meter_id
        self.key = f"L1:burst_pipe:{self.meter}"
        self.state = TierState()
        self.out: list[Finding] = []

    def update(self, chunk: Chunk, ts: Ints, values: Floats, first_new: int) -> list[Finding]:
        """Findings from the new samples ``ts[first_new:]`` (earlier ones are history)."""
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
        low = np.where(
            np.isnan(values), np.nan, (values < 0.5 * self.p.burst_lpm).astype(np.float64)
        )
        low_share = rolling_mean(ts, low, window_us, expected, 0.8)[first_new:]
        high = np.nan_to_num(mean) > self.p.burst_lpm
        unbroken = np.nan_to_num(low_share, nan=1.0) == 0.0
        tiers = (high & unbroken).astype(np.int64)
        raise_ = functools.partial(self._raise, mean)
        track_tiers(self.state, ts[first_new:], tiers, chunk_end_us=chunk.t1_us,
                    clear_after_us=3600 * US, on_raise=raise_, on_clear=clear)  # fmt: skip
        return self.out

    def _raise(self, mean: Any, j: int, at_us: int, tier: int) -> None:
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
        self.info = DetectorInfo("l1_burst_rule", "L1", "rule", "1.0.0", streams=frozenset({FLOW}))
        self.tracker = FindingTracker(house.house_id, self.info.source(), house.zone)
        self.watch = BurstWatch(house, self.tracker, params or BurstParams())
        self.carry = Carry(round((params or BurstParams()).burst_window_s * US))

    def update(self, chunk: Chunk) -> list[Finding]:
        ts, values, first_new = self.carry.extend(chunk.get(FLOW))
        return self.watch.update(chunk, ts, values, first_new)


def _never(index: int, at_us: int, tier: int) -> None:
    raise AssertionError("no samples, so nothing can be raised")
