"""streams.json: derived from house.json, complete, and consistent with the sensor kit."""

from __future__ import annotations

from collections import Counter

import pytest

from penjaga_contract.catalogue import build_catalogue
from penjaga_contract.house import ElementKind, HouseIndex
from penjaga_contract.validate import validate_catalogue_document


def test_committed_catalogue_matches_derivation(house_doc, streams_doc):
    assert streams_doc == build_catalogue(house_doc)
    assert validate_catalogue_document(streams_doc, house_doc).ok


def test_stream_inventory(streams_doc, index: HouseIndex):
    ids = [s["id"] for s in streams_doc["streams"]]
    assert len(ids) == len(set(ids))
    n_circuits = len(index.ids(ElementKind.circuit))
    n_rccbs = len(index.ids(ElementKind.rccb))
    # 3 water + 2 main + 2 per circuit + 1 per RCCB + 1 plug + 2 events + 3 weather
    assert len(ids) == 3 + 2 + 2 * n_circuits + n_rccbs + 1 + 2 + 3
    for cid in index.ids(ElementKind.circuit):
        assert f"elec.circuit.{cid}.current_a" in ids
        assert f"elec.circuit.{cid}.energy_wh" in ids
    for rid in index.ids(ElementKind.rccb):
        assert f"elec.rcd.{rid}.residual_ma" in ids
    assert "elec.appliance.app_fridge_kitchen.power_w" in ids
    assert "water.tank.level_pct" in ids
    assert "weather.precip_mm" in ids


def test_streams_reference_real_sensors_and_objects(streams_doc, index: HouseIndex):
    for stream in streams_doc["streams"]:
        assert index.kind_of(stream["sensor_id"]) is ElementKind.sensor
        for oid in stream["object_ids"]:
            assert index.exists(oid), (stream["id"], oid)


def test_no_stream_reveals_per_fixture_flow(streams_doc):
    # Integrity rule (PRD §8.1): one water meter, so no stream may localise a leak by itself.
    water = [s for s in streams_doc["streams"] if s["id"].startswith("water.")]
    assert {s["id"] for s in water} == {
        "water.main.flow_lpm",
        "water.main.pressure_bar",
        "water.tank.level_pct",
    }
    assert all(
        not oid.startswith("fix_") for s in streams_doc["streams"] for oid in s["object_ids"]
    )


def test_metrology_derived_values(streams_doc):
    flow = next(s for s in streams_doc["streams"] if s["id"] == "water.main.flow_lpm")
    # Q4 = 1.25 * Q3 = 3.125 m3/h = 52.083 L/min; resolution = 0.01 L per 10 s = 0.06 L/min.
    assert flow["value_max"] == pytest.approx(52.083333, abs=1e-6)
    assert flow["resolution"] == pytest.approx(0.06)
    current = next(
        s
        for s in streams_doc["streams"]
        if s["id"] == "elec.circuit.circ_sockets_kitchen.current_a"
    )
    assert current["value_max"] == pytest.approx(100.0)  # 5 x 20 A


def test_event_streams(streams_doc):
    events = {s["id"]: s for s in streams_doc["streams"] if s["kind"] == "event"}
    assert set(events) == {"elec.protection.trip", "elec.supply.state"}
    assert events["elec.protection.trip"]["event_payload"] == "protection_device_state"


def test_layer_usage(streams_doc):
    layers = Counter(layer for s in streams_doc["streams"] for layer in s["layers"])
    assert set(layers) <= {"L1", "L2", "L3", "L5"}  # L4 is knowledge-base driven, no streams


def test_catalogue_follows_kit(house_doc):
    # Removing the optional tank level sensor removes its stream (kit ablations stay consistent).
    house_doc["sensors"] = [s for s in house_doc["sensors"] if s["kind"] != "tank_level_sensor"]
    ids = {s["id"] for s in build_catalogue(house_doc)["streams"]}
    assert "water.tank.level_pct" not in ids


def test_drift_is_detected(house_doc, streams_doc):
    streams_doc["streams"][0]["resolution"] = 0.5
    codes = {i.code for i in validate_catalogue_document(streams_doc, house_doc).errors}
    assert codes == {"streams.drift"}
