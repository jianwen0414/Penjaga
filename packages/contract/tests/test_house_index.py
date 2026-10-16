"""HouseIndex: topology answers derived from the canonical house."""

from __future__ import annotations

from penjaga_contract.house import ElementKind, HouseIndex


def test_load_validates_and_indexes_every_element(index: HouseIndex):
    assert index.house_id == "elmina_g6_demo"
    assert len(index.ids(ElementKind.room)) == 17
    assert len(index.ids(ElementKind.fixture)) == 22
    assert len(index.ids(ElementKind.circuit)) == 18
    assert len(index.ids(ElementKind.rccb)) == 6
    assert len(index.ids(ElementKind.mcb)) == 18
    # Counts quoted in docs/design/contract.md; update both together.
    structural = (ElementKind.wall, ElementKind.ceiling, ElementKind.duct)
    assert sum(len(index.ids(k)) for k in structural) == 8
    assert len(index.ids(ElementKind.pipe)) == 13
    assert len(index.ids(ElementKind.valve)) == 11
    assert len(index.ids(ElementKind.socket_group)) == 14
    assert len(index.ids(ElementKind.appliance)) == 21
    assert len(index.ids(ElementKind.sensor)) == 6


def test_prd_example_ids_exist(index: HouseIndex):
    # IDs used as examples in PRD §10.2 / §10.4 stay valid so teammates' examples keep working.
    for oid in [
        "room_bath_master",
        "room_bath2",
        "fix_bath2_wc_cistern",
        "pipe_bath2_cold_supply",
        "valve_roof_tank_float",
        "tank_roof_main",
        "meter_water_main",
        "db_main",
        "circ_ac_master",
        "circ_sockets_kitchen",
        "rcd_main",
        "app_ac_master",
        "app_fridge_kitchen",
        "app_wh_bath_master",
    ]:
        assert index.exists(oid), oid


def test_supply_sources(index: HouseIndex):
    assert set(index.main_fed_fixtures) == {
        "fix_kitchen_sink_tap",
        "fix_kitchen_ro_purifier",
        "fix_porch_garden_tap",
    }
    assert len(index.tank_fed_fixtures) == 19
    assert index.supply_source("fix_bath2_wc_cistern") == "tank"


def test_every_fixture_path_ends_at_the_meter(index: HouseIndex):
    for fid in index.ids(ElementKind.fixture):
        path = index.upstream_path(fid)
        assert path[0] == fid
        assert path[-1] == index.meter_id


def test_isolation_semantics(index: HouseIndex):
    assert index.isolated_by("valve_bath2_wc_angle") == ("fix_bath2_wc_cistern",)
    assert set(index.isolated_by("valve_bath2_branch")) == {
        "fix_bath2_wc_cistern",
        "fix_bath2_basin_tap",
        "fix_bath2_shower",
        "fix_bath2_bidet_spray",
    }
    # Closing the main stop valve stops only main-fed fixtures at once: the tank keeps supplying.
    assert set(index.isolated_by("valve_main_stop")) == set(index.main_fed_fixtures)
    assert index.stops_tank_refill("valve_main_stop")
    # The tank outlet valve isolates everything behind the tank and does not stop the refill.
    assert set(index.isolated_by("valve_downfeed_main")) == set(index.tank_fed_fixtures)
    assert not index.stops_tank_refill("valve_downfeed_main")
    assert index.isolated_by("valve_roof_tank_float") == ()
    assert index.stops_tank_refill("valve_roof_tank_float")


def test_protection_chains_follow_reg36_design(index: HouseIndex):
    for cid in index.ids(ElementKind.circuit):
        circuit = index.circuit(cid)
        chain = index.rccb_chain(cid)
        assert chain[-1] == "rcd_main"
        effective = index.effective_residual_ma(cid)
        if cid.startswith("circ_wh_"):
            assert effective == 10.0
        elif circuit.kind.value == "socket":
            assert effective == 30.0
        else:
            assert effective == 100.0
    assert len(index.circuits_under("rcd_main")) == 18
    assert index.circuits_under("rcd_wh_bath2") == ("circ_wh_bath2",)


def test_appliance_circuits(index: HouseIndex):
    assert index.circuit_of_appliance("app_fridge_kitchen") == "circ_sockets_kitchen"
    assert index.circuit_of_appliance("app_ac_master") == "circ_ac_master"
    assert index.circuit_of_appliance("app_hob_kitchen_wet") is None
    assert index.appliances_on_circuit("circ_wh_bath4") == ("app_wh_bath4",)


def test_rooms_of(index: HouseIndex):
    assert index.rooms_of("rcd_main") == ("room_living",)
    assert index.rooms_of("sensor_weather_feed") == ()
    assert index.rooms_of("pipe_bath2_cold_supply") == ("room_bath2",)
    assert index.rooms_of("fix_bath2_wc_cistern") == ("room_bath2",)
    assert index.room_level("room_bath2") == 1
