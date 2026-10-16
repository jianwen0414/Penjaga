"""L5 supply interruptions: mains and water supply, deterministic.

* Power: the board monitor reports mains lost and restored (``elec.supply.state``). A loss
  opens a finding and its restoration clears it.
* Water: the inlet pressure collapses when the network stops supplying the house. The rule is
  a 10-minute mean inlet pressure below 0.5 bar. That threshold is an assumption, well below
  the 7.5 m (about 0.74 bar) minimum residual head of SPAN's design guidelines and far below
  the usual 1.5-3 bar (DS-018). The finding clears once the pressure has been back for 10
  minutes. With a tank level sensor, the evidence says how much water the roof tank still
  holds and, from the last half hour's fall in level, about how long it will last.
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


@dataclass(frozen=True)
class SupplyParams:
    pressure_window_s: float = 600.0
    low_pressure_bar: float = 0.5  # assumption, see module docstring
    restored_after_s: float = 600.0
    pressure_period_s: float = 10.0  # catalogue period of the pressure stream
    tank_trend_s: float = 1800.0
    min_coverage: float = 0.5


class L5SupplyDetector:
    def __init__(self, house: HouseContext, params: SupplyParams | None = None) -> None:
        self.p = params or SupplyParams()
        self.house = house
        ix = house.index
        self.meter = ix.meter_id
        self.board = ix.house.electrical.boards[0].id
        tank = ix.house.water.tanks[0]
        self.tank_l = float(tank.capacity_l)
        self.info = DetectorInfo(
            "l5_supply",
            "L5",
            "rule",
            "1.0.0",
            streams=frozenset({PRESSURE, TANK}),
            event_streams=frozenset({SUPPLY}),
        )
        self.tracker = FindingTracker(house.house_id, self.info.source(), house.zone)
        keep = round(max(self.p.pressure_window_s, self.p.tank_trend_s) * US)
        self.pressure = Carry(keep)
        self.tank = Carry(round(self.p.tank_trend_s * US))
        self.water_state = TierState()
        self.water_key = f"L5:water_supply_interruption:{self.meter}"
        self.power_key = f"L5:power_supply_interruption:{self.board}"
        self.out: list[Finding] = []

    def update(self, chunk: Chunk) -> list[Finding]:
        self.out = []
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

    def _tank_outlook(
        self, tank: tuple[Ints, Floats], at_us: int
    ) -> tuple[float, float | None] | None:
        ts, level = tank
        sel = (ts <= at_us) & (ts > at_us - round(self.p.tank_trend_s * US)) & ~np.isnan(level)
        if not sel.any():
            return None
        litres = float(level[sel][-1]) / 100.0 * self.tank_l
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


def _never(index: int, at_us: int, tier: int) -> None:
    raise AssertionError("no samples, so nothing can be raised")
