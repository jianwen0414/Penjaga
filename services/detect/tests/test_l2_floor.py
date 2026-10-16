"""L2 safety floor: thresholds on synthetic signals, chunk invariance, and simulated houses."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pytest

from penjaga_contract.core import Finding
from penjaga_detect.chunk import Chunk, EventRecord, Series
from penjaga_detect.detector import HouseContext
from penjaga_detect.l2_floor import L2SafetyFloor, rolling_mean
from penjaga_sim.testing import START, at, fault, new_world

US = 1_000_000
T0 = 1_790_784_000 * US  # 2026-10-01T00:00:00+08:00
KITCHEN = "elec.circuit.circ_sockets_kitchen.current_a"  # MCB 20 A
SOCKET_RCD = "elec.rcd.rcd_sockets.residual_ma"  # IΔn 30 mA


def series(*segments: tuple[float, float]) -> tuple[np.ndarray, np.ndarray]:
    """1 s samples from (seconds, value) segments, starting at T0 + 1 s."""
    values = np.concatenate([np.full(int(sec), v, dtype=np.float64) for sec, v in segments])
    ts = T0 + (np.arange(values.size, dtype=np.int64) + 1) * US
    return ts, values


def feed(
    streams: dict[str, tuple[np.ndarray, np.ndarray]],
    events: Sequence[EventRecord] = (),
    step_s: float = 3600.0,
    end_us: int | None = None,
) -> list[Finding]:
    floor = L2SafetyFloor(HouseContext.canonical())
    last = max([ts[-1] for ts, _ in streams.values()] + [e.ts_us for e in events] + [T0])
    end = end_us or int(last) + 4 * 3600 * US
    out: list[Finding] = []
    t = T0
    step = round(step_s * US)
    while t < end:
        t1 = min(end, t + step)
        parts = {}
        for name, (ts, values) in streams.items():
            sel = (ts > t) & (ts <= t1)
            parts[name] = Series(ts[sel], values[sel], np.zeros(int(sel.sum()), np.uint8))
        chunk_events = tuple(e for e in events if t < e.ts_us <= t1)
        out += floor.update(Chunk("elmina_g6_demo", t, t1, parts, chunk_events))
        t = t1
    return out


def summary(findings: list[Finding]) -> list[tuple[str, str, int, str]]:
    return [(f.type.value, f.severity.value, f.revision, f.status.value) for f in findings]


def test_rolling_mean_needs_coverage() -> None:
    ts = np.arange(1, 11, dtype=np.int64) * US
    values = np.array([1, 2, 3, 4, np.nan, np.nan, np.nan, np.nan, np.nan, 10.0])
    m = rolling_mean(ts, values, 4 * US, 4, 0.5)
    assert m[3] == pytest.approx(2.5)
    assert np.isnan(m[8])  # only nans in the last 4 s
    assert np.isnan(m[9])  # one sample of four is below the 50% coverage


def test_overload_escalates_by_rating_multiples_then_clears() -> None:
    current = series((3600, 10.0), (1200, 21.0), (120, 30.0), (7200, 5.0))
    findings = feed({KITCHEN: current})
    assert summary(findings) == [
        ("circuit_overload", "heads_up", 1, "active"),  # 15-min mean above 20 A
        ("circuit_overload", "urgent", 2, "active"),  # 1-min mean above 1.45 x 20 = 29 A
        ("circuit_overload", "urgent", 3, "cleared"),  # an hour after the last overload
    ]
    first, urgent, _ = findings
    assert first.severity_floor
    assert first.object_ids == ["circ_sockets_kitchen", "mcb_sockets_kitchen"]
    assert urgent.safety_guidance_key == "sg_circuit_overload"
    # the 15-minute mean of 10 A then 21 A passes 20 A after (20 - 10) / (21 - 10) x 15 min
    crossing_s = 3600 + (20 - 10) / (21 - 10) * 900
    assert abs(first.first_seen.timestamp() - (T0 / US + crossing_s)) <= 2


def test_conventional_non_tripping_current_is_act_soon() -> None:
    findings = feed({KITCHEN: series((3600, 0.0), (1800, 23.0), (7200, 0.0))})
    assert [f.severity.value for f in findings if f.status.value == "active"] == [
        "heads_up",
        "act_soon",
    ]


def test_severity_never_falls_while_open() -> None:
    findings = feed({KITCHEN: series((1800, 30.0), (900, 21.0), (300, 0.0), (900, 21.0))})
    active = [f for f in findings if f.status.value == "active"]
    assert [f.severity.value for f in active] == ["urgent"]


@pytest.mark.parametrize("scale", [0.5, 0.9, 1.05, 1.2, 1.6, 2.0])
def test_more_current_never_means_lower_severity(scale: float) -> None:
    ranks = {"heads_up": 1, "act_soon": 2, "urgent": 3}

    def worst(k: float) -> int:
        fs = feed({KITCHEN: series((2400, 20.0 * k), (7200, 0.0))})
        return max((ranks[f.severity.value] for f in fs), default=0)

    assert worst(scale) <= worst(scale * 1.1)


def test_residual_current_tiers() -> None:
    residual = series((3600, 5.0), (1200, 10.0), (1200, 16.0), (7200, 2.0))
    findings = feed({SOCKET_RCD: residual})
    assert summary(findings) == [
        ("earth_leakage_elevated", "heads_up", 1, "active"),  # above 30% of 30 mA
        ("earth_leakage_elevated", "act_soon", 2, "active"),  # above 50%: non-operating limit
        ("earth_leakage_elevated", "act_soon", 3, "cleared"),
    ]
    assert findings[0].object_ids == ["rcd_sockets"]


def test_rising_leakage_is_projected_to_the_limit() -> None:
    day = 86_400
    residual = series(*[(day, 1.0 + d) for d in range(6)])  # +1 mA/day; limit 15 mA
    findings = feed({SOCKET_RCD: residual}, step_s=6 * 3600)
    rising = [f for f in findings if f.type.value == "earth_leakage_rising"]
    assert rising
    assert rising[0].severity.value == "heads_up"  # about 9 days to 15 mA
    evidence = {e.name: e.value for e in rising[0].evidence}
    assert evidence["trend"] == pytest.approx(1.0, abs=0.01)


def test_trips_and_repeated_trips() -> None:
    def trip(h: float, state: str) -> EventRecord:
        return EventRecord(
            T0 + round(h * 3600 * US),
            "elec.protection.trip",
            {"type": "protection_device_state", "device_id": "rcd_sockets", "state": state},
        )

    events = [trip(1, "tripped"), trip(1.2, "reset"), trip(5, "tripped"), trip(5.3, "reset")]
    findings = feed({}, events, end_us=T0 + 40 * 3600 * US)
    assert summary(findings) == [
        ("protective_device_trip", "act_soon", 1, "active"),
        ("repeated_trips", "urgent", 1, "active"),
        ("protective_device_trip", "act_soon", 2, "cleared"),
        ("repeated_trips", "urgent", 2, "cleared"),
    ]
    assert findings[1].safety_guidance_key == "sg_repeated_trips"
    assert findings[2].cleared_at == at(START, 29.0)  # a day after the last trip


def test_chunking_does_not_change_findings() -> None:
    streams = {
        KITCHEN: series((3600, 10.0), (1200, 21.0), (120, 30.0), (7200, 5.0)),
        SOCKET_RCD: series((3600, 5.0), (1200, 10.0), (1200, 16.0), (7200, 2.0)),
    }
    reference = feed(streams, step_s=24 * 3600)
    assert reference
    for step in (600.0, 3600.0, 5555.0):
        assert feed(streams, step_s=step) == reference


def simulate(world_faults: Sequence[object], days: int = 2) -> list[Finding]:
    from penjaga_detect.chunk import chunk_from_messages
    from penjaga_sim.export import SeqCounters, ingest_messages

    world = new_world()
    for f in world_faults:
        world.inject(f)  # type: ignore[arg-type]
    floor = L2SafetyFloor(HouseContext.canonical())
    counters = SeqCounters()
    out: list[Finding] = []
    t = world.day_start_us(START)
    for _ in range(days * 4):
        t1 = t + 6 * 3600 * US
        msgs = ingest_messages(world, t, t1, counters)
        out += floor.update(chunk_from_messages(world.model.index.house_id, t, t1, msgs))
        t = t1
    return out


def test_quiet_in_a_fault_free_house() -> None:
    assert simulate([]) == []


def test_repeated_trips_in_a_simulated_house() -> None:
    findings = simulate([fault("E3", "circ_lights_gf", 6.0, at(START, 0.5))])
    kinds = {(f.type.value, f.severity.value) for f in findings}
    assert ("protective_device_trip", "act_soon") in kinds
    assert ("repeated_trips", "urgent") in kinds
    assert all(f.object_ids == ["rcd_main"] for f in findings)


def test_overload_in_a_simulated_house() -> None:
    findings = simulate([fault("E1", "circ_sockets_living", 25.0, at(START, 0.5))])
    overloads = [f for f in findings if f.type.value == "circuit_overload"]
    assert overloads
    assert overloads[0].object_ids == ["circ_sockets_living", "mcb_sockets_living"]
