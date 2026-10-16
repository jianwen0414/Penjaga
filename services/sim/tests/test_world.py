"""World generation: determinism, the sensor kit's output grid and quality, anomalies, truth."""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pytest

from penjaga_contract import schema_registry
from penjaga_contract.validate import ContractContext
from penjaga_sim.sensors import MISSING, OK, OUT_OF_RANGE
from penjaga_sim.testing import (
    START,
    US,
    anomaly,
    at,
    fault,
    meter_litres,
    new_world,
    same_output,
    window,
)
from penjaga_sim.world import World, WorldError

DAY2 = START + timedelta(days=1)


# ---------------------------------------------------------------------------- determinism


def test_same_seed_reproduces_the_world(baseline: World) -> None:
    assert same_output(new_world().output(START), baseline.output(START))


def test_another_seed_gives_another_world(baseline: World) -> None:
    assert not same_output(new_world(seed=8).output(START), baseline.output(START))


def test_skipping_ahead_equals_generating_in_order(baseline: World) -> None:
    assert same_output(new_world().output(DAY2), baseline.output(DAY2))


def test_forgotten_days_regenerate_identically(baseline: World) -> None:
    world = new_world()
    world.output(DAY2)
    world.forget_before(DAY2 + timedelta(days=1))
    assert same_output(world.output(START), baseline.output(START))


def test_days_before_the_start_do_not_exist() -> None:
    with pytest.raises(WorldError, match="before the episode start"):
        new_world().output(START - timedelta(days=1))


def test_configuration_is_checked() -> None:
    with pytest.raises(WorldError, match="unknown archetype"):
        new_world(archetype="robots")
    with pytest.raises(WorldError, match="unknown sensors"):
        new_world(enabled_sensors=frozenset({"sensor_does_not_exist"}))


@pytest.mark.parametrize(
    "archetype",
    ["working_family", "retired_couple", "young_couple", "large_family", "shift_family"],
)
def test_every_archetype_generates(archetype: str) -> None:
    out = new_world(archetype=archetype).output(START)
    assert out.summary["water_true_l"] > 0
    assert out.summary["energy_kwh"] > 0


# ---------------------------------------------------------------------------- sensor kit


def test_streams_are_the_catalogue_streams_of_enabled_sensors(
    baseline: World, ctx: ContractContext
) -> None:
    sampled = {s.id for s in ctx.catalogue.streams if s.kind.value == "sampled"}
    assert set(baseline.output(START).streams) == sampled
    events = {s.id for s in ctx.catalogue.streams if s.kind.value == "event"}
    assert {s.id for s in baseline.event_streams} == events


def test_disabling_a_sensor_removes_only_its_streams(baseline: World, ctx: ContractContext) -> None:
    kit = frozenset(s.id for s in ctx.house.sensors) - {"sensor_tank_level"}
    out = new_world(enabled_sensors=kit).output(START)
    ref = baseline.output(START)
    assert set(ref.streams) - set(out.streams) == {"water.tank.level_pct"}
    for sid, sd in out.streams.items():  # the house and the other sensors are unaffected
        assert np.array_equal(sd.values, ref.streams[sid].values, equal_nan=True), sid


def test_sample_grid_follows_the_catalogue(baseline: World, ctx: ContractContext) -> None:
    out = baseline.output(START)
    for sid, sd in out.streams.items():
        period_s = ctx.streams[sid].nominal_period_s
        assert period_s is not None
        assert sd.period_us == period_s * US
        assert sd.values.size == 86_400 / period_s
        assert sd.t0_us == out.start_us + sd.period_us  # stamped at the end of each period
        assert sd.t0_us + (sd.values.size - 1) * sd.period_us == out.start_us + 86_400 * US


def test_quality_flags_match_values(baseline: World, ctx: ContractContext) -> None:
    for day in (START, DAY2):
        for sid, sd in baseline.output(day).streams.items():
            stream = ctx.streams[sid]
            missing = np.isnan(sd.values)
            assert np.array_equal(missing, sd.quality == MISSING), sid
            present = sd.values[~missing]
            lo, hi = stream.value_min, stream.value_max
            outside = (present < lo) | (present > hi) if lo is not None and hi is not None else 0
            assert np.array_equal(sd.quality[~missing] == OUT_OF_RANGE, np.asarray(outside)), sid
            assert set(np.unique(sd.quality)) <= {OK, MISSING, OUT_OF_RANGE}


def test_values_are_quantised_to_the_resolution(baseline: World, ctx: ContractContext) -> None:
    out = baseline.output(START)
    for sid, sd in out.streams.items():
        resolution = ctx.streams[sid].resolution
        values = sd.values[~np.isnan(sd.values)]
        if sid == "water.main.flow_lpm":  # whole meter scale intervals per period
            values = values * (sd.period_us / US) / 60.0
            resolution = 0.01
        assert resolution
        steps = values / resolution
        assert np.allclose(steps, np.round(steps), atol=1e-6), sid


def test_meter_reports_what_it_registered(baseline: World) -> None:
    out = baseline.output(START)
    registered = out.summary["water_registered_l"]
    assert meter_litres(out) <= registered + 0.01  # gaps lose volume, nothing is invented
    assert meter_litres(out) >= 0.95 * registered


def test_no_trips_and_plausible_ranges_without_faults(baseline: World) -> None:
    for day in (START, DAY2):
        out = baseline.output(day)
        assert out.events == []
        volts = out.streams["elec.main.voltage_v"].values
        assert np.nanmin(volts) >= baseline.model.cal.value("electrical.supply.min_v")  # DS-022
        assert np.nanmax(volts) <= baseline.model.cal.value("electrical.supply.max_v")
        bar = out.streams["water.main.pressure_bar"].values
        assert np.nanmin(bar) >= 1.0  # DS-018 design band
        assert np.nanmax(bar) <= 3.0
        level = out.streams["water.tank.level_pct"].values
        assert np.nanmin(level) >= 50.0


# ---------------------------------------------------------------------------- anomalies


def test_guests_raise_water_use(baseline: World) -> None:
    world = new_world()
    world.impose(anomaly("guests", at(START, 10), 3600, count=3))
    assert (
        world.output(START).summary["occupants"] == baseline.output(START).summary["occupants"] + 3
    )
    assert (
        world.output(START).summary["water_true_l"]
        > 1.3 * baseline.output(START).summary["water_true_l"]
    )


def test_holiday_absence_empties_the_house() -> None:
    """With nobody home, only always-on loads remain (day 1 also tops up the roof tank from its
    random starting level, so look at day 2)."""
    world = new_world()
    world.impose(anomaly("holiday_absence", at(START), 2 * 86_400))
    out = world.output(DAY2)
    assert out.summary["water_true_l"] < 0.01
    assert out.summary["kwh.app_fridge_kitchen"] > 0.5
    assert 1.0 < out.summary["energy_kwh"] < 5.0


def test_heatwave_raises_aircon_energy(baseline: World) -> None:
    world = new_world()
    world.impose(anomaly("heatwave", at(START), 86_400, extra_c=3.0))

    def ac_kwh(summary: dict[str, float]) -> float:
        return sum(v for k, v in summary.items() if k.startswith("kwh.app_ac_"))

    assert ac_kwh(world.output(START).summary) > ac_kwh(baseline.output(START).summary)
    temps = window(world.output(START), "weather.outdoor.temp_c", 0, 24)
    ref = window(baseline.output(START), "weather.outdoor.temp_c", 0, 24)
    assert np.allclose(temps - ref, 3.0, atol=0.11)


def test_imposed_anomaly_truth_spans_the_affected_days() -> None:
    world = new_world()
    record_id = world.impose(anomaly("festive_cooking", at(START, 18), 8 * 3600))
    (record,) = [r for r in world.truth_records() if r["record_id"] == record_id]
    assert record["start"] == at(START).isoformat()
    assert record["end"] == at(START + timedelta(days=2)).isoformat()  # 18:00 + 8 h ends on day 2
    assert record["params"]["imposed"] is True
    schema_registry.validate("lab", "truth_record", record)


def test_imposed_occurrence_is_labelled_once_at_its_time() -> None:
    world = new_world()
    request = world.impose(anomaly("night_bucket_fill", at(START), 86_400))
    world.output(START)
    records = [r for r in world.truth_records() if r["label"] == "night_bucket_fill"]
    assert len(records) == 1
    (record,) = records
    assert record["record_id"] != request
    assert record["params"]["natural"] is False
    assert record["params"]["imposed_by"] == request
    assert record["start"] >= at(START, 23).isoformat()


def test_naturally_occurring_anomalies_cannot_be_imposed() -> None:
    with pytest.raises(WorldError, match="occurs naturally"):
        new_world().impose(anomaly("post_outage_tank_refill", at(START), 3600))


def test_anomalies_cannot_rewrite_published_days() -> None:
    world = new_world()
    world.published_until_us = world.day_start_us(START) + 3600 * US
    with pytest.raises(WorldError, match="already-published"):
        world.impose(anomaly("guests", at(START, 18), 3600))
    world.impose(anomaly("guests", at(DAY2, 18), 3600))


def test_natural_truth_survives_forgetting_days() -> None:
    world = new_world()
    world.impose(anomaly("night_bucket_fill", at(START), 86_400))
    world.output(DAY2)
    before = world.truth_records()
    assert [r["label"] for r in before] == ["night_bucket_fill"]
    world.forget_before(DAY2 + timedelta(days=1))
    assert world.truth_records() == before


def test_post_outage_refill_is_recorded() -> None:
    world = new_world()
    world.inject(fault("U1", None, 1, at(START, 18), duration_s=4 * 3600))
    world.output(START)
    refills = [r for r in world.truth_records() if r["label"] == "post_outage_tank_refill"]
    assert len(refills) == 1
    assert refills[0]["start"] == at(START, 22).isoformat()
    for record in world.truth_records():
        schema_registry.validate("lab", "truth_record", record)


# ---------------------------------------------------------------------------- fingerprint

# Day-1 totals of the reference world. If a change to the simulator moves these, the generator's
# output has changed: bump penjaga_sim.GENERATOR_VERSION and update the numbers here.
FINGERPRINT = {
    "water_true_l": 791.71,
    "water_registered_l": 784.789,
    "energy_kwh": 11.229,
    "kwh.app_fridge_kitchen": 1.182,
}


def test_generator_fingerprint(baseline: World) -> None:
    summary = baseline.output(START).summary
    got = {k: round(summary[k], 3) for k in FINGERPRINT}
    assert got == pytest.approx(FINGERPRINT, rel=1e-6), (
        f"generator output changed: bump GENERATOR_VERSION and update FINGERPRINT to {got}"
    )


def test_night_toilet_visits_follow_the_sourced_rates() -> None:
    """Elders wake to void about 1.6 times a night, working-age adults about 0.65 (DS-026)."""
    from penjaga_sim.household import DayModifiers, _Planner

    for archetype, expected in (("retired_couple", 1.6), ("young_couple", 0.65)):
        world = new_world(archetype=archetype)
        cisterns = {b.wc for b in world.model.bathrooms.values()}
        visits = people = 0
        for k in range(200):
            planner = _Planner(world.model, archetype, START + timedelta(days=k), DayModifiers())
            for i, o in enumerate(planner.occupants()):
                before = len(planner.water)
                planner.night_voids(i, o)
                visits += sum(u.fixture in cisterns for u in planner.water[before:])
                people += 1
        assert visits / people == pytest.approx(expected, rel=0.12)
