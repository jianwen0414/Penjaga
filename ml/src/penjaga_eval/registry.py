"""Detectors the harness can run, and their parameters.

Parameters come from, in order: the detector's defaults, the tuned file
``ml/params/<detector>.json`` (written by ``penjaga-eval tune`` from the validation split
only), then explicit overrides.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from penjaga_detect.baselines.mnf import MnfBaseline, MnfParams
from penjaga_detect.detector import Detector, HouseContext
from penjaga_detect.l2_floor import FloorParams, L2SafetyFloor

PARAMS_DIR = Path(__file__).resolve().parents[2] / "params"


@dataclasses.dataclass(frozen=True)
class Entry:
    build: Callable[[HouseContext, Any], Detector]
    params: type[Any]  # a frozen dataclass of parameters


REGISTRY: dict[str, Entry] = {
    "l1_mnf_baseline": Entry(MnfBaseline, MnfParams),
    "l2_safety_floor": Entry(L2SafetyFloor, FloorParams),
}


def tuned(name: str, directory: Path = PARAMS_DIR) -> dict[str, Any]:
    path = directory / f"{name}.json"
    if not path.exists():
        return {}
    return dict(json.loads(path.read_text(encoding="utf-8"))["params"])


def parameters(name: str, overrides: Mapping[str, Any] | None = None) -> Any:
    entry = REGISTRY[name]
    values = {**tuned(name), **(overrides or {})}
    fields = {f.name: f.type for f in dataclasses.fields(entry.params)}
    unknown = set(values) - set(fields)
    if unknown:
        raise KeyError(f"{name}: unknown parameters {sorted(unknown)}")
    defaults = entry.params()
    coerced = {k: type(getattr(defaults, k))(v) for k, v in values.items()}
    return dataclasses.replace(defaults, **coerced)


def build(name: str, house: HouseContext, params: Any) -> Detector:
    if name not in REGISTRY:
        raise KeyError(f"unknown detector {name!r}; known: {sorted(REGISTRY)}")
    return REGISTRY[name].build(house, params)
