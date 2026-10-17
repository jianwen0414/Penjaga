"""Electrical physics for one day at 1 s resolution.

Loads -> per-circuit phasor currents (I = P / (V pf) at angle -acos(pf)), real power and
leakage -> residual current per RCCB (sum over every circuit downstream of it) -> protection:

* MCB: damage accumulates at 1 / t_trip(I/In) per second above 1.13 In and decays with a
  thermal time constant below it; t_trip follows the IEC 60898-1 points (DS-021).
* RCCB: trips when its residual current reaches a per-device fraction of IΔn between 0.5 and
  1.0 (DS-024).
* After a trip the circuits stay dead until someone at home resets the device; after repeated
  trips within an hour the household leaves it off (PRD 15.3 guidance).

Trips are resolved iteratively: the earliest trip is applied, loads recomputed, and so on.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import NamedTuple

import numpy as np
import numpy.typing as npt

from penjaga_contract.core import ApplianceKind, Room, RoomKind
from penjaga_sim.faults import DayEffects
from penjaga_sim.household import DayPlan, N
from penjaga_sim.model import HouseModel, sample
from penjaga_sim.rng import rng
from penjaga_sim.weather import DayWeather

Arr = npt.NDArray[np.float64]
Mask = npt.NDArray[np.bool_]
BTU_H_TO_W = 0.29307107


@dataclass
class FridgeState:
    on: bool = False
    remaining_s: float = 0.0
    since_defrost_s: float = 0.0


@dataclass
class ElecState:
    mcb_damage: dict[str, float] = field(default_factory=dict)
    fridge: FridgeState = field(default_factory=FridgeState)
    off_devices: dict[str, int] = field(
        default_factory=dict
    )  # device -> reset second (<0: next morning)
    voltage_ar: float = 0.0
    power_on: bool = True


@dataclass(frozen=True)
class ElecInputs:
    plan: DayPlan
    weather: DayWeather
    effects: DayEffects  # mains loss, extra load/leakage, appliance degradation (per second)


@dataclass(frozen=True)
class Event:
    second: int
    stream: str
    payload: dict[str, str]


@dataclass(frozen=True)
class ElecDay:
    circuit_current_a: dict[str, Arr]
    circuit_power_w: dict[str, Arr]
    residual_ma: dict[str, Arr]
    main_power_w: Arr
    voltage_v: Arr
    appliance_power_w: dict[str, Arr]
    plug_power_w: dict[str, Arr]
    events: list[Event]


# ------------------------------------------------------------------ load construction


@dataclass
class _Load:
    circuit: str
    power: Arr
    pf: float
    leak_ma: float
    appliance: str | None = None


class _Loads:
    def __init__(self, model: HouseModel, inputs: ElecInputs, day_ordinal: int) -> None:
        self.m = model
        self.cal = model.cal
        self.d = model.draws
        self.inp = inputs
        self.plan = inputs.plan
        self.ordinal = day_ordinal
        self.loads: list[_Load] = []
        self.v_supply = self.d.house("electrical.supply.voltage_mean")

    def pf(self, name: str) -> float:
        return self.cal.value(f"electrical.power_factor.{name}")

    def leak(self, name: str, key: str) -> float:
        return self.d.house(f"electrical.leakage.{name}", key)

    def add(
        self,
        circuit: str | None,
        power: Arr,
        pf: float,
        leak_ma: float,
        appliance: str | None = None,
    ) -> None:
        if circuit is not None:
            self.loads.append(_Load(circuit, power, pf, leak_ma, appliance))

    def h(self, path: str, *extra: str) -> float:
        return self.d.house(f"appliances.{path}", *extra)

    def c(self, path: str) -> float:
        """A fixed appliance constant (same for every house)."""
        return self.cal.value(f"appliances.{path}")

    # aircons ---------------------------------------------------------------

    def aircons(self) -> None:
        w = self.inp.weather
        hours = (np.arange(N) + 0.5) / 3600.0
        by_app: dict[str, Arr] = {}
        for app in self.m.appliances_of(ApplianceKind.air_conditioner):
            by_app[app] = np.full(N, self.h("aircon.standby_w", app))
        for s in self.plan.ac:
            app, room = s.appliance, self.m.appliances[s.appliance].room_id
            plate = self.m.nameplate(app)
            cap_rated = float(plate["cooling_capacity_btu_h"]) * BTU_H_TO_W
            eer = cap_rated / float(plate["rated_input_w"])
            level = self.m.index.room_level(room)
            room_model: Room = self.m.index.get(room, Room)
            if room_model.kind is RoomKind.bedroom:
                ua = self.h("aircon.ua_bedroom_1f" if level >= 1 else "aircon.ua_bedroom_gf", room)
            else:
                ua = self.h("aircon.ua_living", room)
            sl = slice(s.start, s.end)
            t_out, rh = w.temp_c[sl], w.rh_pct[sl]
            excess = np.zeros(t_out.size)
            if level >= 1:
                since_peak = (hours[sl] - self.c("aircon.roof_peak_h")) % 24.0
                excess = self.h("aircon.roof_excess_peak") * np.exp(
                    -since_peak / self.h("aircon.roof_decay_hours")
                )
            drive = t_out + excess
            gains = s.persons * self.h("aircon.gain_per_person_w") + self.h(
                "aircon.gain_equipment_w", room
            )
            elapsed = np.arange(t_out.size)
            pulldown = (
                self.h("aircon.pulldown_ua_multiple")
                * ua
                * np.exp(-elapsed / (60 * self.h("aircon.pulldown_minutes")))
            )
            sensible = (ua + pulldown) * np.maximum(0.0, drive - s.setpoint) + gains
            humid = np.maximum(0.0, rh - self.c("aircon.latent_rh_onset"))
            load = sensible * (
                1 + self.h("aircon.latent_k") * humid / self.c("aircon.latent_rh_span")
            )
            ones = np.ones(N)
            cap_factor = self.inp.effects.ac_capacity_factor.get(app, ones)[sl]
            cop_factor = self.inp.effects.ac_cop_factor.get(app, ones)[sl]
            cap_max = (
                cap_rated * self.cal.value("appliances.aircon.max_capacity_ratio") * cap_factor
            )
            delivered = np.minimum(load, cap_max)
            lr = delivered / (cap_rated * cap_factor)
            lr_min = self.cal.value("appliances.aircon.min_load_ratio")
            lr_eff = np.maximum(lr, lr_min)
            cop = (
                eer
                * (1 + self.h("aircon.cop_temp_coef") * (self.c("aircon.rating_outdoor_c") - t_out))
                * (1 + self.h("aircon.part_load_gain") * (1 - np.minimum(lr_eff, 1.0)))
                * cop_factor
            )
            compressor = lr_eff * cap_rated * cap_factor / cop * np.minimum(1.0, lr / lr_min)
            by_app[app][sl] = np.maximum(by_app[app][sl], compressor + self.h("aircon.fan_w", app))
        for app, power in by_app.items():
            self.add(
                self.m.circuit_of(app),
                power,
                self.pf("inverter_ac"),
                self.leak("aircon_ma", app),
                app,
            )

    # water heaters -----------------------------------------------------------

    def heaters(self) -> None:
        powers: dict[str, Arr] = {}
        for u in self.plan.water:
            if u.heater is None:
                continue
            plate = self.m.nameplate(u.heater)
            p = (
                float(plate["rated_input_w"])
                * (self.v_supply / float(plate["rated_voltage_v"])) ** 2
            )
            arr = powers.setdefault(u.heater, np.zeros(N))
            arr[u.start : u.start + u.dur] = p
        for app in self.m.appliances_of(ApplianceKind.water_heater):
            self.add(
                self.m.circuit_of(app),
                powers.get(app, np.zeros(N)),
                self.pf("resistive"),
                self.leak("water_heater_ma", app),
                app,
            )

    # fridge -------------------------------------------------------------------

    def fridge(self, state: FridgeState) -> tuple[Arr, FridgeState]:
        app = self.m.appliance_of(ApplianceKind.refrigerator)
        power = np.zeros(N)
        if app is None:
            return power, state
        f = self.inp.effects
        cycle_rng = rng(self.m.seed, "day", self.ordinal, "fridge_cycles")
        on_w = self.h("fridge.on_w") * f.fridge_power_factor
        off_w = self.h("fridge.off_w")
        kitchen_base = self.c("fridge.kitchen_base_c")
        kitchen_t = kitchen_base + self.c("fridge.kitchen_coupling") * (
            self.inp.weather.temp_c - self.c("fridge.kitchen_ref_outdoor_c")
        )
        meal_s = int(60 * self.c("fridge.meal_window_minutes"))
        meal = np.zeros(N, np.bool_)
        for u in self.plan.uses:
            if u.kind in ("rice_cooker", "microwave", "kettle"):
                meal[max(0, u.start - meal_s) : u.start + meal_s] = True
        duty = np.clip(
            self.h("fridge.base_duty")
            * (1 + self.cal.value("appliances.fridge.duty_per_k") * (kitchen_t - kitchen_base))
            + meal * self.cal.value("appliances.fridge.door_duty_boost")
            + f.fridge_duty_add,
            self.c("fridge.duty_min"),
            self.c("fridge.duty_max"),
        )
        defrost_every = 3600 * self.h("fridge.defrost_every_h")
        defrost_len = int(60 * self.h("fridge.defrost_minutes"))
        surge = self.h("fridge.startup_surge_w")
        t = 0
        on, remaining, since_defrost = state.on, state.remaining_s, state.since_defrost_s
        while t < N:
            if since_defrost >= defrost_every:
                end = min(N, t + defrost_len)
                power[t:end] = self.h("fridge.defrost_w")
                since_defrost = 0.0
                t, on, remaining = end, False, 0.0
                continue
            if remaining <= 0:
                on = not on
                d = float(duty[t])
                normal = sample(self.cal, "appliances.fridge.on_minutes", cycle_rng)
                short = f.fridge_on_minutes[t]
                on_s = 60 * (normal if np.isnan(short) else float(short))
                remaining = on_s if on else on_s * (1 - d) / d
                if on:
                    power[t : min(N, t + 3)] = on_w[t : min(N, t + 3)] + surge
            step = int(min(remaining, N - t, max(1, defrost_every - since_defrost)))
            step = max(step, 1)
            if on:
                seg = power[t : t + step]
                power[t : t + step] = np.where(seg > 0, seg, on_w[t : t + step])
            else:
                power[t : t + step] = off_w
            t += step
            remaining -= step
            since_defrost += step
        self.add(
            self.m.circuit_of(app), power, self.pf("compressor"), self.leak("fridge_ma", app), app
        )
        return power, FridgeState(on, remaining, since_defrost)

    # RO purifier --------------------------------------------------------------------

    def purifier(self) -> None:
        """The countertop purifier's heater and compressor keep its hot and cold tanks at
        temperature (DS-036, DS-037).

        Each runs a thermostat cycle: a fixed run length, repeated so that the two together use
        the house's standby energy per day (the ENERGY STAR on-mode figure, which excludes
        dispensing), split between them by ``heater_share``. The cycle phase is fixed per house
        and keyed to absolute time, so it runs on across midnight. Power-saving mode is off,
        the manual's default (DS-029). Dispensing hot or cold water would add energy; it is not
        modelled.
        """
        app = self.m.appliance_of(ApplianceKind.water_purifier)
        if app is None:
            return
        seconds = (self.ordinal * N + np.arange(N, dtype=np.int64)).astype(np.float64)
        daily_j = self.h("purifier.standby_kwh_per_day") * 3.6e6
        share = self.h("purifier.heater_share")
        heater_w = self.h("purifier.heater_w")
        compressor_w = self.c("purifier.compressor_a") * self.v_supply * self.pf("compressor")
        hot_l = self.c("purifier.hot_tank_l")
        heater_on_s = 4186.0 * hot_l * self.h("purifier.thermostat_band_k") / heater_w
        compressor_on_s = 60.0 * self.h("purifier.compressor_on_minutes")

        def cycle(on_s: float, power_w: float, energy_j: float, part: str) -> Arr:
            period = on_s * power_w * N / energy_j  # on for on_s in every period
            phase = self.h(f"purifier.{part}_phase") * period
            return ((seconds + phase) % period < on_s).astype(np.float64)

        heater = cycle(heater_on_s, heater_w, share * daily_j, "heater")
        compressor = cycle(compressor_on_s, compressor_w, (1 - share) * daily_j, "compressor")
        leak = self.leak("small_appliance_ma", app)
        self.add(self.m.circuit_of(app), heater * heater_w, self.pf("resistive"), leak, app)
        self.add(
            self.m.circuit_of(app), compressor * compressor_w, self.pf("compressor"), leak, app
        )

    # small appliances and household loads ---------------------------------------

    def events(self) -> None:
        kitchen: dict[str, Arr] = {}

        def arr(app: str) -> Arr:
            return kitchen.setdefault(app, np.zeros(N))

        iron = np.zeros(N)
        for u in self.plan.uses:
            sl = slice(u.start, min(N, u.start + u.dur))
            if u.kind == "rice_cooker" and u.appliance:
                a = arr(u.appliance)
                a[sl] = self.h("kitchen.rice_cooker_w")
                end = sl.stop
                a[end : min(N, end + int(u.params.get("keepwarm_s", 0)))] = self.h(
                    "kitchen.rice_keepwarm_w"
                )
            elif u.kind == "kettle" and u.appliance:
                arr(u.appliance)[sl] = self.h("kitchen.kettle_w")
            elif u.kind == "microwave" and u.appliance:
                arr(u.appliance)[sl] = self.h("kitchen.microwave_w")
            elif u.kind == "airfryer" and u.appliance:
                a = arr(u.appliance)
                n = sl.stop - sl.start
                pattern = np.ones(n)
                duty = self.h("kitchen.airfryer_duty")
                warm = min(n, int(self.c("kitchen.airfryer_warmup_s")))
                cycle = int(self.c("kitchen.airfryer_cycle_s"))
                cyc = (np.arange(n - warm) % cycle) < cycle * duty
                pattern[warm:] = cyc
                a[sl] = np.maximum(a[sl], self.h("kitchen.airfryer_w") * pattern)
            elif u.kind == "tv" and u.appliance:
                arr(u.appliance)[sl] = self.h("other.tv_w")
            elif u.kind == "washer" and u.appliance:
                a = arr(u.appliance)
                n = sl.stop - sl.start
                pattern = np.full(n, self.h("washer.idle_w"))
                period = int(self.c("washer.agitate_period_s"))
                agit = (np.arange(n) % period) < self.c("washer.agitate_on_s")
                phases = self.cal.table("appliances.washer.motor_phases")
                for lo, hi in phases["agitate"]:
                    seg = slice(int(lo * n), int(hi * n))
                    pattern[seg] = np.where(
                        agit[seg], self.h("washer.wash_w"), self.h("washer.idle_w")
                    )
                pattern[int(phases["spin_from"] * n) :] = self.h("washer.spin_w")
                a[sl] = pattern
            elif u.kind == "iron":
                n = sl.stop - sl.start
                cycle = int(self.c("other.iron_cycle_s"))
                on = (np.arange(n) % cycle) < cycle * self.h("other.iron_duty")
                iron[sl] = on * self.h("other.iron_w")
        pf_kind = {
            ApplianceKind.washing_machine: "motor",
            ApplianceKind.microwave: "electronics",
            ApplianceKind.television: "electronics",
            ApplianceKind.router: "electronics",
        }
        leak_kind = {
            ApplianceKind.washing_machine: "washer_ma",
            ApplianceKind.television: "electronics_ma",
            ApplianceKind.router: "electronics_ma",
        }
        for kind in (
            ApplianceKind.rice_cooker,
            ApplianceKind.kettle,
            ApplianceKind.microwave,
            ApplianceKind.air_fryer,
            ApplianceKind.television,
            ApplianceKind.washing_machine,
            ApplianceKind.router,
        ):
            for app in self.m.appliances_of(kind):
                power = kitchen.get(app, np.zeros(N))
                if kind is ApplianceKind.router:
                    power = np.full(N, self.h("other.router_w"))
                elif kind is ApplianceKind.television:
                    power = np.maximum(power, self.cal.value("appliances.other.tv_standby_w"))
                self.add(
                    self.m.circuit_of(app),
                    power,
                    self.pf(pf_kind.get(kind, "resistive")),
                    self.leak(leak_kind.get(kind, "small_appliance_ma"), app),
                    app,
                )
        self.add(
            self.m.yard_socket_circuit,
            iron,
            self.pf("resistive"),
            self.leak("small_appliance_ma", "iron"),
        )

    def household(self) -> None:
        plan, w = self.plan, self.inp.weather
        t = np.arange(N)
        dark = (t < 60 * self.cal.value("appliances.other.sunrise_min")) | (
            t >= 60 * self.cal.value("appliances.other.sunset_min")
        )
        ac_on: dict[str, Mask] = {}
        for s in plan.ac:
            room = self.m.appliances[s.appliance].room_id
            ac_on.setdefault(room, np.zeros(N, np.bool_))[s.start : s.end] = True
        lights: dict[str, Arr] = {}
        fans: dict[str, Arr] = {}
        sockets: dict[str, Arr] = {}
        any_awake = np.zeros(N, np.bool_)
        warm = w.temp_c > self.cal.value("household.comfort.fan_threshold_degC")
        for o in plan.occupants:
            awake = o.awake_home
            any_awake |= awake
            circ = self.m.lighting_circuit_for_room(o.bedroom)
            lights.setdefault(circ, np.zeros(N))[:] += (awake & dark) * self.h(
                "other.light_w_per_awake_person", o.bedroom
            )
            room_ac = ac_on.get(o.bedroom, np.zeros(N, np.bool_))
            fans.setdefault(circ, np.zeros(N))[:] += (o.asleep & ~room_ac & warm) * self.h(
                "other.fan_w"
            )
            gf = self.m.lighting_circuit_by_level[min(self.m.lighting_circuit_by_level)]
            fans.setdefault(gf, np.zeros(N))[:] += (
                (awake & ~dark & warm) * self.c("other.fan_share_awake") * self.h("other.fan_w")
            )
            sock = self.m.socket_circuit_by_room.get(o.bedroom)
            if sock:
                evening = (t >= 60 * self.cal.value("household.timing.evening_from_clock")) & awake
                sockets.setdefault(sock, np.zeros(N))[:] += evening * self.h(
                    "other.socket_active_w", o.bedroom
                )
        gf = self.m.lighting_circuit_by_level[min(self.m.lighting_circuit_by_level)]
        lights.setdefault(gf, np.zeros(N))[:] += (any_awake & dark) * self.h(
            "other.light_common_w"
        ) + dark * self.h("other.night_light_w")
        for circ in {c for c in self.m.socket_circuit_by_room.values()}:
            sockets.setdefault(circ, np.zeros(N))[:] += self.h("other.socket_standby_w", circ)
        for circ, p in lights.items():
            self.add(circ, p, self.pf("electronics"), 0.0)
        for circ, p in fans.items():
            self.add(circ, p, self.pf("fan"), 0.0)
        for circ, p in sockets.items():
            self.add(circ, p, self.pf("electronics"), self.leak("electronics_ma", circ))
        for circ, p in self.inp.effects.extra_load_w.items():
            self.add(circ, p, self.pf("resistive"), 0.0)


# ------------------------------------------------------------------ protection


class _Curve(NamedTuple):
    """An MCB's time-current curve: trip times (s) at multiples of In, and the no-trip multiple."""

    t145: float
    t255: float
    t3: float
    t5: float
    no_trip: float


def _t_trip(multiple: Arr, curve: _Curve) -> Arr:
    """Trip time (s) at a constant current multiple of In (inf at or below no_trip)."""
    t145, t255, t3, t5, no_trip = curve
    m = np.asarray(multiple, dtype=np.float64)
    out = np.full(m.shape, np.inf)
    a = (m > no_trip) & (m <= 1.45)
    out[a] = t145 * (1.45**2 - no_trip**2) / (m[a] ** 2 - no_trip**2)

    def loglog(mask: Mask, x0: float, y0: float, x1: float, y1: float) -> None:
        slope = math.log(y1 / y0) / math.log(x1 / x0)
        out[mask] = y0 * (m[mask] / x0) ** slope

    loglog((m > 1.45) & (m <= 2.55), 1.45, t145, 2.55, t255)
    loglog((m > 2.55) & (m <= 3.0), 2.55, t255, 3.0, t3)
    loglog((m > 3.0) & (m <= 5.0), 3.0, t3, 5.0, t5)
    out[m > 5.0] = t5
    return out


@dataclass(frozen=True)
class _Trip:
    second: int
    device: str


@dataclass
class _Sums:
    """Per-circuit phasor current parts, real power and leakage before protection."""

    re: dict[str, Arr]
    im: dict[str, Arr]
    real: dict[str, Arr]
    leak: dict[str, Arr]


STREAM_TRIP = "elec.protection.trip"
STREAM_SUPPLY = "elec.supply.state"


def _supply_voltage(model: HouseModel, ordinal: int, state: ElecState) -> tuple[Arr, float]:
    """Supply voltage before the drop caused by the house's own load (AR(1) noise, evening sag)."""
    d, cal = model.draws, model.cal
    r = rng(model.seed, "day", ordinal, "voltage")
    phi = math.exp(-1.0 / cal.value("electrical.supply.noise_tau_s"))
    shocks = r.normal(0, cal.value("electrical.supply.noise_sd_v") * math.sqrt(1 - phi * phi), N)
    ar = np.empty(N)
    x = state.voltage_ar
    for i in range(N):
        x = phi * x + shocks[i]
        ar[i] = x
    hours = (np.arange(N) + 0.5) / 3600.0
    peak_from, peak_until = (
        cal.value("electrical.supply.peak_from_h"),
        cal.value("electrical.supply.peak_until_h"),
    )
    peak = ((hours >= peak_from) & (hours < peak_until)).astype(np.float64)
    mean = d.house("electrical.supply.voltage_mean")
    return mean * (1 - d.house("electrical.supply.peak_sag_frac") * peak) + ar, x


def _sums(model: HouseModel, loads: list[_Load], v_supply: Arr, inputs: ElecInputs) -> _Sums:
    d = model.draws
    onset = model.cal.value("electrical.leakage.humidity_onset_rh")
    span = model.cal.value("electrical.leakage.humidity_span_rh")
    humid = np.clip((inputs.weather.rh_pct - onset) / span, 0.0, 1.0)
    energised = model.cal.value("electrical.leakage.appliance_on_w")
    by_circuit: dict[str, list[_Load]] = {c: [] for c in model.circuits}
    for ld in loads:
        by_circuit[ld.circuit].append(ld)
    sums = _Sums({}, {}, {}, {})
    for c in model.circuits:
        real, re, im = np.zeros(N), np.zeros(N), np.zeros(N)
        leak = np.full(N, d.house("electrical.leakage.cable_ma_per_circuit", c))
        # an appliance leaks once while any of its loads is energised (the purifier has two)
        appliance_on: dict[str, tuple[Mask, float]] = {}
        for ld in by_circuit[c]:
            real += ld.power
            amps = ld.power / v_supply
            re += amps
            im += amps * math.tan(math.acos(min(1.0, ld.pf)))
            on = ld.power > energised
            if ld.appliance is None:
                leak += np.where(on, ld.leak_ma, 0.0)
            else:
                seen, ma = appliance_on.get(ld.appliance, (np.zeros(N, np.bool_), 0.0))
                appliance_on[ld.appliance] = (seen | on, max(ma, ld.leak_ma))
        for on, ma in appliance_on.values():
            leak += np.where(on, ma, 0.0)
        if c in model.outdoor_circuits:
            leak *= 1 + d.house("electrical.leakage.humidity_gain") * humid
        leak += inputs.effects.extra_leak_ma.get(c, 0.0)
        sums.re[c], sums.im[c], sums.real[c], sums.leak[c] = re, im, real, leak
    return sums


class _Protection:
    """Resolve trips over the day: earliest first, recomputing live circuits after each."""

    def __init__(
        self, model: HouseModel, ordinal: int, inputs: ElecInputs, sums: _Sums, state: ElecState
    ) -> None:
        d, cal = model.draws, model.cal
        self.m, self.sums, self.state = model, sums, state
        self.power_on = inputs.effects.power_on
        self.trip_fraction = {
            dev: d.house("electrical.rcd.trip_fraction", dev) for dev in model.rccbs
        }
        self.curve = {
            c: _Curve(
                d.house("electrical.mcb.t_at_1_45", c),
                d.house("electrical.mcb.t_at_2_55", c),
                d.house("electrical.mcb.t_at_3_00", c),
                cal.value("electrical.mcb.t_at_5_00"),
                cal.value("electrical.mcb.no_trip_multiple"),
            )
            for c in model.circuits
        }
        self.tau = {c: d.house("electrical.mcb.cooling_tau_s", c) for c in model.circuits}
        self.chain = {c: model.index.protection_chain(c) for c in model.circuits}
        self.awake_home = np.zeros(N, np.bool_)
        for o in inputs.plan.occupants:
            self.awake_home |= o.awake_home
        self.reset_rng = rng(model.seed, "day", ordinal, "resets")
        self.reset_delay = cal["electrical.reset.minutes_when_home"]
        self.give_up = int(cal.value("electrical.reset.give_up_after_trips"))
        self.events: list[Event] = []
        self.off: dict[str, list[tuple[int, int]]] = {}
        self.left_off: set[str] = set()
        self.mcb_damage_end = dict(state.mcb_damage)
        self._carry_over()

    def _first_awake_home(self, start: int) -> int:
        idx = np.flatnonzero(self.awake_home[start:])
        return start + int(idx[0]) if idx.size else N

    def _carry_over(self) -> None:
        """Devices left off at the end of the previous day are reset when someone is up."""
        for dev, when in self.state.off_devices.items():
            until = when if when >= 0 else self._first_awake_home(0)
            self.off[dev] = [(0, min(N, until))]
            if until < N:
                self.events.append(Event(until, STREAM_TRIP, {"device_id": dev, "state": "reset"}))
            else:
                self.left_off.add(dev)

    def live(self, c: str) -> Mask:
        mask = self.power_on.copy()
        for dev in self.chain[c]:
            for a, b in self.off.get(dev, []):
                mask[a:b] = False
        return mask

    def _mcb_trip(self, c: str, live: Mask) -> int | None:
        curve = self.curve[c]
        sums = self.sums
        multiple = np.hypot(sums.re[c], sums.im[c]) * live / self.m.mcb_rating(c)
        start = self.state.mcb_damage.get(c, 0.0)
        if multiple.max() <= curve.no_trip and start < 1e-6:
            self.mcb_damage_end[c] = 0.0
            return None
        rate = 1.0 / _t_trip(multiple, curve)
        decay = math.exp(-1.0 / self.tau[c])
        dmg = start
        for i in range(N):
            dmg = dmg + rate[i] if rate[i] > 0 else dmg * decay
            if dmg >= 1.0:
                self.mcb_damage_end[c] = 0.0
                return i
        self.mcb_damage_end[c] = dmg
        return None

    def _candidates(self) -> list[_Trip]:
        m = self.m
        live = {c: self.live(c) for c in m.circuits}
        out: list[_Trip] = []
        for dev in m.rccbs:
            residual = np.zeros(N)
            for c in m.rccb_circuits[dev]:
                residual += self.sums.leak[c] * live[c]
            threshold = self.trip_fraction[dev] * m.residual_rating_ma(dev)
            hit = np.flatnonzero(residual >= threshold)
            if hit.size:
                out.append(_Trip(int(hit[0]), dev))
        for c in m.circuits:
            at = self._mcb_trip(c, live[c])
            if at is not None:
                out.append(_Trip(at, m.index.circuit(c).mcb_id))
        return out

    def _order(self, trip: _Trip) -> tuple[int, float]:
        """Earliest trip first; on ties the most sensitive (nearest) RCCB opens first."""
        sensitivity = (
            self.m.residual_rating_ma(trip.device) if trip.device in self.trip_fraction else 0.0
        )
        return trip.second, sensitivity

    def resolve(self) -> None:
        history: dict[str, list[int]] = {}
        for _ in range(64):  # trips per day are few; the bound guards against oscillation
            candidates = self._candidates()
            if not candidates:
                return
            first = min(candidates, key=self._order)
            dev, t0 = first.device, first.second
            recent = [s for s in history.get(dev, []) if s > t0 - 3600]
            history.setdefault(dev, []).append(t0)
            self.events.append(Event(t0, STREAM_TRIP, {"device_id": dev, "state": "tripped"}))
            if len(recent) + 1 >= self.give_up:
                reset = N  # left off for the rest of the day
            else:
                delay = int(60 * self.reset_delay.sample(self.reset_rng))
                reset = self._first_awake_home(min(N, t0 + delay))
            self.off.setdefault(dev, []).append((t0, min(N, reset)))
            if reset < N:
                self.events.append(Event(reset, STREAM_TRIP, {"device_id": dev, "state": "reset"}))
                self.left_off.discard(dev)
            else:
                self.left_off.add(dev)


def _event_key(ev: Event) -> tuple[int, str, str, bool]:
    return ev.second, ev.stream, ev.payload.get("device_id", ""), ev.payload["state"] != "tripped"


def simulate_electrical(
    model: HouseModel, day: date, inputs: ElecInputs, state: ElecState
) -> tuple[ElecDay, ElecState]:
    ordinal = day.toordinal()
    loads = _Loads(model, inputs, ordinal)
    loads.aircons()
    loads.heaters()
    fridge_power, fridge_state = loads.fridge(state.fridge)
    loads.events()
    loads.purifier()
    loads.household()
    v_supply, voltage_ar = _supply_voltage(model, ordinal, state)
    sums = _sums(model, loads.loads, v_supply, inputs)
    protection = _Protection(model, ordinal, inputs, sums, state)
    protection.resolve()

    live = {c: protection.live(c) for c in model.circuits}
    current = {c: np.hypot(sums.re[c], sums.im[c]) * live[c] for c in model.circuits}
    power = {c: sums.real[c] * live[c] for c in model.circuits}
    residual: dict[str, Arr] = {}
    for dev in model.rccbs:
        residual[dev] = np.zeros(N)
        for c in model.rccb_circuits[dev]:
            residual[dev] += sums.leak[c] * live[c]
    total_current = np.zeros(N)
    main_power = np.zeros(N)
    for c in model.circuits:
        total_current += current[c]
        main_power += power[c]
    power_on = inputs.effects.power_on
    z = model.draws.house("electrical.supply.source_impedance_ohm")
    voltage = np.where(power_on, v_supply - z * total_current, 0.0)
    app_power: dict[str, Arr] = {}
    for ld in loads.loads:  # an appliance may have several loads (the purifier: two)
        if ld.appliance:
            app_power[ld.appliance] = app_power.get(ld.appliance, 0.0) + ld.power * live[ld.circuit]
    plug: dict[str, Arr] = {}
    fridge = model.appliance_of(ApplianceKind.refrigerator)
    fridge_circuit = model.circuit_of(fridge) if fridge else None
    if fridge and fridge_circuit:
        plug[fridge] = fridge_power * live[fridge_circuit]
    events = list(protection.events)
    previous = np.concatenate(([state.power_on], power_on[:-1]))
    for e in np.flatnonzero(power_on != previous):
        state_name = "restored" if power_on[e] else "lost"
        events.append(Event(int(e), STREAM_SUPPLY, {"state": state_name}))
    events.sort(key=_event_key)
    out = ElecDay(current, power, residual, main_power, voltage, app_power, plug, events)
    new_state = ElecState(
        mcb_damage={c: v for c, v in protection.mcb_damage_end.items() if v > 1e-6},
        fridge=fridge_state,
        off_devices=dict.fromkeys(sorted(protection.left_off), -1),
        voltage_ar=voltage_ar,
        power_on=bool(power_on[-1]),
    )
    return out, new_state
