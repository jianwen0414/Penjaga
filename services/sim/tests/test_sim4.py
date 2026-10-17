"""penjaga-sim/4: low supply pressure (DS-033 to DS-035), the purifier's electrical load (DS-036,
DS-037), guided-check actions and the per-minute water truth."""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pytest

from penjaga_sim.actions import CheckAction
from penjaga_sim.testing import START, anomaly, at, fault, new_world
from penjaga_sim.water import low_pressure_weight
from penjaga_sim.world import World, WorldError

US = 1_000_000
DAY1 = START + timedelta(days=1)


def _minute(hour: float) -> int:
    return round(hour * 60)


def _us(day_offset: int, hour: float) -> int:
    return int(at(START + timedelta(days=day_offset), hour).timestamp()) * US


# ---------------------------------------------------------------------------- low pressure


def test_low_pressure_weight_is_low_by_day_and_recovers_at_night() -> None:
    model = new_world().model
    hour = 3600
    recovery = model.draws.house("water.supply.low_recovery_hours")
    regained = model.draws.house("water.supply.low_night_recovery")
    night_level = 1.0 - regained
    first = low_pressure_weight(model, yesterday=False, today=True)
    assert first[3 * hour] == 0.0  # the night before the first low day is normal
    assert first[12 * hour] == 1.0
    end_of_recovery = 21.7 + recovery  # evening peak 20.5 h + one 1.2 h width
    if end_of_recovery < 24:
        assert first[round(end_of_recovery * hour) + 60] == pytest.approx(night_level)
    middle = low_pressure_weight(model, yesterday=True, today=True)
    assert middle[12 * hour] == 1.0
    assert middle[0] >= night_level - 1e-12  # still at (or recovering to) the night level
    after = low_pressure_weight(model, yesterday=True, today=False)
    assert after[12 * hour] == 0.0  # the day after the last low day is normal
    assert np.all(np.diff(after[: 8 * hour]) <= 1e-12)  # only ever recovering
    assert low_pressure_weight(model, yesterday=False, today=False).max() == 0.0


def _low_world() -> World:
    """A world whose draws give a deep daytime low and a good night recovery."""
    for seed in range(1, 200):
        world = new_world(seed=seed)
        draws = world.model.draws
        if (
            draws.house("water.supply.low_day_bar") < 0.7
            and draws.house("water.supply.low_night_recovery") > 0.6
        ):
            return world
    raise AssertionError("no such seed")


def test_low_pressure_moves_the_tank_refill_into_the_night() -> None:
    low = _low_world()
    normal = new_world(seed=low.config.seed)
    low.impose(anomaly("low_supply_pressure", at(START), 3 * 86_400))
    n, lo = normal.output(DAY1).water_truth, low.output(DAY1).water_truth
    noon = slice(_minute(11), _minute(13))
    assert lo["pressure_bar"][noon].mean() < 0.8
    assert n["pressure_bar"][noon].mean() > 1.2
    day = slice(_minute(8), _minute(20))
    assert lo["tank_inflow_lpm"][day].sum() < 0.5 * n["tank_inflow_lpm"][day].sum()
    assert lo["tank_l"][day].min() < n["tank_l"][day].min()
    nxt = START + timedelta(days=2)

    def night(world: World, today: dict[str, np.ndarray]) -> float:
        tomorrow = world.output(nxt).water_truth["tank_inflow_lpm"][: _minute(6)]
        return float(today["tank_inflow_lpm"][_minute(22) :].sum() + tomorrow.sum())

    assert night(low, lo) > night(normal, n)


def test_low_pressure_is_recorded_as_an_imposed_anomaly() -> None:
    world = new_world()
    world.impose(anomaly("low_supply_pressure", at(START, 5), 86_400))
    records = [r for r in world.truth_records() if r["label"] == "low_supply_pressure"]
    assert len(records) == 1
    assert records[0]["start"] == at(START).isoformat()
    assert records[0]["end"] == at(START + timedelta(days=2)).isoformat()  # whole days


def test_households_never_see_low_pressure_without_the_anomaly() -> None:
    world = new_world()
    for d in range(2):
        p = world.output(START + timedelta(days=d)).water_truth["pressure_bar"]
        assert p.min() > 1.0


# ---------------------------------------------------------------------------- purifier load


def test_purifier_uses_its_standby_energy_in_heater_and_compressor_runs() -> None:
    world = new_world()
    out = world.output(DAY1)
    app = "app_ro_purifier_kitchen"
    kwh = out.summary[f"kwh.{app}"]
    standby = world.model.draws.house("appliances.purifier.standby_kwh_per_day")
    assert 0.52 <= standby <= 0.80
    # the voltage varies a little around the mean the cycles were sized with
    assert kwh == pytest.approx(standby, rel=0.08)


def test_purifier_cycles_continue_across_midnight() -> None:
    world = new_world()
    a = world.output(START)
    b = world.output(DAY1)
    assert a.summary["kwh.app_ro_purifier_kitchen"] == pytest.approx(
        b.summary["kwh.app_ro_purifier_kitchen"], rel=0.05
    )


# ---------------------------------------------------------------------------- guided checks


def _concealed_leak_world(lpm: float = 0.5) -> World:
    world = new_world(
        enabled_sensors=frozenset(
            {"sensor_water_meter_main", "sensor_tank_level", "sensor_water_pressure_inlet"}
        )
    )
    world.inject(fault("W4", "pipe_bath2_cold_supply", lpm, at(START, 1)))
    return world


def test_closing_the_branch_valve_stops_a_leak_behind_it() -> None:
    world = _concealed_leak_world()
    start, end = _us(1, 3.0), _us(1, 3.5)
    world.act(CheckAction(start, end, ("valve_bath2_branch",)))
    flow = world.output(DAY1).water_truth["meter_true_lpm"]
    before = flow[_minute(2.75) : _minute(3.0)].mean()
    after = flow[_minute(3.1) : _minute(3.5)].mean()
    assert before == pytest.approx(0.5, abs=0.15)
    assert after < 0.02
    assert flow[_minute(3.6) : _minute(3.9)].mean() > 0.35  # reopened: the leak runs again


def test_closing_another_branch_leaves_the_leak_running() -> None:
    world = _concealed_leak_world()
    world.act(CheckAction(_us(1, 3.0), _us(1, 3.5), ("valve_bath3_branch",)))
    flow = world.output(DAY1).water_truth["meter_true_lpm"]
    assert flow[_minute(3.1) : _minute(3.5)].mean() == pytest.approx(0.5, abs=0.15)


def test_the_main_stop_valve_stops_the_meter_but_not_a_tank_fed_leak() -> None:
    world = _concealed_leak_world(1.0)
    world.act(CheckAction(_us(1, 3.0), _us(1, 4.0), ("valve_main_stop",)))
    truth = world.output(DAY1).water_truth
    closed = slice(_minute(3.0) + 1, _minute(4.0))
    assert truth["meter_true_lpm"][closed].max() == 0.0
    tank = truth["tank_l"][closed]
    assert tank[-1] < tank[0] - 40  # about 60 L drawn by the leak in the hour
    assert truth["pressure_bar"][closed].min() > 1.0  # the network pressure stays


def test_holding_water_use_removes_the_household_uses() -> None:
    world = new_world()
    base = world.output(DAY1).water_truth["meter_true_lpm"]
    window = slice(_minute(6), _minute(23))
    held = new_world()
    held.act(CheckAction(_us(1, 6.0), _us(1, 23.0)))
    flows = held.output(DAY1).water_truth["meter_true_lpm"]
    assert flows[window].sum() < 0.2 * base[window].sum()


def test_actions_are_checked() -> None:
    world = new_world()
    with pytest.raises(WorldError, match="float"):
        world.act(CheckAction(_us(1, 3), _us(1, 4), ("valve_roof_tank_float",)))
    with pytest.raises(WorldError, match="unknown valve"):
        world.act(CheckAction(_us(1, 3), _us(1, 4), ("valve_nowhere",)))
    with pytest.raises(WorldError):
        world.act(CheckAction(_us(1, 4), _us(1, 3)))
    world.published_until_us = _us(1, 5)
    with pytest.raises(WorldError, match="published"):
        world.act(CheckAction(_us(1, 3), _us(1, 4)))


def test_an_action_leaves_the_days_before_it_unchanged() -> None:
    world = _concealed_leak_world()
    before = world.output(START).water_truth["meter_true_lpm"].copy()
    world.act(CheckAction(_us(1, 3.0), _us(1, 3.5), ("valve_bath2_branch",)))
    assert np.array_equal(world.output(START).water_truth["meter_true_lpm"], before)


# ---------------------------------------------------------------------------- water truth


def test_water_truth_has_one_row_per_minute() -> None:
    truth = new_world().output(START).water_truth
    assert set(truth) == {
        "tank_l",
        "meter_true_lpm",
        "tank_inflow_lpm",
        "tank_outflow_lpm",
        "overflow_lpm",
        "pressure_bar",
    }
    assert all(v.shape == (1440,) for v in truth.values())
    assert np.all(truth["tank_l"] <= 1364.0 + 1e-9)


# ---------------------------------------------------------------------------- MCB switched off


def test_switching_off_a_leaking_circuit_removes_its_leakage_from_the_rccb() -> None:
    world = new_world()
    world.inject(fault("E2", "circ_sockets_kitchen", 1.5, at(START, 1)))
    start, end = _us(1, 20.0), _us(1, 20.5)
    world.act(
        CheckAction(start, end, hold_water_use=False, switch_off_circuits=("circ_sockets_kitchen",))
    )
    out = world.output(DAY1)
    residual = out.streams["elec.rcd.rcd_sockets.residual_ma"].values
    current = out.streams["elec.circuit.circ_sockets_kitchen.current_a"].values
    second = 3600
    before = np.nanmean(residual[int(19.6 * second) : 20 * second])
    during = np.nanmean(residual[int(20.1 * second) : int(20.5 * second)])
    assert before - during > 1.0  # the 1.5 mA fault and the circuit's own leakage
    assert np.nanmax(current[int(20.01 * second) : int(20.5 * second)]) < 0.05


def test_switching_off_another_circuit_leaves_the_leak() -> None:
    world = new_world()
    world.inject(fault("E2", "circ_sockets_kitchen", 1.5, at(START, 1)))
    world.act(
        CheckAction(
            _us(1, 20.0),
            _us(1, 20.5),
            hold_water_use=False,
            switch_off_circuits=("circ_sockets_living",),
        )
    )
    residual = world.output(DAY1).streams["elec.rcd.rcd_sockets.residual_ma"].values
    assert np.nanmean(residual[int(20.1 * 3600) : int(20.5 * 3600)]) > 1.4


def test_unknown_circuits_cannot_be_switched_off() -> None:
    with pytest.raises(WorldError, match="unknown circuit"):
        new_world().act(CheckAction(_us(1, 3), _us(1, 4), switch_off_circuits=("circ_nowhere",)))
