"""Detectors the harness can run, with tuned parameters from ``ml/params``.

The registry itself lives in ``penjaga_detect.registry``, shared with the live service; this
module fixes the tuned-parameter directory to ``ml/params``.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from penjaga_detect import registry as _shared
from penjaga_detect.detector import Detector, HouseContext
from penjaga_detect.registry import REGISTRY, Entry

PARAMS_DIR = Path(__file__).resolve().parents[2] / "params"

__all__ = ["PARAMS_DIR", "REGISTRY", "Entry", "build", "parameters", "tuned"]


def tuned(name: str, directory: Path = PARAMS_DIR) -> dict[str, Any]:
    return _shared.tuned(name, directory)


def parameters(name: str, overrides: Mapping[str, Any] | None = None) -> Any:
    return _shared.parameters(name, PARAMS_DIR, overrides)


def build(name: str, house: HouseContext, params: Any) -> Detector:
    return _shared.build(name, house, params)
