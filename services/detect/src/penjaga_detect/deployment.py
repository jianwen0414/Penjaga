"""Which detectors the live service runs, and with which parameters.

``deployment.toml`` lists them by name. Only detectors that won on M11 against a baseline are
deployed (PRD §15.1; docs/DECISIONS.md D-022). Parameters are the tuned ones in the params
directory (``ml/params``), resolved by ``penjaga_detect.registry`` exactly as the evaluation
resolves them.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from penjaga_detect import registry
from penjaga_detect.detector import HouseContext
from penjaga_detect.engine import DetectorSpec

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "deployment.toml"


@dataclass(frozen=True)
class Deployment:
    detectors: tuple[str, ...]
    params_dir: Path | None

    @staticmethod
    def load(path: Path = DEFAULT_PATH, params_dir: Path | None = None) -> Deployment:
        doc = tomllib.loads(path.read_text(encoding="utf-8"))
        names = doc.get("deployed")
        if not isinstance(names, list) or not names or not all(isinstance(n, str) for n in names):
            raise ValueError(f"{path}: 'deployed' must be a non-empty list of detector names")
        unknown = sorted(set(names) - set(registry.REGISTRY))
        if unknown:
            raise ValueError(f"{path}: unknown detectors {unknown}")
        if len(set(names)) != len(names):
            raise ValueError(f"{path}: a detector is listed twice")
        return Deployment(tuple(names), params_dir)

    def params(self, name: str) -> Any:
        return registry.parameters(name, self.params_dir)

    def specs(self, house: HouseContext) -> list[DetectorSpec]:
        """One spec per deployed detector; building one instance up front checks that it can
        be built (parameters, model artifacts) before the service starts."""
        out = []
        for name in self.detectors:
            params = self.params(name)
            probe = registry.build(name, house, params)

            def build(name: str = name, params: Any = params) -> Any:
                return registry.build(name, house, params)

            out.append(DetectorSpec(name, probe.info.layer, build))
        return out
