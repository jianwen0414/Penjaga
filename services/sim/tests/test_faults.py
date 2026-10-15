"""Fault injection: validation, the null-fault canary, causality and a visible effect per type."""

from __future__ import annotations

import itertools
from collections.abc import Callable
from datetime import timedelta

import numpy as np
import pytest

from penjaga_contract.lab import FaultType
from penjaga_sim.faults import UNIT, apply_faults, catalogue, random_spec, valid_locations
from penjaga_sim.rng import rng
from penjaga_sim.testing import (
    START,
    US,
    at,
    fault,
    meter_litres,
    new_world,
    same_output,
    window,
)
from penjaga_sim.world import DayOutput, World, WorldConflictError, WorldError

DAY2 = START + timedelta(days=1)


def stream_mean(out: DayOutput, stream: str) -> float:
    return float(np.nanmean(out.streams[stream].values))


def faulted(
    fault_type: str,
    location: str | None,
    magnitude: float,
    *,
    hour: float = 0.0,
    duration_s: float | None = None,
) -> DayOutput:
    """Day 1 of a fresh world with one fault starting ``hour`` hours after midnight."""
    world = new_world()
    world.inject(fault(fault_type, location, magnitude, at(START, hour), duration_s=duration_s))
    return world.output(START)


# ---------------------------------------------------------------------------- catalogue


def test_catalogue_covers_every_fault_type_with_locations(baseline: World) -> None:
    entries = {e["fault_type"]: e for e in catalogue(baseline.model)}
    assert set(entries) == {t.value for t in FaultType}
    for t, e in entries.items():
        lo, hi = e["magnitude_range"]
        assert lo <= hi
        assert e["magnitude_unit"] == UNIT[t]
        if t.startswith("U"):
            assert e["locations"] == []
            assert e["requires_duration"]
        else:
            assert e["locations"], t
            assert all(baseline.model.index.exists(loc) for loc in e["locations"])


# ---------------------------------------------------------------------------- validation


@pytest.mark.parametrize(
    ("build", "message"),
    [
        (lambda loc: fault("W1", "circ_lights_gf", 0.05, at(START)), "not a valid location"),
        (lambda loc: fault("W2", loc("W1"), 0.3, at(START)), "not a valid location"),
        (lambda loc: fault("U1", loc("W1"), 1, at(START), duration_s=60), "supply-level"),
        (lambda loc: fault("U3", None, 1, at(START)), "needs duration_s"),
        (lambda loc: fault("W1", loc("W1"), 9.0, at(START)), "outside"),
        (lambda loc: fault("U2", None, 0.5, at(START), duration_s=60), r"1 \(fault\) or 0"),
        (
            lambda loc: fault(
                "W4", loc("W4"), 0.4, at(START), progression={"kind": "intermittent"}
            ),
            "required",
        ),
        (
            lambda loc: fault("W4", loc("W4"), 0.4, at(START), progression={"kind": "worsening"}),
            "required",
        ),
    ],
)
def test_invalid_specs_are_rejected(
    location: Callable[[str], str], build: Callable[..., object], message: str
) -> None:
    world = new_world()
    with pytest.raises(WorldError, match=message):
        world.inject(build(location))  # type: ignore[arg-type]
    assert world.faults == []


def test_wrong_unit_is_rejected(location: Callable[[str], str]) -> None:
    spec = fault("W1", location("W1"), 0.05, at(START))
    spec = spec.model_copy(update={"magnitude": spec.magnitude.model_copy(update={"unit": "L/h"})})
    with pytest.raises(WorldError, match="unit"):
        new_world().inject(spec)


def test_fault_before_episode_or_published_time_is_rejected(
    location: Callable[[str], str],
) -> None:
    world = new_world()
    with pytest.raises(WorldError, match="before the episode"):
        world.inject(fault("W1", location("W1"), 0.05, at(START - timedelta(days=1))))
    world.inject(fault("W1", location("W1"), 0.05, at(START)))  # nothing published yet
    world.published_until_us = world.day_start_us(START) + 3600 * US
    with pytest.raises(WorldConflictError, match="already-published"):
        world.inject(fault("W1", location("W1"), 0.05, at(START, 0.5)))
    with pytest.raises(
        WorldConflictError, match="already-published"
    ):  # (t0, t1]: the boundary is sent
        world.inject(fault("W1", location("W1"), 0.05, at(START, 1.0)))
    world.inject(fault("W1", location("W1"), 0.05, at(START, 1.0 + 1 / 3600)))


def test_fault_without_start_uses_now(location: Callable[[str], str]) -> None:
    world = new_world()
    spec = fault("W1", location("W1"), 0.05, at(START)).model_copy(update={"start_at": None})
    with pytest.raises(WorldError, match="start_at or the current house time"):
        world.inject(spec)
    now = world.day_start_us(START) + 5 * US
    assert world.inject(spec, now_us=now).start_us == now


# ---------------------------------------------------------------------------- canaries


def test_null_fault_canary_every_type(baseline: World, location: Callable[[str], str]) -> None:
    """Magnitude 0 must leave every reading and event byte-identical (PRD §19 leakage canary)."""
    world = new_world()
    for t in FaultType:
        supply = t.value.startswith("U")
        world.inject(
            fault(
                t.value,
                None if supply else location(t.value),
                0,
                at(START, 3.0),
                duration_s=3600 if supply else None,
            )
        )
    assert same_output(world.output(START), baseline.output(START))
    assert same_output(world.output(DAY2), baseline.output(DAY2))
    assert len([r for r in world.truth_records() if r["kind"] == "fault"]) == len(FaultType)


@pytest.mark.parametrize(
    ("fault_type", "magnitude", "duration_s"),
    [("W2", 0.3, None), ("W5", 10.0, None), ("E3", 6.0, None), ("A3", 0.3, None), ("U3", 1, 7200)],
)
def test_injection_never_changes_the_past(
    baseline: World,
    location: Callable[[str], str],
    fault_type: str,
    magnitude: float,
    duration_s: float | None,
) -> None:
    """Every sample stamped at or before the fault start, and every event before it, is identical
    to the fault-free world (an event at the start instant is the fault's own effect)."""
    loc = None if fault_type.startswith("U") else location(fault_type)
    world = new_world()
    f = world.inject(fault(fault_type, loc, magnitude, at(START, 12.0), duration_s=duration_s))
    out, ref = world.output(START), baseline.output(START)
    changed = False
    for sid, sd in out.streams.items():
        ts = sd.t0_us + np.arange(sd.values.size) * sd.period_us
        before = ts <= f.start_us
        ref_sd = ref.streams[sid]
        assert np.array_equal(sd.values[before], ref_sd.values[before], equal_nan=True), sid
        assert np.array_equal(sd.quality[before], ref_sd.quality[before]), sid
        changed |= not np.array_equal(sd.values, ref_sd.values, equal_nan=True)
    assert [e for e in out.events if e.ts_us < f.start_us] == [
        e for e in ref.events if e.ts_us < f.start_us
    ]
    assert changed, "the fault had no effect after its start"


# ---------------------------------------------------------------------------- effects


@pytest.mark.parametrize(
    ("fault_type", "lpm"), [("W1", 0.1), ("W2", 0.3), ("W3", 5.0), ("W4", 0.4), ("W5", 10.0)]
)
def test_water_faults_add_their_flow(
    baseline: World, location: Callable[[str], str], fault_type: str, lpm: float
) -> None:
    out = faulted(fault_type, location(fault_type), lpm)
    ref = baseline.output(START)
    added_true = out.summary["water_true_l"] - ref.summary["water_true_l"]
    expected = lpm * 1440
    # W3 loses what the overflow pipe carries; it cannot exceed the float valve's stuck flow.
    assert 0.85 * expected <= added_true <= 1.05 * expected
    # The meter sees most of it (slow float-valve refills partly under-register, as real meters do).
    assert meter_litres(out) - meter_litres(ref) >= 0.7 * expected
    if fault_type == "W3":
        assert out.summary["overflow_l"] > 0.8 * expected
    else:
        assert out.summary["overflow_l"] == 0


def test_cistern_fault_reports_its_pattern(location: Callable[[str], str]) -> None:
    world = new_world()
    world.inject(fault("W2", location("W2"), 0.3, at(START)))
    world.output(START)
    (record,) = [r for r in world.truth_records() if r["kind"] == "fault"]
    assert record["params"]["pattern"] in {"continuous", "periodic_refill"}
    if record["params"]["pattern"] == "periodic_refill":
        period = record["params"]["refill_period_s"]
        litres = record["params"]["refill_litres"]
        assert period == pytest.approx(litres / (0.3 / 60), rel=0.01)


def test_overload_raises_circuit_current(baseline: World, location: Callable[[str], str]) -> None:
    circuit = location("E1")
    out = faulted("E1", circuit, 10.0)
    stream = f"elec.circuit.{circuit}.current_a"
    assert stream_mean(out, stream) > stream_mean(baseline.output(START), stream) + 0.2
    evening = window(out, stream, 17, 24)
    assert np.nanmax(evening) > 9.0


def test_earth_leakage_raises_rccb_residual(
    baseline: World, location: Callable[[str], str]
) -> None:
    circuit = location("E2")
    device = baseline.model.index.rccb_chain(circuit)[0]
    stream = f"elec.rcd.{device}.residual_ma"
    out = faulted("E2", circuit, 2.0)
    rise = stream_mean(out, stream) - stream_mean(baseline.output(START), stream)
    assert rise == pytest.approx(2.0, abs=0.3)


def test_repeated_trips_produce_trip_and_reset_events(location: Callable[[str], str]) -> None:
    circuit = location("E3")
    out = faulted("E3", circuit, 6.0)
    trips = [e for e in out.events if e.stream == "elec.protection.trip"]
    assert any(e.payload["state"] == "tripped" for e in trips)
    devices = {e.payload["device_id"] for e in trips}
    world = new_world()
    assert devices <= set(world.model.index.rccb_chain(circuit))
    # each device alternates tripped / reset
    for dev in devices:
        states = [e.payload["state"] for e in trips if e.payload["device_id"] == dev]
        assert all(a != b for a, b in itertools.pairwise(states))


def test_filter_clogging_lowers_cop_over_weeks(location: Callable[[str], str]) -> None:
    world = new_world()
    f = world.inject(fault("A1", location("A1"), 3.0, at(START)))
    day28 = START + timedelta(days=28)
    effects, _ = apply_faults(
        world.model, day28, world.day_start_us(day28), [f], np.full(86_400, 70.0)
    )
    factor = effects.ac_cop_factor[location("A1")]
    assert factor[0] == pytest.approx(1 / (1 + 0.03 * 4), rel=1e-3)
    assert np.all(np.diff(factor) <= 0)


@pytest.mark.parametrize(
    ("fault_type", "magnitude", "min_rise"),
    [("A2", 30.0, 0.1), ("A3", 0.3, 0.3), ("A4", 25.0, 0.15)],
)
def test_appliance_faults_raise_energy(
    baseline: World,
    location: Callable[[str], str],
    fault_type: str,
    magnitude: float,
    min_rise: float,
) -> None:
    app = location(fault_type)
    out = faulted(fault_type, app, magnitude)
    before = baseline.output(START).summary[f"kwh.{app}"]
    assert out.summary[f"kwh.{app}"] >= before * (1 + min_rise)


@pytest.mark.parametrize("fault_type", ["U1", "U2"])
def test_water_cut_stops_the_meter_and_drains_the_tank(baseline: World, fault_type: str) -> None:
    """The roof tank keeps the house supplied: the meter stops while the tank level falls."""
    out = faulted(fault_type, None, 1, hour=18.0, duration_s=4 * 3600)
    assert np.nansum(window(out, "water.main.flow_lpm", 18, 22)) == 0
    level = window(out, "water.tank.level_pct", 18, 22)
    ref = window(baseline.output(START), "water.tank.level_pct", 18, 22)
    assert np.nanmin(level) < np.nanmin(ref) - 5.0
    after = window(out, "water.main.flow_lpm", 22, 23)
    assert np.nanmax(after) > 5.0  # the float valve opens fully when supply returns


def test_power_cut_events_and_zero_voltage() -> None:
    out = faulted("U3", None, 1, hour=9.0, duration_s=4 * 3600)
    supply = [
        (e.payload["state"], (e.ts_us - out.start_us) / 3_600_000_000)
        for e in out.events
        if e.stream == "elec.supply.state"
    ]
    assert supply == [("lost", 9.0), ("restored", 13.0)]
    assert np.nanmax(window(out, "elec.main.voltage_v", 9.01, 13)) == 0
    assert np.nanmax(window(out, "elec.main.power_w", 9.01, 13)) == 0


def test_clear_ends_the_fault(baseline: World, location: Callable[[str], str]) -> None:
    world = new_world()
    f = world.inject(fault("W5", location("W5"), 10.0, at(START)))
    cleared = world.clear(f.fault_id, world.day_start_us(START) + 6 * 3600 * US)
    assert cleared.end_us == world.day_start_us(START) + 6 * 3600 * US
    added = (
        world.output(START).summary["water_true_l"] - baseline.output(START).summary["water_true_l"]
    )
    assert added == pytest.approx(10.0 * 360, rel=0.05)
    with pytest.raises(WorldError, match="already ended"):
        world.clear(f.fault_id, world.day_start_us(START) + 7 * 3600 * US)
    with pytest.raises(WorldError, match="unknown fault"):
        world.clear("flt_000000000000", 0)
    (record,) = [r for r in world.truth_records() if r["kind"] == "fault"]
    assert record["end"] == "2026-10-01T06:00:00+08:00"


def test_random_specs_are_valid_and_varied(baseline: World) -> None:
    """random_spec draws injectable faults for every type ("surprise me", sampled episodes)."""
    r = rng(5, "test", "random_spec")
    kinds: dict[str, set[str]] = {}
    for t in FaultType:
        for _ in range(20):
            spec = random_spec(baseline.model, t.value, at(START, 6.0), r)
            new_world().inject(spec)  # validates and accepts
            kinds.setdefault(t.value, set()).add(spec.progression.kind.value)
            if t.value.startswith("U"):
                assert spec.duration_s is not None
                assert spec.location_id is None
            else:
                assert spec.location_id in valid_locations(baseline.model, t.value)
    assert kinds["W4"] == {"constant", "worsening"}
    assert kinds["E2"] == {"worsening"}
    first = random_spec(baseline.model, "W1", at(START), rng(9, "x"))
    assert first == random_spec(baseline.model, "W1", at(START), rng(9, "x"))
