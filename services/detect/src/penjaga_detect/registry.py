"""Every detector the system knows, and how its parameters are resolved.

The live service and the evaluation harness both build detectors here, so a detector runs with
the same parameters in both. Parameters come from, in order: the detector's defaults, its tuned
file ``<params_dir>/<detector>.json`` (written by ``penjaga-eval tune`` from the validation
split only), then explicit overrides.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from penjaga_detect.baselines.mnf import MnfBaseline, MnfParams
from penjaga_detect.detector import Detector, HouseContext
from penjaga_detect.l1_burst import BurstParams, L1BurstRule
from penjaga_detect.l1_model import L1LeakModel, L1Params
from penjaga_detect.l2_floor import FloorParams, L2SafetyFloor
from penjaga_detect.l3_health import L3HealthBaseline, L3Params
from penjaga_detect.l5_supply import L5SupplyDetector, SupplyParams


@dataclasses.dataclass(frozen=True)
class Entry:
    build: Callable[[HouseContext, Any], Detector]
    params: type[Any]  # a frozen dataclass of parameters


REGISTRY: dict[str, Entry] = {
    "l1_mnf_baseline": Entry(MnfBaseline, MnfParams),
    "l1_burst_rule": Entry(L1BurstRule, BurstParams),
    "l1_leak_model": Entry(L1LeakModel, L1Params),
    "l2_safety_floor": Entry(L2SafetyFloor, FloorParams),
    "l3_health_baseline": Entry(L3HealthBaseline, L3Params),
    "l5_supply": Entry(L5SupplyDetector, SupplyParams),
}


def _entry(name: str) -> Entry:
    if name not in REGISTRY:
        raise KeyError(f"unknown detector {name!r}; known: {sorted(REGISTRY)}")
    return REGISTRY[name]


def tuned(name: str, params_dir: Path | None) -> dict[str, Any]:
    """The tuned parameters recorded for ``name`` ({} without a file or a directory)."""
    if params_dir is None:
        return {}
    path = params_dir / f"{name}.json"
    if not path.exists():
        return {}
    return dict(json.loads(path.read_text(encoding="utf-8"))["params"])


def parameters(
    name: str, params_dir: Path | None, overrides: Mapping[str, Any] | None = None
) -> Any:
    entry = _entry(name)
    values = {**tuned(name, params_dir), **(overrides or {})}
    fields = {f.name for f in dataclasses.fields(entry.params)}
    unknown = set(values) - fields
    if unknown:
        raise KeyError(f"{name}: unknown parameters {sorted(unknown)}")
    defaults = entry.params()
    coerced = {k: type(getattr(defaults, k))(v) for k, v in values.items()}
    return dataclasses.replace(defaults, **coerced)


def build(name: str, house: HouseContext, params: Any) -> Detector:
    return _entry(name).build(house, params)
