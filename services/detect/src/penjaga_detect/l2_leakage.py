"""L2 earth-leakage baseline: rising residual current judged against each RCCB's own normal.

The safety floor (``l2_floor``) judges residual current against the RCCB's rating IΔn. A leak
that starts near 1 mA under a 30 or 100 mA RCCB stays far below those thresholds for weeks
(docs/EVALUATION.md). This detector instead learns what is normal for each RCCB in this house
and flags a sustained rise above it, early, as a heads-up for an electrician check
(PRD §15.3, the risk model's job).

**Method**, per RCCB, decided at each house-hour boundary (so chunking does not matter):
1. The hour's median residual current, if at least ``min_coverage`` of the hour is present.
   The median ignores trips and short spikes.
2. **Commissioning:** over the first ``commission_hours`` hourly medians, the level (step 3)
   of every full window gives the baseline. It is their median, with a spread of 1.4826 MAD,
   never below the monitor's resolution.
3. RCCBs nest: the main RCCB also carries what each downstream RCCB carries, and leakage
   currents add. So each hour, the downstream RCCBs' medians are subtracted, leaving the
   leakage of the circuits each RCCB protects directly. Steps 2, 4 and 5 use that series, so
   a leak is attributed to the nearest RCCB.
4. The level is a low quantile (``level_quantile``) of the last ``window_hours`` hourly
   medians. Appliances add leakage while they run (the main RCCB rises about 0.8 mA on
   nights with aircon), so the floor of the window shows a fault, which leaks all day, while
   normal switching does not; the same idea as minimum night flow for water. The rise is the
   level minus the baseline.
5. Page's one-sided CUSUM on the standardised rise: S = max(0, S + z - k). Alarm when
   S > h and the rise is at least ``min_rise_ma``. S is capped at 2h. The finding clears once
   the rise has stayed below ``min_rise_ma`` for ``clear_hours``.

**Localisation.** The residual monitor sees the RCCB, not the circuit. The finding names the
RCCB, and gives the circuits it protects directly as equally likely candidates.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from penjaga_contract.core import Finding
from penjaga_detect.chunk import Chunk
from penjaga_detect.detector import DetectorInfo, HouseContext
from penjaga_detect.findings import FindingTracker, Observation

US = 1_000_000
HOUR_US = 3600 * US
MAD_TO_SD = 1.4826


@dataclass(frozen=True)
class LeakageParams:
    commission_hours: int = 72
    window_hours: int = 24
    level_quantile: float = 0.1
    cusum_k: float = 0.5
    cusum_h: float = 8.0
    min_rise_ma: float = 0.3
    clear_hours: int = 24
    min_coverage: float = 0.5


@dataclass
class _Device:
    rated_ma: float
    parent: str | None
    children: list[str]
    direct_circuits: list[str]
    pending: dict[int, list[np.ndarray]] = field(default_factory=dict)  # hour -> values
    hourly: list[float] = field(default_factory=list)  # valid hourly medians, in order
    baseline: float | None = None
    spread: float | None = None
    s: float = 0.0
    quiet_hours: int = 0


class L2LeakageBaseline:
    def __init__(self, house: HouseContext, params: LeakageParams | None = None) -> None:
        self.p = params or LeakageParams()
        self.house = house
        ix = house.index
        rated = {
            d.id: float(d.rated_residual_ma)
            for d in ix.house.electrical.protective_devices
            if d.rated_residual_ma is not None
        }
        nearest: dict[str, list[str]] = {d: [] for d in rated}
        parent: dict[str, str | None] = {d: None for d in rated}
        for c in ix.house.electrical.circuits:
            chain = ix.rccb_chain(c.id)
            if chain:
                nearest[chain[0]].append(c.id)
                for below, above in itertools.pairwise(chain):
                    parent[below] = above
        self.devices = {
            d: _Device(
                rated_ma=rated[d],
                parent=parent[d],
                children=sorted(x for x, p in parent.items() if p == d),
                direct_circuits=sorted(nearest[d]),
            )
            for d in rated
        }
        dbm = [s for s in ix.house.sensors if s.kind.value == "db_monitor"]
        spec = dbm[0].spec if dbm else {}
        self.resolution = float(spec.get("residual_resolution_ma", 0.1))
        self.info = DetectorInfo(
            "l2_leakage_baseline",
            "L2",
            "baseline",
            "1.0.0",
            streams=frozenset(self._stream(d) for d in self.devices),
        )
        self.tracker = FindingTracker(house.house_id, self.info.source(), house.zone)
        self.next_hour: int | None = None
        self.out: list[Finding] = []

    @staticmethod
    def _stream(device: str) -> str:
        return f"elec.rcd.{device}.residual_ma"

    # ------------------------------------------------------------------ chunks

    def update(self, chunk: Chunk) -> list[Finding]:
        self.out = []
        for device, dev in self.devices.items():
            s = chunk.get(self._stream(device))
            if not len(s):
                continue
            hours = (s.ts_us - 1) // HOUR_US  # a sample stamped on the hour ends that hour
            for h in np.unique(hours):
                dev.pending.setdefault(int(h), []).append(s.values[hours == h])
        if self.next_hour is None:
            first = -(-chunk.t0_us // HOUR_US)  # the first whole hour after t0
            self.next_hour = int(first)
        while (self.next_hour + 1) * HOUR_US <= chunk.t1_us:
            self._close_hour(self.next_hour)
            self.next_hour += 1
        return self.out

    def _close_hour(self, hour: int) -> None:
        end_us = (hour + 1) * HOUR_US
        medians: dict[str, float | None] = {}
        for device, dev in self.devices.items():
            parts = dev.pending.pop(hour, [])
            for old in [h for h in dev.pending if h < hour]:
                del dev.pending[old]
            values = np.concatenate(parts) if parts else np.empty(0)
            present = values[~np.isnan(values)]
            enough = present.size >= self.p.min_coverage * 3600  # 1 s stream
            medians[device] = float(np.median(present)) if enough else None
        for device, dev in self.devices.items():
            mine, below = medians[device], [medians[c] for c in dev.children]
            if mine is None or any(b is None for b in below):
                continue  # an hour without every reading says nothing
            dev.hourly.append(mine - sum(b for b in below if b is not None))
            keep = max(self.p.commission_hours, self.p.window_hours)
            if dev.baseline is not None and len(dev.hourly) > keep:
                dev.hourly = dev.hourly[-keep:]
            self._commission(dev)
            rise = self._rise(dev)
            if rise is not None:
                self._decide(device, dev, rise, end_us)

    def _level(self, hourly: list[float]) -> float:
        return float(np.quantile(hourly[-self.p.window_hours :], self.p.level_quantile))

    def _commission(self, dev: _Device) -> None:
        p = self.p
        if dev.baseline is None and len(dev.hourly) >= p.commission_hours:
            first = dev.hourly[: p.commission_hours]
            ends = range(min(p.window_hours, len(first)), len(first) + 1)
            levels = np.array([self._level(first[:end]) for end in ends])
            dev.baseline = float(np.median(levels))
            mad = float(np.median(np.abs(levels - dev.baseline)))
            dev.spread = max(MAD_TO_SD * mad, self.resolution)

    def _rise(self, dev: _Device) -> float | None:
        if dev.baseline is None or len(dev.hourly) < self.p.window_hours:
            return None
        return self._level(dev.hourly) - dev.baseline

    # ------------------------------------------------------------------ findings

    def _decide(self, device: str, dev: _Device, rise: float, end_us: int) -> None:
        p = self.p
        assert dev.spread is not None
        assert dev.baseline is not None
        z = rise / dev.spread
        # capped, so evidence from a long fault cannot keep the alarm up long after it ends
        dev.s = min(2 * p.cusum_h, max(0.0, dev.s + z - p.cusum_k))
        dev.quiet_hours = dev.quiet_hours + 1 if rise < p.min_rise_ma else 0
        key = f"L2:earth_leakage_rising:{device}:baseline"
        if dev.s > p.cusum_h and rise >= p.min_rise_ma:
            n = len(dev.direct_circuits)
            share = math.floor(1e6 / n) / 1e6 if n else 0.0  # never sums above 1
            candidates = [
                {"object_id": c, "p": share, "basis": "behind_rccb"} for c in dev.direct_circuits
            ]
            finding = self.tracker.observe(
                Observation(
                    key=key,
                    type="earth_leakage_rising",
                    severity="heads_up",
                    confidence=1.0,
                    at_us=end_us,
                    object_ids=[device],
                    candidates=candidates,
                    candidates_other_p=round(1.0 - share * n, 6),
                    evidence=self._evidence(dev, rise),
                )
            )
        elif self.tracker.is_open(key) and dev.quiet_hours >= p.clear_hours:
            dev.s = 0.0
            finding = self.tracker.clear(key, end_us)
        else:
            finding = None
        if finding is not None:
            self.out.append(finding)

    def _evidence(self, dev: _Device, rise: float) -> list[dict[str, Any]]:
        assert dev.baseline is not None
        return [
            {"name": "baseline_direct_leakage", "value": round(dev.baseline, 3), "unit": "mA"},
            {"name": "recent_direct_leakage", "value": round(dev.baseline + rise, 3), "unit": "mA"},
            {
                "name": "rise_on_direct_circuits",
                "value": round(rise, 3),
                "unit": "mA",
                "threshold": self.p.min_rise_ma,
            },
            {"name": "cusum", "value": round(dev.s, 2), "threshold": self.p.cusum_h},
            {"name": "rated_residual", "value": dev.rated_ma, "unit": "mA"},
        ]
