"""Fault injection (PRD §12.2): catalogue, validation and per-second effects.

A fault never touches the household's random streams: its own randomness (refill phase,
spike times, overload windows) comes from rng(seed, 'fault', fault_id, ...). Every effect is
zero before the fault's start, so regenerating a day after an injection leaves everything
already produced unchanged. A fault with magnitude 0 has no effect at all (null-fault canary).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import numpy as np
import numpy.typing as npt

from penjaga_contract import schema_registry
from penjaga_contract.core import (
    ApplianceKind,
    CircuitKind,
    FixtureKind,
    PipeKind,
    PipeRouting,
    ValveKind,
)
from penjaga_contract.house import ElementKind
from penjaga_contract.lab import FaultSpec, FaultType, ProgressionKind
from penjaga_sim.household import N
from penjaga_sim.model import HouseModel
from penjaga_sim.rng import rng

Arr = npt.NDArray[np.float64]
Mask = npt.NDArray[np.bool_]
US = 1_000_000

LAYER = {
    **{t: "L1" for t in ("W1", "W2", "W3", "W4", "W5")},
    **{t: "L2" for t in ("E1", "E2", "E3")},
    **{t: "L3" for t in ("A1", "A2", "A3", "A4")},
    **{t: "L5" for t in ("U1", "U2", "U3")},
}
UNIT = {
    "W1": "L/min", "W2": "L/min", "W3": "L/min", "W4": "L/min", "W5": "L/min",
    "E1": "A", "E2": "mA", "E3": "1/day",
    "A1": "%/week", "A2": "%", "A3": "1", "A4": "%",
    "U1": "1", "U2": "1", "U3": "1",
}  # fmt: skip
MAGNITUDE_PARAM = {
    "W1": "faults.W1.flow_lpm",
    "W2": "faults.W2.leak_lpm",
    "W3": "faults.W3.overflow_lpm",
    "W4": "faults.W4.leak_lpm",
    "W5": "faults.W5.flow_lpm",
    "E1": "faults.E1.extra_a",
    "E2": "faults.E2.start_ma",
    "E3": "faults.E3.trips_per_day",
    "A1": "faults.A1.loss_pct_per_week",
    "A2": "faults.A2.capacity_loss_pct",
    "A3": "faults.A3.duty_increase",
    "A4": "faults.A4.power_increase_pct",
}
TAPS = frozenset({
    FixtureKind.basin_tap, FixtureKind.sink_tap, FixtureKind.laundry_tap, FixtureKind.garden_tap,
    FixtureKind.shower, FixtureKind.washing_machine_tap, FixtureKind.bidet_spray,
})  # fmt: skip


class FaultError(ValueError):
    pass


def valid_locations(model: HouseModel, fault_type: str) -> list[str]:
    """Contract elements where a fault of this type can occur (empty for supply-level faults)."""
    house = model.index.house
    water, elec = house.water, house.electrical
    locate: dict[str, Callable[[], list[str]]] = {
        "W1": lambda: [f.id for f in water.fixtures if f.kind in TAPS],
        "W2": lambda: [f.id for f in water.fixtures if f.kind is FixtureKind.wc_cistern],
        "W3": lambda: [v.id for v in water.valves if v.kind is ValveKind.float],
        "W4": lambda: [p.id for p in water.pipes if p.routing is PipeRouting.concealed],
        "W5": lambda: [p.id for p in water.pipes if p.kind is not PipeKind.overflow],
        "E1": lambda: [c.id for c in elec.circuits if c.kind is CircuitKind.socket],
        "E2": lambda: [c.id for c in elec.circuits if model.index.rccb_chain(c.id)],
        "A1": lambda: model.appliances_of(ApplianceKind.air_conditioner),
        "A3": lambda: model.appliances_of(ApplianceKind.refrigerator),
    }
    locate["E3"], locate["A2"], locate["A4"] = locate["E2"], locate["A1"], locate["A3"]
    return locate[fault_type]() if fault_type in locate else []


def catalogue(model: HouseModel) -> list[dict[str, Any]]:
    """What the Lab may inject: GET /sim/v1/faults/catalogue."""
    out = []
    for ft in FaultType:
        t = ft.value
        entry: dict[str, Any] = {
            "fault_type": t,
            "layer": LAYER[t],
            "magnitude_unit": UNIT[t],
            "locations": valid_locations(model, t),
            "requires_duration": t.startswith("U"),
        }
        if t in MAGNITUDE_PARAM:
            lo, hi = model.cal[MAGNITUDE_PARAM[t]].bounds()
            entry["magnitude_range"] = [lo, hi]
        else:
            entry["magnitude_range"] = [0, 1]
        out.append(entry)
    return out


def spec_document(spec: FaultSpec) -> dict[str, Any]:
    """The spec as a contract JSON document: optional fields left out when unset, but the
    required, nullable ``location_id`` kept (null for supply-level faults)."""
    return {**spec.model_dump(mode="json", exclude_none=True), "location_id": spec.location_id}


def random_spec(
    model: HouseModel, fault_type: str, start: datetime, r: np.random.Generator
) -> FaultSpec:
    """A plausible fault of this type ("surprise me" in the Lab, sampled evaluation episodes).

    The location is drawn uniformly from the valid ones and the magnitude from the calibrated
    range; concealed leaks may worsen and earth leakage always rises; supply faults get a
    calibrated duration. Everything comes from ``r``, so a seeded draw is reproducible.
    """
    cal, t = model.cal, fault_type
    location = None if t.startswith("U") else str(r.choice(valid_locations(model, t)))
    if t in MAGNITUDE_PARAM:
        param = cal[MAGNITUDE_PARAM[t]]
        lo, hi = param.bounds()
        magnitude = min(hi, max(lo, round(float(param.sample(r)), 4)))
    else:
        magnitude = 1.0
    progression: dict[str, Any] = {"kind": "constant"}
    if t == "E2" or (t == "W4" and r.random() < cal.value("faults.W4.worsening_prob")):
        growth = round(float(cal[f"faults.{t}.growth_per_day"].sample(r)), 4)
        progression = {"kind": "worsening", "growth_per_day": growth}
    doc: dict[str, Any] = {
        "fault_type": t,
        "location_id": location,
        "magnitude": {"value": magnitude, "unit": UNIT[t]},
        "progression": progression,
        "start_at": start.isoformat(),
    }
    if t.startswith("U"):
        doc["duration_s"] = round(3600 * float(cal[f"faults.{t}.hours"].sample(r)))
    spec = FaultSpec.model_validate(doc)
    validate_spec(model, spec)
    return spec


def validate_spec(model: HouseModel, spec: FaultSpec) -> None:
    """Full contract validation (including the schema's conditional rules, which generated models
    do not enforce) plus the simulator's own rules: location, unit and magnitude range."""
    doc = spec_document(spec)
    problems = schema_registry.error_messages("lab", "fault_spec", doc)
    if problems:
        raise FaultError("; ".join(problems))
    t = spec.fault_type.value
    locations = valid_locations(model, t)
    if t.startswith("U"):
        if spec.location_id is not None:
            raise FaultError(f"{t} is supply-level: location_id must be null")
        if spec.duration_s is None:
            raise FaultError(f"{t} needs duration_s")
    elif spec.location_id not in locations:
        raise FaultError(
            f"{spec.location_id!r} is not a valid location for {t}; valid: {locations}"
        )
    if spec.magnitude.unit != UNIT[t]:
        raise FaultError(f"{t} magnitude unit must be {UNIT[t]!r}")
    lo, hi = (0.0, 1.0) if t not in MAGNITUDE_PARAM else model.cal[MAGNITUDE_PARAM[t]].bounds()
    value = spec.magnitude.value
    if value != 0 and not lo <= value <= hi:
        raise FaultError(
            f"{t} magnitude {value} outside [{lo}, {hi}] {UNIT[t]} (0 is allowed: no effect)"
        )
    if t.startswith("U") and value not in (0, 1):
        raise FaultError(f"{t} magnitude is 1 (fault) or 0 (no effect)")


@dataclass(frozen=True)
class ActiveFault:
    fault_id: str
    spec: FaultSpec
    start_us: int
    end_us: int | None  # exclusive

    @property
    def type(self) -> str:
        return self.spec.fault_type.value

    @property
    def magnitude(self) -> float:
        return float(self.spec.magnitude.value)


@dataclass
class DayEffects:
    tank_leak_lps: Arr = field(default_factory=lambda: np.zeros(N))
    main_leak_lps: Arr = field(default_factory=lambda: np.zeros(N))
    stuck_float_lps: Arr = field(default_factory=lambda: np.zeros(N))
    water_supply_on: Mask = field(default_factory=lambda: np.ones(N, np.bool_))
    power_on: Mask = field(default_factory=lambda: np.ones(N, np.bool_))
    extra_load_w: dict[str, Arr] = field(default_factory=dict)
    extra_leak_ma: dict[str, Arr] = field(default_factory=dict)
    ac_cop_factor: dict[str, Arr] = field(default_factory=dict)
    ac_capacity_factor: dict[str, Arr] = field(default_factory=dict)
    fridge_duty_add: Arr = field(default_factory=lambda: np.zeros(N))
    fridge_power_factor: Arr = field(default_factory=lambda: np.ones(N))
    fridge_on_minutes: Arr = field(default_factory=lambda: np.full(N, np.nan))
    # each water leak's own flow (location, drawn through the tank?, L/s), already included in
    # tank_leak_lps or main_leak_lps; a closed valve uses it to stop the leaks it isolates
    water_leaks: list[tuple[str, bool, Arr]] = field(default_factory=list)


def _window(fault: ActiveFault, day_start_us: int) -> Mask:
    """Seconds of this day during which the fault is in force."""
    t = day_start_us + np.arange(N, dtype=np.int64) * US
    mask = t >= fault.start_us
    if fault.end_us is not None:
        mask &= t < fault.end_us
    return mask


def _progression(fault: ActiveFault, day_start_us: int, model: HouseModel) -> Arr:
    """Magnitude multiplier per second (0 outside the fault window)."""
    window = _window(fault, day_start_us)
    prog = fault.spec.progression
    t = day_start_us + np.arange(N, dtype=np.int64) * US
    days = np.maximum(0, t - fault.start_us) / (86_400 * US)
    factor = np.ones(N)
    if prog.kind is ProgressionKind.worsening:
        assert prog.growth_per_day is not None  # validate_spec enforces it
        factor = (1.0 + float(prog.growth_per_day)) ** days
    elif prog.kind is ProgressionKind.intermittent:
        # validate_spec enforces both for intermittent faults
        assert prog.cycle_s is not None
        assert prog.on_fraction is not None
        cycle = float(prog.cycle_s)
        phase = rng(model.seed, "fault", fault.fault_id, "phase").uniform(0, cycle)
        elapsed = (t - fault.start_us) / US + phase
        factor = ((elapsed % cycle) < prog.on_fraction * cycle).astype(np.float64)
    return np.where(window, factor, 0.0)


def _water_target(model: HouseModel, location: str) -> bool:
    """True if water lost at ``location`` is drawn through the roof tank."""
    kind = model.index.kind_of(location)
    if kind is ElementKind.fixture:
        return model.is_tank_fed(location)
    path = model.index.upstream_path(location)
    return any(model.index.kind_of(n) is ElementKind.tank for n in path[1:])


@dataclass
class _FaultDay:
    """One fault on one day: what a handler needs to add its effects."""

    model: HouseModel
    fault: ActiveFault
    day_start_us: int
    prog: Arr  # magnitude multiplier per second, 0 outside the fault window
    r: np.random.Generator  # this fault's randomness for this day
    rh_pct: Arr
    eff: DayEffects
    info: dict[str, Any]

    @property
    def loc(self) -> str:
        loc = self.fault.spec.location_id
        if loc is None:
            raise FaultError(f"{self.fault.type} needs a location")
        return loc

    @property
    def mag(self) -> float:
        return self.fault.magnitude

    def param(self, name: str) -> float:
        return self.model.cal.value(f"faults.{self.fault.type}.{name}")

    def sample(self, name: str, r: np.random.Generator | None = None) -> float:
        return float(self.model.cal[f"faults.{self.fault.type}.{name}"].sample(r or self.r))


def _add_leak(c: _FaultDay, tank_side: bool, lps: Arr) -> None:
    target = c.eff.tank_leak_lps if tank_side else c.eff.main_leak_lps
    target += lps
    c.eff.water_leaks.append((c.loc, tank_side, lps))


def _leak(c: _FaultDay) -> None:  # W1, W4, W5: continuous flow out of the pipework
    _add_leak(c, _water_target(c.model, c.loc), c.prog * c.mag / 60.0)


def _cistern(c: _FaultDay) -> None:  # W2: a trickle, or periodic phantom refills
    m, f = c.model, c.fault
    tank_side = m.is_tank_fed(c.loc)
    fr = rng(m.seed, "fault", f.fault_id, "kind")  # fixed for the fault's life, not per day
    if fr.random() < c.param("continuous_prob"):
        _add_leak(c, tank_side, c.prog * c.mag / 60.0)
        c.info["pattern"] = "continuous"
        return
    drop = c.sample("refill_drop_l", fr)
    fill_lps = m.draws.house("water.fixtures.wc_fill_lpm") / 60.0
    period = drop / (c.mag / 60.0)
    dur = max(1, round(drop / fill_lps))
    phase = fr.uniform(0, period)
    t_since = (np.arange(N) + (c.day_start_us - f.start_us) / US + phase) % period
    refills = ((t_since < dur) & (c.prog > 0)) * fill_lps
    _add_leak(c, tank_side, np.asarray(refills, dtype=np.float64))
    c.info.update(
        pattern="periodic_refill", refill_period_s=round(period, 1), refill_litres=round(drop, 2)
    )


def _float_valve(c: _FaultDay) -> None:  # W3
    c.eff.stuck_float_lps[:] = np.maximum(c.eff.stuck_float_lps, c.prog * c.mag / 60.0)


def _water_cut(c: _FaultDay) -> None:  # U1, U2
    c.eff.water_supply_on &= c.prog <= 0


def _power_cut(c: _FaultDay) -> None:  # U3
    c.eff.power_on &= c.prog <= 0


def _overload(c: _FaultDay) -> None:  # E1: extra load for some hours each evening
    hours = c.sample("hours_per_day")
    opens = c.param("window_open_h") * 3600
    start = int(c.r.uniform(opens, max(opens + 1, N - hours * 3600)))
    window = np.zeros(N)
    window[start : start + int(hours * 3600)] = 1.0
    voltage = c.model.draws.house("electrical.supply.voltage_mean")
    c.eff.extra_load_w.setdefault(c.loc, np.zeros(N))[:] += c.prog * window * c.mag * voltage
    c.info["window_start_s"] = start


def _earth_leak(c: _FaultDay) -> None:  # E2: worse when damp, on outdoor circuits
    leak = c.prog * c.mag
    if c.loc in c.model.outdoor_circuits:
        wet = np.clip((c.rh_pct - c.param("wet_onset_rh")) / c.param("wet_span_rh"), 0, 1)
        leak = leak * (1 + (c.sample("rain_multiplier") - 1) * wet)
    c.eff.extra_leak_ma.setdefault(c.loc, np.zeros(N))[:] += leak


def _leak_spikes(c: _FaultDay) -> None:  # E3: random spikes above the nearest RCCB's threshold
    m = c.model
    device = m.index.rccb_chain(c.loc)[0]
    threshold = m.draws.house("electrical.rcd.trip_fraction", device) * m.residual_rating_ma(device)
    length = int(c.param("spike_s"))
    spikes = np.zeros(N)
    for s in c.r.uniform(0, N, c.r.poisson(c.mag)).astype(int):
        if c.prog[s] > 0:
            spikes[s : s + length] = threshold + c.sample("spike_ma_over_threshold")
    c.eff.extra_leak_ma.setdefault(c.loc, np.zeros(N))[:] += spikes


def _filter_clog(c: _FaultDay) -> None:  # A1: COP falls steadily with weeks since onset
    weeks = np.maximum(
        0.0, (c.day_start_us + np.arange(N) * US - c.fault.start_us) / (7 * 86_400 * US)
    )
    factor = np.where(c.prog > 0, 1.0 / (1.0 + c.mag / 100.0 * weeks * c.prog), 1.0)
    cop = c.eff.ac_cop_factor
    cop[c.loc] = cop.get(c.loc, np.ones(N)) * factor


def _low_refrigerant(c: _FaultDay) -> None:  # A2
    loss = c.prog * c.mag / 100.0
    cap, cop = c.eff.ac_capacity_factor, c.eff.ac_cop_factor
    cap[c.loc] = cap.get(c.loc, np.ones(N)) * (1 - loss)
    cop[c.loc] = cop.get(c.loc, np.ones(N)) * (1 - c.param("cop_loss_ratio") * loss)


def _door_seal(c: _FaultDay) -> None:  # A3
    c.eff.fridge_duty_add += c.prog * c.mag


def _compressor_wear(c: _FaultDay) -> None:  # A4: more power per run, short-cycling
    c.eff.fridge_power_factor *= 1 + c.prog * c.mag / 100.0
    cycle_rng = rng(c.model.seed, "fault", c.fault.fault_id, "cycle")
    on_minutes = c.sample("short_cycle_on_minutes", cycle_rng)
    c.eff.fridge_on_minutes[:] = np.where(c.prog > 0, on_minutes, c.eff.fridge_on_minutes)
    c.info["short_cycle_on_minutes"] = round(on_minutes, 2)


HANDLERS: dict[str, Callable[[_FaultDay], None]] = {
    "W1": _leak, "W2": _cistern, "W3": _float_valve, "W4": _leak, "W5": _leak,
    "E1": _overload, "E2": _earth_leak, "E3": _leak_spikes,
    "A1": _filter_clog, "A2": _low_refrigerant, "A3": _door_seal, "A4": _compressor_wear,
    "U1": _water_cut, "U2": _water_cut, "U3": _power_cut,
}  # fmt: skip


def apply_faults(
    model: HouseModel, day: date, day_start_us: int, faults: list[ActiveFault], rh_pct: Arr
) -> tuple[DayEffects, dict[str, dict[str, Any]]]:
    """Per-second effects of the active faults on this day, plus realised parameters per fault."""
    eff = DayEffects()
    realised: dict[str, dict[str, Any]] = {}
    for f in faults:
        prog = _progression(f, day_start_us, model)
        if not prog.any() or f.magnitude == 0:
            continue
        r = rng(model.seed, "fault", f.fault_id, "day", day.toordinal())
        info: dict[str, Any] = {"magnitude": f.magnitude, "unit": UNIT[f.type]}
        HANDLERS[f.type](_FaultDay(model, f, day_start_us, prog, r, rh_pct, eff, info))
        realised[f.fault_id] = info
    return eff, realised


def object_ids(model: HouseModel, fault: ActiveFault) -> list[str]:
    """True location(s) for ground truth."""
    if fault.spec.location_id is not None:
        return [fault.spec.location_id]
    if fault.type in ("U1", "U2"):
        return [model.index.meter_id]
    return [b.id for b in model.index.house.electrical.boards]
