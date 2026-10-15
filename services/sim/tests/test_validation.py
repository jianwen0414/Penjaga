"""Simulator validation against sourced aggregates (docs/DATA_SOURCES.md).

The bands are deliberately honest rather than tight: most behaviour parameters are assumptions,
and the aggregates come from surveys of mixed housing stock. tools/validation_report.py prints
the full comparison (several seeds, two weeks per archetype) for docs/reports.
"""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pytest

from penjaga_sim.calibration import load_calibration
from penjaga_sim.testing import START, new_world

ARCHETYPES = ["working_family", "retired_couple", "young_couple", "large_family", "shift_family"]
DS012_L_PER_PERSON_DAY = 241.0  # Selangor/KL/Putrajaya domestic average (SPAN 2023)
DS014_KWH_MONTH = (345.0, 648.31)  # two Malaysian household surveys
DAYS = range(1, 7)  # day 0 tops up the roof tank from its random starting level


@pytest.fixture(scope="module")
def totals() -> dict[str, tuple[float, float]]:
    """Per archetype: mean litres per person per day, and kWh per 30.4-day month."""
    out = {}
    for archetype in ARCHETYPES:
        world = new_world(archetype=archetype, seed=1)
        summaries = [world.output(START + timedelta(days=k)).summary for k in DAYS]
        litres = np.mean([s["water_true_l"] / s["occupants"] for s in summaries])
        kwh = np.mean([s["energy_kwh"] for s in summaries]) * 30.4
        out[archetype] = (float(litres), float(kwh))
    return out


def test_water_per_person_matches_ds012(totals: dict[str, tuple[float, float]]) -> None:
    per_person = [litres for litres, _ in totals.values()]
    assert all(150 <= x <= 330 for x in per_person), totals
    assert 0.8 * DS012_L_PER_PERSON_DAY <= np.mean(per_person) <= 1.2 * DS012_L_PER_PERSON_DAY


def test_household_energy_matches_ds014(totals: dict[str, tuple[float, float]]) -> None:
    monthly = [kwh for _, kwh in totals.values()]
    assert all(150 <= x <= 800 for x in monthly), totals
    low, high = DS014_KWH_MONTH
    assert 0.7 * low <= np.mean(monthly) <= high


def test_bigger_households_use_more(totals: dict[str, tuple[float, float]]) -> None:
    cal = load_calibration()
    size = {a: len(cal.table(f"household.archetypes.{a}")["roles"]) for a in ARCHETYPES}
    litres_household = {a: totals[a][0] * size[a] for a in ARCHETYPES}
    assert litres_household["large_family"] > litres_household["young_couple"]
    assert totals["large_family"][1] > totals["young_couple"][1]
