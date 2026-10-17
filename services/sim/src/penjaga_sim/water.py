"""Water physics for one day at 1 s resolution: supply pressure, roof tank, meter.

* Supply pressure: per-house mean, dips at the morning and evening demand peaks, AR(1) noise,
  interruptions (pressure falls to a residual, then ramps back), and a friction drop across
  the service pipe proportional to the square of the metered flow. On days with low supply
  pressure (a legitimate anomaly, DS-033 to DS-035) the pressure falls to a low daytime level
  after the morning peak and recovers slowly after the evening peak, so the roof tank, which
  cannot fill by day, refills at night.
* Roof tank: the float valve admits q = k * sqrt(p - static_head) when fully open; it opens
  proportionally as the level falls within the float band below full, closes at full. A stuck
  float valve (W3) keeps admitting water; the excess leaves by the overflow pipe.
* RO purifier (DS-029, DS-030): drinking water comes from its storage; while that is below
  full and the main is on at working pressure, RO production refills it at the rated rate and
  the unit draws permeate x (1 + reject ratio) from the main.
* Meter (OIML R49 class 2, DS-007): registered volume = true volume x (1 + error(flow)),
  nothing below the cutoff, larger under-registration between the cutoff and Q1.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

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
    purifier_deficit_l: float = 0.0  # purifier storage still to refill


@dataclass(frozen=True)
class WaterInputs:
    tank_demand_lps: Arr  # requested draw from tank-fed fixtures and downstream leaks
    main_demand_lps: Arr  # requested draw from main-fed fixtures and leaks on the main side
    supply_on: npt.NDArray[np.bool_]
    stuck_float_lps: Arr  # W3: minimum inflow forced by a float valve that does not shut
    purifier_draw_l: Arr  # litres taken from the purifier's storage in each second
    low_pressure: Arr | None = None  # weight (0..1) of the low daytime supply pressure
    inlet_closed: npt.NDArray[np.bool_] | None = None  # main stop valve closed (guided check)


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
    purifier_feed_lps: Arr  # drawn from the main by RO production (permeate + reject)


def low_pressure_weight(model: HouseModel, yesterday: bool, today: bool) -> Arr:
    """Weight (0..1) of the low daytime level in the supply pressure at each second of a day.

    On a low-pressure day the pressure falls over ``low_fall_hours`` from the start of the
    morning demand peak (one standard width before its centre) and stays at the low level
    through the day. From the end of the evening peak it recovers linearly over
    ``low_recovery_hours``, but only by the house's ``low_night_recovery`` fraction of the way
    back to normal: 1 is a full recovery at night, 0 is pressure that stays low through the
    night (DS-035 reports both). The night level holds until the next morning, which either
    falls to the low level again or, after the last low day, returns to normal.
    """
    cal, d = model.cal, model.draws
    t = (np.arange(N) + 0.5) / 3600.0
    fall_from = cal.value("water.supply.demand_peak_morning_h") - cal.value(
        "water.supply.demand_peak_morning_width_h"
    )
    rise_from = cal.value("water.supply.demand_peak_evening_h") + cal.value(
        "water.supply.demand_peak_evening_width_h"
    )
    fall = cal.value("water.supply.low_fall_hours")
    recovery = d.house("water.supply.low_recovery_hours")
    regained = d.house("water.supply.low_night_recovery")

    def night(hours_since_rise: npt.NDArray[np.floating[Any]]) -> Arr:
        return np.asarray(1.0 - regained * np.clip(hours_since_rise / recovery, 0.0, 1.0))

    early = night(t + 24.0 - rise_from) if yesterday else np.zeros(N)
    morning = np.clip((t - fall_from) / fall, 0.0, 1.0)
    weight = early * (1.0 - morning) + (1.0 if today else 0.0) * morning
    if today:
        weight = np.where(t >= rise_from, night(t - rise_from), weight)
    return np.asarray(weight)


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
    model: HouseModel,
    day_ordinal: int,
    supply_on: npt.NDArray[np.bool_],
    state: WaterState,
    low: Arr | None = None,
) -> tuple[Arr, WaterState]:
    """Static supply pressure before friction (bar), with interruptions and recovery.

    ``low`` (from ``low_pressure_weight``) blends the house's mean towards its low daytime
    level; the demand-peak dips and the noise still apply on top.
    """
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
    if low is not None and low.any():
        level = mean + (d.house("water.supply.low_day_bar") - mean) * low
    else:
        level = np.full(N, mean)
    p = level - dip * _peak_shape(model) + noise
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
    return np.maximum(p, 0.0), WaterState(state.tank_l, ar, next_recovery, state.purifier_deficit_l)


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
    p_static, next_state = supply_pressure(
        model, day_ordinal, inputs.supply_on, state, inputs.low_pressure
    )

    tank_dem, main_dem, stuck = (
        inputs.tank_demand_lps,
        inputs.main_demand_lps,
        inputs.stuck_float_lps,
    )
    supply = inputs.supply_on
    # a closed main stop valve passes no water, but the network pressure behind it stays
    flowing = supply if inputs.inlet_closed is None else supply & ~inputs.inlet_closed
    level = np.empty(N)
    inflow = np.empty(N)
    overflow = np.zeros(N)
    tank_served = np.empty(N)
    main_served = np.where(flowing, main_dem, 0.0)
    pressure = np.empty(N)
    purifier = model.purifier
    draw = inputs.purifier_draw_l
    feed = np.zeros(N)
    deficit = state.purifier_deficit_l
    v = state.tank_l
    q_prev = 0.0
    for i in range(N):
        p = max(0.0, p_static[i] - friction * q_prev * q_prev)
        pressure[i] = p
        if flowing[i] and p > head:
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
        if purifier is not None:
            deficit = min(purifier.storage_l, deficit + draw[i])
            if deficit > 0.0 and flowing[i] and p >= purifier.min_pressure_bar:
                made = min(purifier.permeate_lps, deficit)
                deficit -= made
                feed[i] = made * purifier.feed_per_permeate
        q_prev = q_in + main_served[i] + feed[i]
    meter_true = inflow + main_served + feed
    day = WaterDay(
        meter_true_lps=meter_true,
        meter_registered_lps=meter_registration(model, meter_true),
        pressure_bar=pressure,
        tank_fraction=level / cap,
        inflow_lps=inflow,
        overflow_lps=overflow,
        tank_served_lps=tank_served,
        main_served_lps=main_served,
        purifier_feed_lps=feed,
    )
    return day, WaterState(v, next_state.pressure_ar, next_state.recovery_from_s, deficit)


def demand_from_uses(model: HouseModel, uses: list[tuple[str, int, int, float]]) -> tuple[Arr, Arr]:
    """(tank-fed, main-fed) demand in L/s from (fixture, start, dur, lpm) uses."""
    tank = np.zeros(N)
    main = np.zeros(N)
    for fixture, start, dur, lpm in uses:
        target = tank if model.is_tank_fed(fixture) else main
        target[start : start + dur] += lpm / 60.0
    return tank, main
