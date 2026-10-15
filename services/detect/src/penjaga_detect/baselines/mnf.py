"""L1 baseline: minimum night flow (MNF).

The classic water-loss screen: during the quiet hours the lowest sustained flow through the
meter should be zero in a house without leaks. For each night the detector averages the meter
flow over fixed buckets inside the night window and takes the minimum bucket; if that minimum
stays above a threshold for ``nights`` consecutive nights it reports unexplained night flow,
and it clears after as many nights below the threshold. It does not localise.

Decisions are stamped at the end of each night window, so they do not depend on chunking.
Parameters (window, bucket, threshold, nights) are chosen on the validation split by M11.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

import numpy as np
import numpy.typing as npt

from penjaga_contract.core import Finding
from penjaga_detect.chunk import Chunk
from penjaga_detect.detector import DetectorInfo, HouseContext
from penjaga_detect.findings import FindingTracker, Observation

US = 1_000_000
FLOW = "water.main.flow_lpm"


@dataclass(frozen=True)
class MnfParams:
    night_start_h: float = 2.0
    night_end_h: float = 4.0
    bucket_min: float = 15.0
    threshold_lpm: float = 0.05
    nights: int = 2
    min_coverage: float = 0.5  # share of samples present for a night to count


class MnfBaseline:
    def __init__(self, house: HouseContext, params: MnfParams | None = None) -> None:
        self.params = params or MnfParams()
        self.house = house
        self.info = DetectorInfo("l1_mnf_baseline", "L1", "baseline", "1.0.0")
        self.tracker = FindingTracker(house.house_id, self.info.source(), house.zone)
        self.key = f"L1:night_flow_unexplained:{house.index.meter_id}"
        self._samples: dict[date, list[tuple[npt.NDArray[np.int64], npt.NDArray[np.float64]]]] = {}
        self._above = 0
        self._below = 0
        self._next_night: date | None = None

    def _window(self, night: date) -> tuple[int, int]:
        start = datetime.combine(night, time(0), tzinfo=self.house.zone)
        t0 = start + timedelta(hours=self.params.night_start_h)
        t1 = start + timedelta(hours=self.params.night_end_h)
        return int(t0.timestamp()) * US, int(t1.timestamp()) * US

    def update(self, chunk: Chunk) -> list[Finding]:
        out: list[Finding] = []
        flow = chunk.get(FLOW)
        first = datetime.fromtimestamp(chunk.t0_us / US, tz=self.house.zone).date()
        last = datetime.fromtimestamp(chunk.t1_us / US, tz=self.house.zone).date()
        if self._next_night is None:
            self._next_night = first
        night = first - timedelta(days=1)
        while night <= last:
            w0, w1 = self._window(night)
            part = flow.between(max(w0, chunk.t0_us), min(w1, chunk.t1_us))
            if len(part):
                self._samples.setdefault(night, []).append((part.ts_us, part.values))
            night += timedelta(days=1)
        # decide every night whose window has ended within this chunk, in order
        while True:
            w0, w1 = self._window(self._next_night)
            if w1 > chunk.t1_us:
                break
            finding = self._decide(self._next_night, w0, w1)
            if finding is not None:
                out.append(finding)
            self._samples.pop(self._next_night, None)
            self._next_night += timedelta(days=1)
        return out

    def _night_minimum(
        self, w0: int, w1: int, pieces: list[tuple[npt.NDArray[np.int64], npt.NDArray[np.float64]]]
    ) -> float | None:
        p = self.params
        if not pieces:
            return None
        ts = np.concatenate([x[0] for x in pieces])
        values = np.concatenate([x[1] for x in pieces])
        present = ~np.isnan(values)
        bucket_us = round(p.bucket_min * 60 * US)
        n_buckets = max(1, (w1 - w0) // bucket_us)
        index = np.minimum((ts - w0 - 1) // bucket_us, n_buckets - 1)
        means = []
        for b in range(n_buckets):
            sel = (index == b) & present
            expected = np.sum(index == b)
            if expected and sel.sum() >= p.min_coverage * expected:
                means.append(float(values[sel].mean()))
        if len(means) < p.min_coverage * n_buckets:
            return None
        return min(means)

    def _decide(self, night: date, w0: int, w1: int) -> Finding | None:
        p = self.params
        mnf = self._night_minimum(w0, w1, self._samples.get(night, []))
        if mnf is None:
            return None  # not enough data: neither evidence for nor against
        if mnf > p.threshold_lpm:
            self._above, self._below = self._above + 1, 0
        else:
            self._above, self._below = 0, self._below + 1
        if self._above >= p.nights:
            return self.tracker.observe(
                Observation(
                    key=self.key,
                    type="night_flow_unexplained",
                    severity="heads_up",
                    confidence=1.0,
                    confidence_kind="deterministic_rule",
                    at_us=w1,
                    evidence=[
                        {"name": "night_min_flow", "value": round(mnf, 4), "unit": "L/min"},
                        {"name": "nights_above_threshold", "value": self._above},
                        {"name": "threshold", "value": p.threshold_lpm, "unit": "L/min"},
                    ],
                    quantities={
                        "water_l_per_day": {"value": round(mnf * 1440, 1), "unit": "L/day"}
                    },
                    series_refs=[
                        {
                            "stream": FLOW,
                            "window": {
                                "from": self.tracker.iso(w0),
                                "to": self.tracker.iso(w1),
                            },
                            "bucket_s": p.bucket_min * 60,
                        }
                    ],
                )
            )
        if self._below >= p.nights and self.tracker.is_open(self.key):
            return self.tracker.clear(self.key, w1)
        return None
