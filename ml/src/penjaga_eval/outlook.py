"""Scoring the roof-tank outlook (L5): litres left and hours left when the water supply is lost.

When the mains water stops, ``l5_supply`` says how much water the roof tank holds and about how
long it will last. For every water cut (U1, U2) the detector found, its first revision is
compared with the truth (``truth/water.parquet``, per minute; scorer only):

- **Litres**: the tank's true volume at the finding's ``first_seen``.
- **Draw rate**: the outlook's implied use (litres / hours) against the tank's true mean outflow
  from ``first_seen`` until the supply came back or the tank ran dry. Measurable for every cut,
  so it is the main score: the median absolute log ratio, shown as a typical factor
  (``exp(median |ln(predicted / true)|)``, 1.0 is perfect), and the bias (median log ratio).
- **Hours**: when the tank ran dry before the supply came back (its volume at or below 2% of
  capacity), the hours error: mean absolute, median relative, and how often the outlook said
  the water would last longer than it did. Otherwise the true time is only known to be longer
  than the cut (censored), and an outlook is consistent if it did not claim the tank would run
  dry sooner.
- **Coverage**: the share of findings that carry an hours figure at all.

``select`` compares outlook settings on a validation split (the smallest typical factor among
settings with coverage of at least ``min_coverage``) and writes the winner, with every
candidate, to ``ml/params/l5_supply.json``.
"""

from __future__ import annotations

import dataclasses
import itertools
import json
import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq

from penjaga_contract.core import Finding
from penjaga_detect.detector import HouseContext
from penjaga_eval import episodes, registry, sealing
from penjaga_eval.replay import replay
from penjaga_eval.scoring import LayerScore, match_episode, result_for
from penjaga_eval.suites import Suite, manifests
from penjaga_sim import GENERATOR_VERSION

US = 1_000_000
DETECTOR = "l5_supply"
DRY_FRACTION = 0.02
MIN_RATE_L_PER_H = 1.0


def _water_truth(episode: Path) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    table = pq.read_table(episode / "truth" / "water.parquet")
    ts = np.asarray(table.column("ts_us").to_numpy(), dtype=np.int64)
    columns = [c for c in table.column_names if c != "ts_us"]
    return ts, {c: np.asarray(table.column(c).to_numpy(), dtype=np.float64) for c in columns}


def case(
    finding: Finding, first_seen_us: int, restored_us: int, episode: Path, capacity_l: float
) -> dict[str, Any]:
    """One water cut: the outlook against the truth."""
    ts, truth = _water_truth(episode)
    q = finding.quantities or {}
    litres = q["tank_water_l"].value if "tank_water_l" in q else None
    hours = q["tank_hours_left"].value if "tank_hours_left" in q else None
    at = int(np.searchsorted(ts, first_seen_us))  # the first minute ending at or after it
    true_litres = float(truth["tank_l"][min(at, ts.size - 1)])
    dry = np.flatnonzero(
        (ts >= first_seen_us) & (ts <= restored_us) & (truth["tank_l"] <= DRY_FRACTION * capacity_l)
    )
    dry_us = int(ts[dry[0]]) if dry.size else None
    end_us = dry_us if dry_us is not None else restored_us
    during = (ts > first_seen_us) & (ts <= end_us)
    true_rate = float(truth["tank_outflow_lpm"][during].mean() * 60.0) if during.any() else None
    predicted_rate = None
    if litres is not None and hours is not None and hours > 0:
        predicted_rate = float(litres) / float(hours)
    out: dict[str, Any] = {
        "litres": litres,
        "true_litres": true_litres,
        "hours": hours,
        "ran_dry": dry_us is not None,
        "true_hours": None if dry_us is None else (dry_us - first_seen_us) / 3600 / US,
        "cut_hours": (restored_us - first_seen_us) / 3600 / US,
        "rate": predicted_rate,
        "true_rate": true_rate,
    }
    return out


def score(cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    with_hours = [c for c in cases if c["hours"] is not None]
    litres_err = [abs(c["litres"] - c["true_litres"]) for c in cases if c["litres"] is not None]
    log_ratios = [
        math.log(max(c["rate"], MIN_RATE_L_PER_H) / max(c["true_rate"], MIN_RATE_L_PER_H))
        for c in with_hours
        if c["rate"] is not None and c["true_rate"] is not None
    ]
    dry = [c for c in with_hours if c["ran_dry"]]
    censored = [c for c in with_hours if not c["ran_dry"]]
    return {
        "cuts": len(cases),
        "coverage": None if not cases else len(with_hours) / len(cases),
        "litres_mae": None if not litres_err else float(np.mean(litres_err)),
        "litres_max_error": None if not litres_err else float(np.max(litres_err)),
        "rate_factor": None if not log_ratios else float(math.exp(np.median(np.abs(log_ratios)))),
        "rate_factor_p90": None
        if not log_ratios
        else float(math.exp(np.percentile(np.abs(log_ratios), 90))),
        "rate_bias": None if not log_ratios else float(math.exp(np.median(log_ratios))),
        "ran_dry": len([c for c in cases if c["ran_dry"]]),
        "hours_mae_dry": None
        if not dry
        else float(np.mean([abs(c["hours"] - c["true_hours"]) for c in dry])),
        # the share of the true time the outlook was off by, and how often it said the water
        # would last longer than it did (the costly direction: the household stores too little)
        "hours_rel_error_dry": None
        if not dry
        else float(np.median([abs(c["hours"] - c["true_hours"]) / c["true_hours"] for c in dry])),
        "hours_late_dry": None
        if not dry
        else sum(c["hours"] > c["true_hours"] for c in dry) / len(dry),
        "censored_consistent": None
        if not censored
        else sum(c["hours"] >= c["cut_hours"] for c in censored) / len(censored),
    }


def cases_for(
    suite: Suite,
    split: str,
    overrides: Mapping[str, Any] | None = None,
    *,
    episodes_root: Path = episodes.DEFAULT_ROOT,
    workers: int | None = None,
) -> list[dict[str, Any]]:
    paths = episodes.ensure(manifests(suite, split), episodes_root, workers, suite.streams)
    house = HouseContext.canonical()
    capacity = float(house.index.house.water.tanks[0].capacity_l)
    params = registry.parameters(DETECTOR, overrides)
    out = []
    for path in paths:
        found = replay(path, [registry.build(DETECTOR, house, params)])[DETECTOR]
        result = result_for(path, found)
        layer = LayerScore()
        match_episode(result, "L5", layer)
        first: dict[str, Finding] = {}
        for f in found:
            first.setdefault(f.id, f)
        for m in layer.matches:
            if m.fault.fault_type not in ("U1", "U2") or m.finding is None:
                continue
            c = case(first[m.finding.id], m.finding.first_seen_us, m.fault.end_us, path, capacity)
            out.append({"episode": path.name, "fault_type": m.fault.fault_type, **c})
    return out


def evaluate(
    suite: Suite,
    split: str,
    overrides: Mapping[str, Any] | None = None,
    *,
    episodes_root: Path = episodes.DEFAULT_ROOT,
    workers: int | None = None,
    sealed_dir: Path = sealing.SEALED_DIR,
    test_log: Path = sealing.TEST_LOG,
) -> dict[str, Any]:
    if split == "test":
        sealing.check(suite, manifests(suite, "test"), sealed_dir)
    params = registry.parameters(DETECTOR, overrides)
    cases = cases_for(suite, split, overrides, episodes_root=episodes_root, workers=workers)
    report = {
        "kind": "tank_outlook",
        "suite": suite.name,
        "split": split,
        "generator": GENERATOR_VERSION,
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "git": sealing.git_state(),
        "params": dataclasses.asdict(params),
        "metrics": score(cases),
        "by_fault_type": {
            t: score([c for c in cases if c["fault_type"] == t]) for t in ("U1", "U2")
        },
        "cases": cases,
    }
    if split == "test":
        sealing.log_test_run(
            {
                "created": report["created"],
                "git": report["git"],
                "suite": suite.name,
                "generator": GENERATOR_VERSION,
                "tank_outlook": {"params": report["params"], "metrics": report["metrics"]},
            },
            test_log,
        )
    return report


def select(
    suite: Suite,
    grid: Mapping[str, Sequence[Any]],
    *,
    min_coverage: float = 0.9,
    episodes_root: Path = episodes.DEFAULT_ROOT,
    workers: int | None = None,
    params_dir: Path = registry.PARAMS_DIR,
) -> dict[str, Any]:
    """Choose outlook settings on the validation split (never test)."""
    names = sorted(grid)
    candidates = []
    for values in itertools.product(*(grid[n] for n in names)):
        setting = dict(zip(names, values, strict=True))
        cases = cases_for(
            suite, "validation", setting, episodes_root=episodes_root, workers=workers
        )
        candidates.append({"params": setting, **score(cases)})
    eligible = [c for c in candidates if (c["coverage"] or 0) >= min_coverage and c["rate_factor"]]
    if eligible:
        best = min(eligible, key=lambda c: (c["rate_factor"], c["rate_factor_p90"]))
        note = f"smallest typical draw-rate factor with coverage >= {min_coverage:.0%}"
    else:
        best = max(candidates, key=lambda c: c["coverage"] or 0)
        note = "no setting reached the coverage floor: highest coverage"
    previous = registry.tuned(DETECTOR, params_dir)
    record = {
        "detector": DETECTOR,
        "params": {**previous, **best["params"]},
        "tuned_on": {
            "suite": suite.name,
            "split": "validation",
            "generator": GENERATOR_VERSION,
            "objective": note,
            "date": datetime.now(UTC).date().isoformat(),
        },
        "candidates": candidates,
    }
    params_dir.mkdir(parents=True, exist_ok=True)
    (params_dir / f"{DETECTOR}.json").write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return record


def render(report: Mapping[str, Any]) -> str:
    def num(x: float | None, fmt: str) -> str:
        return "-" if x is None else format(x, fmt)

    lines = [
        f"### Roof-tank outlook · `{report['suite']}` {report['split']} split",
        "",
        f"Generator `{report['generator']}`, commit "
        f"`{(report['git']['commit'] or 'unknown')[:10]}`; outlook "
        f"`{report['params']['outlook_method']}`, tank share {report['params']['tank_share']}.",
        "",
        "| Cuts | Coverage | Litres MAE (L) | Draw-rate factor (median / p90) | Bias | Ran dry "
        "| Hours MAE when dry (median % off) | Said it lasts longer | Consistent when censored |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for label, m in [("all", report["metrics"]), *report["by_fault_type"].items()]:
        lines.append(
            f"| {label}: {m['cuts']} | {num(m['coverage'], '.0%')} | {num(m['litres_mae'], '.1f')} "
            f"| {num(m['rate_factor'], '.2f')} / {num(m['rate_factor_p90'], '.2f')} "
            f"| {num(m['rate_bias'], '.2f')} | {m['ran_dry']} | {num(m['hours_mae_dry'], '.1f')} "
            f"({num(m.get('hours_rel_error_dry'), '.0%')}) | {num(m.get('hours_late_dry'), '.0%')} "
            f"| {num(m['censored_consistent'], '.0%')} |"
        )
    return "\n".join(lines)
