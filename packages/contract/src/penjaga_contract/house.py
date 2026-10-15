"""Typed, indexed view of a validated house record.

``HouseIndex`` answers the structural questions every module asks of the contract: what kind
of element an ID is, which rooms it is in, which fixtures a pipe or valve feeds, whether a
fixture is fed from the main or the roof tank, and which RCCBs protect a circuit. Everything
is derived from the topology in house.json; nothing is stored twice.

Construct it only from a house that passed ``validate_house_document``;
``penjaga_contract.validate.load_index()`` does that for the canonical record.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from functools import cached_property
from typing import Any, Literal

from penjaga_contract.core import (
    Appliance,
    Circuit,
    Fixture,
    House,
    Pipe,
    ProtectiveDevice,
    Room,
    Sensor,
    SocketGroup,
    Tank,
    Valve,
    ValveKind,
    WaterMeter,
)


class ElementKind(StrEnum):
    """Kinds of referenceable elements and their ID prefixes (see ``ID_PREFIX``)."""

    room = "room"
    wall = "wall"
    ceiling = "ceiling"
    duct = "duct"
    meter = "meter"
    tank = "tank"
    pipe = "pipe"
    valve = "valve"
    fixture = "fixture"
    board = "board"
    rccb = "rccb"
    mcb = "mcb"
    circuit = "circuit"
    socket_group = "socket_group"
    appliance = "appliance"
    sensor = "sensor"


ID_PREFIX: dict[ElementKind, str] = {
    ElementKind.room: "room_",
    ElementKind.wall: "wall_",
    ElementKind.ceiling: "ceil_",
    ElementKind.duct: "duct_",
    ElementKind.meter: "meter_",
    ElementKind.tank: "tank_",
    ElementKind.pipe: "pipe_",
    ElementKind.valve: "valve_",
    ElementKind.fixture: "fix_",
    ElementKind.board: "db_",
    ElementKind.rccb: "rcd_",
    ElementKind.mcb: "mcb_",
    ElementKind.circuit: "circ_",
    ElementKind.socket_group: "sock_",
    ElementKind.appliance: "app_",
    ElementKind.sensor: "sensor_",
}

SupplySource = Literal["main", "tank"]


@dataclass(frozen=True)
class IndexedElement:
    """An element of the house with its kind."""

    id: str
    kind: ElementKind
    model: Any


class HouseIndex:
    """Read-only index over a validated ``House``."""

    def __init__(self, house: House) -> None:
        self.house = house
        self._elements: dict[str, IndexedElement] = {}
        for kind, models in self._element_groups():
            for model in models:
                if model.id in self._elements:
                    raise ValueError(f"duplicate element id {model.id!r}")
                self._elements[model.id] = IndexedElement(model.id, kind, model)

    def _element_groups(self) -> Iterable[tuple[ElementKind, Iterable[Any]]]:
        h = self.house
        yield ElementKind.room, h.rooms
        for wall in h.walls:
            yield ElementKind(wall.kind.value), (wall,)
        yield ElementKind.meter, h.water.meters
        yield ElementKind.tank, h.water.tanks
        yield ElementKind.pipe, h.water.pipes
        yield ElementKind.valve, h.water.valves
        yield ElementKind.fixture, h.water.fixtures
        yield ElementKind.board, h.electrical.boards
        for device in h.electrical.protective_devices:
            yield ElementKind(device.kind.value), (device,)
        yield ElementKind.circuit, h.electrical.circuits
        yield ElementKind.socket_group, h.electrical.socket_groups
        yield ElementKind.appliance, h.appliances
        yield ElementKind.sensor, h.sensors

    # ------------------------------------------------------------------ generic lookups

    @property
    def house_id(self) -> str:
        return self.house.house_id

    def ids(self, kind: ElementKind | None = None) -> tuple[str, ...]:
        """All element IDs (of ``kind``, if given) in contract order."""
        return tuple(e.id for e in self._elements.values() if kind is None or e.kind == kind)

    def exists(self, object_id: str) -> bool:
        return object_id in self._elements

    def element(self, object_id: str) -> IndexedElement:
        try:
            return self._elements[object_id]
        except KeyError:
            raise KeyError(f"unknown contract id {object_id!r}") from None

    def kind_of(self, object_id: str) -> ElementKind:
        return self.element(object_id).kind

    def get(self, object_id: str, expected: type[Any]) -> Any:
        """Model of ``object_id``, checked to be an instance of ``expected``."""
        model = self.element(object_id).model
        if not isinstance(model, expected):
            raise TypeError(f"{object_id!r} is a {type(model).__name__}, not {expected.__name__}")
        return model

    def rooms_of(self, object_id: str) -> tuple[str, ...]:
        """Rooms an element is in (or serves, for circuits). Empty for the weather feed."""
        e = self.element(object_id)
        m = e.model
        if e.kind is ElementKind.room:
            return (m.id,)
        if e.kind in (ElementKind.wall, ElementKind.ceiling, ElementKind.duct, ElementKind.pipe):
            return tuple(m.room_ids)
        if e.kind is ElementKind.circuit:
            return tuple(m.serves_room_ids)
        if e.kind in (ElementKind.rccb, ElementKind.mcb):
            return self.rooms_of(m.board_id)
        if e.kind is ElementKind.sensor:
            return () if m.mounted_on_id is None else self.rooms_of(m.mounted_on_id)
        return (m.room_id,)

    def room_level(self, room_id: str) -> int:
        room: Room = self.get(room_id, Room)
        return room.level

    # ------------------------------------------------------------------ water

    @cached_property
    def meter_id(self) -> str:
        (meter,) = self.house.water.meters
        return meter.id

    @cached_property
    def _water_children(self) -> dict[str, tuple[str, ...]]:
        children: dict[str, list[str]] = {}
        for pipe in self.house.water.pipes:
            children.setdefault(pipe.upstream_id, []).append(pipe.id)
        for tank in self.house.water.tanks:
            children.setdefault(tank.inlet_pipe_id, []).append(tank.id)
        return {k: tuple(v) for k, v in children.items()}

    def water_children(self, element_id: str) -> tuple[str, ...]:
        """Pipes and tanks directly downstream of a meter, pipe or tank."""
        return self._water_children.get(element_id, ())

    def water_upstream(self, element_id: str) -> str | None:
        """The element immediately upstream of a pipe or tank (None for the meter)."""
        m = self.element(element_id).model
        if isinstance(m, Pipe):
            return m.upstream_id
        if isinstance(m, Tank):
            return m.inlet_pipe_id
        if isinstance(m, WaterMeter):
            return None
        raise TypeError(f"{element_id!r} is not part of the water network")

    def upstream_path(self, element_id: str) -> tuple[str, ...]:
        """From ``element_id`` (a pipe, tank, fixture or valve) up to and including the meter."""
        e = self.element(element_id)
        start = (
            e.model.pipe_id if e.kind in (ElementKind.fixture, ElementKind.valve) else element_id
        )
        path = [element_id] if start != element_id else []
        node: str | None = start
        while node is not None:
            if node in path:
                raise ValueError(f"cycle in water network at {node!r}")
            path.append(node)
            node = self.water_upstream(node)
        return tuple(path)

    def supply_source(self, fixture_id: str) -> SupplySource:
        """'tank' if the fixture is fed through the roof tank, else 'main'."""
        fixture: Fixture = self.get(fixture_id, Fixture)
        path = self.upstream_path(fixture.id)
        return "tank" if any(self.kind_of(n) is ElementKind.tank for n in path) else "main"

    def fixtures_on_pipe(self, pipe_id: str) -> tuple[str, ...]:
        return tuple(f.id for f in self.house.water.fixtures if f.pipe_id == pipe_id)

    def network_downstream(self, element_id: str, *, through_tanks: bool = True) -> tuple[str, ...]:
        """Pipes and tanks downstream of ``element_id`` (inclusive), depth-first in contract order.

        With ``through_tanks=False`` the walk stops at tanks (the tank itself is included,
        what it feeds is not).
        """
        out: list[str] = []
        stack = [element_id]
        while stack:
            node = stack.pop()
            out.append(node)
            if not through_tanks and node != element_id and self.kind_of(node) is ElementKind.tank:
                continue
            stack.extend(reversed(self.water_children(node)))
        return tuple(out)

    def fixtures_fed_by(self, element_id: str, *, through_tanks: bool = True) -> tuple[str, ...]:
        """Fixtures downstream of a meter, pipe or tank (optionally not crossing tanks)."""
        nodes = self.network_downstream(element_id, through_tanks=through_tanks)
        return tuple(
            f
            for n in nodes
            if self.kind_of(n) is ElementKind.pipe
            for f in self.fixtures_on_pipe(n)
        )

    def isolated_by(self, valve_id: str) -> tuple[str, ...]:
        """Fixtures that stop receiving water as soon as ``valve_id`` is closed.

        An angle valve isolates only the fixture that names it. Any other valve isolates its
        pipe and what is downstream of it, but not across a tank: fixtures behind the roof
        tank keep drawing stored water when the supply side is closed. A float valve therefore
        isolates no fixture; it only stops the tank refilling.
        """
        valve: Valve = self.get(valve_id, Valve)
        if valve.kind is ValveKind.angle:
            return tuple(
                f.id for f in self.house.water.fixtures if f.isolation_valve_id == valve.id
            )
        if valve.kind is ValveKind.float:
            return ()
        return self.fixtures_fed_by(valve.pipe_id, through_tanks=False)

    def stops_tank_refill(self, valve_id: str) -> bool:
        """Whether closing ``valve_id`` cuts the supply that refills a tank."""
        valve: Valve = self.get(valve_id, Valve)
        if valve.kind is ValveKind.angle:
            return False
        nodes = self.network_downstream(valve.pipe_id, through_tanks=False)
        return any(self.kind_of(n) is ElementKind.tank for n in nodes)

    @cached_property
    def tank_fed_fixtures(self) -> tuple[str, ...]:
        return tuple(f.id for f in self.house.water.fixtures if self.supply_source(f.id) == "tank")

    @cached_property
    def main_fed_fixtures(self) -> tuple[str, ...]:
        return tuple(f.id for f in self.house.water.fixtures if self.supply_source(f.id) == "main")

    # ------------------------------------------------------------------ electrical

    def device(self, device_id: str) -> ProtectiveDevice:
        model: ProtectiveDevice = self.get(device_id, ProtectiveDevice)
        return model

    def circuit(self, circuit_id: str) -> Circuit:
        model: Circuit = self.get(circuit_id, Circuit)
        return model

    def protection_chain(self, circuit_id: str) -> tuple[str, ...]:
        """Protective devices from the circuit's MCB up to the main device."""
        chain: list[str] = []
        node: str | None = self.circuit(circuit_id).mcb_id
        while node is not None:
            if node in chain:
                raise ValueError(f"cycle in protection chain at {node!r}")
            chain.append(node)
            node = self.device(node).upstream_device_id
        return tuple(chain)

    def rccb_chain(self, circuit_id: str) -> tuple[str, ...]:
        """RCCBs protecting the circuit, nearest first."""
        return tuple(
            d for d in self.protection_chain(circuit_id) if self.kind_of(d) is ElementKind.rccb
        )

    def effective_residual_ma(self, circuit_id: str) -> float | None:
        """Most sensitive rated residual operating current protecting the circuit (None if none)."""
        ratings = [self.device(d).rated_residual_ma for d in self.rccb_chain(circuit_id)]
        values = [r for r in ratings if r is not None]
        return min(values) if values else None

    def rated_current_a(self, circuit_id: str) -> float:
        """Rated current of the circuit's MCB."""
        return self.device(self.circuit(circuit_id).mcb_id).rated_current_a

    def circuits_under(self, device_id: str) -> tuple[str, ...]:
        """Circuits protected (directly or through downstream devices) by ``device_id``."""
        self.device(device_id)
        return tuple(
            c.id for c in self.house.electrical.circuits if device_id in self.protection_chain(c.id)
        )

    def circuit_of_appliance(self, appliance_id: str) -> str | None:
        """Circuit feeding an appliance (dedicated or via its socket group); None for gas items."""
        app: Appliance = self.get(appliance_id, Appliance)
        if app.circuit_id is not None:
            return app.circuit_id
        if app.socket_group_id is not None:
            group: SocketGroup = self.get(app.socket_group_id, SocketGroup)
            return group.circuit_id
        return None

    def appliances_on_circuit(self, circuit_id: str) -> tuple[str, ...]:
        self.circuit(circuit_id)
        return tuple(
            a.id for a in self.house.appliances if self.circuit_of_appliance(a.id) == circuit_id
        )

    def sensors_of_kind(self, kind: str) -> tuple[Sensor, ...]:
        return tuple(s for s in self.house.sensors if s.kind.value == kind)
