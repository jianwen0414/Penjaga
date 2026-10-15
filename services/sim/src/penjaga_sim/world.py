"""The simulated world: one house, one household, faults and anomalies, generated day by day.

Days are generated in order (each day starts from the previous day's end state) and cached.
Injecting or clearing a fault invalidates the affected day and everything after it; because
fault effects are zero before the fault starts and every random stream is keyed by date and
component, regenerating a day reproduces everything before the injection exactly. The world
refuses changes that would alter time already published: readings and events stamped at or
before ``published_until_us`` have been sent (windows are half-open, (t0, t1]), so a change
must take effect strictly after it.

Ground truth (fault records, legitimate-anomaly occurrences) is kept here and handed only to
the truth store; it never appears in the readings.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from penjaga_contract.core import StreamDef
from penjaga_contract.house import HouseIndex
from penjaga_contract.lab import AnomalySpec, AnomalyType, FaultSpec
from penjaga_contract.validate import load_context
from penjaga_sim.calibration import Calibration, load_calibration
from penjaga_sim.electrical import ElecInputs, ElecState, simulate_electrical
from penjaga_sim.faults import (
    ActiveFault,
    FaultError,
    apply_faults,
    object_ids,
    spec_document,
    validate_spec,
)
from penjaga_sim.household import DayModifiers, N, NaturalAnomaly, plan_day
from penjaga_sim.model import HouseModel
from penjaga_sim.sensors import DayPhysics, Registers, SensorKit, StreamDay
from penjaga_sim.timeutil import to_us
from penjaga_sim.water import WaterInputs, WaterState, demand_from_uses, simulate_water
from penjaga_sim.weather import day_weather

US = 1_000_000
DAY_US = 86_400 * US
IMPOSABLE_ANOMALIES = {
    AnomalyType.guests,
    AnomalyType.holiday_absence,
    AnomalyType.festive_cooking,
    AnomalyType.aircon_setpoint_change,
    AnomalyType.occupancy_change,
    AnomalyType.heatwave,
    AnomalyType.night_bucket_fill,
    AnomalyType.washer_delay_timer,
}
# Imposing these forces an occurrence on each affected day; the occurrence (with its exact time)
# is the ground truth, so no whole-day record is written for the request itself.
OCCURRENCE_ANOMALIES = {AnomalyType.night_bucket_fill, AnomalyType.washer_delay_timer}


class WorldError(ValueError):
    """A request the world cannot accept as given (invalid spec, unknown archetype...)."""


class WorldConflictError(WorldError):
    """A valid request that conflicts with the world's state (published time, ended fault)."""


class UnknownFaultError(WorldError):
    """No fault with that id."""


@dataclass(frozen=True)
class WorldConfig:
    seed: int
    archetype: str
    start: date  # first simulated house day
    enabled_sensors: frozenset[str] | None = None  # None: the full kit


@dataclass
class _DayState:
    water: WaterState
    elec: ElecState
    registers: Registers


@dataclass(frozen=True)
class SimEvent:
    ts_us: int
    stream: str
    payload: dict[str, str]


@dataclass(frozen=True)
class DayOutput:
    day: date
    start_us: int
    streams: dict[str, StreamDay]
    events: list[SimEvent]
    natural: list[NaturalAnomaly]
    summary: dict[str, float]


@dataclass
class _Anomaly:
    spec: AnomalySpec
    start_us: int
    end_us: int
    record_id: str


@dataclass
class World:
    config: WorldConfig
    index: HouseIndex | None = None
    calibration: Calibration | None = None
    faults: list[ActiveFault] = field(default_factory=list)
    published_until_us: int = 0
    _anomalies: list[_Anomaly] = field(default_factory=list)
    _states: dict[date, _DayState] = field(default_factory=dict)
    _outputs: dict[date, DayOutput] = field(default_factory=dict)
    _natural: dict[date, list[tuple[dict[str, Any], int]]] = field(default_factory=dict)
    _fault_params: dict[str, dict[str, Any]] = field(default_factory=dict)
    _counter: int = 0

    def __post_init__(self) -> None:
        ctx = load_context()
        self.index = self.index or ctx.index()
        self.calibration = self.calibration or load_calibration()
        archetypes = self.calibration.table("household.archetypes")
        if self.config.archetype not in archetypes:
            raise WorldError(
                f"unknown archetype {self.config.archetype!r}; known: {sorted(archetypes)}"
            )
        self.model = HouseModel(self.index, self.calibration, self.config.seed)
        self.zone = ZoneInfo(self.index.house.location.timezone)
        streams: list[StreamDef] = list(ctx.catalogue.streams)
        sensor_ids = {s.id for s in self.index.house.sensors}
        if (
            self.config.enabled_sensors is not None
            and not self.config.enabled_sensors <= sensor_ids
        ):
            raise WorldError(f"unknown sensors {sorted(self.config.enabled_sensors - sensor_ids)}")
        self.kit = SensorKit(self.model, streams, self.config.enabled_sensors)
        tank = self.index.house.water.tanks[0]
        start_level = self.model.draws.house("water.tank.start_level")
        self._states[self.config.start] = _DayState(
            WaterState(tank_l=start_level * float(tank.capacity_l)), ElecState(), Registers()
        )
        # nothing published yet: even the first instant of the first day may still change
        self.published_until_us = self.day_start_us(self.config.start) - 1

    # ------------------------------------------------------------------ time

    def day_start_us(self, day: date) -> int:
        start = datetime.combine(day, time(0), tzinfo=self.zone)
        return int(start.timestamp()) * US

    def day_of(self, ts_us: int) -> date:
        return datetime.fromtimestamp(ts_us / US, tz=self.zone).date()

    @property
    def streams(self) -> list[StreamDef]:
        return self.kit.streams

    @property
    def event_streams(self) -> list[StreamDef]:
        return self.kit.event_streams

    # ------------------------------------------------------------------ faults

    def _new_id(self, prefix: str) -> str:
        self._counter += 1
        digest = hashlib.blake2b(
            f"{self.config.seed}:{prefix}:{self._counter}".encode(), digest_size=6
        )
        return f"{prefix}_{digest.hexdigest()}"

    def _invalidate_from(self, ts_us: int) -> None:
        first = self.day_of(ts_us)
        for d in [d for d in self._outputs if d >= first]:
            del self._outputs[d]
        for d in [d for d in self._natural if d >= first]:
            del self._natural[d]
        for d in [d for d in self._states if d > first]:
            del self._states[d]

    def _check_not_published(self, ts_us: int, what: str) -> None:
        if ts_us <= self.published_until_us:
            raise WorldConflictError(
                f"{what} at {ts_us} is not after already-published house time "
                f"{self.published_until_us}"
            )

    def inject(self, spec: FaultSpec, now_us: int | None = None) -> ActiveFault:
        """Add a fault starting at spec.start_at (or now_us). Returns it with its assigned id."""
        try:
            validate_spec(self.model, spec)
        except FaultError as exc:
            raise WorldError(str(exc)) from None
        if spec.start_at is not None:
            start = to_us(spec.start_at)
        elif now_us is not None:
            start = now_us
        else:
            raise WorldError("a fault needs start_at or the current house time")
        if start < self.day_start_us(self.config.start):
            raise WorldError("fault starts before the episode")
        self._check_not_published(start, "fault start")
        end = None if spec.duration_s is None else start + int(spec.duration_s * US)
        fault_id = self._new_id("flt")
        fault = ActiveFault(fault_id, spec.model_copy(update={"fault_id": fault_id}), start, end)
        self.faults.append(fault)
        self._invalidate_from(start)
        return fault

    def clear(self, fault_id: str, now_us: int) -> ActiveFault:
        for i, f in enumerate(self.faults):
            if f.fault_id == fault_id:
                if f.end_us is not None and (f.end_us <= now_us or f.end_us <= f.start_us):
                    raise WorldConflictError(f"{fault_id} already ended or was cancelled")
                self._check_not_published(now_us, "fault clear")
                cleared = ActiveFault(f.fault_id, f.spec, f.start_us, max(f.start_us, now_us))
                self.faults[i] = cleared
                self._invalidate_from(now_us)
                return cleared
        raise UnknownFaultError(f"unknown fault {fault_id!r}")

    def impose(self, spec: AnomalySpec) -> str:
        """Impose a legitimate anomaly on whole house days.

        The behaviour change applies to every day the requested interval touches, so the truth
        record spans those whole days: that is when the house actually behaves differently.
        """
        if spec.anomaly_type not in IMPOSABLE_ANOMALIES:
            raise WorldError(f"{spec.anomaly_type.value} occurs naturally and cannot be imposed")
        start_day = spec.start_at.astimezone(self.zone).date()
        if start_day < self.config.start:
            raise WorldError("anomaly starts before the episode")
        start = self.day_start_us(start_day)
        self._check_not_published(start, "anomaly start (whole days)")
        requested_end = to_us(spec.start_at) + int(spec.duration_s * US)
        end = self.day_start_us(self.day_of(requested_end - 1) + timedelta(days=1))
        record = self._new_id("tr")
        self._anomalies.append(_Anomaly(spec, start, end, record))
        self._invalidate_from(start)
        return record

    # ------------------------------------------------------------------ generation

    def _modifiers(self, day: date) -> tuple[DayModifiers, np.ndarray]:
        start, end = self.day_start_us(day), self.day_start_us(day) + DAY_US
        mods: dict[str, Any] = {}
        heat = np.zeros(N)
        for a in self._anomalies:
            if a.start_us >= end or a.end_us <= start:
                continue
            p = a.spec.params or {}
            match a.spec.anomaly_type:
                case AnomalyType.guests:
                    mods["guests"] = int(p.get("count", 2))
                case AnomalyType.holiday_absence:
                    mods["holiday_absence"] = True
                case AnomalyType.festive_cooking:
                    mods["festive_cooking"] = True
                case AnomalyType.aircon_setpoint_change:
                    mods["setpoint_offset_c"] = float(p.get("offset_c", -1.5))
                case AnomalyType.occupancy_change:
                    mods["extra_occupant"] = True
                case AnomalyType.night_bucket_fill:
                    mods["force_night_bucket"] = True
                case AnomalyType.washer_delay_timer:
                    mods["force_washer_delay"] = True
                case AnomalyType.heatwave:
                    heat += float(p.get("extra_c", 3.0))
        return DayModifiers(**mods), heat

    def _state(self, day: date) -> _DayState:
        if day < self.config.start:
            raise WorldError(f"{day} is before the episode start {self.config.start}")
        if day not in self._states:
            known = max(d for d in self._states if d <= day)
            cursor = known
            while cursor < day:  # iterative, so long gaps cannot exhaust the stack
                self.output(cursor)
                cursor += timedelta(days=1)
        return self._states[day]

    def output(self, day: date) -> DayOutput:
        """The generated day (cached)."""
        if day in self._outputs:
            return self._outputs[day]
        state = self._state(day)
        start_us = self.day_start_us(day)
        model = self.model
        mods, heat = self._modifiers(day)
        weather = day_weather(day)
        if heat.any():
            weather = weather.shifted(heat)
        plan = plan_day(model, self.config.archetype, day, mods)
        effects, realised = apply_faults(model, day, start_us, self.faults, weather.rh_pct)
        for fid, info in realised.items():
            self._fault_params.setdefault(fid, info)
        tank_dem, main_dem = demand_from_uses(
            model, [(u.fixture, u.start, u.dur, u.lpm) for u in plan.water]
        )
        water_in = WaterInputs(
            tank_dem + effects.tank_leak_lps,
            main_dem + effects.main_leak_lps,
            effects.water_supply_on,
            effects.stuck_float_lps,
        )
        water, water_end = simulate_water(model, day.toordinal(), water_in, state.water)
        elec, elec_end = simulate_electrical(
            model, day, ElecInputs(plan, weather, effects), state.elec
        )
        streams, regs = self.kit.render(
            day.toordinal(), start_us, DayPhysics(water, elec, weather), state.registers
        )
        enabled_events = {s.id for s in self.kit.event_streams}
        events = [
            SimEvent(start_us + e.second * US, e.stream, _payload(e.stream, e.payload))
            for e in elec.events
            if e.stream in enabled_events
        ]
        natural = list(plan.natural) + _post_outage_refills(
            effects.water_supply_on, water.tank_fraction
        )
        summary = {
            "water_true_l": float(water.meter_true_lps.sum()),
            "water_registered_l": float(water.meter_registered_lps.sum()),
            "overflow_l": float(water.overflow_lps.sum()),
            "energy_kwh": float(elec.main_power_w.sum() / 3.6e6),
            "occupants": float(len(plan.occupants)),
        }
        for app, p in elec.appliance_power_w.items():
            summary[f"kwh.{app}"] = float(p.sum() / 3.6e6)
        out = DayOutput(day, start_us, streams, events, natural, summary)
        self._outputs[day] = out
        self._natural[day] = [self._natural_record(day, start_us, n) for n in natural]
        self._states[day + timedelta(days=1)] = _DayState(water_end, elec_end, regs)
        return out

    def forget_before(self, day: date) -> None:
        """Drop cached outputs before ``day`` (bounds memory; natural-anomaly truth is kept)."""
        for d in [d for d in self._outputs if d < day]:
            del self._outputs[d]

    # ------------------------------------------------------------------ truth

    def truth_records(self, until_us: int | None = None) -> list[dict[str, Any]]:
        """Ground truth for the truth store (never for detection)."""
        house_id = self.index.house_id if self.index else ""
        records: list[dict[str, Any]] = []
        for f in self.faults:
            if until_us is not None and f.start_us >= until_us:
                continue
            if f.end_us is not None and f.end_us <= f.start_us:
                continue  # cleared before it started: it never happened
            records.append({
                "record_id": "tr_" + f.fault_id.removeprefix("flt_"),
                "house_id": house_id,
                "kind": "fault",
                "label": f.type,
                "fault_id": f.fault_id,
                "object_ids": object_ids(self.model, f),
                "start": self._iso(f.start_us),
                "end": None if f.end_us is None else self._iso(f.end_us),
                "params": {"spec": _spec_doc(f.spec), **self._fault_params.get(f.fault_id, {})},
            })  # fmt: skip
        for a in self._anomalies:
            if a.spec.anomaly_type in OCCURRENCE_ANOMALIES:
                continue
            if until_us is not None and a.start_us >= until_us:
                continue
            records.append({
                "record_id": a.record_id, "house_id": house_id, "kind": "legit_anomaly",
                "label": a.spec.anomaly_type.value, "object_ids": [],
                "start": self._iso(a.start_us), "end": self._iso(a.end_us),
                "params": {"spec": _spec_doc(a.spec), "imposed": True},
            })  # fmt: skip
        for _, natural in sorted(self._natural.items()):
            for record, start in natural:
                if until_us is None or start < until_us:
                    records.append(record)
        return records

    def _natural_record(
        self, day: date, day_start_us: int, n: NaturalAnomaly
    ) -> tuple[dict[str, Any], int]:
        start = day_start_us + n.start * US
        digest = hashlib.blake2b(f"{day}:{n.anomaly_type}:{n.start}".encode(), digest_size=6)
        imposed_by = [
            a.record_id
            for a in self._anomalies
            if a.spec.anomaly_type.value == n.anomaly_type
            and a.start_us < day_start_us + DAY_US
            and a.end_us > day_start_us
        ]
        origin = (
            {"natural": False, "imposed_by": imposed_by[0]} if imposed_by else {"natural": True}
        )
        record = {
            "record_id": f"tr_{digest.hexdigest()}", "house_id": self.model.index.house_id,
            "kind": "legit_anomaly", "label": n.anomaly_type, "object_ids": [],
            "start": self._iso(start), "end": self._iso(start + n.dur * US),
            "params": {**n.params, **origin},
        }  # fmt: skip
        return record, start

    def _iso(self, ts_us: int) -> str:
        return datetime.fromtimestamp(ts_us / US, tz=self.zone).isoformat()


def _spec_doc(spec: FaultSpec | AnomalySpec) -> dict[str, Any]:
    if isinstance(spec, FaultSpec):
        return spec_document(spec)
    return spec.model_dump(mode="json", exclude_none=True)


def _payload(stream: str, payload: dict[str, str]) -> dict[str, str]:
    if stream == "elec.protection.trip":
        return {"type": "protection_device_state", **payload}
    return {"type": "supply_state", **payload}


def _post_outage_refills(supply_on: np.ndarray, tank_fraction: np.ndarray) -> list[NaturalAnomaly]:
    """After a water supply interruption ends, the tank refills at high flow (extends PRD §12.3)."""
    out = []
    for restore in np.flatnonzero(np.diff(supply_on.astype(np.int8)) == 1) + 1:
        full = np.flatnonzero(tank_fraction[restore:] >= 0.98)
        end = int(restore + full[0]) if full.size else N
        if end > restore:
            out.append(NaturalAnomaly("post_outage_tank_refill", int(restore), end - int(restore)))
    return out
