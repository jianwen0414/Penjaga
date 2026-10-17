"""One evaluation run: episodes → replay through detectors → score → report."""

from __future__ import annotations

import dataclasses
import json
import os
from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from penjaga_detect.detector import HouseContext
from penjaga_eval import episodes, registry, sealing
from penjaga_eval.replay import replay
from penjaga_eval.scoring import EpisodeResult, result_for, score_episodes
from penjaga_eval.suites import Suite, manifests
from penjaga_sim import GENERATOR_VERSION

DEFAULT_RUNS = Path("var/eval/runs")


def evaluate(
    suite: Suite,
    split: str,
    detectors: Sequence[str],
    *,
    overrides: Mapping[str, Mapping[str, Any]] | None = None,
    episodes_root: Path = episodes.DEFAULT_ROOT,
    workers: int | None = None,
    sealed_dir: Path = sealing.SEALED_DIR,
    test_log: Path = sealing.TEST_LOG,
    limit: int | None = None,
) -> dict[str, Any]:
    items = manifests(suite, split)
    if split == "test":
        sealing.check(suite, items, sealed_dir)
        if limit is not None:
            raise ValueError("test runs use the whole sealed split")
    if limit is not None:
        items = items[:limit]
    paths = episodes.ensure(items, episodes_root, workers, suite.streams)
    house = HouseContext.canonical()
    params = {name: registry.parameters(name, (overrides or {}).get(name)) for name in detectors}
    per_detector: dict[str, list[EpisodeResult]] = {name: [] for name in detectors}
    jobs = [(path, tuple(detectors), params) for path in paths]
    n = workers if workers is not None else max(1, (os.cpu_count() or 2) - 1)
    if n == 1 or len(jobs) <= 1:
        results = [_episode(job) for job in jobs]
    else:  # episodes are independent: replay them in parallel, results in episode order
        with ProcessPoolExecutor(max_workers=min(n, len(jobs))) as pool:
            results = list(pool.map(_episode, jobs))
    for result in results:
        for name in detectors:
            per_detector[name].append(result[name])
    report: dict[str, Any] = {
        "suite": suite.name,
        "split": split,
        "generator": GENERATOR_VERSION,
        "episodes": len(paths),
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "git": sealing.git_state(),
        "detectors": {},
    }
    for name in detectors:
        detector = registry.build(name, house, params[name])
        report["detectors"][name] = {
            "kind": detector.info.kind,
            "layer": detector.info.layer,
            "version": detector.info.version,
            "params": dataclasses.asdict(params[name]),
            "metrics": score_episodes(per_detector[name], suite.layers, house.index),
        }
    if split == "test":
        sealing.log_test_run(
            {
                "created": report["created"],
                "git": report["git"],
                "suite": suite.name,
                "generator": GENERATOR_VERSION,
                "detectors": {
                    n: {
                        "version": d["version"],
                        "params": d["params"],
                        "headline": headline(d["metrics"]),
                    }
                    for n, d in report["detectors"].items()
                },
            },
            test_log,
        )
    return report


def _episode(job: tuple[Path, tuple[str, ...], Mapping[str, Any]]) -> dict[str, EpisodeResult]:
    """Replay one episode through fresh detectors and score each detector's findings."""
    path, detectors, params = job
    house = HouseContext.canonical()
    built = [registry.build(name, house, params[name]) for name in detectors]
    findings = replay(path, built)
    return {name: result_for(path, findings[name]) for name in detectors}


def headline(metrics: Mapping[str, Any]) -> dict[str, Any]:
    return {
        layer: {k: m[k] for k in ("recall", "precision", "false_alarms_per_house_month")}
        for layer, m in metrics.items()
    }


def write(report: Mapping[str, Any], runs_dir: Path = DEFAULT_RUNS) -> Path:
    stamp = report["created"].replace(":", "").replace("-", "")
    path = runs_dir / f"{report['suite']}-{report['split']}-{stamp}" / "report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path
