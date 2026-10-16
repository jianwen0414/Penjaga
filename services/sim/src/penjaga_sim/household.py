"""Household behaviour: who is home and awake each second, and what they use.

A day plan is drawn from the generator keyed by (seed, 'day', date, 'household'), so it depends
only on the seed and the date, never on faults or on how time is chunked. Legitimate
anomalies (PRD §12.3) either occur naturally (delay-timer washes, night bucket filling) or
are imposed by modifiers (guests, holidays...); natural occurrences are reported so the
evaluation harness can attribute any alarm they cause.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import numpy.typing as npt

from penjaga_contract.core import ApplianceKind, Room, RoomKind
from penjaga_sim.model import HouseModel, sample
from penjaga_sim.rng import rng

N = 86_400
MIN = 60
Mask = npt.NDArray[np.bool_]


@dataclass(frozen=True)
class WaterUse:
    fixture: str
    start: int
    dur: int
    lpm: float
    heater: str | None = None  # water heater appliance running during this use


@dataclass(frozen=True)
class Use:
    """An appliance or circuit load event."""

    kind: str  # rice_cooker | kettle | microwave | airfryer | tv | washer | iron
    start: int
    dur: int
    appliance: str | None = None
    circuit: str | None = None
    params: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class AcSession:
    appliance: str
    start: int
    end: int
    setpoint: float
    persons: int


@dataclass(frozen=True)
class NaturalAnomaly:
    anomaly_type: str
    start: int
    dur: int
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class Occupant:
    role: str
    bedroom: str
    bathroom: str
    routine: bool
    home: Mask
    asleep: Mask

    @property
    def awake_home(self) -> Mask:
        return self.home & ~self.asleep


@dataclass(frozen=True)
class DayModifiers:
    """Imposed legitimate anomalies active on this day."""

    holiday_absence: bool = False
    guests: int = 0
    festive_cooking: bool = False
    setpoint_offset_c: float = 0.0
    extra_occupant: bool = False
    force_night_bucket: bool = False
    force_washer_delay: bool = False


@dataclass
class DayPlan:
    day: date
    weekend: bool
    occupants: list[Occupant]
    water: list[WaterUse]
    uses: list[Use]
    ac: list[AcSession]
    natural: list[NaturalAnomaly]

    def count(self, attr: str) -> npt.NDArray[np.int16]:
        total = np.zeros(N, np.int16)
        for o in self.occupants:
            total += getattr(o, attr)
        return total


def _span(mask: Mask, start: float, end: float, value: bool = True) -> None:
    a, b = max(0, int(start)), min(N, int(end))
    if b > a:
        mask[a:b] = value


def _m(minutes: float) -> int:
    return round(minutes * MIN)


class _Planner:
    def __init__(self, model: HouseModel, archetype: str, day: date, mods: DayModifiers) -> None:
        self.model = model
        self.cal = model.cal
        self.day = day
        self.mods = mods
        self.weekend = day.weekday() >= 5
        self.r = rng(model.seed, "day", day.toordinal(), "household")
        self.spec = self.cal.table(f"household.archetypes.{archetype}")
        self.water: list[WaterUse] = []
        self.uses: list[Use] = []
        self.ac: list[AcSession] = []
        self.natural: list[NaturalAnomaly] = []

    def p(self, path: str) -> float:
        return sample(self.cal, f"household.{path}", self.r)

    def a(self, path: str) -> float:
        return sample(self.cal, f"appliances.{path}", self.r)

    def t(self, name: str) -> float:
        """A timing parameter (household.timing.*), in its calibrated unit."""
        return sample(self.cal, f"household.timing.{name}", self.r)

    def clock(self, name: str) -> int:
        """A timing parameter in minutes (clock time or duration), as seconds."""
        return _m(self.t(name))

    def chance(self, path: str) -> bool:
        return bool(self.r.random() < self.cal.value(f"household.{path}"))

    # ------------------------------------------------------------------ occupants

    def occupants(self) -> list[Occupant]:
        roles: list[str] = list(self.spec["roles"])
        bedrooms: list[str] = list(self.spec["bedrooms"])
        bathrooms: list[str] = list(self.spec["bathrooms"])
        routine: list[bool] = list(self.spec["routine"])
        spare = self._spare_rooms(bedrooms, bathrooms)
        extra = self.mods.guests + (1 if self.mods.extra_occupant else 0)
        for i in range(extra):
            bed, bath = spare[i % len(spare)] if spare else (bedrooms[0], bathrooms[0])
            roles.append("guest")
            bedrooms.append(bed)
            bathrooms.append(bath)
            routine.append(False)
        return [
            self._schedule(r, b, ba, ro)
            for r, b, ba, ro in zip(roles, bedrooms, bathrooms, routine, strict=True)
        ]

    def _spare_rooms(
        self, bedrooms: Sequence[str], bathrooms: Sequence[str]
    ) -> list[tuple[str, str]]:
        used_beds = set(bedrooms)
        spare_beds = [
            r for r in self.model.aircon_by_room if r not in used_beds and self._is_bedroom(r)
        ]
        spare_baths = [b for b in self.model.bathrooms if b not in set(bathrooms)] or list(
            bathrooms
        )
        return [(bed, spare_baths[i % len(spare_baths)]) for i, bed in enumerate(spare_beds)]

    def _is_bedroom(self, room_id: str) -> bool:
        room: Room = self.model.index.get(room_id, Room)
        return room.kind is RoomKind.bedroom

    def _schedule(self, role: str, bedroom: str, bathroom: str, routine: bool) -> Occupant:
        home = np.ones(N, np.bool_)
        asleep = np.zeros(N, np.bool_)
        base = "roles.worker" if role == "guest" else f"roles.{role}"
        if role == "shift_worker" and not self.weekend:
            back, start = (
                _m(self.p("roles.shift_worker.return_morning")),
                _m(self.p("roles.shift_worker.sleep_day_start")),
            )
            wake, leave = (
                _m(self.p("roles.shift_worker.wake_afternoon")),
                _m(self.p("roles.shift_worker.leave_evening")),
            )
            _span(home, 0, back, False)
            _span(home, leave, N, False)
            _span(asleep, max(start, back + self.clock("shift_sleep_after_return")), wake)
        else:
            if role == "shift_worker":
                base = "roles.worker"
            weekday_key = "wake_weekend" if self.weekend else "wake_weekday"
            wake = _m(self.p(f"{base}.{weekday_key}"))
            sleep = _m(self.p(f"{base}.sleep"))
            if routine:
                first = _m(self.cal.table("household.routine_clock")["times"][0])
                wake = min(wake, first - self.clock("routine_wake_lead"))
            _span(asleep, 0, wake)
            _span(asleep, sleep, N)
            if role in ("worker", "child") and not self.weekend:
                leave = max(
                    _m(self.p(f"{base}.leave_weekday")),
                    wake + self.clock("min_awake_before_leaving"),
                )
                ret = max(_m(self.p(f"{base}.return_weekday")), leave + self.clock("min_time_away"))
                _span(home, leave, min(ret, sleep - self.clock("home_before_sleep")), False)
            elif role in ("worker", "child", "guest"):
                if self.chance(f"{base}.weekend_outing_prob"):
                    s = _m(self.p(f"{base}.weekend_outing_start"))
                    _span(home, s, s + _m(60 * self.p(f"{base}.weekend_outing_hours")), False)
            elif role == "elder":
                if self.chance("roles.elder.outing_prob"):
                    s = _m(self.p("roles.elder.outing_start"))
                    _span(home, s, s + _m(60 * self.p("roles.elder.outing_hours")), False)
                if self.chance("roles.elder.nap_prob"):
                    nap = self.clock("elder_nap_start")
                    _span(asleep, nap, nap + self.clock("elder_nap_minutes"))
        if self.mods.holiday_absence:
            home[:] = False
            asleep[:] = False
        asleep &= home
        return Occupant(role, bedroom, bathroom, routine, home, asleep)

    # ------------------------------------------------------------------ helpers

    def _random_times(self, mask: Mask, n: int) -> list[int]:
        idx = np.flatnonzero(mask)
        if n <= 0 or idx.size == 0:
            return []
        return sorted(int(t) for t in self.r.choice(idx, size=n, replace=True))

    def _first_after(self, mask: Mask, t: int) -> int | None:
        idx = np.flatnonzero(mask[t:]) if 0 <= t < N else np.array([], dtype=np.int64)
        return int(t + idx[0]) if idx.size else None

    def _add_water(
        self, fixture: str, start: int, seconds: float, lpm: float, heater: str | None = None
    ) -> None:
        dur = max(1, round(seconds))
        if 0 <= start < N:
            self.water.append(WaterUse(fixture, start, min(dur, N - start), lpm, heater))

    def _wc(self, bath: str, t: int, g: np.random.Generator | None = None) -> None:
        """A WC visit at ``t``: optional bidet spray, then the flush refill. Draws come from
        ``g`` (default: the household stream)."""
        g = self.r if g is None else g
        cal = self.cal
        fx = self.model.bathrooms[bath]
        if fx.bidet and g.random() < cal.value("household.bath.bidet_prob"):
            secs = sample(cal, "household.bath.bidet_seconds", g)
            self._add_water(fx.bidet, t, secs, sample(cal, "water.fixtures.bidet_lpm", g))
            t += int(secs)
        full = g.random() < cal.value("household.bath.wc_full_flush_prob")
        litres = cal.value("water.fixtures.wc_full_l" if full else "water.fixtures.wc_half_l")
        fill = sample(cal, "water.fixtures.wc_fill_lpm", g)
        self._add_water(fx.wc, t + int(self.t("wc_flush_delay_s")), 60 * litres / fill, fill)

    # ------------------------------------------------------------------ activities

    def bathroom(self, o: Occupant) -> None:
        fx = self.model.bathrooms[o.bathroom]
        awake_home = o.awake_home
        frac = awake_home.mean() * N / (self.t("reference_awake_home_hours") * 3600)
        # showers: one after waking, one later in the day
        wake_t = self._first_after(awake_home, 0)
        anchors: list[tuple[int | None, str]] = [
            (None if wake_t is None else wake_t + self.clock("shower_after_wake"), "morning")
        ]
        away_end = np.flatnonzero(np.diff(o.home.astype(np.int8)) == 1)
        evening = (
            int(away_end[-1]) + self.clock("shower_after_return")
            if away_end.size
            else self.clock("shower_evening_clock")
        )
        anchors.append((evening, "evening"))
        for start, when in anchors[: int(self.cal.value("household.bath.showers_per_day"))]:
            if (
                start is None
                or start >= N
                or not awake_home[min(start, N - 1)]
                or not self.chance("bath.shower_prob")
            ):
                continue
            heated = fx.heater is not None and self.chance(f"bath.heater_prob_{when}")
            lpm = sample(
                self.cal,
                "water.fixtures.shower_heated_lpm"
                if heated
                else "water.fixtures.shower_unheated_lpm",
                self.r,
            )
            self._add_water(
                fx.shower,
                start,
                60 * self.p("bath.shower_minutes"),
                lpm,
                fx.heater if heated else None,
            )
        for t in self._random_times(
            awake_home,
            int(self.r.poisson(self.cal.value("household.bath.wc_uses_per_awake_home_day") * frac)),
        ):
            self._wc(o.bathroom, t)
        for t in self._random_times(
            awake_home,
            int(
                self.r.poisson(
                    self.cal.value("household.bath.basin_uses_per_awake_home_day") * frac
                )
            ),
        ):
            self._add_water(
                fx.basin,
                t,
                self.p("bath.basin_seconds"),
                sample(self.cal, "water.fixtures.basin_lpm", self.r),
            )
        if o.routine:
            for clock in self.cal.table("household.routine_clock")["times"]:
                t = _m(clock + self.p("bath.routine_jitter"))
                if 0 <= t < N and awake_home[t]:
                    self._add_water(
                        fx.basin,
                        t,
                        self.p("bath.routine_seconds"),
                        sample(self.cal, "water.fixtures.routine_basin_lpm", self.r),
                    )

    def night_voids(self, index: int, o: Occupant) -> None:
        """Toilet visits while asleep (nocturia, DS-026/DS-027): a Poisson count per sleep day,
        each a WC visit and sometimes a hand wash. Own random stream, so adding or tuning it
        never shifts the rest of the household's day."""
        g = rng(self.model.seed, "day", self.day.toordinal(), "night_voids", index)
        base = "roles.worker" if o.role == "guest" else f"roles.{o.role}"
        count = int(g.poisson(self.cal.value(f"household.{base}.night_voids_mean")))
        if not count or not o.asleep.any():
            return
        times = np.sort(g.choice(np.flatnonzero(o.asleep), size=count, replace=False))
        fx = self.model.bathrooms[o.bathroom]
        for t in times:
            self._wc(o.bathroom, int(t), g)
            if g.random() < self.cal.value("household.bath.night_basin_prob"):
                lpm = sample(self.cal, "water.fixtures.basin_lpm", g)
                self._add_water(
                    fx.basin, int(t) + 60, sample(self.cal, "household.bath.basin_seconds", g), lpm
                )

    def kitchen(self, occ: list[Occupant]) -> None:
        awake_home = np.zeros(N, np.bool_)
        for o in occ:
            awake_home |= o.awake_home
        n_people = max(1, sum(int(o.home.any()) for o in occ))
        festive = self.t("festive_multiplier") if self.mods.festive_cooking else 1.0
        if not awake_home.any():
            return
        sink, prep = self.model.kitchen_drinking_sink, self.model.kitchen_prep_sink
        duty = self.p("kitchen.sink_duty")

        def sink_session(fixture: str, t: int, minutes: float) -> None:
            lpm = sample(self.cal, "water.fixtures.kitchen_sink_lpm", self.r)
            # intermittent: tap on for `duty` of the session in short bursts
            end, cursor = t + _m(minutes), t
            while cursor < end:
                burst = int(self.t("sink_burst_s"))
                if self.r.random() < duty:
                    self._add_water(fixture, cursor, min(burst, end - cursor), lpm)
                cursor += burst

        first = self._first_after(awake_home, 0)
        assert first is not None  # someone is awake at home today (checked above)
        breakfast = first + self.clock("breakfast_after_wake")
        sink_session(sink, breakfast, self.p("kitchen.breakfast_sink_minutes"))
        kettle = self.model.appliance_of(ApplianceKind.kettle)
        if kettle:
            for k in range(round(self.p("kitchen.kettle_uses") * festive)):
                t = (
                    breakfast + self.clock("kettle_after_breakfast")
                    if k == 0
                    else (self._random_times(awake_home, 1) or [breakfast])[0]
                )
                self.uses.append(Use("kettle", t, _m(self.a("kitchen.kettle_minutes")), kettle))
        lunch = self.clock("lunch_clock")
        if awake_home[lunch]:
            sink_session(sink, lunch, self.p("kitchen.lunch_sink_minutes") * festive)
        mw = self.model.appliance_of(ApplianceKind.microwave)
        if mw:
            for t in self._random_times(awake_home, round(self.p("kitchen.microwave_uses"))):
                self.uses.append(Use("microwave", t, _m(self.a("kitchen.microwave_minutes")), mw))
        # dinner: after the last afternoon/evening arrival (or a default time)
        arrive_from, arrive_until = (
            self.clock("dinner_arrival_from"),
            self.clock("dinner_arrival_until"),
        )
        arrivals = [
            int(i)
            for o in occ
            for i in np.flatnonzero(np.diff(o.home.astype(np.int8)) == 1)
            if arrive_from < i < arrive_until
        ]
        default = self.clock("dinner_default_clock")
        dinner = max(arrivals, default=default) + self.clock("dinner_after_arrival")
        dinner = min(dinner, self.clock("dinner_latest_clock"))
        if (
            self.r.random() < self.cal.value("household.kitchen.cook_dinner_prob")
            or self.mods.festive_cooking
        ):
            sink_session(
                prep,
                dinner - self.clock("prep_before_dinner"),
                self.p("kitchen.dinner_prep_minutes") * festive,
            )
            rice = self.model.appliance_of(ApplianceKind.rice_cooker)
            if rice:
                self.uses.append(
                    Use(
                        "rice_cooker",
                        dinner - self.clock("rice_before_dinner"),
                        _m(self.a("kitchen.rice_cooker_minutes")),
                        rice,
                        params={"keepwarm_s": 3600 * self.a("kitchen.rice_keepwarm_hours")},
                    )
                )
            fryer = self.model.appliance_of(ApplianceKind.air_fryer)
            if fryer and (self.chance("kitchen.airfryer_prob") or self.mods.festive_cooking):
                start = dinner - self.clock("airfryer_before_dinner")
                minutes = _m(self.a("kitchen.airfryer_minutes"))
                self.uses.append(Use("airfryer", start, minutes, fryer))
        sink_session(
            sink,
            dinner + self.clock("dishwash_after_dinner"),
            self.p("kitchen.dishwash_minutes_per_person") * n_people * festive,
        )

    def laundry(self, occ: list[Occupant]) -> None:
        washer = self.model.appliance_of(ApplianceKind.washing_machine)
        home_any = np.zeros(N, np.bool_)
        for o in occ:
            home_any |= o.awake_home
        n = sum(int(o.home.any()) for o in occ)
        if (
            washer
            and n
            and home_any.any()
            and (
                self.r.random()
                < min(1.0, n * self.cal.value("household.laundry.cycles_per_person_day"))
                or self.mods.force_washer_delay
            )
        ):
            delayed = self.mods.force_washer_delay or self.chance("laundry.delay_timer_prob")
            first = self._first_after(home_any, 0)
            assert first is not None  # home_any.any() was checked
            if delayed:
                start = self.clock("washer_delayed_clock")
            elif self.chance("laundry.morning_prob"):
                start = first + self.clock("washer_after_wake")
            else:
                start = self.clock("washer_evening_clock")
            start = min(start, self.clock("washer_latest_clock"))
            minutes = sample(self.cal, "appliances.washer.cycle_minutes", self.r)
            litres = sample(self.cal, "water.fixtures.washer_litres_per_cycle", self.r)
            lpm = sample(self.cal, "water.fixtures.washer_tap_lpm", self.r)
            phases = self.cal.table("appliances.washer.fill_phases")
            for frac, offset in zip(phases["volume_fraction"], phases["cycle_offset"], strict=True):
                self._add_water(
                    self.model.washer_tap,
                    start + _m(minutes * offset),
                    60 * frac * litres / lpm,
                    lpm,
                )
            self.uses.append(Use("washer", start, _m(minutes), washer))
            if delayed:
                self.natural.append(
                    NaturalAnomaly(
                        "washer_delay_timer", start, _m(minutes), {"litres": round(litres, 1)}
                    )
                )
        if home_any.any() and self.chance("laundry.mop_bucket_prob"):
            t = self._random_times(home_any, 1)[0]
            litres = sample(self.cal, "water.fixtures.mop_bucket_l", self.r)
            lpm = sample(self.cal, "water.fixtures.laundry_tap_lpm", self.r)
            self._add_water(self.model.laundry_tap, t, 60 * litres / lpm, lpm)

    def outdoor(self, occ: list[Occupant]) -> None:
        home_any = np.zeros(N, np.bool_)
        for o in occ:
            home_any |= o.awake_home
        if not home_any.any():
            return
        key = "outdoor.garden_prob_weekend" if self.weekend else "outdoor.garden_prob_weekday"
        morning = home_any.copy()
        morning[: self.clock("garden_from_clock")] = False
        morning[self.clock("garden_until_clock") :] = False
        if morning.any() and self.chance(key):
            t = self._random_times(morning, 1)[0]
            self._add_water(
                self.model.garden_tap,
                t,
                60 * self.p("outdoor.garden_minutes"),
                sample(self.cal, "water.fixtures.garden_lpm", self.r),
            )
        if self.weekend and morning.any() and self.chance("outdoor.car_wash_prob_weekend"):
            t = self._random_times(morning, 1)[0]
            self._add_water(
                self.model.garden_tap,
                t,
                60 * self.p("outdoor.car_wash_minutes"),
                sample(self.cal, "water.fixtures.garden_lpm", self.r),
            )
        late = home_any.copy()
        late[: self.clock("night_bucket_from_clock")] = False
        if late.any() and (
            self.mods.force_night_bucket or self.chance("outdoor.night_bucket_prob")
        ):
            t = self._random_times(late, 1)[0]
            litres = self.p("outdoor.night_bucket_litres")
            lpm = sample(self.cal, "water.fixtures.laundry_tap_lpm", self.r)
            self._add_water(self.model.laundry_tap, t, 60 * litres / lpm, lpm)
            self.natural.append(
                NaturalAnomaly(
                    "night_bucket_fill", t, int(60 * litres / lpm), {"litres": round(litres, 1)}
                )
            )

    def comfort(self, occ: list[Occupant]) -> None:
        setpoint = (
            self.model.draws.house("household.comfort.setpoint") + self.mods.setpoint_offset_c
        )
        by_room: dict[str, list[Occupant]] = {}
        for o in occ:
            by_room.setdefault(o.bedroom, []).append(o)
        for room, people in by_room.items():
            ac = self.model.aircon_by_room.get(room)
            if ac is None:
                continue
            sleeping = np.zeros(N, np.int16)
            for o in people:
                sleeping += o.asleep
            if not sleeping.any() or self.r.random() >= self.cal.value(
                "household.comfort.bedroom_ac_prob"
            ):
                continue
            # one session per contiguous sleep block (night, day sleep of shift workers, naps)
            on = sleeping > 0
            edges = np.flatnonzero(np.diff(np.concatenate(([0], on.astype(np.int8), [0]))))
            for start, end in zip(edges[::2], edges[1::2], strict=True):
                is_nap = end - start < self.t("nap_max_hours") * 3600
                if is_nap and self.r.random() >= self.cal.value("household.comfort.nap_ac_prob"):
                    continue
                pre = 0 if start == 0 else _m(self.p("comfort.ac_on_before_sleep"))
                self.ac.append(
                    AcSession(
                        ac,
                        max(0, int(start) - pre),
                        int(end),
                        setpoint,
                        int(sleeping[start:end].max()),
                    )
                )
        living = [r for r in self.model.aircon_by_room if not self._is_bedroom(r)]
        awake_home = np.zeros(N, np.int16)
        for o in occ:
            awake_home += o.awake_home
        tv = self.model.appliance_of(ApplianceKind.television)
        evening = awake_home.copy()
        evening[: self.clock("evening_from_clock")] = 0
        if evening.any():
            start = int(np.flatnonzero(evening)[0]) + self.clock("evening_start_delay")
            dur = _m(60 * self.p("comfort.tv_hours_evening"))
            if tv:
                self.uses.append(Use("tv", start, dur, tv))
            if living and self.chance("comfort.living_ac_evening_prob"):
                self.ac.append(
                    AcSession(
                        self.model.aircon_by_room[living[0]],
                        start,
                        min(N, start + dur),
                        setpoint,
                        int(evening.max()),
                    )
                )
        day = awake_home.copy()
        day[: self.clock("afternoon_from_clock")] = 0
        day[self.clock("afternoon_until_clock") :] = 0
        if day.any():
            start = int(np.flatnonzero(day)[0])
            home_long = (day > 0).sum() > self.t("afternoon_tv_min_home_hours") * 3600
            if tv and (self.weekend or home_long):
                self.uses.append(Use("tv", start, _m(60 * self.p("comfort.tv_hours_day_home")), tv))
            if living and self.weekend and self.chance("comfort.living_ac_weekend_afternoon_prob"):
                self.ac.append(
                    AcSession(
                        self.model.aircon_by_room[living[0]],
                        start,
                        min(N, start + round(self.t("living_ac_afternoon_hours") * 3600)),
                        setpoint,
                        int(day.max()),
                    )
                )
        if (
            awake_home.any()
            and self.r.random() < self.cal.value("household.comfort.iron_sessions_per_week") / 7
        ):
            t = self._random_times(awake_home > 0, 1)[0]
            self.uses.append(
                Use(
                    "iron",
                    t,
                    _m(self.p("comfort.iron_minutes")),
                    circuit=self.model.yard_socket_circuit,
                )
            )


def plan_day(
    model: HouseModel, archetype: str, day: date, mods: DayModifiers | None = None
) -> DayPlan:
    """Plan one house day for ``archetype`` (deterministic in seed and date)."""
    planner = _Planner(model, archetype, day, mods or DayModifiers())
    occupants = planner.occupants()
    for i, o in enumerate(occupants):
        planner.bathroom(o)
        planner.night_voids(i, o)
    planner.kitchen(occupants)
    planner.laundry(occupants)
    planner.outdoor(occupants)
    planner.comfort(occupants)
    planner.water.sort(key=lambda w: (w.start, w.fixture))
    planner.uses.sort(key=lambda u: (u.start, u.kind))
    return DayPlan(
        day, planner.weekend, occupants, planner.water, planner.uses, planner.ac, planner.natural
    )


def archetypes(model: HouseModel) -> list[str]:
    return sorted(model.cal.table("household.archetypes"))
