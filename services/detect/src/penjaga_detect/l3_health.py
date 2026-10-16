"""L3 appliance health baseline: weather-normalised daily baselines with CUSUM (D-009).

Each house day (decided at its end, so chunking does not matter):

* Fridge (smart plug, 1 s power). Daily duty cycle (share of time drawing more than
  ``fridge_on_w``), mean power while running, and mean run length. The first
  ``fridge_commissioning_days`` complete days set a baseline: duty against the day's mean
  outdoor temperature (the kitchen follows outdoors), on-power and run length as means. After
  that, Page's one-sided CUSUM on standardised residuals: rising duty, a door-seal-type fault,
  gives fridge_duty_cycle_rise; rising on-power or shortening runs, compressor wear, gives
  fridge_compressor_wear.
* Aircon (dedicated circuit, energy every 60 s). Hours of operation (minutes drawing more than
  ``ac_on_wh_per_min``), energy per operating hour, and the mean outdoor temperature while
  operating. The first ``ac_commissioning_days`` days with enough operation fit energy per
  operating hour as a linear function of outdoor temperature. Afterwards the daily ratio of
  actual to expected is the efficiency gap; a CUSUM on it gives aircon_efficiency_decline, with
  the gap, its trend (%/week over the last 14 operating days) and the extra energy per day.

CUSUM: S = max(0, S + z - k), alarm at S > h (Page 1954). k and h are tuned on validation.
A finding clears when S has been 0 for ``clear_days`` consecutive days.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np

from penjaga_contract.core import ApplianceKind, Finding
from penjaga_detect.chunk import Chunk
from penjaga_detect.detector import DetectorInfo, HouseContext
from penjaga_detect.findings import FindingTracker, Observation
from penjaga_detect.signals import Floats

US = 1_000_000
DAY_US = 86_400 * US
TEMP = "weather.outdoor.temp_c"


@dataclass(frozen=True)
class L3Params:
    fridge_on_w: float = 30.0
    fridge_commissioning_days: int = 7
    ac_on_wh_per_min: float = 0.5  # 30 W: drawing more than standby
    ac_min_hours: float = 1.0  # days with less operation say nothing about efficiency
    ac_commissioning_days: int = 14
    cusum_k: float = 0.5
    cusum_h: float = 5.0
    clear_days: int = 3
    # floor on the baseline spread, as a share of the mean: a few days underestimate it
    min_sd_fraction: float = 0.05


@dataclass
class _Cusum:
    s: float = 0.0
    quiet: int = 0

    def step(self, z: float, k: float) -> float:
        self.s = max(0.0, self.s + z - k)
        self.quiet = self.quiet + 1 if self.s == 0.0 else 0
        return self.s


@dataclass
class _Daily:
    """Samples of one stream, grouped by house day until the day is complete."""

    pending: dict[int, list[tuple[np.ndarray, Floats]]] = field(default_factory=dict)


@dataclass
class _Baseline:
    rows: list[dict[str, float]] = field(default_factory=list)
    fitted: dict[str, Any] | None = None


class L3HealthBaseline:
    def __init__(self, house: HouseContext, params: L3Params | None = None) -> None:
        self.p = params or L3Params()
        self.house = house
        ix = house.index
        fridges = [a.id for a in ix.house.appliances if a.kind is ApplianceKind.refrigerator]
        self.fridge = fridges[0] if fridges else None
        self.fridge_stream = f"elec.appliance.{self.fridge}.power_w" if self.fridge else None
        self.aircons: dict[str, str] = {}  # circuit -> aircon appliance
        for a in ix.house.appliances:
            if a.kind is ApplianceKind.air_conditioner:
                circuit = ix.circuit_of_appliance(a.id)
                if circuit is not None:
                    self.aircons[circuit] = a.id
        streams = {TEMP} | {f"elec.circuit.{c}.energy_wh" for c in self.aircons}
        if self.fridge_stream:
            streams.add(self.fridge_stream)
        self.info = DetectorInfo(
            "l3_health_baseline", "L3", "baseline", "1.0.0", streams=frozenset(streams)
        )
        self.tracker = FindingTracker(house.house_id, self.info.source(), house.zone)
        self.days: dict[str, _Daily] = {s: _Daily() for s in streams}
        self.next_day: date | None = None
        self.fridge_base = _Baseline()
        self.ac_base = {c: _Baseline() for c in self.aircons}
        self.cusums: dict[str, _Cusum] = {}
        self.ac_gaps: dict[str, list[tuple[int, float]]] = {c: [] for c in self.aircons}
        self.out: list[Finding] = []

    # ------------------------------------------------------------------ chunks

    def _day_index(self, ts: np.ndarray) -> np.ndarray:
        offset = datetime.fromtimestamp(int(ts[0]) / US, tz=self.house.zone).utcoffset()
        assert offset is not None
        local = (
            ts + int(offset.total_seconds()) * US - 1
        )  # a sample at midnight ends the day before
        return np.asarray(local // DAY_US + date(1970, 1, 1).toordinal(), dtype=np.int64)

    def update(self, chunk: Chunk) -> list[Finding]:
        self.out = []
        for stream, daily in self.days.items():
            s = chunk.get(stream)
            if not len(s):
                continue
            days = self._day_index(s.ts_us)
            for d in np.unique(days):
                sel = days == d
                daily.pending.setdefault(int(d), []).append((s.ts_us[sel], s.values[sel]))
        if self.next_day is None:
            self.next_day = datetime.fromtimestamp(chunk.t0_us / US, tz=self.house.zone).date()
            start = datetime.combine(self.next_day, datetime.min.time(), tzinfo=self.house.zone)
            if int(start.timestamp()) * US < chunk.t0_us:
                self.next_day += timedelta(days=1)  # partial first day: skip it
        while True:
            end = datetime.combine(
                self.next_day + timedelta(days=1), datetime.min.time(), tzinfo=self.house.zone
            )
            end_us = int(end.timestamp()) * US
            if end_us > chunk.t1_us:
                break
            self._close_day(self.next_day.toordinal(), end_us)
            self.next_day += timedelta(days=1)
        self.out.sort(key=lambda f: (f.updated_at, f.key, f.revision))
        return self.out

    def _take(self, stream: str, day: int) -> tuple[np.ndarray, Floats]:
        parts = self.days[stream].pending.pop(day, [])
        for d in [d for d in self.days[stream].pending if d < day]:
            del self.days[stream].pending[d]
        if not parts:
            return np.empty(0, np.int64), np.empty(0)
        return np.concatenate([p[0] for p in parts]), np.concatenate([p[1] for p in parts])

    def _close_day(self, day: int, end_us: int) -> None:
        t_ts, t_values = self._take(TEMP, day)
        if self.fridge_stream:
            ts, values = self._take(self.fridge_stream, day)
            self._fridge_day(ts, values, t_values, end_us)
        for circuit in self.aircons:
            ts, values = self._take(f"elec.circuit.{circuit}.energy_wh", day)
            self._ac_day(circuit, ts, values, t_ts=t_ts, temp=t_values, end_us=end_us)

    # ------------------------------------------------------------------ fridge

    def _fridge_day(self, ts: np.ndarray, power: Floats, temp: Floats, end_us: int) -> None:
        p = self.p
        ok = ~np.isnan(power)
        if ok.sum() < 0.5 * 86_400 or not np.isfinite(temp).any():
            return  # not enough of the day to judge
        on = (power > p.fridge_on_w) & ok
        edges = np.flatnonzero(np.diff(np.concatenate(([0], on.astype(np.int8), [0]))))
        runs = max(1, edges.size // 2)
        row = {
            "duty": float(on.sum() / ok.sum()),
            "on_w": float(power[on].mean()) if on.any() else float("nan"),
            "run_min": float(on.sum() / runs / 60.0),
            "kwh": float(np.nansum(power) / 3_600_000 * 86_400 / ok.sum()),
            "temp": float(np.nanmean(temp)),
        }
        base = self.fridge_base
        if base.fitted is None:
            base.rows.append(row)
            if len(base.rows) >= p.fridge_commissioning_days:
                base.fitted = self._fit_fridge(base.rows)
            return
        f = base.fitted
        duty_z = (row["duty"] - (f["duty_a"] + f["duty_b"] * row["temp"])) / f["duty_sd"]
        power_z = (row["on_w"] - f["on_w"]) / f["on_w_sd"]
        run_z = (f["run_min"] - row["run_min"]) / f["run_min_sd"]  # shorter runs are worse
        assert self.fridge is not None
        expected_kwh = f["kwh"]
        evidence = [
            {"name": "duty_cycle", "value": round(row["duty"], 3)},
            {
                "name": "baseline_duty_cycle",
                "value": round(f["duty_a"] + f["duty_b"] * row["temp"], 3),
            },
            {"name": "running_power", "value": round(row["on_w"], 1), "unit": "W"},
            {"name": "baseline_running_power", "value": round(f["on_w"], 1), "unit": "W"},
            {"name": "mean_run", "value": round(row["run_min"], 1), "unit": "min"},
            {"name": "baseline_mean_run", "value": round(f["run_min"], 1), "unit": "min"},
        ]
        extra = {
            "extra_energy_kwh_per_day": {
                "value": round(row["kwh"] - expected_kwh, 3),
                "unit": "kWh/day",
            }
        }
        self._cusum_finding(
            f"L3:fridge_duty_cycle_rise:{self.fridge}",
            "fridge_duty_cycle_rise",
            duty_z,
            at_us=end_us,
            objects=[self.fridge],
            evidence=evidence,
            quantities=extra,
        )
        self._cusum_finding(
            f"L3:fridge_compressor_wear:{self.fridge}",
            "fridge_compressor_wear",
            max(power_z, run_z),
            at_us=end_us,
            objects=[self.fridge],
            evidence=evidence,
            quantities=extra,
        )

    def _fit_fridge(self, rows: list[dict[str, float]]) -> dict[str, Any]:
        floor = self.p.min_sd_fraction
        temp = np.array([r["temp"] for r in rows])
        duty = np.array([r["duty"] for r in rows])
        if temp.std() > 0.3:
            b, a = np.polyfit(temp, duty, 1)
        else:
            a, b = float(duty.mean()), 0.0
        resid = duty - (a + b * temp)
        out: dict[str, Any] = {
            "duty_a": float(a),
            "duty_b": float(b),
            "duty_sd": max(float(resid.std(ddof=1)), floor * max(float(duty.mean()), 1e-6)),
        }
        for name in ("on_w", "run_min", "kwh"):
            v = np.array([r[name] for r in rows if np.isfinite(r[name])])
            out[name] = float(v.mean())
            out[f"{name}_sd"] = max(float(v.std(ddof=1)), floor * abs(float(v.mean())))
        return out

    # ------------------------------------------------------------------ aircon

    def _ac_day(
        self,
        circuit: str,
        ts: np.ndarray,
        energy: Floats,
        *,
        t_ts: np.ndarray,
        temp: Floats,
        end_us: int,
    ) -> None:
        p = self.p
        ok = ~np.isnan(energy)
        if ok.sum() < 0.5 * 1440 or not np.isfinite(temp).any():
            return
        running = (energy > p.ac_on_wh_per_min) & ok
        hours = float(running.sum() / 60.0)
        if hours < p.ac_min_hours:
            return
        t_at = (
            np.interp(ts[running], t_ts, temp) if t_ts.size else np.full(int(running.sum()), np.nan)
        )
        row = {
            "hours": hours,
            "kwh": float(energy[running].sum() / 1000.0),
            "per_hour": float(energy[running].sum() / 1000.0 / hours),
            "temp": float(np.nanmean(t_at)),
        }
        base = self.ac_base[circuit]
        if base.fitted is None:
            base.rows.append(row)
            if len(base.rows) >= p.ac_commissioning_days:
                base.fitted = self._fit_ac(base.rows)
            return
        f = base.fitted
        expected = max(f["a"] + f["b"] * (row["temp"] - f["t0"]), 1e-6)
        gap = row["per_hour"] / expected - 1.0
        z = gap / f["sd"]
        gaps = [*self.ac_gaps[circuit], (end_us, gap)][-14:]
        self.ac_gaps[circuit] = gaps
        trend = None
        if len(gaps) >= 5:
            weeks = np.array([g[0] for g in gaps], dtype=np.float64) / (7 * DAY_US)
            trend = float(np.polyfit(weeks - weeks[-1], [g[1] for g in gaps], 1)[0])
        appliance = self.aircons[circuit]
        evidence = [
            {
                "name": "energy_per_running_hour",
                "value": round(row["per_hour"], 3),
                "unit": "kWh/h",
            },
            {
                "name": "expected_energy_per_running_hour",
                "value": round(expected, 3),
                "unit": "kWh/h",
            },
            {
                "name": "outdoor_temperature_while_running",
                "value": round(row["temp"], 1),
                "unit": "degC",
            },
            {"name": "running_hours", "value": round(hours, 1), "unit": "h"},
        ]
        quantities: dict[str, dict[str, Any]] = {
            "efficiency_gap_pct": {"value": round(100 * gap, 1), "unit": "%"},
            "extra_energy_kwh_per_day": {
                "value": round(gap * expected * hours, 3),
                "unit": "kWh/day",
            },
        }
        if trend is not None:
            quantities["efficiency_gap_trend_pct_per_week"] = {
                "value": round(100 * trend, 2),
                "unit": "%/week",
            }
        self._cusum_finding(
            f"L3:aircon_efficiency_decline:{appliance}",
            "aircon_efficiency_decline",
            z,
            at_us=end_us,
            objects=[appliance],
            evidence=evidence,
            quantities=quantities,
        )

    def _fit_ac(self, rows: list[dict[str, float]]) -> dict[str, Any]:
        temp = np.array([r["temp"] for r in rows])
        per_hour = np.array([r["per_hour"] for r in rows])
        t0 = float(temp.mean())
        if temp.std() > 0.3:
            b, a = np.polyfit(temp - t0, per_hour, 1)
        else:
            a, b = float(per_hour.mean()), 0.0
        ratio = per_hour / np.maximum(a + b * (temp - t0), 1e-6) - 1.0
        sd = max(float(ratio.std(ddof=1)), self.p.min_sd_fraction)
        return {"a": float(a), "b": float(b), "t0": t0, "sd": sd}

    # ------------------------------------------------------------------ findings

    def _cusum_finding(
        self,
        key: str,
        kind: str,
        z: float,
        *,
        at_us: int,
        objects: list[str],
        evidence: list[dict[str, Any]],
        quantities: dict[str, dict[str, Any]],
    ) -> None:
        if not np.isfinite(z):
            return
        cusum = self.cusums.setdefault(key, _Cusum())
        s = cusum.step(z, self.p.cusum_k)
        if s > self.p.cusum_h:
            finding = self.tracker.observe(
                Observation(
                    key=key,
                    type=kind,
                    severity="heads_up",
                    confidence=1.0,
                    at_us=at_us,
                    object_ids=objects,
                    evidence=[*evidence, {"name": "cusum", "value": round(s, 2)}],
                    quantities=quantities,
                )
            )
        elif self.tracker.is_open(key) and cusum.quiet >= self.p.clear_days:
            finding = self.tracker.clear(key, at_us)
        else:
            finding = None
        if finding is not None:
            self.out.append(finding)
