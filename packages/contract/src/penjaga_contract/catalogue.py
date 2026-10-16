"""Build the stream catalogue (streams.json) from the house record.

The catalogue is derived, never hand-edited: every stream follows from a sensor in the kit
and the elements it measures. Rules:

* Water meter: ``water.main.flow_lpm`` (10 s, mean flow over the period). Resolution is one
  register scale interval per period; the range ends at the overload flow rate Q4 = 1.25 Q3
  (OIML R49-1:2013 §4.1.6).
* Inlet pressure sensor: ``water.main.pressure_bar`` (10 s, instantaneous).
* Tank level sensor: ``water.tank.level_pct`` (60 s, instantaneous).
* DB monitor: main power and voltage (1 s); per final circuit RMS current (1 s) and energy
  (60 s); per RCCB residual current (1 s); protective-device state events; supply events.
* Smart plug: ``elec.appliance.<appliance_id>.power_w`` (1 s).
* Weather feed: hourly outdoor temperature, relative humidity, precipitation.

Plausible-range rules: currents up to 5 x In (the upper bound of a type-B MCB's
instantaneous-trip band; beyond it the device has tripped), powers up to rated current x
nominal voltage x 1.1.
"""

from __future__ import annotations

from typing import Any

from penjaga_contract import SCHEMA_VERSION

WATER_FLOW_PERIOD_S = 10.0
WATER_PRESSURE_PERIOD_S = 10.0
TANK_LEVEL_PERIOD_S = 60.0
ELEC_FAST_PERIOD_S = 1.0
ENERGY_PERIOD_S = 60.0
WEATHER_PERIOD_S = 3600.0

VOLTAGE_HEADROOM = 1.1
MCB_B_INSTANT_TRIP_MAX_MULTIPLE = 5.0
MAINS_VOLTAGE_RANGE_MAX_V = 300.0


def _one(items: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    matches = [s for s in items if s["kind"] == kind]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one sensor of kind {kind!r}, found {len(matches)}")
    return matches[0]


def _optional(items: list[dict[str, Any]], kind: str) -> dict[str, Any] | None:
    matches = [s for s in items if s["kind"] == kind]
    if len(matches) > 1:
        raise ValueError(f"expected at most one sensor of kind {kind!r}, found {len(matches)}")
    return matches[0] if matches else None


def _sampled(
    stream_id: str,
    *,
    aggregation: str,
    unit: str,
    period_s: float,
    value_min: float,
    value_max: float,
    resolution: float,
    layers: list[str],
    sensor_id: str,
    object_ids: list[str],
    description: str,
) -> dict[str, Any]:
    return {
        "id": stream_id,
        "kind": "sampled",
        "aggregation": aggregation,
        "unit": unit,
        "nominal_period_s": period_s,
        "value_min": value_min,
        "value_max": value_max,
        "resolution": resolution,
        "layers": layers,
        "sensor_id": sensor_id,
        "object_ids": object_ids,
        "description": description,
    }


def _event(
    stream_id: str,
    *,
    payload: str,
    layers: list[str],
    sensor_id: str,
    object_ids: list[str],
    description: str,
) -> dict[str, Any]:
    return {
        "id": stream_id,
        "kind": "event",
        "aggregation": "event",
        "unit": "none",
        "nominal_period_s": None,
        "value_min": None,
        "value_max": None,
        "resolution": None,
        "layers": layers,
        "sensor_id": sensor_id,
        "object_ids": object_ids,
        "event_payload": payload,
        "description": description,
    }


def _round(value: float, digits: int = 6) -> float:
    """Round derived limits so the committed JSON is stable across platforms."""
    return round(value, digits)


def build_catalogue(house: dict[str, Any]) -> dict[str, Any]:
    """Derive the full stream catalogue for ``house`` (a house.json document)."""
    sensors: list[dict[str, Any]] = house["sensors"]
    elec = house["electrical"]
    nominal_v = float(elec["supply"]["nominal_voltage_v"])
    devices = {d["id"]: d for d in elec["protective_devices"]}
    streams: list[dict[str, Any]] = []

    meter = _one(sensors, "water_meter")
    spec = meter["spec"]
    q4_lpm = 1.25 * float(spec["q3_m3_per_h"]) * 1000.0 / 60.0
    flow_resolution = float(spec["scale_interval_l"]) / (WATER_FLOW_PERIOD_S / 60.0)
    streams.append(
        _sampled(
            "water.main.flow_lpm",
            aggregation="mean_over_period",
            unit="L/min",
            period_s=WATER_FLOW_PERIOD_S,
            value_min=0.0,
            value_max=_round(q4_lpm),
            resolution=_round(flow_resolution),
            layers=["L1", "L5"],
            sensor_id=meter["id"],
            object_ids=[meter["mounted_on_id"]],
            description="Mean flow through the main meter over the period (register increments).",
        )
    )

    pressure = _optional(sensors, "pressure_sensor")
    if pressure is not None:
        streams.append(
            _sampled(
                "water.main.pressure_bar",
                aggregation="instant",
                unit="bar",
                period_s=WATER_PRESSURE_PERIOD_S,
                value_min=0.0,
                value_max=float(pressure["spec"]["range_max_bar"]),
                resolution=float(pressure["spec"]["resolution_bar"]),
                layers=["L1", "L5"],
                sensor_id=pressure["id"],
                object_ids=[pressure["mounted_on_id"]],
                description="Supply pressure at the house inlet.",
            )
        )

    level = _optional(sensors, "tank_level_sensor")
    if level is not None:
        streams.append(
            _sampled(
                "water.tank.level_pct",
                aggregation="instant",
                unit="%",
                period_s=TANK_LEVEL_PERIOD_S,
                value_min=0.0,
                value_max=100.0,
                resolution=float(level["spec"]["resolution_pct"]),
                layers=["L1", "L5"],
                sensor_id=level["id"],
                object_ids=[level["mounted_on_id"]],
                description="Roof tank water level as a percentage of usable capacity.",
            )
        )

    dbm = _one(sensors, "db_monitor")
    dspec = dbm["spec"]
    board_id = dbm["mounted_on_id"]
    roots = [d for d in devices.values() if d["upstream_device_id"] is None]
    if len(roots) != 1:
        raise ValueError(f"expected exactly one root protective device, found {len(roots)}")
    main_rated_a = float(roots[0]["rated_current_a"])
    streams.append(
        _sampled(
            "elec.main.power_w",
            aggregation="mean_over_period",
            unit="W",
            period_s=ELEC_FAST_PERIOD_S,
            value_min=0.0,
            value_max=_round(main_rated_a * nominal_v * VOLTAGE_HEADROOM),
            resolution=float(dspec["power_resolution_w"]),
            layers=["L2", "L3", "L5"],
            sensor_id=dbm["id"],
            object_ids=[board_id],
            description="Total active power at the incomer.",
        )
    )
    streams.append(
        _sampled(
            "elec.main.voltage_v",
            aggregation="mean_over_period",
            unit="V",
            period_s=ELEC_FAST_PERIOD_S,
            value_min=0.0,
            value_max=MAINS_VOLTAGE_RANGE_MAX_V,
            resolution=float(dspec["voltage_resolution_v"]),
            layers=["L2", "L5"],
            sensor_id=dbm["id"],
            object_ids=[board_id],
            description="RMS supply voltage at the incomer (measured upstream of the main switch).",
        )
    )
    for circuit in elec["circuits"]:
        cid = circuit["id"]
        rated_a = float(devices[circuit["mcb_id"]]["rated_current_a"])
        streams.append(
            _sampled(
                f"elec.circuit.{cid}.current_a",
                aggregation="mean_over_period",
                unit="A",
                period_s=ELEC_FAST_PERIOD_S,
                value_min=0.0,
                value_max=_round(rated_a * MCB_B_INSTANT_TRIP_MAX_MULTIPLE),
                resolution=float(dspec["current_resolution_a"]),
                layers=["L2", "L3"],
                sensor_id=dbm["id"],
                object_ids=[cid],
                description=f"RMS current of final circuit {cid}.",
            )
        )
        streams.append(
            _sampled(
                f"elec.circuit.{cid}.energy_wh",
                aggregation="sum_over_period",
                unit="Wh",
                period_s=ENERGY_PERIOD_S,
                value_min=0.0,
                value_max=_round(rated_a * nominal_v * VOLTAGE_HEADROOM * ENERGY_PERIOD_S / 3600.0),
                resolution=float(dspec["energy_resolution_wh"]),
                layers=["L3"],
                sensor_id=dbm["id"],
                object_ids=[cid],
                description=f"Active energy of final circuit {cid} over the period.",
            )
        )
    for device in elec["protective_devices"]:
        if device["kind"] != "rccb":
            continue
        streams.append(
            _sampled(
                f"elec.rcd.{device['id']}.residual_ma",
                aggregation="mean_over_period",
                unit="mA",
                period_s=ELEC_FAST_PERIOD_S,
                value_min=0.0,
                value_max=float(dspec["residual_range_max_ma"]),
                resolution=float(dspec["residual_resolution_ma"]),
                layers=["L2"],
                sensor_id=dbm["id"],
                object_ids=[device["id"]],
                description=(
                    f"RMS residual (earth leakage) current through RCCB {device['id']}, "
                    "including every circuit downstream of it."
                ),
            )
        )

    for plug in (s for s in sensors if s["kind"] == "smart_plug"):
        appliance_id = plug["mounted_on_id"]
        streams.append(
            _sampled(
                f"elec.appliance.{appliance_id}.power_w",
                aggregation="mean_over_period",
                unit="W",
                period_s=ELEC_FAST_PERIOD_S,
                value_min=0.0,
                value_max=_round(float(plug["spec"]["max_load_a"]) * nominal_v * VOLTAGE_HEADROOM),
                resolution=float(plug["spec"]["power_resolution_w"]),
                layers=["L3"],
                sensor_id=plug["id"],
                object_ids=[appliance_id],
                description=f"Active power of {appliance_id} measured by its smart plug.",
            )
        )

    if dspec["aux_contacts"]:
        streams.append(
            _event(
                "elec.protection.trip",
                payload="protection_device_state",
                layers=["L2"],
                sensor_id=dbm["id"],
                object_ids=[d["id"] for d in elec["protective_devices"]],
                description="Tripped/reset changes of protective devices (aux contacts; no cause).",
            )
        )
    if dspec["backup_power"]:  # without it the monitor goes dark with the mains (D-018)
        streams.append(
            _event(
                "elec.supply.state",
                payload="supply_state",
                layers=["L2", "L5"],
                sensor_id=dbm["id"],
                object_ids=[board_id],
                description="Mains lost/restored at the incomer.",
            )
        )

    weather = _optional(sensors, "weather_feed")
    if weather is not None:
        wid = weather["id"]
        streams.append(
            _sampled(
                "weather.outdoor.temp_c",
                aggregation="instant",
                unit="degC",
                period_s=WEATHER_PERIOD_S,
                value_min=-10.0,
                value_max=50.0,
                resolution=0.1,
                layers=["L3"],
                sensor_id=wid,
                object_ids=[],
                description="Outdoor air temperature at 2 m.",
            )
        )
        streams.append(
            _sampled(
                "weather.outdoor.rh_pct",
                aggregation="instant",
                unit="%",
                period_s=WEATHER_PERIOD_S,
                value_min=0.0,
                value_max=100.0,
                resolution=1.0,
                layers=["L2", "L3"],
                sensor_id=wid,
                object_ids=[],
                description="Outdoor relative humidity at 2 m.",
            )
        )
        streams.append(
            _sampled(
                "weather.precip_mm",
                aggregation="sum_over_period",
                unit="mm",
                period_s=WEATHER_PERIOD_S,
                value_min=0.0,
                value_max=200.0,
                resolution=0.1,
                layers=["L2"],
                sensor_id=wid,
                object_ids=[],
                description="Precipitation over the preceding hour.",
            )
        )

    return {"schema_version": SCHEMA_VERSION, "house_id": house["house_id"], "streams": streams}
