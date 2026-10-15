"""The simulated house: contract topology plus per-house calibration draws.

Everything structural comes from the House Data Contract through ``HouseIndex`` queries
(fixture kinds, rooms, circuits), never from hard-coded IDs. Per-house constants (a meter's
bias, an RCCB's trip point, an aircon room's heat-loss coefficient) are drawn once per
episode, each from its own stream keyed by the parameter path, so they never depend on
draw order.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any

import numpy as np

from penjaga_contract.core import (
    Appliance,
    ApplianceKind,
    CircuitKind,
    Fixture,
    FixtureKind,
    RoomKind,
)
from penjaga_contract.house import ElementKind, HouseIndex
from penjaga_sim.calibration import Calibration
from penjaga_sim.rng import rng


@dataclass
class Draws:
    """Per-house constant draws, cached and keyed by (seed, 'house', path, *extra)."""

    seed: int
    calibration: Calibration
    _cache: dict[tuple[str, ...], float] = field(default_factory=dict)

    def house(self, path: str, *extra: str) -> float:
        key = (path, *extra)
        if key not in self._cache:
            value = self.calibration[path].sample(rng(self.seed, "house", *key))
            self._cache[key] = float(value)
        return self._cache[key]


@dataclass(frozen=True)
class BathroomFixtures:
    room_id: str
    wc: str
    basin: str
    shower: str
    bidet: str | None
    heater: str | None  # water heater appliance serving the shower


class HouseModel:
    def __init__(self, index: HouseIndex, calibration: Calibration, seed: int) -> None:
        self.index = index
        self.cal = calibration
        self.seed = seed
        self.draws = Draws(seed, calibration)
        house = index.house
        self.fixtures: dict[str, Fixture] = {f.id: f for f in house.water.fixtures}
        self.appliances: dict[str, Appliance] = {a.id: a for a in house.appliances}

    # ------------------------------------------------------------------ water topology

    def _fixtures_in(self, room_id: str, kind: FixtureKind) -> list[str]:
        return [f.id for f in self.fixtures.values() if f.room_id == room_id and f.kind is kind]

    @cached_property
    def bathrooms(self) -> dict[str, BathroomFixtures]:
        out = {}
        heaters = {
            a.fixture_id: a.id
            for a in self.appliances.values()
            if a.kind is ApplianceKind.water_heater and a.fixture_id
        }
        for room in self.index.house.rooms:
            if room.kind is not RoomKind.bathroom:
                continue
            (wc,) = self._fixtures_in(room.id, FixtureKind.wc_cistern)
            (basin,) = self._fixtures_in(room.id, FixtureKind.basin_tap)
            (shower,) = self._fixtures_in(room.id, FixtureKind.shower)
            bidets = self._fixtures_in(room.id, FixtureKind.bidet_spray)
            out[room.id] = BathroomFixtures(
                room.id, wc, basin, shower, bidets[0] if bidets else None, heaters.get(shower)
            )
        return out

    def _one_fixture(self, kind: FixtureKind, *, prefer_main: bool | None = None) -> str:
        found = [f.id for f in self.fixtures.values() if f.kind is kind]
        if prefer_main is not None:
            wanted = "main" if prefer_main else "tank"
            preferred = [f for f in found if self.index.supply_source(f) == wanted]
            found = preferred or found
        if not found:
            raise LookupError(f"house has no {kind.value} fixture")
        return found[0]

    @cached_property
    def kitchen_drinking_sink(self) -> str:
        return self._one_fixture(FixtureKind.sink_tap, prefer_main=True)

    @cached_property
    def kitchen_prep_sink(self) -> str:
        sinks = [f.id for f in self.fixtures.values() if f.kind is FixtureKind.sink_tap]
        others = [s for s in sinks if s != self.kitchen_drinking_sink]
        return others[0] if others else self.kitchen_drinking_sink

    @cached_property
    def washer_tap(self) -> str:
        return self._one_fixture(FixtureKind.washing_machine_tap)

    @cached_property
    def laundry_tap(self) -> str:
        return self._one_fixture(FixtureKind.laundry_tap)

    @cached_property
    def garden_tap(self) -> str:
        return self._one_fixture(FixtureKind.garden_tap)

    def is_tank_fed(self, fixture_id: str) -> bool:
        return self.index.supply_source(fixture_id) == "tank"

    # ------------------------------------------------------------------ appliances

    def appliances_of(self, kind: ApplianceKind) -> list[str]:
        return [a.id for a in self.appliances.values() if a.kind is kind]

    def appliance_of(self, kind: ApplianceKind) -> str | None:
        found = self.appliances_of(kind)
        return found[0] if found else None

    @cached_property
    def aircon_by_room(self) -> dict[str, str]:
        return {
            self.appliances[a].room_id: a for a in self.appliances_of(ApplianceKind.air_conditioner)
        }

    def circuit_of(self, appliance_id: str) -> str | None:
        return self.index.circuit_of_appliance(appliance_id)

    @cached_property
    def circuits(self) -> list[str]:
        return [c.id for c in self.index.house.electrical.circuits]

    @cached_property
    def lighting_circuit_by_level(self) -> dict[int, str]:
        out: dict[int, str] = {}
        for c in self.index.house.electrical.circuits:
            if c.kind is CircuitKind.lighting:
                for room in c.serves_room_ids:
                    out.setdefault(self.index.room_level(room), c.id)
        return out

    def lighting_circuit_for_room(self, room_id: str) -> str:
        level = self.index.room_level(room_id)
        by_level = self.lighting_circuit_by_level
        return by_level.get(level) or by_level[min(by_level)]

    @cached_property
    def socket_circuit_by_room(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for group in self.index.house.electrical.socket_groups:
            out.setdefault(group.room_id, group.circuit_id)
        return out

    @cached_property
    def yard_socket_circuit(self) -> str:
        for room in self.index.house.rooms:
            if room.kind is RoomKind.utility and room.id in self.socket_circuit_by_room:
                return self.socket_circuit_by_room[room.id]
        return next(iter(self.socket_circuit_by_room.values()))

    @cached_property
    def outdoor_circuits(self) -> set[str]:
        """Circuits exposed to weather (outdoor socket groups or rooms of kind outdoor/utility)."""
        out = {g.circuit_id for g in self.index.house.electrical.socket_groups if g.outdoor}
        for room in self.index.house.rooms:
            if (
                room.kind in (RoomKind.outdoor, RoomKind.utility)
                and room.id in self.socket_circuit_by_room
            ):
                out.add(self.socket_circuit_by_room[room.id])
        return out

    @cached_property
    def rccbs(self) -> list[str]:
        return list(self.index.ids(ElementKind.rccb))

    @cached_property
    def rccb_circuits(self) -> dict[str, tuple[str, ...]]:
        return {d: self.index.circuits_under(d) for d in self.rccbs}

    @cached_property
    def device_circuits(self) -> dict[str, tuple[str, ...]]:
        """Circuits de-energised when a protective device opens."""
        out = dict(self.rccb_circuits)
        for c in self.index.house.electrical.circuits:
            out[c.mcb_id] = (c.id,)
        return out

    def mcb_rating(self, circuit_id: str) -> float:
        return float(self.index.rated_current_a(circuit_id))

    def residual_rating_ma(self, device_id: str) -> float:
        rated = self.index.device(device_id).rated_residual_ma
        assert rated is not None
        return float(rated)

    # ------------------------------------------------------------------ misc

    def nameplate(self, appliance_id: str) -> dict[str, Any]:
        plate = self.appliances[appliance_id].nameplate
        return {} if plate is None else plate.model_dump(exclude_none=True)

    def rooms_by_level(self) -> dict[int, list[str]]:
        out: dict[int, list[str]] = defaultdict(list)
        for room in self.index.house.rooms:
            out[room.level].append(room.id)
        return dict(out)


def sample(cal: Calibration, path: str, generator: np.random.Generator) -> float:
    """One per-event draw."""
    return float(cal[path].sample(generator))
