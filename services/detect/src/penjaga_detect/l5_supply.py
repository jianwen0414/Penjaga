"""L5 supply interruptions: mains and water supply, deterministic.

* Power: the board monitor reports mains lost and restored (``elec.supply.state``). A loss
  opens a finding and its restoration clears it.
* Water: the inlet pressure collapses when the network stops supplying the house. The rule is
  a 10-minute mean inlet pressure below 0.5 bar. That threshold is an assumption, well below
  the 7.5 m (about 0.74 bar) minimum residual head of SPAN's design guidelines and far below
  the usual 1.5-3 bar (DS-018). The finding clears once the pressure has been back for 10
  minutes. With a tank level sensor, the evidence says how much water the roof tank still
  holds and about how long it will last (``outlook_method``, D-028):
  - ``slope_30min``: from the last half hour's fall in tank level (v1.0.0);
  - ``daily_mean``: the household's mean metered use over the last full 24 hours;
  - ``hour_profile``: the household's mean metered use for each hour of the day over the last
    ``profile_days`` days, walked forward from now until the tank's water is used up.
  The meter also counts water that bypasses the tank (main-fed taps), so the history methods
  scale it by ``tank_share``, chosen on validation. Only whole hours that ended before the
  supply was lost count, so the outlook never depends on how house time was chunked.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass

import numpy as np

from penjaga_contract.core import Finding
from penjaga_detect.chunk import Chunk
from penjaga_detect.detector import DetectorInfo, HouseContext
from penjaga_detect.findings import FindingTracker, Observation
from penjaga_detect.signals import Carry, Floats, Ints, TierState, rolling_mean, track_tiers

US = 1_000_000
SUPPLY = "elec.supply.state"
PRESSURE = "water.main.pressure_bar"
TANK = "water.tank.level_pct"
FLOW = "water.main.flow_lpm"
HOUR_US = 3600 * US
OUTLOOK_METHODS = ("slope_30min", "daily_mean", "hour_profile")
MAX_OUTLOOK_H = 96.0


@dataclass(frozen=True)
class SupplyParams:
    pressure_window_s: float = 600.0
    low_pressure_bar: float = 0.5  # assumption, see module docstring
    restored_after_s: float = 600.0
    pressure_period_s: float = 10.0  # catalogue period of the pressure stream
    tank_trend_s: float = 1800.0
    min_coverage: float = 0.5
    outlook_method: str = "slope_30min"  # see the module docstring; chosen on validation
    tank_share: float = 1.0  # share of metered water drawn through the roof tank
    profile_days: int = 7
    flow_period_s: float = 10.0  # catalogue period of the flow stream


class L5SupplyDetector:
    def __init__(self, house: HouseContext, params: SupplyParams | None = None) -> None:
        self.p = params or SupplyParams()
        self.house = house
        ix = house.index
        self.meter = ix.meter_id
        self.board = ix.house.electrical.boards[0].id
        tank = ix.house.water.tanks[0]
        self.tank_l = float(tank.capacity_l)
        if self.p.outlook_method not in OUTLOOK_METHODS:
            raise ValueError(f"outlook_method must be one of {OUTLOOK_METHODS}")
        self.info = DetectorInfo(
            "l5_supply",
            "L5",
            "rule",
            "1.1.0",
            streams=frozenset({PRESSURE, TANK, FLOW}),
            event_streams=frozenset({SUPPLY}),
        )
        self.tracker = FindingTracker(house.house_id, self.info.source(), house.zone)
        keep = round(max(self.p.pressure_window_s, self.p.tank_trend_s) * US)
        self.pressure = Carry(keep)
        self.tank = Carry(round(self.p.tank_trend_s * US))
        self.water_state = TierState()
        self.water_key = f"L5:water_supply_interruption:{self.meter}"
        self.power_key = f"L5:power_supply_interruption:{self.board}"
        self.hour_litres: dict[int, float] = {}  # metered litres per house hour (absolute index)
        self.hour_samples: dict[int, int] = {}
        self.out: list[Finding] = []

    def update(self, chunk: Chunk) -> list[Finding]:
        self.out = []
        self._history(chunk)
        self._water(chunk)
        self._power(chunk)
        self.out.sort(key=lambda f: (f.updated_at, f.key, f.revision))
        return self.out

    def _emit(self, finding: Finding | None) -> None:
        if finding is not None:
            self.out.append(finding)

    # ------------------------------------------------------------------ water

    def _water(self, chunk: Chunk) -> None:
        p = self.p
        tank_ts, tank_values, _ = self.tank.extend(chunk.get(TANK))
        ts, values, first_new = self.pressure.extend(chunk.get(PRESSURE))
        clear = functools.partial(self._clear, self.water_key)
        if ts.size == first_new:
            empty = np.empty(0, np.int64)
            track_tiers(self.water_state, empty, empty, chunk_end_us=chunk.t1_us,
                        clear_after_us=round(p.restored_after_s * US),
                        on_raise=_never, on_clear=clear)  # fmt: skip
            return
        expected = p.pressure_window_s / p.pressure_period_s
        mean = rolling_mean(ts, values, round(p.pressure_window_s * US), expected, p.min_coverage)
        mean = mean[first_new:]
        tiers = np.where(np.nan_to_num(mean, nan=np.inf) < p.low_pressure_bar, 1, 0).astype(
            np.int64
        )
        raise_ = functools.partial(self._raise_water, mean, (tank_ts, tank_values))
        track_tiers(self.water_state, ts[first_new:], tiers, chunk_end_us=chunk.t1_us,
                    clear_after_us=round(p.restored_after_s * US),
                    on_raise=raise_, on_clear=clear)  # fmt: skip

    def _history(self, chunk: Chunk) -> None:
        """Metered litres per absolute house hour (a sample covers the period ending at it)."""
        flow = chunk.get(FLOW)
        ok = ~np.isnan(flow.values)
        if not ok.any():
            return
        hours = (flow.ts_us[ok] - 1) // HOUR_US
        litres = flow.values[ok] * self.p.flow_period_s / 60.0
        for h, total, n in zip(*_by_hour(hours, litres), strict=True):
            self.hour_litres[h] = self.hour_litres.get(h, 0.0) + total
            self.hour_samples[h] = self.hour_samples.get(h, 0) + n
        keep_from = int(hours.max()) - 24 * (self.p.profile_days + 1)
        for h in [h for h in self.hour_litres if h < keep_from]:
            del self.hour_litres[h]
            del self.hour_samples[h]

    def _hour_use(self, h: int) -> float | None:
        """Metered litres in house hour ``h`` if enough of its samples arrived."""
        expected = 3600.0 / self.p.flow_period_s
        if self.hour_samples.get(h, 0) < self.p.min_coverage * expected:
            return None
        return self.hour_litres[h] * expected / self.hour_samples[h]

    def _history_hours(self, litres: float, since_us: int) -> float | None:
        """Hours until the tank's ``litres`` are used, from the household's metered history
        in whole hours that ended at or before ``since_us``."""
        last = since_us // HOUR_US - 1  # the last whole hour before the supply was lost
        share = self.p.tank_share
        if self.p.outlook_method == "daily_mean":
            uses = [self._hour_use(h) for h in range(last - 23, last + 1)]
            known = [u for u in uses if u is not None]
            if len(known) < 12:
                return None
            rate = share * float(np.mean(known))
            return None if rate <= 1.0 else min(MAX_OUTLOOK_H, litres / rate)
        profile = []
        for hour_of_day in range(24):
            same = [
                self._hour_use(h)
                for h in range(last - 24 * self.p.profile_days + 1, last + 1)
                if h % 24 == hour_of_day
            ]
            known = [u for u in same if u is not None]
            profile.append(share * float(np.mean(known)) if known else None)
        if sum(p is None for p in profile) > 6 or not any(profile):
            return None
        fill = float(np.mean([p for p in profile if p is not None]))
        rates = [fill if p is None else p for p in profile]
        left, t_h = litres, 0.0
        position = since_us / HOUR_US
        while t_h < MAX_OUTLOOK_H:
            hour = int(position // 1)
            part = min(1.0, hour + 1 - position)
            use = rates[hour % 24] * part
            if use >= left:
                return t_h + part * left / use if use > 0 else None
            left -= use
            t_h += part
            position = hour + 1
        return MAX_OUTLOOK_H

    def _tank_outlook(
        self, tank: tuple[Ints, Floats], at_us: int
    ) -> tuple[float, float | None] | None:
        ts, level = tank
        sel = (ts <= at_us) & (ts > at_us - round(self.p.tank_trend_s * US)) & ~np.isnan(level)
        if not sel.any():
            return None
        litres = float(level[sel][-1]) / 100.0 * self.tank_l
        if self.p.outlook_method != "slope_30min":
            lost_at = at_us - round(self.p.pressure_window_s * US)
            return litres, self._history_hours(litres, lost_at)
        hours = None
        if sel.sum() >= 3:
            slope_pct_per_h = np.polyfit((ts[sel] - at_us) / 3600 / US, level[sel], 1)[0]
            use_l_per_h = -slope_pct_per_h / 100.0 * self.tank_l
            if use_l_per_h > 1.0:
                hours = litres / use_l_per_h
        return litres, hours

    def _raise_water(
        self, mean: Floats, tank: tuple[Ints, Floats], j: int, at_us: int, tier: int
    ) -> None:
        evidence = [
            {"name": "mean_inlet_pressure_10min", "value": round(float(mean[j]), 3), "unit": "bar"},
            {"name": "threshold", "value": self.p.low_pressure_bar, "unit": "bar"},
        ]
        quantities = {}
        outlook = self._tank_outlook(tank, at_us)
        if outlook is not None:
            litres, hours = outlook
            evidence.append({"name": "tank_water", "value": round(litres), "unit": "L"})
            quantities["tank_water_l"] = {"value": round(litres), "unit": "L"}
            if hours is not None:
                quantities["tank_hours_left"] = {"value": round(hours, 1), "unit": "h"}
        self._emit(
            self.tracker.observe(
                Observation(
                    key=self.water_key,
                    type="water_supply_interruption",
                    severity="heads_up",
                    confidence=1.0,
                    at_us=at_us,
                    object_ids=[self.meter],
                    evidence=evidence,
                    quantities=quantities,
                )
            )
        )

    def _clear(self, key: str, at_us: int) -> None:
        self._emit(self.tracker.clear(key, at_us))

    # ------------------------------------------------------------------ power

    def _power(self, chunk: Chunk) -> None:
        for e in sorted((e for e in chunk.events if e.stream == SUPPLY), key=lambda e: e.ts_us):
            if e.payload["state"] == "lost":
                self._emit(
                    self.tracker.observe(
                        Observation(
                            key=self.power_key,
                            type="power_supply_interruption",
                            severity="heads_up",
                            confidence=1.0,
                            at_us=e.ts_us,
                            object_ids=[self.board],
                            evidence=[{"name": "mains", "value": "lost"}],
                        )
                    )
                )
            elif self.tracker.is_open(self.power_key):
                self._clear(self.power_key, e.ts_us)


def _by_hour(hours: Ints, litres: Floats) -> tuple[list[int], list[float], list[int]]:
    unique, inverse = np.unique(hours, return_inverse=True)
    totals = np.bincount(inverse, weights=litres)
    counts = np.bincount(inverse)
    return [int(h) for h in unique], [float(t) for t in totals], [int(c) for c in counts]


def _never(index: int, at_us: int, tier: int) -> None:
    raise AssertionError("no samples, so nothing can be raised")
