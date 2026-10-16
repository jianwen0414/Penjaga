"""The RO purifier: drinking draws come from its storage, RO production refills it from the main
(DS-029, DS-030), and only that refill reaches the meter."""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import timedelta
from typing import Any

import numpy as np
import pytest

from penjaga_contract.core import House
from penjaga_contract.house import HouseIndex
from penjaga_contract.resources import load_house_document
from penjaga_sim.calibration import load_calibration
from penjaga_sim.household import DayModifiers, _Planner, plan_day
from penjaga_sim.model import HouseModel, Purifier
from penjaga_sim.testing import SEED, START, new_world
from penjaga_sim.water import N, WaterInputs, WaterState, simulate_water

DAY = START.toordinal()
QUIET = 3 * 3600  # 03:00: no other demand, no network peak


def _model() -> HouseModel:
    return new_world().model


def _purifier(model: HouseModel) -> Purifier:
    purifier = model.purifier
    assert purifier is not None
    return purifier


def _run(
    model: HouseModel,
    draw: np.ndarray,
    state: WaterState | None = None,
    supply_on: np.ndarray | None = None,
) -> tuple[Any, WaterState]:
    zeros = np.zeros(N)
    supply = np.ones(N, np.bool_) if supply_on is None else supply_on
    full = float(model.index.house.water.tanks[0].capacity_l)
    inputs = WaterInputs(zeros, zeros, supply, zeros, draw)
    return simulate_water(model, DAY, inputs, state or WaterState(tank_l=full))


def _draw_at(second: int, litres: float) -> np.ndarray:
    draw = np.zeros(N)
    draw[second] = litres
    return draw


def test_the_canonical_house_has_a_main_fed_purifier() -> None:
    purifier = _purifier(_model())
    assert purifier.fixture == "fix_kitchen_ro_purifier"
    assert purifier.permeate_lps * 86_400 == pytest.approx(76.0)
    assert 2.3 <= purifier.reject_ratio <= 5.0
    assert purifier.storage_l == pytest.approx(5.8)
    assert purifier.min_pressure_bar == pytest.approx(1.37)


def test_each_draw_is_refilled_from_the_main_at_the_rated_rate() -> None:
    model = _model()
    purifier = _purifier(model)
    day, end = _run(model, _draw_at(QUIET, 0.5))
    feed = day.purifier_feed_lps
    on = np.flatnonzero(feed)
    assert on[0] == QUIET
    assert len(on) == math.ceil(0.5 / purifier.permeate_lps)
    assert np.all(np.diff(on) == 1)  # one continuous refill
    assert feed.sum() == pytest.approx(0.5 * purifier.feed_per_permeate)
    assert end.purifier_deficit_l == 0.0
    assert day.meter_true_lps.sum() == pytest.approx(feed.sum())  # only the refill is metered
    lph = feed[QUIET] * 3600
    assert 10.0 < lph < 20.0  # 76 L/day x (1 + 2.3..5) is 10.4-19.0 L/h


def test_refill_waits_for_the_supply_and_carries_over_midnight() -> None:
    model = _model()
    purifier = _purifier(model)
    _, end = _run(model, _draw_at(N - 10, 1.0))
    assert end.purifier_deficit_l == pytest.approx(1.0 - 10 * purifier.permeate_lps)
    off = np.zeros(N, np.bool_)
    day2, end2 = _run(model, np.zeros(N), end, supply_on=off)
    assert day2.purifier_feed_lps.sum() == 0.0
    assert end2.purifier_deficit_l == pytest.approx(end.purifier_deficit_l)
    day3, end3 = _run(model, np.zeros(N), replace(end2, tank_l=end.tank_l))
    assert np.flatnonzero(day3.purifier_feed_lps)[0] == 0
    assert end3.purifier_deficit_l == 0.0


def test_no_production_below_the_working_pressure() -> None:
    model = _model()
    model.__dict__["purifier"] = replace(_purifier(model), min_pressure_bar=99.0)
    day, end = _run(model, _draw_at(QUIET, 0.5))
    assert day.purifier_feed_lps.sum() == 0.0
    assert end.purifier_deficit_l == pytest.approx(0.5)


def test_the_deficit_never_exceeds_the_storage() -> None:
    model = _model()
    purifier = _purifier(model)
    day, _ = _run(model, _draw_at(QUIET, 20.0))
    assert day.purifier_feed_lps.sum() == pytest.approx(
        purifier.storage_l * purifier.feed_per_permeate
    )


def test_drinks_are_taken_awake_at_home_at_the_sourced_rate() -> None:
    """8 glasses of 250 mL a day (DS-031, DS-032), scaled by the waking time spent at home."""
    world = new_world(archetype="young_couple")
    drinks = expected = 0.0
    for k in range(150):
        planner = _Planner(world.model, "young_couple", START + timedelta(days=k), DayModifiers())
        for i, o in enumerate(planner.occupants()):
            before = len(planner.drinks)
            planner.drinking(i, o)
            new = planner.drinks[before:]
            assert all(o.awake_home[d.start] for d in new)
            assert all(d.litres == 0.25 for d in new)
            drinks += len(new)
            awake = int((~o.asleep).sum())
            expected += 8 * int(o.awake_home.sum()) / max(1, awake)
    assert drinks == pytest.approx(expected, rel=0.05)


def test_nobody_home_means_no_drinks() -> None:
    model = _model()
    plan = plan_day(model, "working_family", START, DayModifiers(holiday_absence=True))
    assert plan.drinks == []


def _model_from(doc: dict[str, Any]) -> HouseModel:
    return HouseModel(HouseIndex(House.model_validate(doc)), load_calibration(), SEED)


def test_a_house_without_a_purifier_draws_nothing() -> None:
    doc = load_house_document()
    doc["water"]["fixtures"] = [
        f for f in doc["water"]["fixtures"] if f["kind"] != "water_purifier"
    ]
    doc["appliances"] = [a for a in doc["appliances"] if a["kind"] != "water_purifier"]
    model = _model_from(doc)
    assert model.purifier is None
    day, end = _run(model, _draw_at(QUIET, 0.5))
    assert day.purifier_feed_lps.sum() == 0.0
    assert end.purifier_deficit_l == 0.0
    assert plan_day(model, "working_family", START).drinks == []


def test_a_tank_fed_purifier_is_refused() -> None:
    doc = load_house_document()
    for f in doc["water"]["fixtures"]:
        if f["kind"] == "water_purifier":
            f["pipe_id"] = "pipe_kitchen_wet_supply"
            f["room_id"] = "room_kitchen_wet"
    for a in doc["appliances"]:
        if a["kind"] == "water_purifier":
            a["room_id"] = "room_kitchen_wet"
            a["socket_group_id"] = "sock_kitchen_wet"
    with pytest.raises(ValueError, match="must be fed from the main"):
        _ = _model_from(doc).purifier
