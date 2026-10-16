"""Scenario presets (``scenarios.toml``): a household, a weather period, drawn faults.

Applying a preset resets the world. Each fault is then drawn with ``random_spec`` (location
and magnitude from the calibrated catalogue, as "surprise me" does), and anomalies are imposed
on whole days. The draws come from ``rng(seed, "scenario", name, i)``, so a seed reproduces
a scenario exactly. Nothing about the outcome is scripted: detection still has to find the
faults in the readings.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import cache
from importlib.resources import files
from typing import Any

import numpy as np

from penjaga_contract.lab import AnomalyType, FaultType
from penjaga_sim.weather import load_weather_cache
from penjaga_sim.world import IMPOSABLE_ANOMALIES

PERIODS = ("any", "hottest_week", "wettest_week")


class ScenarioError(ValueError):
    """An invalid preset file (a bug in scenarios.toml, caught by the tests)."""


class UnknownScenarioError(LookupError):
    """No preset of that name (404)."""


@dataclass(frozen=True)
class FaultDraw:
    types: tuple[str, ...]
    start_day: tuple[float, float]


@dataclass(frozen=True)
class AnomalyDays:
    anomaly_type: str
    start_day: int
    days: int


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    archetype: str
    period: str
    faults: tuple[FaultDraw, ...]
    anomalies: tuple[AnomalyDays, ...]

    def document(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "archetype": self.archetype,
            "period": self.period,
            "faults": [
                {"types": list(f.types), "start_day": list(f.start_day)} for f in self.faults
            ],
            "anomalies": [
                {"anomaly_type": a.anomaly_type, "start_day": a.start_day, "days": a.days}
                for a in self.anomalies
            ],
        }


def _scenario(name: str, raw: dict[str, Any], archetypes: set[str]) -> Scenario:
    known_faults = {f.value for f in FaultType}
    imposable = {a.value for a in IMPOSABLE_ANOMALIES}
    if raw.get("archetype") not in archetypes:
        raise ScenarioError(f"{name}: unknown archetype {raw.get('archetype')!r}")
    period = raw.get("period", "any")
    if period not in PERIODS:
        raise ScenarioError(f"{name}: period must be one of {PERIODS}")
    faults = []
    for f in raw.get("faults", []):
        types = tuple(f["types"])
        lo, hi = (float(x) for x in f["start_day"])
        if not types or not set(types) <= known_faults:
            raise ScenarioError(f"{name}: fault types must be from {sorted(known_faults)}")
        if not 0 < lo <= hi:
            raise ScenarioError(f"{name}: start_day must be 0 < low <= high")
        faults.append(FaultDraw(types, (lo, hi)))
    anomalies = []
    for a in raw.get("anomalies", []):
        if a["type"] not in imposable:
            raise ScenarioError(f"{name}: {a['type']!r} cannot be imposed")
        if int(a["start_day"]) < 1 or int(a["days"]) < 1:
            raise ScenarioError(f"{name}: anomalies start on day 1 or later and last 1+ days")
        anomalies.append(AnomalyDays(a["type"], int(a["start_day"]), int(a["days"])))
    description = str(raw.get("description", "")).strip()
    if not description:
        raise ScenarioError(f"{name}: needs a description")
    return Scenario(name, description, raw["archetype"], period, tuple(faults), tuple(anomalies))


@cache
def load_scenarios(archetypes: frozenset[str]) -> dict[str, Scenario]:
    text = files("penjaga_sim").joinpath("scenarios.toml").read_text(encoding="utf-8")
    raw = tomllib.loads(text)
    return {name: _scenario(name, body, set(archetypes)) for name, body in raw.items()}


@cache
def period_start(period: str) -> date:
    """Start of the hottest or wettest 7-day window in the latest cached weather year."""
    cache_ = load_weather_cache()
    days = cache_.temp_c.size // 24
    first = cache_.first_date
    year = cache_.years.stop - 1
    lo = (date(year, 1, 1) - first).days
    if period == "hottest_week":
        daily = cache_.temp_c[: days * 24].reshape(days, 24).mean(axis=1)
    elif period == "wettest_week":
        daily = cache_.precip_mm[: days * 24].reshape(days, 24).sum(axis=1)
    else:
        raise ScenarioError(f"no fixed start for period {period!r}")
    window = np.convolve(daily[lo:], np.ones(7), mode="valid")
    return first + timedelta(days=lo + int(np.argmax(window)))


def start_of(day: date, zone: Any) -> datetime:
    return datetime.combine(day, time.min, tzinfo=zone)


def anomaly_document(a: AnomalyDays, start: datetime) -> dict[str, Any]:
    return {
        "anomaly_type": AnomalyType(a.anomaly_type).value,
        "start_at": (start + timedelta(days=a.start_day)).isoformat(),
        "duration_s": a.days * 86_400,
    }
