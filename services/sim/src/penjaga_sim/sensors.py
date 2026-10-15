"""Sensor kit: turn one day of physics into the catalogue's streams, as real sensors would.

* Every stream in streams.json whose sensor is enabled is produced, and nothing else.
* Sample k of a stream with period P is stamped day_start + k*P (k = 1 .. 86400/P): the
  timestamp marks the END of the period (catalogue convention). Mean streams average the
  seconds of the period; instant streams take the last second; sum streams total it.
* Values are quantised to the stream's resolution. The water meter and the energy channels
  report increments of a cumulative register, so small flows accumulate into whole scale
  intervals instead of rounding to zero.
* Radio and gateway dropouts make samples missing (value NaN, quality 'missing').
* Values outside the catalogue range keep their value and are flagged 'out_of_range'.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt

from penjaga_contract.core import Quality, Sensor, StreamDef
from penjaga_sim.electrical import ElecDay
from penjaga_sim.household import N
from penjaga_sim.model import HouseModel
from penjaga_sim.rng import rng
from penjaga_sim.water import WaterDay
from penjaga_sim.weather import DayWeather

Arr = npt.NDArray[np.float64]
QUALITY_CODES = list(Quality)
OK = QUALITY_CODES.index(Quality.ok)
MISSING = QUALITY_CODES.index(Quality.missing)
OUT_OF_RANGE = QUALITY_CODES.index(Quality.out_of_range)

DROPOUT_SECTION = {
    "db_monitor": "db_monitor",
    "water_meter": "water_meter",
    "pressure_sensor": "pressure",
    "tank_level_sensor": "tank_level",
    "smart_plug": "smart_plug",
}


@dataclass
class Registers:
    """Cumulative counters carried across days (meter and energy registers)."""

    meter_l: float = 0.0
    energy_wh: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class DayPhysics:
    """Everything the sensors observe on one day."""

    water: WaterDay
    elec: ElecDay
    weather: DayWeather


@dataclass(frozen=True)
class StreamDay:
    t0_us: int  # timestamp of the first sample
    period_us: int
    values: Arr  # NaN where missing
    quality: npt.NDArray[np.uint8]


def _period_view(arr: npt.NDArray[Any], period: int) -> npt.NDArray[Any]:
    return arr.reshape(N // period, period)


def _quantise(values: Arr, resolution: float) -> Arr:
    if resolution <= 0:
        return values
    return np.round(values / resolution) * resolution


def _register_increments(
    per_second: Arr, period: int, scale: float, start: float
) -> tuple[Arr, float]:
    """Per-period increments of a register counting whole scale steps, plus its end value."""
    cumulative = start + np.cumsum(per_second)
    marks = cumulative[period - 1 :: period]
    counts = np.floor(np.concatenate(([start], marks)) / scale + 1e-9)
    return np.diff(counts) * scale, float(cumulative[-1])


class SensorKit:
    def __init__(
        self, model: HouseModel, streams: list[StreamDef], enabled: frozenset[str] | None
    ) -> None:
        self.m = model
        self.streams = [
            s
            for s in streams
            if s.kind.value == "sampled" and (enabled is None or s.sensor_id in enabled)
        ]
        self.event_streams = [
            s
            for s in streams
            if s.kind.value == "event" and (enabled is None or s.sensor_id in enabled)
        ]
        self.sensor_kind = {s.id: s.kind.value for s in model.index.house.sensors}

    def _dropouts(self, sensor: str, ordinal: int) -> npt.NDArray[np.bool_]:
        section = DROPOUT_SECTION.get(self.sensor_kind[sensor])
        mask = np.zeros(N, np.bool_)
        if section is None:
            return mask
        r = rng(self.m.seed, "day", ordinal, "dropouts", sensor)
        rate = self.m.draws.house(f"sensors.{section}.dropouts_per_day", sensor)
        length = self.m.cal[f"sensors.{section}.dropout_seconds"]
        for _ in range(int(r.poisson(rate))):
            start = int(r.uniform(0, N))
            mask[start : start + int(length.sample(r))] = True
        return mask

    def render(
        self, ordinal: int, day_start_us: int, phys: DayPhysics, registers: Registers
    ) -> tuple[dict[str, StreamDay], Registers]:
        out: dict[str, StreamDay] = {}
        regs = Registers(registers.meter_l, dict(registers.energy_wh))
        drop = {s: self._dropouts(s, ordinal) for s in {st.sensor_id for st in self.streams}}
        for s in self.streams:
            period = int(s.nominal_period_s or 1)
            res = float(s.resolution or 0.0)
            r = rng(self.m.seed, "day", ordinal, "noise", s.id)
            family = {"water": self._water, "elec": self._elec, "weather": self._weather}
            values = family[s.id.split(".")[0]](s, _Sampling(period, res, r), phys, regs)
            # remove binary floating-point residue (0.6000000000000001) from reported values
            values = np.round(values, 9)
            quality = np.full(values.size, OK, np.uint8)
            lo, hi = s.value_min, s.value_max
            if lo is not None and hi is not None:
                quality[(values < lo) | (values > hi)] = OUT_OF_RANGE
            gap = _period_view(drop[s.sensor_id], period).any(axis=1)
            values[gap] = np.nan
            quality[gap] = MISSING
            out[s.id] = StreamDay(
                day_start_us + period * 1_000_000, period * 1_000_000, values, quality
            )
        return out, regs

    def _gain(self, section: str, key: str) -> float:
        return 1.0 + self.m.draws.house(f"sensors.{section}.gain_error_pct", key) / 100.0

    def _water(self, s: StreamDef, smp: _Sampling, phys: DayPhysics, regs: Registers) -> Arr:
        water, cal, period, r = phys.water, self.m.cal, smp.period, smp.r
        if s.id == "water.main.flow_lpm":
            scale = float(self.m.index.sensors_of_kind("water_meter")[0].spec["scale_interval_l"])
            litres, regs.meter_l = _register_increments(
                water.meter_registered_lps, period, scale, regs.meter_l
            )
            return litres / (period / 60.0)
        if s.id == "water.main.pressure_bar":
            sensor: Sensor = self.m.index.get(s.sensor_id, Sensor)
            offset = (
                self.m.draws.house("sensors.pressure.offset_pct_fs", s.sensor_id)
                / 100.0
                * float(sensor.spec["range_max_bar"])
            )
            inst = water.pressure_bar[period - 1 :: period] + offset
            inst = inst + r.normal(0, cal.value("sensors.pressure.noise_bar"), inst.size)
            return np.maximum(_quantise(inst, smp.res), 0.0)
        if s.id == "water.tank.level_pct":
            height_mm = 1000 * cal.value("water.tank.height_m")
            inst = water.tank_fraction[period - 1 :: period] * 100.0
            filling = _period_view(water.inflow_lps, period).max(axis=1) > 0
            noise_mm = cal.value("sensors.tank_level.noise_mm") + filling * cal.value(
                "sensors.tank_level.ripple_mm"
            )
            inst = inst + r.normal(0, 1, inst.size) * noise_mm / height_mm * 100.0
            return np.clip(_quantise(inst, smp.res), 0.0, 100.0)
        raise ValueError(f"no sensor model for stream {s.id!r}")

    def _elec(self, s: StreamDef, smp: _Sampling, phys: DayPhysics, regs: Registers) -> Arr:
        elec, cal, period, res, r = phys.elec, self.m.cal, smp.period, smp.res, smp.r
        parts = s.id.split(".")

        def mean(series: Arr) -> Arr:
            return _period_view(series, period).mean(axis=1)

        match parts:
            case ["elec", "main", "power_w"]:
                # active power is computed from voltage and current: exactly 0 without voltage
                m = mean(elec.main_power_w)
                noise = r.normal(0, cal.value("sensors.db_monitor.power_noise_w"), m.size)
                noisy = np.where(
                    mean(elec.voltage_v) > 0, m * self._gain("db_monitor", "main") + noise, 0.0
                )
            case ["elec", "main", "voltage_v"]:
                m = mean(elec.voltage_v)
                noise = r.normal(0, cal.value("sensors.db_monitor.voltage_noise_v"), m.size)
                noisy = np.where(m > 0, m + noise, 0.0)
            case ["elec", "circuit", c, "current_a"]:
                m = mean(elec.circuit_current_a[c])
                noise = r.normal(0, cal.value("sensors.db_monitor.current_noise_a"), m.size)
                noisy = m * self._gain("db_monitor", c) + noise
            case ["elec", "circuit", c, "energy_wh"]:
                wh_per_s = elec.circuit_power_w[c] * self._gain("db_monitor", c) / 3600.0
                inc, regs.energy_wh[c] = _register_increments(
                    wh_per_s, period, res, regs.energy_wh.get(c, 0.0)
                )
                return inc
            case ["elec", "rcd", dev, *_]:
                m = mean(elec.residual_ma[dev])
                sd = cal.value("sensors.db_monitor.residual_noise_ma")
                noisy = m + np.abs(r.normal(0, sd, m.size))
            case ["elec", "appliance", app, *_]:
                m = mean(elec.plug_power_w.get(app, np.zeros(N)))
                noise = r.normal(0, cal.value("sensors.smart_plug.noise_w"), m.size)
                noisy = m * self._gain("smart_plug", app) + noise
            case _:
                raise ValueError(f"no sensor model for stream {s.id!r}")
        return np.maximum(_quantise(noisy, res), 0.0)

    def _weather(self, s: StreamDef, smp: _Sampling, phys: DayPhysics, regs: Registers) -> Arr:
        hourly = {
            "weather.outdoor.temp_c": phys.weather.hourly_temp_c,
            "weather.outdoor.rh_pct": phys.weather.hourly_rh_pct,
            "weather.precip_mm": phys.weather.hourly_precip_mm,
        }
        if s.id not in hourly:
            raise ValueError(f"no sensor model for stream {s.id!r}")
        return _quantise(hourly[s.id].copy(), smp.res)


@dataclass(frozen=True)
class _Sampling:
    period: int  # seconds
    res: float  # quantisation step (0: none)
    r: np.random.Generator  # this stream's noise for this day
