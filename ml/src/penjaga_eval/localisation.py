"""Localisation after guided checks (PRD §18.2, §21): how far do checks narrow a leak down?

A meter at the boundary cannot say where water is lost; checks with the house's valves can.
For every water fault a detector found in a split:

1. **Prior**: the matched finding's location belief (``penjaga_detect.localise.prior``): its
   candidates, or uniform when it names none (the minimum-night-flow baseline).
2. **Checks**: from 19:00 house time on the first evening at least 30 minutes after the finding
   (when households are usually home), the household does the checks the localiser proposes,
   one after another (at most ``max_checks``).
3. **Physics**: each check is applied to the episode's own simulated world (``penjaga_sim``
   actions: no water use during the check, the valve closed as asked) and measured exactly as
   the live service measures it (``penjaga_detect.checks`` on the rendered sensor readings).
4. **Update**: the localiser updates its belief from each measured verdict.

**Likelihood.** The update needs P(verdict | the leak is or is not behind the valve). ``fit``
estimates it on a validation split by doing every possible check once for every water fault
(on the first evening a day after the fault starts), so the counts do not depend on any policy;
it is written to ``ml/params/l1_localiser.json``. Test runs only use it.

**Reported**, per detector and fault type, before and after the checks: the truth's rank (top-1,
top-3, room of the top-1), the truth's probability, the perplexity (the number of equally likely
places the belief is worth, 2^entropy), the number of checks, and how often the checks lowered
the truth's probability.

**Assumptions.** The household follows the instructions; checks hold water use for their whole
slot; automatic flows (the purifier refilling, the tank topping up) and drinking continue.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from penjaga_contract.core import Finding
from penjaga_contract.lab import EpisodeManifest
from penjaga_contract.validate import load_context
from penjaga_detect import checks
from penjaga_detect.chunk import Series
from penjaga_detect.detector import HouseContext
from penjaga_detect.localise import (
    CheckOption,
    Likelihood,
    LocaliseParams,
    Localiser,
    check_options,
    entropy_bits,
)
from penjaga_eval import episodes, registry, sealing
from penjaga_eval.replay import replay
from penjaga_eval.scoring import LayerScore, load_truth, match_episode, result_for
from penjaga_eval.suites import Suite, manifests
from penjaga_sim import GENERATOR_VERSION
from penjaga_sim.actions import CheckAction
from penjaga_sim.export import world_for
from penjaga_sim.world import World

US = 1_000_000
MINUTE_US = 60 * US
LIKELIHOOD_PATH = registry.PARAMS_DIR / "l1_localiser.json"


@dataclass(frozen=True)
class CheckPlan:
    """How each check is done (the request a guided check would send)."""

    start_hour: float = 19.0  # first check of an evening
    after_finding_min: float = 30.0
    slot_min: float = 35.0  # one check, then a pause before the next
    flow_window_s: float = 600.0
    flow_settle_s: float = 120.0
    expect_below_lpm: float = 0.02
    tank_window_s: float = 1800.0


# ---------------------------------------------------------------------------- one check


def _stream_info() -> dict[str, tuple[float, float]]:
    return {
        s.id: (s.nominal_period_s, s.resolution)
        for s in load_context().catalogue.streams
        if s.nominal_period_s is not None and s.resolution is not None
    }


def _series(world: World, stream: str, t0_us: int, t1_us: int) -> Series:
    day = world.day_of(t0_us)
    parts = []
    while world.day_start_us(day) < t1_us:
        out = world.output(day)
        sd = out.streams.get(stream)
        if sd is not None:
            ts = sd.t0_us + np.arange(sd.values.size, dtype=np.int64) * sd.period_us
            parts.append(Series(ts, sd.values, sd.quality))
        day += timedelta(days=1)
    if not parts:
        return Series.empty()
    return Series(
        np.concatenate([p.ts_us for p in parts]),
        np.concatenate([p.values for p in parts]),
        np.concatenate([p.quality for p in parts]),
    ).between(t0_us, t1_us)


def do_check(world: World, option: CheckOption, slot_us: int, plan: CheckPlan) -> str:
    """Apply ``option`` in ``world`` at ``slot_us`` and return the measured verdict."""
    info = _stream_info()
    if option.kind == "tank_level_trend":
        since = slot_us
        ready = since + round(plan.tank_window_s * US)
        world.act(CheckAction(since, ready, (option.valve_id,), True))
        period, resolution = info[checks.TANK_STREAM]
        data = _series(world, checks.TANK_STREAM, since, ready)
        outcome = checks.tank_level_trend(checks.Window.of(data, since, ready, period), resolution)
    else:
        window, settle = round(plan.flow_window_s * US), round(plan.flow_settle_s * US)
        since = slot_us + window
        ready = since + settle + window
        world.act(CheckAction(slot_us, since, (), True))  # measure "before" with no use
        world.act(CheckAction(since, ready, (option.valve_id,), True))
        period, resolution = info[checks.FLOW_STREAM]
        data = _series(world, checks.FLOW_STREAM, since - window, ready)
        before = checks.Window.of(data, since - window, since, period)
        after = checks.Window.of(data, since + settle, ready, period)
        outcome = checks.flow_stop(before, after, plan.expect_below_lpm, resolution)
    return outcome.verdict or "unavailable"


def first_slot(after_us: int, zone: ZoneInfo, plan: CheckPlan) -> int:
    """The first evening start at least ``after_finding_min`` after ``after_us``."""
    earliest = after_us + round(plan.after_finding_min * MINUTE_US)
    day = datetime.fromtimestamp(earliest / US, tz=zone).date()
    while True:
        start = datetime.combine(day, time(0), tzinfo=zone) + timedelta(hours=plan.start_hour)
        start_us = int(start.timestamp()) * US
        if start_us >= earliest:
            return start_us
        day += timedelta(days=1)


# ---------------------------------------------------------------------------- tasks


@dataclass(frozen=True)
class Task:
    episode: str  # episode directory
    fault_type: str
    truth: str  # the faulty element
    fault_start_us: int
    detector: str | None = None  # None: likelihood fitting (every check, no finding)
    finding: str | None = None  # the finding's first revision (JSON)
    first_seen_us: int = 0


def _run(args: tuple[Task, dict[str, Any], dict[str, Any], dict[str, Any]]) -> dict[str, Any]:
    task, lik_table, loc_params, plan_doc = args
    plan = CheckPlan(**plan_doc)
    house = HouseContext.canonical()
    manifest = EpisodeManifest.model_validate_json(
        (Path(task.episode) / "manifest.json").read_text(encoding="utf-8")
    )
    world = world_for(manifest)
    loc = Localiser(house.index, Likelihood(lik_table), LocaliseParams(**loc_params))
    if task.detector is None:  # fitting: every check once, a day after the fault starts
        slot = first_slot(task.fault_start_us + 86_400 * US, house.zone, plan)
        done = []
        for each in check_options(house.index):
            verdict = do_check(world, each, slot, plan)
            done.append(_record(each, task.truth, verdict))
            slot += round(plan.slot_min * MINUTE_US)
        return {"task": asdict(task), "checks": done}
    finding = Finding.model_validate_json(task.finding or "{}")
    loc.start_from(finding)
    before = dict(loc.posterior)
    slot = first_slot(task.first_seen_us, house.zone, plan)
    done = []
    while (option := loc.next_check()) is not None:
        verdict = do_check(world, option, slot, plan)
        loc.observe(option, verdict)
        done.append(_record(option, task.truth, verdict))
        slot += round(plan.slot_min * MINUTE_US)
    return {
        "task": {**asdict(task), "finding": None},
        "before": _summary(before, task.truth, house),
        "after": _summary(loc.posterior, task.truth, house),
        "checks": done,
    }


def _record(option: CheckOption, truth: str, verdict: str) -> dict[str, Any]:
    return {
        "check": option.key,
        "kind": option.kind,
        "behind": truth in option.behind,
        "verdict": verdict,
    }


def _summary(p: Mapping[str, float], truth: str, house: HouseContext) -> dict[str, Any]:
    ranked = sorted(p.items(), key=lambda kv: (-kv[1], kv[0]))
    names = [h for h, _ in ranked]
    rank = names.index(truth) + 1 if truth in names else None
    top_rooms = set(house.index.rooms_of(names[0])) if house.index.exists(names[0]) else set()
    return {
        "truth_rank": rank,
        "truth_p": float(p.get(truth, 0.0)),
        "top1": names[0],
        "top1_p": ranked[0][1],
        "room_top1": bool(top_rooms & set(house.index.rooms_of(truth))),
        "perplexity": 2 ** entropy_bits(p),
    }


def _pool(tasks: list[Task], lik: Likelihood, params: LocaliseParams, plan: CheckPlan,
          workers: int | None) -> list[dict[str, Any]]:  # fmt: skip
    jobs = [(t, lik.as_dict(), asdict(params), asdict(plan)) for t in tasks]
    n = workers if workers is not None else max(1, (os.cpu_count() or 2) - 1)
    if n == 1 or len(jobs) <= 1:
        return [_run(j) for j in jobs]
    with ProcessPoolExecutor(max_workers=min(n, len(jobs))) as pool:
        return list(pool.map(_run, jobs))


# ---------------------------------------------------------------------------- fitting


def fit(
    suites: Sequence[Suite],
    *,
    plan: CheckPlan | None = None,
    episodes_root: Path = episodes.DEFAULT_ROOT,
    workers: int | None = None,
    out: Path = LIKELIHOOD_PATH,
) -> dict[str, Any]:
    """Estimate the check likelihood on validation splits (every check, every water fault)."""
    plan = plan or CheckPlan()
    tasks = []
    for suite in suites:
        paths = episodes.ensure(
            manifests(suite, "validation"), episodes_root, workers, suite.streams
        )
        for path in paths:
            faults, _, _ = load_truth(path)
            for f in faults:
                if f.layer == "L1" and f.object_ids:
                    tasks.append(Task(str(path), f.fault_type, f.object_ids[0], f.start_us))
    records = _pool(tasks, Likelihood.default(), LocaliseParams(), plan, workers)
    counts: dict[str, dict[str, dict[str, int]]] = {}
    for r in records:
        for c in r["checks"]:
            side = "behind" if c["behind"] else "other"
            bucket = counts.setdefault(c["kind"], {}).setdefault(side, {})
            bucket[c["verdict"]] = bucket.get(c["verdict"], 0) + 1
    lik = Likelihood.from_counts(counts)
    doc = {
        "likelihood": lik.as_dict(),
        "counts": counts,
        "check_plan": asdict(plan),
        "fitted_on": {
            "suites": [s.name for s in suites],
            "split": "validation",
            "generator": GENERATOR_VERSION,
            "faults": len(tasks),
            "date": datetime.now(UTC).date().isoformat(),
        },
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n")
    return doc


def load_likelihood(path: Path = LIKELIHOOD_PATH) -> tuple[Likelihood, CheckPlan, str]:
    if not path.exists():
        return Likelihood.default(), CheckPlan(), "default (not fitted)"
    doc = json.loads(path.read_text(encoding="utf-8"))
    return Likelihood(doc["likelihood"]), CheckPlan(**doc["check_plan"]), path.as_posix()


# ---------------------------------------------------------------------------- evaluation


def evaluate(
    suite: Suite,
    split: str,
    detectors: Sequence[str],
    *,
    params: LocaliseParams | None = None,
    likelihood_path: Path = LIKELIHOOD_PATH,
    episodes_root: Path = episodes.DEFAULT_ROOT,
    workers: int | None = None,
    sealed_dir: Path = sealing.SEALED_DIR,
    test_log: Path = sealing.TEST_LOG,
    limit: int | None = None,
) -> dict[str, Any]:
    params = params or LocaliseParams()
    lik, plan, lik_source = load_likelihood(likelihood_path)
    items = manifests(suite, split)
    if split == "test":
        sealing.check(suite, items, sealed_dir)
        if limit is not None:
            raise ValueError("test runs use the whole sealed split")
    if limit is not None:
        items = items[:limit]
    paths = episodes.ensure(items, episodes_root, workers, suite.streams)
    house = HouseContext.canonical()
    tasks: list[Task] = []
    for path in paths:
        built = [registry.build(n, house, registry.parameters(n)) for n in detectors]
        found = replay(path, built)
        for name in detectors:
            score = LayerScore()
            match_episode(result_for(path, found[name]), "L1", score)
            first: dict[str, Finding] = {}
            for f in found[name]:
                first.setdefault(f.id, f)
            for m in score.matches:
                if m.finding is None or not m.fault.object_ids:
                    continue
                tasks.append(
                    Task(
                        str(path),
                        m.fault.fault_type,
                        m.fault.object_ids[0],
                        m.fault.start_us,
                        name,
                        first[m.finding.id].model_dump_json(by_alias=True),
                        m.finding.first_seen_us,
                    )
                )
    records = _pool(tasks, lik, params, plan, workers)
    report: dict[str, Any] = {
        "kind": "localisation",
        "suite": suite.name,
        "split": split,
        "generator": GENERATOR_VERSION,
        "episodes": len(paths),
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "git": sealing.git_state(),
        "likelihood": lik_source,
        "localiser": asdict(params),
        "check_plan": asdict(plan),
        "detectors": {
            name: summarise([r for r in records if r["task"]["detector"] == name])
            for name in detectors
        },
    }
    if split == "test":
        sealing.log_test_run(
            {
                "created": report["created"],
                "git": report["git"],
                "suite": suite.name,
                "generator": GENERATOR_VERSION,
                "localisation": {
                    n: {k: d["all"][k] for k in ("n", "top1_before", "top1_after", "top3_after")}
                    for n, d in report["detectors"].items()
                },
            },
            test_log,
        )
    return report


def _rate(flags: Sequence[bool]) -> float | None:
    return None if not flags else sum(flags) / len(flags)


def _ci(flags: Sequence[bool], reps: int = 1000, seed: int = 0) -> list[float] | None:
    if not flags:
        return None
    a = np.asarray(flags, dtype=np.float64)
    r = np.random.default_rng(seed)
    values = [a[r.integers(0, a.size, a.size)].mean() for _ in range(reps)]
    lo, hi = np.percentile(values, [2.5, 97.5])
    return [round(float(lo), 4), round(float(hi), 4)]


def _stat(f: Any, values: Sequence[float]) -> float | None:
    return None if not values else float(f(values))


def _group(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    def top(k: int, when: str) -> list[bool]:
        return [r[when]["truth_rank"] is not None and r[when]["truth_rank"] <= k for r in records]

    return {
        "n": len(records),
        "top1_before": _rate(top(1, "before")),
        "top1_after": _rate(top(1, "after")),
        "top1_after_ci95": _ci(top(1, "after")),
        "top3_before": _rate(top(3, "before")),
        "top3_after": _rate(top(3, "after")),
        "room_before": _rate([r["before"]["room_top1"] for r in records]),
        "room_after": _rate([r["after"]["room_top1"] for r in records]),
        "truth_p_before": _stat(np.mean, [r["before"]["truth_p"] for r in records]),
        "truth_p_after": _stat(np.mean, [r["after"]["truth_p"] for r in records]),
        "perplexity_before": _stat(np.median, [r["before"]["perplexity"] for r in records]),
        "perplexity_after": _stat(np.median, [r["after"]["perplexity"] for r in records]),
        "checks_mean": _stat(np.mean, [len(r["checks"]) for r in records]),
        "truth_p_fell": _rate(
            [r["after"]["truth_p"] < r["before"]["truth_p"] - 1e-9 for r in records]
        ),
    }


def summarise(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    by_type: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        by_type.setdefault(r["task"]["fault_type"], []).append(r)
    verdicts: dict[str, dict[str, int]] = {}
    for r in records:
        for c in r["checks"]:
            key = f"{c['kind']}:{'behind' if c['behind'] else 'other'}"
            verdicts.setdefault(key, {})
            verdicts[key][c["verdict"]] = verdicts[key].get(c["verdict"], 0) + 1
    return {
        "all": _group(records),
        "by_fault_type": {t: _group(rs) for t, rs in sorted(by_type.items())},
        "verdicts": verdicts,
    }


def render(report: Mapping[str, Any]) -> str:
    def pct(x: float | None) -> str:
        return "-" if x is None else f"{100 * x:.0f}%"

    def num(x: float | None, fmt: str = ".1f") -> str:
        return "-" if x is None or (isinstance(x, float) and math.isnan(x)) else format(x, fmt)

    lines = [
        f"### Localisation after guided checks · `{report['suite']}` {report['split']} split",
        "",
        f"{report['episodes']} episodes, generator `{report['generator']}`, commit "
        f"`{(report['git']['commit'] or 'unknown')[:10]}`; likelihood `{report['likelihood']}`; "
        f"at most {report['localiser']['max_checks']} checks, stopping at "
        f"{report['localiser']['confident_p']:.0%}.",
        "",
    ]
    for name, d in report["detectors"].items():
        lines += [
            f"#### `{name}`",
            "",
            "| Fault | n | Top-1 before → after | Top-3 before → after | Room before → after "
            "| Places (perplexity) before → after | Checks |",
            "|---|---|---|---|---|---|---|",
        ]
        rows = [("all", d["all"]), *d["by_fault_type"].items()]
        for label, g in rows:
            lines.append(
                f"| {label} | {g['n']} | {pct(g['top1_before'])} → {pct(g['top1_after'])} "
                f"| {pct(g['top3_before'])} → {pct(g['top3_after'])} "
                f"| {pct(g['room_before'])} → {pct(g['room_after'])} "
                f"| {num(g['perplexity_before'])} → {num(g['perplexity_after'])} "
                f"| {num(g['checks_mean'])} |"
            )
        lines += [
            "",
            "Checks lowered the truth's probability for "
            f"{pct(d['all']['truth_p_fell'])} of faults.",
            "",
        ]
    return "\n".join(lines)
