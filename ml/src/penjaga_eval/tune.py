"""Choose a detector's parameters on the validation split only (never the test split).

The objective is the operating point a homeowner would accept: the highest recall whose false
alarms stay within a budget per house-month; ties go to higher precision. If no setting meets
the budget, the one with the fewest false alarms wins (and the file says so). With several
suites, a setting's counts are pooled over their validation splits (recall over all faults,
false alarms over all house-months).
"""

from __future__ import annotations

import itertools
import json
import os
from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from penjaga_detect.detector import HouseContext
from penjaga_eval import episodes, registry
from penjaga_eval.replay import replay_each
from penjaga_eval.scoring import EpisodeResult, result_for, score_episodes
from penjaga_eval.suites import Suite, manifests
from penjaga_sim import GENERATOR_VERSION


def _pooled(metrics: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    faults = sum(m["faults"] for m in metrics)
    tp = sum(m["detected"] for m in metrics)
    fp = sum(m["false_alarms"] for m in metrics)
    dup = sum(m["duplicate_findings"] for m in metrics)
    months = sum(m["house_months"] for m in metrics)
    return {
        "recall": None if faults == 0 else tp / faults,
        "precision": None if tp + dup + fp == 0 else (tp + dup) / (tp + dup + fp),
        "false_alarms_per_house_month": None if months == 0 else round(fp / months, 4),
    }


def _grid_episode(job: tuple[Path, str, list[Any]]) -> list[EpisodeResult]:
    """One validation episode replayed once through the detector under every setting."""
    path, detector, settings = job
    house = HouseContext.canonical()
    built = [registry.build(detector, house, params) for params in settings]
    return [result_for(path, found) for found in replay_each(path, built)]


def _grid_metrics(
    suite: Suite,
    detector: str,
    settings: list[Any],
    layer: str,
    *,
    episodes_root: Path,
    workers: int | None,
    limit: int | None,
) -> list[dict[str, Any]]:
    """The layer's metrics for each setting on the suite's validation split."""
    items = manifests(suite, "validation")
    if limit is not None:
        items = items[:limit]
    paths = episodes.ensure(items, episodes_root, workers, suite.streams)
    jobs = [(path, detector, settings) for path in paths]
    n = workers if workers is not None else max(1, (os.cpu_count() or 2) - 1)
    if n == 1 or len(jobs) <= 1:
        per_episode = [_grid_episode(job) for job in jobs]
    else:
        with ProcessPoolExecutor(max_workers=min(n, len(jobs))) as pool:
            per_episode = list(pool.map(_grid_episode, jobs))
    index = HouseContext.canonical().index
    return [
        score_episodes([results[i] for results in per_episode], suite.layers, index)[layer]
        for i in range(len(settings))
    ]


def tune(
    suites: Suite | Sequence[Suite],
    detector: str,
    grid: Mapping[str, Sequence[Any]],
    *,
    budget_fa_per_house_month: float,
    episodes_root: Path = episodes.DEFAULT_ROOT,
    params_dir: Path = registry.PARAMS_DIR,
    workers: int | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    pooled = [suites] if isinstance(suites, Suite) else list(suites)
    names = sorted(grid)
    layer = registry.build(
        detector, HouseContext.canonical(), registry.parameters(detector)
    ).info.layer
    combos = [
        dict(zip(names, v, strict=True)) for v in itertools.product(*(grid[n] for n in names))
    ]
    settings = [registry.parameters(detector, combo) for combo in combos]
    per_suite = [
        _grid_metrics(
            suite,
            detector,
            settings,
            layer,
            episodes_root=episodes_root,
            workers=workers,
            limit=limit,
        )
        for suite in pooled
    ]
    candidates = [
        {"params": combo, **_pooled([metrics[i] for metrics in per_suite])}
        for i, combo in enumerate(combos)
    ]

    def fa(c: dict[str, Any]) -> float:
        return float(c["false_alarms_per_house_month"] or 0.0)

    within = [c for c in candidates if fa(c) <= budget_fa_per_house_month]
    if within:
        best = max(within, key=lambda c: (c["recall"] or 0.0, c["precision"] or 0.0))
        note = "highest recall within the false-alarm budget"
    else:
        best = min(candidates, key=lambda c: (fa(c), -(c["recall"] or 0.0)))
        note = "no setting met the budget: fewest false alarms"
    previous = registry.tuned(detector, params_dir)  # e.g. a model's artifact path
    record = {
        "detector": detector,
        "params": {**previous, **best["params"]},
        "tuned_on": {
            "suite": "+".join(s.name for s in pooled),
            "suites": [s.name for s in pooled],
            "split": "validation",
            "generator": GENERATOR_VERSION,
            "budget_false_alarms_per_house_month": budget_fa_per_house_month,
            "objective": note,
            "date": datetime.now(UTC).date().isoformat(),
        },
        "candidates": candidates,
    }
    params_dir.mkdir(parents=True, exist_ok=True)
    (params_dir / f"{detector}.json").write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return record
