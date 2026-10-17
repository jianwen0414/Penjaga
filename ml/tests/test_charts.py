"""Pitch-deck charts are drawn from the right saved reports."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from penjaga_eval import charts, registry


def _write(directory: Path, name: str, report: dict[str, Any]) -> None:
    (directory / name).mkdir(parents=True)
    (directory / name / "report.json").write_text(json.dumps(report), encoding="utf-8")


def _outlook(method: str, created: str, dry_hours: list[tuple[float, float]]) -> dict[str, Any]:
    cases = [
        {"episode": f"ep{i}", "fault_type": "U1", "ran_dry": True, "hours": h, "true_hours": t}
        for i, (h, t) in enumerate(dry_hours)
    ]
    return {
        "kind": "tank_outlook",
        "suite": "l5_drain_v0",
        "split": "test",
        "created": created,
        "params": {"outlook_method": method},
        "cases": cases,
        "metrics": {"hours_mae_dry": 1.0, "hours_rel_error_dry": 0.05},
    }


def _localised(suite: str, detector: str, created: str) -> dict[str, Any]:
    group = {
        "n": 10, "top1_before": 0.3, "top1_after": 0.8, "top3_before": 0.5, "top3_after": 1.0,
        "checks_mean": 4.0,
    }  # fmt: skip
    return {
        "kind": "localisation",
        "suite": suite,
        "split": "test",
        "created": created,
        "detectors": {detector: {"all": group}},
    }


def test_the_outlook_chart_uses_the_deployed_method(tmp_path: Path) -> None:
    deployed = registry.parameters("l5_supply").outlook_method
    other = "slope_30min" if deployed != "slope_30min" else "daily_mean"
    reports = tmp_path / "runs"
    _write(reports, "a", _outlook(deployed, "2026-10-17T10:00:00+00:00", [(10, 11), (20, 19)]))
    _write(reports, "b", _outlook(other, "2026-10-17T11:00:00+00:00", [(300, 30)]))  # newer
    latest = charts.latest_reports(reports)
    assert ("l5_drain_v0", f"tank_outlook:{deployed}", "test") in latest
    paths = charts.hours_to_empty(latest, tmp_path / "out")
    assert {p.suffix for p in paths} == {".png", ".svg", ".csv"}
    table = next(p for p in paths if p.suffix == ".csv")
    with table.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert [float(r["outlook_hours"]) for r in rows] == [10.0, 20.0]


def test_the_localisation_chart_shows_water_and_earth_leakage(tmp_path: Path) -> None:
    reports = tmp_path / "runs"
    _write(reports, "a", _localised("l1_v0", "l1_leak_model", "2026-10-17T10:00:00+00:00"))
    _write(
        reports, "b", _localised("l2_leak_v0", "l2_leakage_baseline", "2026-10-17T10:00:00+00:00")
    )
    paths = charts.localisation(charts.latest_reports(reports), tmp_path / "out")
    table = next(p for p in paths if p.suffix == ".csv")
    with table.open(encoding="utf-8") as f:
        assert [r["suite"] for r in csv.DictReader(f)] == ["l1_v0", "l2_leak_v0"]
