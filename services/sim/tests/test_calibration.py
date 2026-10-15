"""Calibration registry: every number has a unit and a basis, and cited sources exist."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from penjaga_sim.calibration import (
    Calibration,
    CalibrationError,
    _parse_param,
    load_calibration,
)
from penjaga_sim.rng import rng

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def cal() -> Calibration:
    return load_calibration()


def test_every_cited_source_is_documented(cal: Calibration) -> None:
    documented = set(
        re.findall(
            r"^## (DS-\d{3})", (ROOT / "docs" / "DATA_SOURCES.md").read_text(encoding="utf-8"), re.M
        )
    )
    assert cal.sources()
    assert cal.sources() <= documented


def test_every_parameter_has_unit_and_basis(cal: Calibration) -> None:
    for path, p in cal.params.items():
        assert p.unit, path
        assert p.basis in {"source", "derived", "assumption"}, path
        if p.basis != "source":
            assert p.note, path
        lo, hi = p.bounds()
        assert lo <= p.value <= hi, path


def test_samples_respect_bounds(cal: Calibration) -> None:
    for path, p in cal.params.items():
        draws = np.asarray(p.sample(rng(1, "calibration", path), 500), dtype=np.float64)
        lo, hi = p.bounds()
        assert np.all(draws >= lo), path
        assert np.all(draws <= hi), path
        assert np.all(np.isfinite(draws)), path


def test_tables_carry_notes(cal: Calibration) -> None:
    for path in (
        "household.routine_clock",
        "appliances.washer.fill_phases",
        "appliances.washer.motor_phases",
    ):
        table = cal.table(path)
        assert len(str(table["note"])) >= 20, path
    phases = cal.table("appliances.washer.fill_phases")
    assert math.isclose(sum(phases["volume_fraction"]), 1.0)
    assert phases["cycle_offset"] == sorted(phases["cycle_offset"])


def test_archetypes_are_consistent(cal: Calibration) -> None:
    archetypes = cal.table("household.archetypes")
    assert set(archetypes) == {
        "working_family",
        "retired_couple",
        "young_couple",
        "large_family",
        "shift_family",
    }
    for name, spec in archetypes.items():
        n = len(spec["roles"])
        assert n == len(spec["bedrooms"]) == len(spec["bathrooms"]) == len(spec["routine"]), name


BASE: dict[str, Any] = {
    "dist": "uniform",
    "low": 1,
    "high": 2,
    "unit": "s",
    "basis": "assumption",
    "note": "a note long enough to pass",
}


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"dist": "weibull"}, "unknown dist"),
        ({"high": None}, "needs"),
        ({"extra": 3}, "unexpected"),
        ({"low": float("inf")}, "finite"),
        ({"low": True}, "finite"),
        ({"low": 3}, "inconsistent"),
        ({"basis": "vibes"}, "basis must be"),
        ({"basis": "source"}, "needs source"),
        ({"basis": "source", "source": "DS-1"}, "needs source"),
        ({"basis": "derived", "source": "DS-001", "note": "short"}, "needs a note"),
        ({"note": None}, "needs a note"),
        ({"unit": ""}, "unit is required"),
    ],
)
def test_bad_parameters_are_rejected(change: dict[str, Any], message: str) -> None:
    table = {k: v for k, v in {**BASE, **change}.items() if v is not None}
    with pytest.raises(CalibrationError, match=message):
        _parse_param("x.y", table)


def test_cited_source_without_note_is_accepted() -> None:
    p = _parse_param(
        "x.y", {"dist": "fixed", "value": 2, "unit": "V", "basis": "source", "source": "DS-022"}
    )
    assert p.value == 2
    assert p.bounds() == (2, 2)


def test_unknown_parameter_is_a_clear_error(cal: Calibration) -> None:
    with pytest.raises(KeyError, match="no calibration parameter"):
        cal["household.nonexistent"]
