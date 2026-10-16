"""Semantic validation of contract documents, beyond what JSON Schema can express.

Every check reports an ``Issue`` with a stable ``code`` (tests and tooling match on codes).
Documents are first validated against their JSON Schema; semantic checks only run on
schema-valid documents, so they can rely on the structure.

Regulatory checks cite Electricity Regulations 1994 reg. 36 (docs/DATA_SOURCES.md DS-001):
RCD protection of every final circuit at <= 10 mA where the floor is likely to be wet, <= 30 mA
where hand-held equipment is likely to be used (socket-outlet circuits), and <= 100 mA otherwise.
"""

from __future__ import annotations

import re
import zoneinfo
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from functools import cache
from typing import Any, Literal

from penjaga_contract import schema_registry
from penjaga_contract.catalogue import build_catalogue
from penjaga_contract.core import House, StreamCatalogue, StreamDef
from penjaga_contract.house import ID_PREFIX, HouseIndex
from penjaga_contract.resources import load_house_document, load_streams_document

IssueSeverity = Literal["error", "warning"]

PREFIX_BY_KIND: dict[str, str] = {kind.value: prefix for kind, prefix in ID_PREFIX.items()}
STRUCTURE_KINDS = frozenset({"wall", "ceiling", "duct"})
GAS_APPLIANCES = frozenset({"gas_hob", "lpg_cylinder", "lpg_hose"})
SENSOR_MOUNT_KIND: dict[str, str | None] = {
    "water_meter": "meter",
    "pressure_sensor": "pipe",
    "tank_level_sensor": "tank",
    "db_monitor": "board",
    "smart_plug": "appliance",
    "weather_feed": None,
}
SENSOR_COUNT: dict[str, tuple[int, int | None]] = {
    "water_meter": (1, 1),
    "db_monitor": (1, 1),
    "pressure_sensor": (0, 1),
    "tank_level_sensor": (0, 1),
    "weather_feed": (0, 1),
    "smart_plug": (0, None),
}
APPLIANCE_FIXTURE_KIND = {
    "water_heater": "shower",
    "washing_machine": "washing_machine_tap",
    "water_purifier": "water_purifier",
}
REG36_WET_MAX_MA = 10.0
REG36_HAND_HELD_MAX_MA = 30.0
REG36_GENERAL_MAX_MA = 100.0
PROBABILITY_TOLERANCE = 1e-9
_QUANTITY_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class Issue:
    severity: IssueSeverity
    code: str
    path: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity}] {self.code} at {self.path}: {self.message}"


class ContractError(ValueError):
    """Raised when a document has validation errors."""

    def __init__(self, what: str, report: Report) -> None:
        self.report = report
        lines = "\n".join(f"  {issue}" for issue in report.errors)
        super().__init__(f"{what} failed validation with {len(report.errors)} error(s):\n{lines}")


@dataclass
class Report:
    issues: list[Issue] = field(default_factory=list)

    def error(self, code: str, path: str, message: str) -> None:
        self.issues.append(Issue("error", code, path, message))

    def warning(self, code: str, path: str, message: str) -> None:
        self.issues.append(Issue("warning", code, path, message))

    def extend(self, other: Report) -> None:
        self.issues.extend(other.issues)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def codes(self) -> set[str]:
        return {i.code for i in self.issues}

    def raise_for_errors(self, what: str) -> None:
        if self.errors:
            raise ContractError(what, self)


def _schema(report: Report, namespace: str, name: str, doc: Any) -> bool:
    messages = schema_registry.error_messages(namespace, name, doc)
    for message in messages:
        report.error("schema.invalid", f"{namespace}/{name}", message)
    return not messages


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.upper())


# ====================================================================== house


@dataclass
class _HouseCtx:
    report: Report
    kinds: dict[str, str]
    rooms: dict[str, dict[str, Any]]
    pipes: dict[str, dict[str, Any]]
    tanks: dict[str, dict[str, Any]]
    valves: dict[str, dict[str, Any]]
    fixtures: dict[str, dict[str, Any]]
    devices: dict[str, dict[str, Any]]
    circuits: dict[str, dict[str, Any]]
    sockets: dict[str, dict[str, Any]]
    appliances: dict[str, dict[str, Any]]

    def ref(self, path: str, target: str | None, allowed: Iterable[str]) -> bool:
        allowed = frozenset(allowed)
        if target is None:
            return False
        kind = self.kinds.get(target)
        if kind is None:
            self.report.error("ref.missing", path, f"{target!r} does not exist")
            return False
        if kind not in allowed:
            self.report.error(
                "ref.kind", path, f"{target!r} is a {kind}, expected {sorted(allowed)}"
            )
            return False
        return True


def _collect_elements(doc: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    """(id, kind, json-path) of every referenceable element."""
    out: list[tuple[str, str, str]] = []
    out += [(r["id"], "room", f"$.rooms[{i}]") for i, r in enumerate(doc["rooms"])]
    out += [(w["id"], w["kind"], f"$.walls[{i}]") for i, w in enumerate(doc["walls"])]
    water = doc["water"]
    for key, kind in (
        ("meters", "meter"),
        ("tanks", "tank"),
        ("pipes", "pipe"),
        ("valves", "valve"),
        ("fixtures", "fixture"),
    ):
        out += [(e["id"], kind, f"$.water.{key}[{i}]") for i, e in enumerate(water[key])]
    elec = doc["electrical"]
    out += [(b["id"], "board", f"$.electrical.boards[{i}]") for i, b in enumerate(elec["boards"])]
    out += [
        (d["id"], d["kind"], f"$.electrical.protective_devices[{i}]")
        for i, d in enumerate(elec["protective_devices"])
    ]
    out += [
        (c["id"], "circuit", f"$.electrical.circuits[{i}]") for i, c in enumerate(elec["circuits"])
    ]
    out += [
        (s["id"], "socket_group", f"$.electrical.socket_groups[{i}]")
        for i, s in enumerate(elec["socket_groups"])
    ]
    out += [(a["id"], "appliance", f"$.appliances[{i}]") for i, a in enumerate(doc["appliances"])]
    out += [(s["id"], "sensor", f"$.sensors[{i}]") for i, s in enumerate(doc["sensors"])]
    return out


def _check_ids(report: Report, elements: list[tuple[str, str, str]]) -> dict[str, str]:
    counts = Counter(eid for eid, _, _ in elements)
    kinds: dict[str, str] = {}
    for eid, kind, path in elements:
        if counts[eid] > 1:
            report.error("id.duplicate", path, f"id {eid!r} is used {counts[eid]} times")
        prefix = PREFIX_BY_KIND[kind]
        if not eid.startswith(prefix):
            report.error("id.prefix", path, f"{kind} id {eid!r} must start with {prefix!r}")
        kinds.setdefault(eid, kind)
    return kinds


def _check_structure(ctx: _HouseCtx, doc: Mapping[str, Any]) -> None:
    r = ctx.report
    levels = [lv["index"] for lv in doc["levels"]]
    for index, count in Counter(levels).items():
        if count > 1:
            r.error("level.duplicate", "$.levels", f"level index {index} defined {count} times")
    level_set = set(levels)
    for i, room in enumerate(doc["rooms"]):
        if room["level"] not in level_set:
            r.error("level.unknown", f"$.rooms[{i}].level", f"level {room['level']} is not defined")
        if room["kind"] == "bathroom" and not room["wet_location"]:
            r.error(
                "room.bathroom_dry", f"$.rooms[{i}]", "bathrooms are wet locations (reg. 36(2))"
            )
    for i, wall in enumerate(doc["walls"]):
        path = f"$.walls[{i}]"
        for level in wall["levels"]:
            if level not in level_set:
                r.error("level.unknown", f"{path}.levels", f"level {level} is not defined")
        for j, room_id in enumerate(wall["room_ids"]):
            if ctx.ref(f"{path}.room_ids[{j}]", room_id, {"room"}):
                room_level = ctx.rooms[room_id]["level"]
                if room_level not in wall["levels"]:
                    r.error(
                        "wall.level_mismatch",
                        path,
                        f"room {room_id!r} (level {room_level}) not on levels {wall['levels']}",
                    )
    try:
        zoneinfo.ZoneInfo(doc["location"]["timezone"])
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        r.error(
            "location.timezone",
            "$.location.timezone",
            f"unknown IANA time zone {doc['location']['timezone']!r}",
        )


def _check_tanks(ctx: _HouseCtx, water: Mapping[str, Any]) -> dict[str, str]:
    """Validate tanks; return {inlet pipe id: tank id}."""
    r = ctx.report
    inlet_of: dict[str, str] = {}
    for i, tank in enumerate(water["tanks"]):
        path = f"$.water.tanks[{i}]"
        ctx.ref(f"{path}.room_id", tank["room_id"], {"room"})
        inlet = tank["inlet_pipe_id"]
        if not ctx.ref(f"{path}.inlet_pipe_id", inlet, {"pipe"}):
            continue
        if ctx.pipes[inlet]["kind"] != "rising_main":
            r.error("water.tank_inlet", path, f"tank inlet {inlet!r} must be a rising_main")
        if inlet in inlet_of:
            r.error("water.tank_inlet", path, f"{inlet!r} already feeds {inlet_of[inlet]!r}")
        inlet_of[inlet] = tank["id"]
    return inlet_of


def _check_pipes(ctx: _HouseCtx, water: Mapping[str, Any], inlet_of: Mapping[str, str]) -> None:
    r = ctx.report
    for i, pipe in enumerate(water["pipes"]):
        path = f"$.water.pipes[{i}]"
        ctx.ref(f"{path}.upstream_id", pipe["upstream_id"], {"meter", "tank", "pipe"})
        for j, room_id in enumerate(pipe["room_ids"]):
            ctx.ref(f"{path}.room_ids[{j}]", room_id, {"room"})
        for j, wall_id in enumerate(pipe["concealed_in"]):
            ctx.ref(f"{path}.concealed_in[{j}]", wall_id, STRUCTURE_KINDS)
        if (pipe["routing"] == "concealed") != bool(pipe["concealed_in"]):
            r.error(
                "water.concealed_routing",
                path,
                "routing 'concealed' requires concealed_in, and only concealed pipes may name "
                "concealing elements",
            )
        upstream_kind = ctx.kinds.get(pipe["upstream_id"])
        if pipe["kind"] == "overflow" and upstream_kind != "tank":
            r.error("water.overflow", path, "an overflow pipe must start at a tank")
        if pipe["kind"] == "downfeed" and upstream_kind not in {"tank", "pipe"}:
            r.error("water.downfeed", path, "a down-feed starts at a tank or another down-feed")
        if pipe["id"] in inlet_of and upstream_kind == "tank":
            r.error("water.cycle", path, "a tank inlet cannot be fed from a tank")
    for tank in water["tanks"]:
        outlets = [p for p in water["pipes"] if p["upstream_id"] == tank["id"]]
        if not any(p["kind"] == "overflow" for p in outlets):
            r.warning("water.no_overflow", tank["id"], "tank has no overflow pipe")


def _check_water_paths(ctx: _HouseCtx) -> None:
    """Every pipe and tank must reach the meter without cycles."""
    for eid, kind in ctx.kinds.items():
        if kind not in {"pipe", "tank"}:
            continue
        seen: list[str] = []
        node: str = eid
        while True:
            if node in seen:
                loop = " -> ".join([*seen, node])
                ctx.report.error("water.cycle", eid, f"water path loops: {loop}")
                break
            seen.append(node)
            node_kind = ctx.kinds.get(node)
            if node_kind == "meter":
                break
            if node_kind == "pipe":
                node = ctx.pipes[node]["upstream_id"]
            elif node_kind == "tank":
                node = ctx.tanks[node]["inlet_pipe_id"]
            else:
                msg = f"{eid!r} does not connect to the meter (stops at {node!r})"
                ctx.report.error("water.unreachable", eid, msg)
                break


def _check_fixtures_and_valves(
    ctx: _HouseCtx, water: Mapping[str, Any], inlet_of: Mapping[str, str]
) -> None:
    r = ctx.report
    angle_users: Counter[str] = Counter()
    for i, fixture in enumerate(water["fixtures"]):
        path = f"$.water.fixtures[{i}]"
        ctx.ref(f"{path}.room_id", fixture["room_id"], {"room"})
        pipe_ok = ctx.ref(f"{path}.pipe_id", fixture["pipe_id"], {"pipe"})
        if pipe_ok and ctx.pipes[fixture["pipe_id"]]["kind"] == "overflow":
            r.error("water.fixture_on_overflow", path, "fixtures cannot draw from an overflow pipe")
        valve_id = fixture.get("isolation_valve_id")
        if valve_id is None or not ctx.ref(f"{path}.isolation_valve_id", valve_id, {"valve"}):
            continue
        valve = ctx.valves[valve_id]
        angle_users[valve_id] += 1
        if valve["kind"] != "angle":
            r.error(
                "water.angle_valve", path, f"isolation valve {valve_id!r} must be an angle valve"
            )
        if valve["pipe_id"] != fixture["pipe_id"]:
            r.error("water.angle_valve", path, f"isolation valve {valve_id!r} is on another pipe")
    for i, valve in enumerate(water["valves"]):
        path = f"$.water.valves[{i}]"
        ctx.ref(f"{path}.room_id", valve["room_id"], {"room"})
        ctx.ref(f"{path}.pipe_id", valve["pipe_id"], {"pipe"})
        if valve["kind"] == "float" and valve["pipe_id"] not in inlet_of:
            r.error("water.float_valve", path, "a float valve must sit on a tank inlet pipe")
        users = angle_users[valve["id"]]
        if valve["kind"] == "angle" and users != 1:
            r.error("water.angle_valve", path, f"angle valve isolates {users} fixtures, not 1")


def _check_water(ctx: _HouseCtx, doc: Mapping[str, Any]) -> None:
    water = doc["water"]
    if len(water["meters"]) != 1:
        n = len(water["meters"])
        ctx.report.error("water.meter_count", "$.water.meters", f"one meter required, found {n}")
    for i, meter in enumerate(water["meters"]):
        ctx.ref(f"$.water.meters[{i}].room_id", meter["room_id"], {"room"})
    inlet_of = _check_tanks(ctx, water)
    _check_pipes(ctx, water, inlet_of)
    _check_water_paths(ctx)
    _check_fixtures_and_valves(ctx, water, inlet_of)


def _protection_chain(ctx: _HouseCtx, start: str) -> list[str] | None:
    chain: list[str] = []
    node: str | None = start
    while node is not None:
        if node in chain or node not in ctx.devices:
            return None
        chain.append(node)
        node = ctx.devices[node]["upstream_device_id"]
    return chain


def _check_electrical(ctx: _HouseCtx, doc: Mapping[str, Any]) -> None:
    r = ctx.report
    elec = doc["electrical"]
    for i, board in enumerate(elec["boards"]):
        ctx.ref(f"$.electrical.boards[{i}].room_id", board["room_id"], {"room"})
    roots = [d["id"] for d in elec["protective_devices"] if d["upstream_device_id"] is None]
    if len(roots) != 1:
        r.error(
            "elec.root",
            "$.electrical.protective_devices",
            f"exactly one main device (no upstream) required, found {roots}",
        )
    for i, device in enumerate(elec["protective_devices"]):
        path = f"$.electrical.protective_devices[{i}]"
        ctx.ref(f"{path}.board_id", device["board_id"], {"board"})
        upstream = device["upstream_device_id"]
        if upstream is not None and ctx.ref(
            f"{path}.upstream_device_id", upstream, {"rccb", "mcb"}
        ):
            if ctx.kinds[upstream] != "rccb":
                r.error(
                    "elec.upstream_not_rccb",
                    path,
                    f"{upstream!r} is an MCB; only RCCBs feed other devices",
                )
            elif device["rated_current_a"] > ctx.devices[upstream]["rated_current_a"]:
                r.error(
                    "elec.rating_exceeds_upstream",
                    path,
                    f"{device['rated_current_a']} A exceeds upstream {upstream!r} rating",
                )
        if _protection_chain(ctx, device["id"]) is None:
            r.error("elec.cycle", path, "protection chain loops or references an unknown device")
    mcb_users: Counter[str] = Counter()
    for i, circuit in enumerate(elec["circuits"]):
        path = f"$.electrical.circuits[{i}]"
        if ctx.ref(f"{path}.mcb_id", circuit["mcb_id"], {"mcb"}):
            mcb_users[circuit["mcb_id"]] += 1
        for j, room_id in enumerate(circuit["serves_room_ids"]):
            ctx.ref(f"{path}.serves_room_ids[{j}]", room_id, {"room"})
    for device in elec["protective_devices"]:
        if device["kind"] == "mcb" and mcb_users[device["id"]] != 1:
            r.error(
                "elec.mcb_usage",
                device["id"],
                f"an MCB protects one final circuit, not {mcb_users[device['id']]}",
            )
        if device["kind"] == "rccb" and not any(
            d["upstream_device_id"] == device["id"] for d in elec["protective_devices"]
        ):
            r.warning("elec.rccb_unused", device["id"], "RCCB protects no device")
    for i, group in enumerate(elec["socket_groups"]):
        path = f"$.electrical.socket_groups[{i}]"
        ctx.ref(f"{path}.room_id", group["room_id"], {"room"})
        if ctx.ref(f"{path}.circuit_id", group["circuit_id"], {"circuit"}):
            circuit = ctx.circuits[group["circuit_id"]]
            if circuit["kind"] != "socket":
                r.error(
                    "elec.socket_circuit_kind", path, f"socket group on a {circuit['kind']} circuit"
                )
            if group["room_id"] not in circuit["serves_room_ids"]:
                r.error(
                    "elec.socket_room",
                    path,
                    f"circuit {circuit['id']!r} does not serve room {group['room_id']!r}",
                )


def _check_appliance_fixture(ctx: _HouseCtx, path: str, app: Mapping[str, Any]) -> None:
    r = ctx.report
    fixture_id = app.get("fixture_id")
    expected_fixture = APPLIANCE_FIXTURE_KIND.get(app["kind"])
    if fixture_id is not None and ctx.ref(f"{path}.fixture_id", fixture_id, {"fixture"}):
        fixture = ctx.fixtures[fixture_id]
        if expected_fixture is None or fixture["kind"] != expected_fixture:
            r.error("app.fixture", path, f"{app['kind']} cannot serve a {fixture['kind']} fixture")
        if fixture["room_id"] != app["room_id"]:
            r.error("app.fixture", path, f"fixture {fixture_id!r} is in another room")
    if app["kind"] == "water_heater":
        if fixture_id is None:
            r.error("app.fixture", path, "a water heater must name the shower it serves")
        if app.get("circuit_id") is None:
            r.error("app.dedicated_kind", path, "a water heater needs a dedicated circuit")
    if app["kind"] == "water_purifier" and fixture_id is None:
        r.error("app.fixture", path, "a water purifier must name the purifier fixture it powers")


NAMEPLATE_REQUIRED: dict[str, tuple[str, ...]] = {
    "air_conditioner": ("rated_input_w", "nominal_hp", "cooling_capacity_btu_h", "inverter"),
    "water_heater": ("rated_input_w", "rated_voltage_v"),
}


def _check_nameplate(r: Report, path: str, app: Mapping[str, Any]) -> None:
    plate = app.get("nameplate") or {}
    missing = [k for k in NAMEPLATE_REQUIRED.get(app["kind"], ()) if k not in plate]
    if missing:
        r.error("app.nameplate", path, f"{app['kind']} nameplate missing {missing}")


def _check_appliances(ctx: _HouseCtx, doc: Mapping[str, Any]) -> None:
    r = ctx.report
    dedicated_users: Counter[str] = Counter()
    for i, app in enumerate(doc["appliances"]):
        path = f"$.appliances[{i}]"
        ctx.ref(f"{path}.room_id", app["room_id"], {"room"})
        circuit_id, socket_id = app.get("circuit_id"), app.get("socket_group_id")
        is_gas = app["kind"] in GAS_APPLIANCES
        if is_gas and (circuit_id or socket_id):
            r.error("app.supply", path, "gas items have no electrical supply")
        if not is_gas and (circuit_id is None) == (socket_id is None):
            r.error(
                "app.supply",
                path,
                "electric appliances need exactly one of circuit_id or socket_group_id",
            )
        if circuit_id is not None and ctx.ref(f"{path}.circuit_id", circuit_id, {"circuit"}):
            if ctx.circuits[circuit_id]["kind"] != "dedicated":
                r.error(
                    "app.dedicated_kind",
                    path,
                    f"circuit_id must name a dedicated circuit, not {circuit_id!r}",
                )
            dedicated_users[circuit_id] += 1
            if app["room_id"] not in ctx.circuits[circuit_id]["serves_room_ids"]:
                r.error(
                    "app.circuit_room",
                    path,
                    f"circuit {circuit_id!r} does not serve room {app['room_id']!r}",
                )
        socket_ok = socket_id is not None and ctx.ref(
            f"{path}.socket_group_id", socket_id, {"socket_group"}
        )
        if socket_ok and ctx.sockets[socket_id]["room_id"] != app["room_id"]:
            r.error("app.socket_room", path, f"socket group {socket_id!r} is in another room")
        _check_appliance_fixture(ctx, path, app)
        _check_nameplate(r, path, app)
    for cid, circuit in ctx.circuits.items():
        if circuit["kind"] == "dedicated" and dedicated_users[cid] != 1:
            r.error(
                "elec.dedicated_usage",
                cid,
                f"a dedicated circuit feeds one appliance, not {dedicated_users[cid]}",
            )
        if circuit["kind"] != "socket" and any(
            s["circuit_id"] == cid for s in ctx.sockets.values()
        ):
            r.error("elec.socket_circuit_kind", cid, "only socket circuits have socket groups")


def _check_sensors(ctx: _HouseCtx, doc: Mapping[str, Any]) -> None:
    r = ctx.report
    counts = Counter(s["kind"] for s in doc["sensors"])
    for kind, (low, high) in SENSOR_COUNT.items():
        n = counts.get(kind, 0)
        if n < low or (high is not None and n > high):
            r.error(
                "sensor.count",
                "$.sensors",
                f"{kind}: expected {low}..{high if high is not None else 'n'}, found {n}",
            )
    plugged: Counter[str] = Counter()
    for i, sensor in enumerate(doc["sensors"]):
        path = f"$.sensors[{i}]"
        expected = SENSOR_MOUNT_KIND[sensor["kind"]]
        mounted = sensor["mounted_on_id"]
        if expected is None:
            if mounted is not None:
                r.error("sensor.mount_kind", path, f"{sensor['kind']} is not mounted on an element")
            continue
        if mounted is None:
            r.error("sensor.mount_kind", path, f"{sensor['kind']} must be mounted on a {expected}")
            continue
        if not ctx.ref(f"{path}.mounted_on_id", mounted, {expected}):
            continue
        if sensor["kind"] == "smart_plug":
            plugged[mounted] += 1
            if ctx.appliances[mounted].get("socket_group_id") is None:
                r.error(
                    "sensor.mount_kind",
                    path,
                    f"a smart plug needs a plug-in appliance; {mounted!r} has no socket group",
                )
        if sensor["kind"] == "pressure_sensor" and ctx.pipes[mounted]["kind"] != "service":
            r.error("sensor.mount_kind", path, "the inlet pressure sensor sits on the service pipe")
    for app_id, n in plugged.items():
        if n > 1:
            r.error("sensor.count", app_id, f"{n} smart plugs on one appliance")


def _check_reg36(ctx: _HouseCtx) -> None:
    r = ctx.report
    for cid, circuit in ctx.circuits.items():
        chain = (
            _protection_chain(ctx, circuit["mcb_id"]) if circuit["mcb_id"] in ctx.devices else None
        )
        if chain is None:
            continue  # reported by the electrical checks
        ratings = [
            ctx.devices[d]["rated_residual_ma"] for d in chain if ctx.devices[d]["kind"] == "rccb"
        ]
        if not ratings:
            r.error("reg36.no_rcd", cid, "every final circuit needs RCD protection (reg. 36(4))")
            continue
        effective = min(ratings)
        wet_rooms = {rid for rid, room in ctx.rooms.items() if room["wet_location"]}
        served_wet = wet_rooms.intersection(circuit["serves_room_ids"])
        equipment_rooms = {
            a["room_id"] for a in ctx.appliances.values() if a.get("circuit_id") == cid
        }
        equipment_rooms |= {s["room_id"] for s in ctx.sockets.values() if s["circuit_id"] == cid}
        if circuit["kind"] == "lighting":
            if served_wet:
                r.warning(
                    "reg36.lighting_wet_review",
                    cid,
                    f"lighting serves wet locations {sorted(served_wet)} at {effective:g} mA; "
                    "reg. 36(2) may be read to require <= 10 mA - confirm with a competent "
                    "electrician",
                )
        elif (served_wet or equipment_rooms & wet_rooms) and effective > REG36_WET_MAX_MA:
            r.error(
                "reg36.wet_location",
                cid,
                f"wet-location circuit at {effective:g} mA; reg. 36(2) requires <= 10 mA",
            )
        if circuit["kind"] == "socket" and effective > REG36_HAND_HELD_MAX_MA:
            r.error(
                "reg36.hand_held",
                cid,
                f"socket circuit protected at {effective:g} mA; reg. 36(3) requires <= 30 mA",
            )
        if effective > REG36_GENERAL_MAX_MA:
            r.error(
                "reg36.general",
                cid,
                f"circuit protected at {effective:g} mA; reg. 36(4) requires <= 100 mA",
            )


def validate_house_document(doc: Any) -> Report:
    """Schema + semantic + regulatory validation of a house.json document."""
    report = Report()
    if not _schema(report, "core", "house", doc):
        return report
    kinds = _check_ids(report, _collect_elements(doc))
    water, elec = doc["water"], doc["electrical"]

    def by_id(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {e["id"]: e for e in items}

    ctx = _HouseCtx(
        report=report,
        kinds=kinds,
        rooms=by_id(doc["rooms"]),
        pipes=by_id(water["pipes"]),
        tanks=by_id(water["tanks"]),
        valves=by_id(water["valves"]),
        fixtures=by_id(water["fixtures"]),
        devices=by_id(elec["protective_devices"]),
        circuits=by_id(elec["circuits"]),
        sockets=by_id(elec["socket_groups"]),
        appliances=by_id(doc["appliances"]),
    )
    _check_structure(ctx, doc)
    _check_water(ctx, doc)
    _check_electrical(ctx, doc)
    _check_appliances(ctx, doc)
    _check_sensors(ctx, doc)
    if report.ok:
        _check_reg36(ctx)
    return report


# ====================================================================== catalogue


def validate_catalogue_document(doc: Any, house_doc: Mapping[str, Any]) -> Report:
    """Schema validation plus agreement with the catalogue derived from ``house_doc``."""
    report = Report()
    if not _schema(report, "core", "streams", doc):
        return report
    if doc["house_id"] != house_doc["house_id"]:
        report.error("streams.house", "$.house_id", "catalogue belongs to another house")
    ids = Counter(s["id"] for s in doc["streams"])
    for sid, n in ids.items():
        if n > 1:
            report.error("streams.duplicate", sid, f"stream defined {n} times")
    expected = build_catalogue(dict(house_doc))
    if doc != expected:
        actual_ids = [s["id"] for s in doc["streams"]]
        expected_ids = [s["id"] for s in expected["streams"]]
        if actual_ids != expected_ids:
            missing = sorted(set(expected_ids) - set(actual_ids))
            extra = sorted(set(actual_ids) - set(expected_ids))
            detail = f"stream ids differ: missing {missing}, extra {extra}"
        else:
            changed = [
                a["id"] for a, b in zip(doc["streams"], expected["streams"], strict=True) if a != b
            ]
            detail = f"definitions differ for {changed[:5]}"
        report.error(
            "streams.drift",
            "$.streams",
            f"streams.json is stale ({detail}); run packages/contract/tools/generate.py",
        )
    return report


# ====================================================================== runtime documents


@dataclass(frozen=True)
class ContractContext:
    """Validated canonical house + catalogue, with lookups for runtime document validation."""

    house: House
    catalogue: StreamCatalogue
    object_ids: frozenset[str]
    protective_device_ids: frozenset[str]
    streams: Mapping[str, StreamDef]

    @property
    def house_id(self) -> str:
        return self.house.house_id

    def index(self) -> HouseIndex:
        return HouseIndex(self.house)


def load_validated_house() -> House:
    """The canonical house.json, fully validated (raises ``ContractError`` otherwise)."""
    doc = load_house_document()
    validate_house_document(doc).raise_for_errors("house.json")
    return House.model_validate(doc)


def load_index() -> HouseIndex:
    """Index over the canonical, fully validated house."""
    return load_context().index()


@cache
def load_context() -> ContractContext:
    """Validated canonical contract (cached: the files are immutable at runtime)."""
    house_doc = load_house_document()
    validate_house_document(house_doc).raise_for_errors("house.json")
    streams_doc = load_streams_document()
    validate_catalogue_document(streams_doc, house_doc).raise_for_errors("streams.json")
    house = House.model_validate(house_doc)
    catalogue = StreamCatalogue.model_validate(streams_doc)
    return ContractContext(
        house=house,
        catalogue=catalogue,
        object_ids=frozenset(eid for eid, _, _ in _collect_elements(house_doc)),
        protective_device_ids=frozenset(d.id for d in house.electrical.protective_devices),
        streams={s.id: s for s in catalogue.streams},
    )


def validate_finding_document(doc: Any, ctx: ContractContext) -> Report:
    report = Report()
    if not _schema(report, "core", "finding", doc):
        return report
    if doc["house_id"] != ctx.house_id:
        report.error("finding.house", "$.house_id", f"unknown house {doc['house_id']!r}")
    for name in doc["quantities"]:
        if not _QUANTITY_NAME.match(name):
            report.error("finding.quantity_name", f"$.quantities.{name}", "use snake_case names")
    if doc["key"].split(":", 1)[0] != doc["layer"]:
        report.error(
            "finding.key_layer",
            "$.key",
            f"key {doc['key']!r} does not start with layer {doc['layer']}",
        )
    for i, oid in enumerate(doc["object_ids"]):
        if oid not in ctx.object_ids:
            report.error(
                "finding.unknown_object", f"$.object_ids[{i}]", f"{oid!r} is not a contract id"
            )
    cand_ids = [c["object_id"] for c in doc["candidates"]]
    for i, oid in enumerate(cand_ids):
        if oid not in ctx.object_ids:
            report.error(
                "finding.unknown_object",
                f"$.candidates[{i}].object_id",
                f"{oid!r} is not a contract id",
            )
    if len(set(cand_ids)) != len(cand_ids):
        report.error(
            "finding.candidate_duplicate", "$.candidates", "candidate object ids must be unique"
        )
    total = sum(c["p"] for c in doc["candidates"]) + doc["candidates_other_p"]
    if total > 1 + PROBABILITY_TOLERANCE:
        report.error(
            "finding.candidates_sum", "$.candidates", f"probabilities sum to {total:.6f} > 1"
        )
    first, last, updated = _ts(doc["first_seen"]), _ts(doc["last_seen"]), _ts(doc["updated_at"])
    if not first <= last <= updated:
        report.error("finding.time_order", "$", "require first_seen <= last_seen <= updated_at")
    if "cleared_at" in doc and _ts(doc["cleared_at"]) < first:
        report.error("finding.time_order", "$.cleared_at", "cleared before first seen")
    if "onset_estimate" in doc and _ts(doc["onset_estimate"]) > last:
        report.error("finding.time_order", "$.onset_estimate", "onset after last_seen")
    for i, ref in enumerate(doc["series_refs"]):
        if ref["stream"] not in ctx.streams:
            report.error(
                "finding.unknown_stream",
                f"$.series_refs[{i}].stream",
                f"{ref['stream']!r} is not in the catalogue",
            )
        if _ts(ref["window"]["from"]) >= _ts(ref["window"]["to"]):
            report.error(
                "finding.time_order", f"$.series_refs[{i}].window", "window must have from < to"
            )
    if doc["severity_floor"] and doc["source"]["kind"] != "rule":
        report.error(
            "finding.floor_source",
            "$.source.kind",
            "safety-floor findings come from deterministic rules",
        )
    if doc.get("superseded_by") == doc["id"]:
        report.error(
            "finding.superseded_self", "$.superseded_by", "a finding cannot supersede itself"
        )
    return report


def validate_reading_batch_document(doc: Any, ctx: ContractContext) -> Report:
    report = Report()
    if not _schema(report, "core", "reading_batch", doc):
        return report
    if doc["house_id"] != ctx.house_id:
        report.error("batch.house", "$.house_id", f"unknown house {doc['house_id']!r}")
    stream = ctx.streams.get(doc["stream"])
    if stream is None:
        report.error(
            "batch.unknown_stream", "$.stream", f"{doc['stream']!r} is not in the catalogue"
        )
        return report
    if stream.kind.value != "sampled":
        report.error("batch.not_sampled", "$.stream", f"{stream.id!r} is an event stream")
        return report
    if doc["unit"] != stream.unit.value:
        report.error(
            "batch.unit", "$.unit", f"unit {doc['unit']!r} != catalogue unit {stream.unit.value!r}"
        )
    if doc["period_s"] != stream.nominal_period_s:
        report.error(
            "batch.period",
            "$.period_s",
            f"period {doc['period_s']} s != nominal {stream.nominal_period_s} s",
        )
    values = doc["values"]
    quality = doc["quality"]
    flags = quality if isinstance(quality, list) else [quality] * len(values)
    if len(flags) != len(values):
        report.error(
            "batch.quality_length", "$.quality", f"{len(flags)} flags for {len(values)} values"
        )
        return report
    lo, hi = stream.value_min, stream.value_max
    for i, (value, flag) in enumerate(zip(values, flags, strict=True)):
        if (value is None) != (flag == "missing"):
            report.error(
                "batch.null_quality",
                f"$.values[{i}]",
                "a value is null exactly when its quality is 'missing'",
            )
        elif (
            value is not None
            and lo is not None
            and hi is not None
            and not lo <= value <= hi
            and flag != "out_of_range"
        ):
            report.error(
                "batch.range",
                f"$.values[{i}]",
                f"{value} outside [{lo}, {hi}] without quality 'out_of_range'",
            )
    return report


def validate_event_document(doc: Any, ctx: ContractContext) -> Report:
    report = Report()
    if not _schema(report, "core", "event", doc):
        return report
    if doc["house_id"] != ctx.house_id:
        report.error("event.house", "$.house_id", f"unknown house {doc['house_id']!r}")
    stream = ctx.streams.get(doc["stream"])
    if stream is None:
        report.error(
            "event.unknown_stream", "$.stream", f"{doc['stream']!r} is not in the catalogue"
        )
        return report
    if stream.kind.value != "event" or stream.event_payload is None:
        report.error("event.not_event", "$.stream", f"{stream.id!r} is not an event stream")
        return report
    if doc["payload"]["type"] != stream.event_payload.value:
        report.error(
            "event.payload_type",
            "$.payload.type",
            f"{stream.id!r} carries {stream.event_payload.value!r}",
        )
    device_id = doc["payload"].get("device_id")
    if device_id is not None and device_id not in ctx.protective_device_ids:
        report.error(
            "event.unknown_device",
            "$.payload.device_id",
            f"{device_id!r} is not a protective device",
        )
    return report
