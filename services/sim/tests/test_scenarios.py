"""Scenario presets: the file is valid and the weather periods come from the data."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np

from penjaga_contract.lab import FaultType
from penjaga_sim.calibration import load_calibration
from penjaga_sim.scenarios import load_scenarios, period_start
from penjaga_sim.weather import load_weather_cache


def test_every_preset_is_valid() -> None:
    archetypes = frozenset(load_calibration().table("household.archetypes"))
    presets = load_scenarios(archetypes)
    assert len(presets) >= 6
    for p in presets.values():
        assert p.archetype in archetypes
        assert p.description
        for f in p.faults:
            assert set(f.types) <= {t.value for t in FaultType}


def _daily(kind: str) -> tuple[date, np.ndarray]:
    cache = load_weather_cache()
    days = cache.temp_c.size // 24
    if kind == "temp":
        return cache.first_date, cache.temp_c[: days * 24].reshape(days, 24).mean(axis=1)
    return cache.first_date, cache.precip_mm[: days * 24].reshape(days, 24).sum(axis=1)


def test_hottest_and_wettest_weeks_are_found_in_the_latest_cached_year() -> None:
    cache = load_weather_cache()
    year = cache.years.stop - 1
    for period, kind in (("hottest_week", "temp"), ("wettest_week", "precip")):
        start = period_start(period)
        assert start.year == year or start + timedelta(days=6) <= cache.last_date
        first, daily = _daily(kind)
        lo = (date(year, 1, 1) - first).days
        weekly = [daily[i : i + 7].sum() for i in range(lo, daily.size - 6)]
        best = first + timedelta(days=lo + int(np.argmax(weekly)))
        assert start == best
