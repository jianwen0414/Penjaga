"""Calibration registry: every number the simulator uses, with where it comes from.

Each parameter is a TOML table with a value or a distribution and a ``basis``:

* ``source``     taken from a cited data source (``source = "DS-nnn"``, docs/DATA_SOURCES.md)
* ``derived``    computed from cited values (``source`` plus a ``note`` with the derivation)
* ``assumption`` our choice, with a ``note`` giving the rationale (no source exists or was found)

Distributions: ``fixed`` (value), ``uniform`` (low, high), ``triangular`` (low, mode, high),
``normal`` (mean, sd, optional low/high clip), ``lognormal`` (median, sigma, optional clip).
Loading fails on any parameter without a valid basis, so no unexplained number can enter
the simulator (PRD §8.2).
"""

from __future__ import annotations

import math
import re
import tomllib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from functools import cache
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

import numpy as np

Basis = Literal["source", "derived", "assumption"]
DistName = Literal["fixed", "uniform", "triangular", "normal", "lognormal"]
_SOURCE_RE = re.compile(r"^DS-\d{3}$")
_DIST_KEYS: dict[str, tuple[set[str], set[str]]] = {
    "fixed": ({"value"}, set()),
    "uniform": ({"low", "high"}, set()),
    "triangular": ({"low", "mode", "high"}, set()),
    "normal": ({"mean", "sd"}, {"low", "high"}),
    "lognormal": ({"median", "sigma"}, {"low", "high"}),
}
_META_KEYS = {"dist", "basis", "source", "note", "unit"}
MIN_NOTE_CHARS = 20


class CalibrationError(ValueError):
    pass


@dataclass(frozen=True)
class Param:
    path: str
    dist: DistName
    basis: Basis
    unit: str
    args: Mapping[str, float]
    source: str | None = None
    note: str | None = None

    @property
    def value(self) -> float:
        """The fixed value, or the distribution's central value (mean/median/mode)."""
        a = self.args
        match self.dist:
            case "fixed":
                return a["value"]
            case "uniform":
                return (a["low"] + a["high"]) / 2
            case "triangular":
                return a["mode"]
            case "normal":
                return a["mean"]
            case "lognormal":
                return a["median"]
        raise AssertionError(self.dist)  # pragma: no cover

    def sample(self, rng: np.random.Generator, size: int | None = None) -> Any:
        a = self.args
        match self.dist:
            case "fixed":
                out: Any = np.full(size, a["value"]) if size is not None else a["value"]
                return out
            case "uniform":
                return rng.uniform(a["low"], a["high"], size)
            case "triangular":
                return rng.triangular(a["low"], a["mode"], a["high"], size)
            case "normal":
                draw = rng.normal(a["mean"], a["sd"], size)
            case "lognormal":
                draw = rng.lognormal(math.log(a["median"]), a["sigma"], size)
        return np.clip(draw, a.get("low", -np.inf), a.get("high", np.inf))

    def bounds(self) -> tuple[float, float]:
        a = self.args
        match self.dist:
            case "fixed":
                return a["value"], a["value"]
            case "uniform" | "triangular":
                return a["low"], a["high"]
        return a.get("low", -math.inf), a.get("high", math.inf)


def _parse_param(path: str, table: Mapping[str, Any]) -> Param:
    dist = table.get("dist", "fixed")
    if dist not in _DIST_KEYS:
        raise CalibrationError(f"{path}: unknown dist {dist!r}")
    required, optional = _DIST_KEYS[dist]
    numeric = {k: v for k, v in table.items() if k not in _META_KEYS}
    missing = required - numeric.keys()
    extra = numeric.keys() - required - optional
    if missing or extra:
        raise CalibrationError(
            f"{path}: {dist} needs {sorted(required)}; "
            f"missing {sorted(missing)}, unexpected {sorted(extra)}"
        )
    for k, v in numeric.items():
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
            raise CalibrationError(f"{path}.{k}: must be a finite number")
    args = {k: float(v) for k, v in numeric.items()}
    _check_shape(path, dist, args)
    basis = table.get("basis")
    source = table.get("source")
    note = table.get("note")
    unit = table.get("unit")
    if basis not in ("source", "derived", "assumption"):
        raise CalibrationError(f"{path}: basis must be source, derived or assumption")
    if basis in ("source", "derived") and (
        not isinstance(source, str) or not _SOURCE_RE.match(source)
    ):
        raise CalibrationError(f"{path}: basis {basis} needs source = 'DS-nnn'")
    if basis in ("derived", "assumption") and (
        not isinstance(note, str) or len(note) < MIN_NOTE_CHARS
    ):
        raise CalibrationError(f"{path}: basis {basis} needs a note explaining it")
    if not isinstance(unit, str) or not unit:
        raise CalibrationError(f"{path}: unit is required (use '1' for dimensionless)")
    return Param(path, dist, basis, unit, args, source, note)


def _check_shape(path: str, dist: str, a: Mapping[str, float]) -> None:
    ok = True
    if dist == "uniform":
        ok = a["low"] < a["high"]
    elif dist == "triangular":
        ok = a["low"] <= a["mode"] <= a["high"] and a["low"] < a["high"]
    elif dist == "normal":
        ok = a["sd"] > 0 and a.get("low", -math.inf) < a.get("high", math.inf)
    elif dist == "lognormal":
        ok = (
            a["median"] > 0 and a["sigma"] > 0 and a.get("low", -math.inf) < a.get("high", math.inf)
        )
    if not ok:
        raise CalibrationError(f"{path}: inconsistent {dist} arguments {dict(a)}")


def _walk(prefix: str, table: Mapping[str, Any]) -> Iterator[tuple[str, Mapping[str, Any]]]:
    """Yield parameter tables (those with a 'basis' key); recurse into plain tables."""
    for key, value in table.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            if "basis" in value:
                yield path, value
            else:
                yield from _walk(path, value)


@dataclass(frozen=True)
class Calibration:
    params: Mapping[str, Param]
    raw: Mapping[str, Any] = field(repr=False)

    def __getitem__(self, path: str) -> Param:
        try:
            return self.params[path]
        except KeyError:
            raise KeyError(f"no calibration parameter {path!r}") from None

    def value(self, path: str) -> float:
        return self[path].value

    def table(self, path: str) -> Mapping[str, Any]:
        """A non-parameter table (e.g. archetype definitions)."""
        node: Any = self.raw
        for part in path.split("."):
            node = node[part]
        if not isinstance(node, dict):
            raise KeyError(path)
        return node

    def sources(self) -> set[str]:
        return {p.source for p in self.params.values() if p.source}


def calibration_dir() -> Path:
    return Path(str(files("penjaga_sim") / "calibration"))


@cache
def load_calibration(directory: Path | None = None) -> Calibration:
    directory = directory or calibration_dir()
    raw: dict[str, Any] = {}
    params: dict[str, Param] = {}
    for path in sorted(directory.glob("*.toml")):
        with path.open("rb") as fh:
            data = tomllib.load(fh)
        domain = path.stem
        if domain in raw:
            raise CalibrationError(f"duplicate calibration domain {domain}")
        raw[domain] = data
        for p_path, table in _walk(domain, data):
            params[p_path] = _parse_param(p_path, table)
    if not params:
        raise CalibrationError(f"no calibration parameters found in {directory}")
    return Calibration(params, raw)
