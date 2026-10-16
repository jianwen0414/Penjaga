"""Evaluation suites: which episodes exist, deterministically, for each split.

A suite file (``ml/suites/<name>.toml``) describes the episode population; ``manifests`` turns
it into EpisodeManifests. Everything random comes from ``rng(suite seed, split, index, ...)``,
so the same suite always yields the same manifests, and splits never share a seed (each split
has its own key) or a weather year (the suite's years must not overlap).
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from penjaga_contract.lab import EpisodeManifest
from penjaga_contract.validate import load_context, load_index
from penjaga_sim import GENERATOR_VERSION
from penjaga_sim.calibration import load_calibration
from penjaga_sim.export import manifest_document
from penjaga_sim.faults import random_spec, spec_document
from penjaga_sim.model import HouseModel
from penjaga_sim.rng import rng, stable_key

SPLITS = ("train", "validation", "test")
SUITES_DIR = Path(__file__).resolve().parents[2] / "suites"
MAX_SEED = 2**53 - 1


class SuiteError(ValueError):
    pass


@dataclass(frozen=True)
class SplitSpec:
    years: tuple[int, ...]
    archetypes: tuple[str, ...]
    episodes_per_fault_type: int
    fault_free_episodes: int


@dataclass(frozen=True)
class Suite:
    name: str
    layers: tuple[str, ...]
    days: int
    fault_types: tuple[str, ...]
    fault_start_days: tuple[float, float]
    anomaly_prob: float
    anomaly_types: tuple[str, ...]
    anomaly_days: tuple[int, int]
    splits: dict[str, SplitSpec]
    sensors: tuple[str, ...] | None = None  # sensor kit; None = the full kit

    @property
    def seed(self) -> int:
        return stable_key(f"suite:{self.name}") % MAX_SEED


def load_suite(name: str, directory: Path = SUITES_DIR) -> Suite:
    path = directory / f"{name}.toml"
    if not path.exists():
        raise SuiteError(f"no suite {name!r} in {directory}")
    raw: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8"))
    if raw.get("name") != name:
        raise SuiteError(f"{path.name}: name must be {name!r}")
    splits = {
        split: SplitSpec(
            years=tuple(raw["split"][split]["years"]),
            archetypes=tuple(raw["split"][split]["archetypes"]),
            episodes_per_fault_type=int(raw["split"][split]["episodes_per_fault_type"]),
            fault_free_episodes=int(raw["split"][split]["fault_free_episodes"]),
        )
        for split in SPLITS
    }
    suite = Suite(
        name=name,
        layers=tuple(raw["layers"]),
        days=int(raw["days"]),
        fault_types=tuple(raw["fault_types"]),
        fault_start_days=(float(raw["fault_start_days"][0]), float(raw["fault_start_days"][1])),
        anomaly_prob=float(raw["anomaly_prob"]),
        anomaly_types=tuple(raw["anomaly_types"]),
        anomaly_days=(int(raw["anomaly_days"][0]), int(raw["anomaly_days"][1])),
        splits=splits,
        sensors=tuple(raw["sensors"]) if "sensors" in raw else None,
    )
    _check(suite)
    return suite


def _check(suite: Suite) -> None:
    years = [set(s.years) for s in suite.splits.values()]
    for i, a in enumerate(years):
        for b in years[i + 1 :]:
            if a & b:
                raise SuiteError(f"{suite.name}: splits share weather years {sorted(a & b)}")
    if suite.sensors is not None:
        house_sensors = {s.id for s in load_context().house.sensors}
        if not suite.sensors or not set(suite.sensors) <= house_sensors:
            raise SuiteError(f"{suite.name}: sensors must be a non-empty subset of the house's")
    known = set(load_calibration().table("household.archetypes"))
    for split, spec in suite.splits.items():
        if not set(spec.archetypes) <= known:
            raise SuiteError(f"{suite.name}.{split}: unknown archetypes")
    lo, hi = suite.fault_start_days
    if not 0 < lo <= hi < suite.days:
        raise SuiteError(f"{suite.name}: fault_start_days must lie inside the episode")


def _start(r: np.random.Generator, year: int, days: int, zone: ZoneInfo) -> datetime:
    first = date(year, 1, 1)
    span = (date(year, 12, 31) - first).days - days
    day = first + timedelta(days=int(r.integers(0, span + 1)))
    return datetime.combine(day, datetime.min.time(), tzinfo=zone)


def manifests(suite: Suite, split: str) -> list[EpisodeManifest]:
    """The split's episodes, in a fixed order: each fault type in turn, then fault-free ones."""
    spec = suite.splits[split]
    ctx = load_context()
    zone = ZoneInfo(ctx.house.location.timezone)
    sensors = sorted(suite.sensors or (s.id for s in ctx.house.sensors))
    plan: list[str | None] = [
        t for t in suite.fault_types for _ in range(spec.episodes_per_fault_type)
    ]
    plan += [None] * spec.fault_free_episodes
    out = []
    for i, fault_type in enumerate(plan):
        r = rng(suite.seed, split, i)
        seed = int(r.integers(0, MAX_SEED))
        archetype = spec.archetypes[int(r.integers(0, len(spec.archetypes)))]
        year = spec.years[int(r.integers(0, len(spec.years)))]
        start = _start(r, year, suite.days, zone)
        faults = []
        if fault_type is not None:
            lo, hi = suite.fault_start_days
            at = start + timedelta(days=float(r.uniform(lo, hi)))
            at = at.replace(microsecond=0)
            model = HouseModel(load_index(), load_calibration(), seed)
            faults.append(random_spec(model, fault_type, at, r))
        anomalies = []
        if suite.anomaly_types and r.random() < suite.anomaly_prob:
            kind = suite.anomaly_types[int(r.integers(0, len(suite.anomaly_types)))]
            first_day = int(r.integers(1, suite.days - 1))
            length = int(r.integers(suite.anomaly_days[0], suite.anomaly_days[1] + 1))
            anomalies.append(
                {
                    "anomaly_type": kind,
                    "start_at": (start + timedelta(days=first_day)).isoformat(),
                    "duration_s": 86_400 * min(length, suite.days - first_day),
                }
            )
        doc = {
            "schema_version": "1.0",
            "episode_id": f"ep_{suite.name}_{split}_{i:04d}",
            "split": split,
            "seed": seed,
            "house_id": ctx.house_id,
            "household_archetype": archetype,
            "start": start.isoformat(),
            "duration_days": suite.days,
            "enabled_sensor_ids": sensors,
            "faults": [spec_document(f) for f in faults],
            "anomalies": anomalies,
            "generator_version": GENERATOR_VERSION,
        }
        out.append(EpisodeManifest.model_validate(doc))
    return out


def manifests_digest(items: list[EpisodeManifest]) -> str:
    """Hash of the canonical JSON of a list of manifests (what sealing records)."""
    text = json.dumps([manifest_document(m) for m in items], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()
