"""Water physics for one day at 1 s resolution: supply pressure, roof tank, meter.

* Supply pressure: per-house mean, dips at the morning and evening demand peaks, AR(1) noise,
  interruptions (pressure falls to a residual, then ramps back), and a friction drop across
  the service pipe proportional to the square of the metered flow.
* Roof tank: the float valve admits q = k * sqrt(p - static_head) when fully open; it opens
  proportionally as the level falls within the float band below full, closes at full. A stuck
  float valve (W3) keeps admitting water; the excess leaves by the overflow pipe.
* Meter (OIML R49 class 2, DS-007): registered volume = true volume x (1 + error(flow)),
  nothing below the cutoff, larger under-registration between the cutoff and Q1.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from penjaga_sim.model import HouseModel
from penjaga_sim.rng import rng

N = 86_400
Arr = npt.NDArray[np.float64]


@dataclass
class WaterState:
    tank_l: float
    pressure_ar: float = 0.0
    recovery_from_s: float | None = (
        None  # seconds before day start that pressure began recovering (<= 0)
    )


@dataclass(frozen=True)
class WaterInputs:
    tank_demand_lps: Arr  # requested draw from tank-fed fixtures and downstream leaks
    main_demand_lps: Arr  # requested draw from main-fed fixtures and leaks on the main side
    supply_on: npt.NDArray[np.bool_]
    stuck_float_lps: Arr  # W3: minimum inflow forced by a float valve that does not shut


@dataclass(frozen=True)
class WaterDay:
    meter_true_lps: Arr
    meter_registered_lps: Arr
    pressure_bar: Arr
    tank_fraction: Arr
    inflow_lps: Arr
    overflow_lps: Arr
    tank_served_lps: Arr
    main_served_lps: Arr


def _peak_shape(model: HouseModel) -> Arr:
    """Relative depth (0..1) of the network pressure dip at each second (demand peaks)."""
    cal = model.cal
    t = (np.arange(N) + 0.5) / 3600.0
    shape = np.zeros(N)
    for when in ("morning", "evening"):
        centre = cal.value(f"water.supply.demand_peak_{when}_h")
        width = cal.value(f"water.supply.demand_peak_{when}_width_h")
        shape = np.maximum(shape, np.exp(-0.5 * ((t - centre) / width) ** 2))
    return shape


def meter_registration(model: HouseModel, true_lps: Arr) -> Arr:
    """Registered flow per second given true flow (per-meter errors, cutoff)."""
    spec = model.index.sensors_of_kind("water_meter")[0].spec
    q1_lph = float(spec["q3_m3_per_h"]) * 1000.0 / float(spec["ratio_r"])
    q2_lph = 1.6 * q1_lph  # OIML R49-1 §4.1.5
    d = model.draws
    cutoff = d.house("water.meter.cutoff_lph")
    lph = true_lps * 3600.0
    bias = np.where(
        lph >= q2_lph,
        d.house("water.meter.upper_zone_bias_pct"),
        np.where(
            lph >= q1_lph,
            d.house("water.meter.lower_zone_bias_pct"),
            d.house("water.meter.below_q1_bias_pct"),
        ),
    )
    registered = true_lps * (1.0 + bias / 100.0)
    return np.where(lph < cutoff, 0.0, registered)


def supply_pressure(
    model: HouseModel, day_ordinal: int, supply_on: npt.NDArray[np.bool_], state: WaterState
) -> tuple[Arr, WaterState]:
    """Static supply pressure before friction (bar), with interruptions and recovery."""
    d = model.draws
    r = rng(model.seed, "day", day_ordinal, "water_pressure")
    mean, dip = d.house("water.supply.pressure_mean"), d.house("water.supply.peak_dip")
    sd = model.cal.value("water.supply.noise_sd")
    phi = math.exp(-1.0 / model.cal.value("water.supply.noise_tau_s"))
    shocks = r.normal(0.0, sd * math.sqrt(1 - phi * phi), N)
    noise = np.empty(N)
    ar = state.pressure_ar
    for i in range(N):
        ar = phi * ar + shocks[i]
        noise[i] = ar
    p = mean - dip * _peak_shape(model) + noise
    residual = d.house("water.supply.outage_residual")
    recovery_s = 60.0 * d.house("water.supply.recovery_minutes")
    out = ~supply_on
    restore_points = np.flatnonzero(np.diff(supply_on.astype(np.int8)) == 1) + 1
    ramp = np.ones(N)
    starts = [*restore_points.tolist()]
    if state.recovery_from_s is not None:
        starts.insert(0, int(state.recovery_from_s))
    next_recovery: float | None = None
    for s in starts:
        idx = np.arange(max(0, s), N)
        frac = np.clip((idx - s) / recovery_s, 0.0, 1.0)
        ramp[idx] = np.minimum(ramp[idx], frac)
        if s + recovery_s > N:
            next_recovery = s - N
    p = np.where(out, residual, residual + (p - residual) * ramp)
    return np.maximum(p, 0.0), WaterState(state.tank_l, ar, next_recovery)


def simulate_water(
    model: HouseModel, day_ordinal: int, inputs: WaterInputs, state: WaterState
) -> tuple[WaterDay, WaterState]:
    d = model.draws
    tank = model.index.house.water.tanks[0]
    cap = float(tank.capacity_l)
    band = d.house("water.tank.float_band") * cap
    k_lps = d.house("water.tank.float_k") / 60.0
    head = model.cal.value("water.tank.static_head_bar")
    friction = d.house("water.supply.friction_bar_at_30lpm") / (0.5**2)  # bar per (L/s)^2
    p_static, next_state = supply_pressure(model, day_ordinal, inputs.supply_on, state)

    tank_dem, main_dem, stuck = (
        inputs.tank_demand_lps,
        inputs.main_demand_lps,
        inputs.stuck_float_lps,
    )
    supply = inputs.supply_on
    level = np.empty(N)
    inflow = np.empty(N)
    overflow = np.zeros(N)
    tank_served = np.empty(N)
    main_served = np.where(supply, main_dem, 0.0)
    pressure = np.empty(N)
    v = state.tank_l
    q_prev = 0.0
    for i in range(N):
        p = max(0.0, p_static[i] - friction * q_prev * q_prev)
        pressure[i] = p
        if supply[i] and p > head:
            q_max = k_lps * math.sqrt(p - head)
            opening = min(1.0, max(0.0, (cap - v) / band))
            q_in = max(opening * q_max, min(q_max, stuck[i]))
        else:
            q_in = 0.0
        want = tank_dem[i]
        out = want if v + q_in >= want else v + q_in
        v = v + q_in - out
        if v > cap:
            overflow[i] = v - cap
            v = cap
        level[i] = v
        inflow[i] = q_in
        tank_served[i] = out
        q_prev = q_in + main_served[i]
    meter_true = inflow + main_served
    day = WaterDay(
        meter_true_lps=meter_true,
        meter_registered_lps=meter_registration(model, meter_true),
        pressure_bar=pressure,
        tank_fraction=level / cap,
        inflow_lps=inflow,
        overflow_lps=overflow,
        tank_served_lps=tank_served,
        main_served_lps=main_served,
    )
    return day, WaterState(v, next_state.pressure_ar, next_state.recovery_from_s)


def demand_from_uses(model: HouseModel, uses: list[tuple[str, int, int, float]]) -> tuple[Arr, Arr]:
    """(tank-fed, main-fed) demand in L/s from (fixture, start, dur, lpm) uses."""
    tank = np.zeros(N)
    main = np.zeros(N)
    for fixture, start, dur, lpm in uses:
        target = tank if model.is_tank_fed(fixture) else main
        target[start : start + dur] += lpm / 60.0
    return tank, main
