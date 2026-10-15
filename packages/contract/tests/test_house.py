"""house.json: valid as committed, and every semantic rule rejects the defect it targets."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

import pytest

from penjaga_contract.validate import (
    REG36_GENERAL_MAX_MA,
    Report,
    _check_reg36,
    _HouseCtx,
    validate_house_document,
)


def by_id(items: list[dict[str, Any]], element_id: str) -> dict[str, Any]:
    matches = [e for e in items if e["id"] == element_id]
    assert len(matches) == 1, element_id
    return matches[0]


def test_canonical_house_has_no_errors(house_doc):
    report = validate_house_document(house_doc)
    assert report.errors == []


def test_canonical_house_warnings_are_the_documented_ones(house_doc):
    # Bathroom luminaires sit on the general lighting circuits at 100 mA. Reg. 36(2) can be read
    # to require <= 10 mA for them; flagged for a competent electrician (docs/design/contract.md).
    report = validate_house_document(house_doc)
    assert sorted((w.code, w.path) for w in report.warnings) == [
        ("reg36.lighting_wet_review", "circ_lights_1f"),
        ("reg36.lighting_wet_review", "circ_lights_gf"),
    ]


Mutation = Callable[[dict[str, Any]], None]
Table = Callable[[dict[str, Any]], list[dict[str, Any]]]


def table(*keys: str) -> Table:
    """Accessor for a list of elements inside a house document, e.g. table("water", "pipes")."""

    def get(doc: dict[str, Any]) -> list[dict[str, Any]]:
        node: Any = doc
        for key in keys:
            node = node[key]
        return cast(list[dict[str, Any]], node)

    return get


ROOMS = table("rooms")
WALLS = table("walls")
METERS = table("water", "meters")
PIPES = table("water", "pipes")
VALVES = table("water", "valves")
FIXTURES = table("water", "fixtures")
DEVICES = table("electrical", "protective_devices")
CIRCUITS = table("electrical", "circuits")
SOCKETS = table("electrical", "socket_groups")
APPLIANCES = table("appliances")
SENSORS = table("sensors")


def _set(items: Table, element_id: str, **changes: Any) -> Mutation:
    def apply(doc: dict[str, Any]) -> None:
        by_id(items(doc), element_id).update(changes)

    return apply


def _append(items: Table, template_id: str, **changes: Any) -> Mutation:
    def apply(doc: dict[str, Any]) -> None:
        elements = items(doc)
        elements.append({**by_id(elements, template_id), **changes})

    return apply


def _pop_key(items: Table, element_id: str, key: str) -> Mutation:
    def apply(doc: dict[str, Any]) -> None:
        by_id(items(doc), element_id).pop(key)

    return apply


NEGATIVE_CASES: list[tuple[str, Mutation, str]] = [
    ("duplicate id", _append(ROOMS, "room_living"), "id.duplicate"),
    ("prefix mismatch", _append(VALVES, "valve_main_stop", id="fix_not_a_valve"), "id.prefix"),
    (
        "dangling reference",
        _set(FIXTURES, "fix_bath2_shower", pipe_id="pipe_missing"),
        "ref.missing",
    ),
    (
        "reference to wrong kind",
        _set(FIXTURES, "fix_bath2_shower", pipe_id="room_living"),
        "ref.kind",
    ),
    ("unknown level", _set(ROOMS, "room_living", level=7), "level.unknown"),
    ("bathroom not wet", _set(ROOMS, "room_bath2", wet_location=False), "room.bathroom_dry"),
    ("wall level mismatch", _set(WALLS, "wall_bath2_wet", levels=[0]), "wall.level_mismatch"),
    ("bad time zone", lambda d: d["location"].update(timezone="Asia/Elmina"), "location.timezone"),
    (
        "two meters",
        _append(METERS, "meter_water_main", id="meter_water_second"),
        "water.meter_count",
    ),
    ("pipe cycle", _set(PIPES, "pipe_rising_main", upstream_id="pipe_gf_downfeed"), "water.cycle"),
    (
        "concealed without wall",
        _set(PIPES, "pipe_bath2_cold_supply", concealed_in=[]),
        "water.concealed_routing",
    ),
    (
        "exposed but concealed",
        _set(PIPES, "pipe_yard_supply", concealed_in=["wall_bath2_wet"]),
        "water.concealed_routing",
    ),
    (
        "overflow from a pipe",
        _set(PIPES, "pipe_roof_tank_overflow", upstream_id="pipe_downfeed_main"),
        "water.overflow",
    ),
    ("tank fed by branch", _set(PIPES, "pipe_rising_main", kind="branch"), "water.tank_inlet"),
    (
        "float valve off inlet",
        _set(VALVES, "valve_roof_tank_float", pipe_id="pipe_downfeed_main"),
        "water.float_valve",
    ),
    (
        "angle valve isolating nothing",
        _pop_key(FIXTURES, "fix_bath2_wc_cistern", "isolation_valve_id"),
        "water.angle_valve",
    ),
    (
        "isolation by stop valve",
        _set(FIXTURES, "fix_bath2_wc_cistern", isolation_valve_id="valve_bath2_branch"),
        "water.angle_valve",
    ),
    (
        "fixture on overflow",
        _set(FIXTURES, "fix_yard_laundry_tap", pipe_id="pipe_roof_tank_overflow"),
        "water.fixture_on_overflow",
    ),
    ("two main devices", _set(DEVICES, "mcb_ac_living", upstream_device_id=None), "elec.root"),
    (
        "MCB feeding a device",
        _set(DEVICES, "mcb_ac_master", upstream_device_id="mcb_ac_living"),
        "elec.upstream_not_rccb",
    ),
    (
        "MCB above upstream rating",
        _set(DEVICES, "mcb_wh_bath2", rated_current_a=32),
        "elec.rating_exceeds_upstream",
    ),
    ("shared MCB", _set(CIRCUITS, "circ_ac_bed3", mcb_id="mcb_ac_bed2"), "elec.mcb_usage"),
    (
        "socket group on dedicated circuit",
        _set(SOCKETS, "sock_bed2", circuit_id="circ_ac_bed2"),
        "elec.socket_circuit_kind",
    ),
    (
        "socket group outside circuit rooms",
        _set(SOCKETS, "sock_bed2", room_id="room_living"),
        "elec.socket_room",
    ),
    (
        "aircon on socket circuit",
        _set(APPLIANCES, "app_ac_bed2", circuit_id="circ_sockets_1f"),
        "app.dedicated_kind",
    ),
    (
        "two appliances on dedicated circuit",
        _append(APPLIANCES, "app_ac_bed2", id="app_ac_bed2_extra"),
        "elec.dedicated_usage",
    ),
    (
        "electric appliance without supply",
        _pop_key(APPLIANCES, "app_kettle_kitchen", "socket_group_id"),
        "app.supply",
    ),
    (
        "gas hob on a socket",
        _set(APPLIANCES, "app_hob_kitchen_wet", socket_group_id="sock_kitchen_wet"),
        "app.supply",
    ),
    (
        "heater serving a basin",
        _set(APPLIANCES, "app_wh_bath2", fixture_id="fix_bath2_basin_tap"),
        "app.fixture",
    ),
    (
        "aircon nameplate incomplete",
        lambda d: by_id(APPLIANCES(d), "app_ac_bed2")["nameplate"].pop("nominal_hp"),
        "app.nameplate",
    ),
    (
        "smart plug on hard-wired aircon",
        _set(SENSORS, "sensor_plug_fridge", mounted_on_id="app_ac_bed2"),
        "sensor.mount_kind",
    ),
    (
        "two DB monitors",
        _append(SENSORS, "sensor_db_monitor", id="sensor_db_monitor_two"),
        "sensor.count",
    ),
    ("heater at 30 mA", _set(DEVICES, "rcd_wh_bath2", rated_residual_ma=30), "reg36.wet_location"),
    ("sockets at 100 mA", _set(DEVICES, "rcd_sockets", rated_residual_ma=100), "reg36.hand_held"),
    ("main RCCB at 300 mA", _set(DEVICES, "rcd_main", rated_residual_ma=300), "reg36.general"),
    (
        "both supply fields",
        _set(APPLIANCES, "app_kettle_kitchen", circuit_id="circ_ac_bed2"),
        "schema.invalid",
    ),
    (
        "MCB with residual rating",
        _set(DEVICES, "mcb_ac_bed2", rated_residual_ma=30),
        "schema.invalid",
    ),
    ("unknown property", lambda d: d.update(notes="x"), "schema.invalid"),
    (
        "naive install date",
        _set(FIXTURES, "fix_bath2_shower", install_date="2024/06/01"),
        "schema.invalid",
    ),
]


@pytest.mark.parametrize(
    ("name", "mutate", "code"), NEGATIVE_CASES, ids=[c[0] for c in NEGATIVE_CASES]
)
def test_semantic_rule_rejects_defect(house_doc, name, mutate, code):
    mutate(house_doc)
    report = validate_house_document(house_doc)
    assert code in {i.code for i in report.errors}, f"{name}: got {[str(i) for i in report.issues]}"


def test_circuit_without_any_rcd_is_rejected():
    # Not reachable through a single mutation of the canonical house (every chain ends at the
    # main RCCB), so exercise the regulatory rule directly.
    report = Report()
    ctx = _HouseCtx(
        report=report,
        kinds={"room_x_a": "room", "mcb_x_a": "mcb", "circ_x_a": "circuit"},
        rooms={"room_x_a": {"id": "room_x_a", "wet_location": False}},
        pipes={},
        tanks={},
        valves={},
        fixtures={},
        devices={
            "mcb_x_a": {
                "id": "mcb_x_a",
                "kind": "mcb",
                "upstream_device_id": None,
                "rated_current_a": 10,
            }
        },
        circuits={
            "circ_x_a": {
                "id": "circ_x_a",
                "kind": "lighting",
                "mcb_id": "mcb_x_a",
                "serves_room_ids": ["room_x_a"],
            }
        },
        sockets={},
        appliances={},
    )
    _check_reg36(ctx)
    assert [i.code for i in report.errors] == ["reg36.no_rcd"]
    assert REG36_GENERAL_MAX_MA == 100.0
