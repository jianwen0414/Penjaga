"""Choose a detector's parameters on the validation split only (never the test split).

The objective is the operating point a homeowner would accept: the highest recall whose false
alarms stay within a budget per house-month; ties go to higher precision. If no setting meets
the budget, the one with the fewest false alarms wins (and the file says so).
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from penjaga_detect.detector import HouseContext
from penjaga_eval import episodes, registry
from penjaga_eval.run import evaluate
from penjaga_eval.suites import Suite
from penjaga_sim import GENERATOR_VERSION


def tune(
    suite: Suite,
    detector: str,
    grid: Mapping[str, Sequence[Any]],
    *,
    budget_fa_per_house_month: float,
    episodes_root: Path = episodes.DEFAULT_ROOT,
    params_dir: Path = registry.PARAMS_DIR,
    workers: int | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    names = sorted(grid)
    layer = registry.build(
        detector, HouseContext.canonical(), registry.parameters(detector)
    ).info.layer
    candidates = []
    for values in itertools.product(*(grid[n] for n in names)):
        params = dict(zip(names, values, strict=True))
        report = evaluate(
            suite,
            "validation",
            [detector],
            overrides={detector: params},
            episodes_root=episodes_root,
            workers=workers,
            limit=limit,
        )
        m = report["detectors"][detector]["metrics"][layer]
        candidates.append(
            {
                "params": params,
                "recall": m["recall"],
                "precision": m["precision"],
                "false_alarms_per_house_month": m["false_alarms_per_house_month"],
            }
        )

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
            "suite": suite.name,
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
