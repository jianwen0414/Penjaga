"""Reference example documents for the runtime interfaces.

These are valid against both the JSON Schemas and the semantic rules (tests enforce it), so
teammates can use them as fixtures for UI, agent and API work. They are illustrative payloads,
not data from the system.
"""

from __future__ import annotations

from typing import Any

HOUSE_ID = "elmina_g6_demo"


def example_finding() -> dict[str, Any]:
    """L1 model finding with uncertain location (two candidate cisterns)."""
    window = {"from": "2026-10-15T01:00:00+08:00", "to": "2026-10-15T05:00:00+08:00"}
    return {
        "schema_version": "1.0",
        "id": "fnd_0123456789abcdef0123",
        "key": "L1:running_cistern_suspected:tank_fed",
        "revision": 2,
        "status": "active",
        "house_id": HOUSE_ID,
        "layer": "L1",
        "type": "running_cistern_suspected",
        "severity": "act_soon",
        "severity_floor": False,
        "confidence": 0.91,
        "confidence_kind": "calibrated_probability",
        "object_ids": [],
        "candidates": [
            {"object_id": "fix_bath2_wc_cistern", "p": 0.55, "basis": "periodic_refill+tank_fed"},
            {"object_id": "fix_bath3_wc_cistern", "p": 0.30, "basis": "periodic_refill+tank_fed"},
        ],
        "candidates_other_p": 0.15,
        "quantities": {
            "water_l_per_day": {"value": 605.0, "unit": "L/day", "ci90": [520.0, 690.0]}
        },
        "evidence": [
            {
                "name": "night_min_flow",
                "value": 0.42,
                "unit": "L/min",
                "baseline": 0.0,
                "window": window,
            },
            {"name": "pattern", "value": "periodic_refill"},
        ],
        "series_refs": [{"stream": "water.main.flow_lpm", "window": window, "bucket_s": 60}],
        "onset_estimate": "2026-10-12T02:00:00+08:00",
        "first_seen": "2026-10-13T05:00:00+08:00",
        "last_seen": "2026-10-16T05:00:00+08:00",
        "updated_at": "2026-10-16T05:00:10+08:00",
        "source": {
            "detector": "l1_leak",
            "kind": "model",
            "model": "leak_gbm",
            "version": "1.2.0",
            "model_card": "docs/models/l1-leak.md",
        },
    }


def example_floor_finding() -> dict[str, Any]:
    """L2 safety-floor finding: urgent, deterministic, with a safety-guidance key."""
    ts = "2026-10-16T19:42:05+08:00"
    return {
        "schema_version": "1.0",
        "id": "fnd_abcdefabcdefabcdef01",
        "key": "L2:circuit_overload:circ_sockets_kitchen",
        "revision": 1,
        "status": "active",
        "house_id": HOUSE_ID,
        "layer": "L2",
        "type": "circuit_overload",
        "severity": "urgent",
        "severity_floor": True,
        "confidence": 1.0,
        "confidence_kind": "deterministic_rule",
        "object_ids": ["circ_sockets_kitchen"],
        "candidates": [],
        "candidates_other_p": 0.0,
        "quantities": {},
        "evidence": [{"name": "current_rms", "value": 26.4, "unit": "A", "threshold": 20.0}],
        "series_refs": [
            {
                "stream": "elec.circuit.circ_sockets_kitchen.current_a",
                "window": {"from": "2026-10-16T19:30:00+08:00", "to": ts},
                "bucket_s": 1,
            }
        ],
        "first_seen": ts,
        "last_seen": ts,
        "updated_at": ts,
        "safety_guidance_key": "sg_circuit_overload",
        "source": {"detector": "l2_safety_floor", "kind": "rule", "version": "1.0.0"},
    }


def example_reading_batch() -> dict[str, Any]:
    """Six 10-second water-flow samples, one missing."""
    return {
        "house_id": HOUSE_ID,
        "stream": "water.main.flow_lpm",
        "t0": "2026-10-16T03:12:40+08:00",
        "period_s": 10.0,
        "unit": "L/min",
        "values": [0.42, 0.36, 0.42, None, 0.48, 0.42],
        "quality": ["ok", "ok", "ok", "missing", "ok", "ok"],
        "seq": 18234,
    }


def example_reading() -> dict[str, Any]:
    return {
        "ts": "2026-10-16T03:12:40+08:00",
        "house_id": HOUSE_ID,
        "stream": "water.main.flow_lpm",
        "value": 0.42,
        "unit": "L/min",
        "quality": "ok",
    }


def example_trip_event() -> dict[str, Any]:
    return {
        "ts": "2026-10-16T21:03:17+08:00",
        "house_id": HOUSE_ID,
        "stream": "elec.protection.trip",
        "seq": 912,
        "payload": {
            "type": "protection_device_state",
            "device_id": "rcd_wh_bath2",
            "state": "tripped",
        },
    }
