"""Localisation after guided checks (PRD §18.2, §21): how far do checks narrow a problem down?

A meter at the boundary cannot say where water is lost, and an RCCB cannot say which of its
circuits leaks; checks with the house's valves and MCBs can. For every water leak (L1) or earth
leakage (L2, E2) a detector found in a split:

1. **Prior**: the matched finding's location belief (``penjaga_detect.localise.prior``): its
   candidates, or uniform when it names none (the minimum-night-flow baseline).
2. **Checks**: from 19:00 house time on the first evening at least 30 minutes after the finding
   (when households are usually home), the household does the checks the localiser proposes,
   one after another (at most ``max_checks``). A flow check first waits, up to ``max_wait_h``,
   for the roof tank to be full: while it refills, closing a valve barely changes the flow.
3. **Physics**: each check is applied to the episode's own simulated world (``penjaga_sim``
   actions: no water use during a water check, the valve closed or the circuit switched off as
   asked) and measured exactly as the live service measures it (``penjaga_detect.checks``).
4. **Update**: the localiser updates its belief from each measured verdict, using the flow
   band of a flow check (D-031).

**Likelihood.** ``fit`` estimates P(verdict | the problem is or is not behind the action) on a
validation split by doing every possible check once for every fault (on the first evening a day
after the fault starts), so the counts do not depend on any policy. It is written to
``ml/params/l1_localiser.json`` or ``l2_localiser.json``, with the check plan; test runs only
use it, and the live service loads the same files.

**Reported**, per detector and fault type, before and after the checks: the truth's rank (top-1,
top-3, room of the top-1), the truth's probability, the perplexity (the number of equally likely
places the belief is worth, 2^entropy), the number of checks, how often the checks lowered the
truth's probability, and how often a flow check found the tank full.

**Assumptions.** The household follows the instructions; water checks hold water use for their
whole slot; automatic flows (the purifier refilling, the tank topping up) and drinking continue;
switching a circuit off cuts its loads for the window.
"""

from __future__ import annotations

import dataclasses
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
    earth_leakage_localiser,
    entropy_bits,
    evidence_verdict,
    flow_band,
    load_band,
)
from penjaga_detect.localise_api import expected_drop
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
LIKELIHOOD_PATHS = {
    "L1": registry.PARAMS_DIR / "l1_localiser.json",
    "L2": registry.PARAMS_DIR / "l2_localiser.json",
}
LIKELIHOOD_PATH = LIKELIHOOD_PATHS["L1"]
LOCALISED = {"L1": ("W1", "W2", "W3", "W4", "W5"), "L2": ("E2",)}


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
    wait_for_full_tank: bool = True
    max_wait_h: float = 4.0
    residual_window_s: float = 120.0
    residual_settle_s: float = 30.0


@dataclass(frozen=True)
class Done:
    verdict: str
    band: str | None
    tank_full: bool | None  # flow checks: was the tank full when the check began
    start_us: int


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


def _tank_ready(world: World, slot_us: int, plan: CheckPlan) -> tuple[int, bool | None]:
    """The first time from ``slot_us`` (5-minute steps, up to ``max_wait_h``) when the tank has
    been full for the last 10 minutes, and whether it was; the slot itself when there is no
    tank level stream or the tank never fills in time."""
    if checks.TANK_STREAM not in world.output(world.day_of(slot_us)).streams:
        return slot_us, None
    period = _stream_info()[checks.TANK_STREAM][0]
    step, look = 5 * MINUTE_US, 10 * MINUTE_US
    end = slot_us + round(plan.max_wait_h * 3600 * US)
    data = _series(world, checks.TANK_STREAM, slot_us - look, end)
    t = slot_us
    while t <= end:
        full = checks.tank_is_full(checks.Window.of(data, t - look, t, period))
        if full:
            return t, True
        if not plan.wait_for_full_tank:
            return slot_us, full
        t += step
    return slot_us, False


def do_check(
    world: World,
    option: CheckOption,
    slot_us: int,
    plan: CheckPlan,
    idle_below_lpm: float,
    *,
    expect_drop_ma: float | None = None,
) -> Done:
    """Apply ``option`` in ``world`` from ``slot_us`` (after waiting for a full tank, for flow
    checks) and return the localiser's verdict for what was measured."""
    info = _stream_info()
    if option.kind == "residual_drop":
        assert option.rccb_id is not None
        window = round(plan.residual_window_s * US)
        settle = round(plan.residual_settle_s * US)
        since = slot_us + window
        ready = since + settle + window
        world.act(
            CheckAction(since, ready, hold_water_use=False, switch_off_circuits=(option.target_id,))
        )
        stream = checks.residual_stream(option.rccb_id)
        period, resolution = info[stream]
        data = _series(world, stream, since - window, ready)
        circuit = checks.circuit_stream(option.target_id)
        c_period = info[circuit][0]
        current = _series(world, circuit, since - window, ready)
        outcome = checks.residual_drop(
            checks.Window.of(data, since - window, since, period),
            checks.Window.of(data, since + settle, ready, period),
            (
                checks.Window.of(current, since - window, since, c_period),
                checks.Window.of(current, since + settle, ready, c_period),
            ),
            resolution,
            expect_drop_ma=expect_drop_ma,
        )
        verdict = evidence_verdict(option.kind, outcome.verdict, None, idle_below_lpm)
        load = next(
            (float(m["value"]) for m in outcome.measurements if m["name"] == "circuit_before"),
            None,
        )
        band = load_band(load) if verdict != "unavailable" else None
        return Done(verdict, band, None, slot_us)
    if option.kind == "tank_level_trend":
        since = slot_us
        ready = since + round(plan.tank_window_s * US)
        world.act(CheckAction(since, ready, (option.valve_id,), True))
        period, resolution = info[checks.TANK_STREAM]
        data = _series(world, checks.TANK_STREAM, since, ready)
        outcome = checks.tank_level_trend(checks.Window.of(data, since, ready, period), resolution)
        verdict = evidence_verdict(option.kind, outcome.verdict, None, idle_below_lpm)
        return Done(verdict, None, None, slot_us)
    start, full = _tank_ready(world, slot_us, plan)
    window, settle = round(plan.flow_window_s * US), round(plan.flow_settle_s * US)
    since = start + window
    ready = since + settle + window
    world.act(CheckAction(start, since, (), True))  # measure "before" with no use
    world.act(CheckAction(since, ready, (option.valve_id,), True))
    period, resolution = info[checks.FLOW_STREAM]
    data = _series(world, checks.FLOW_STREAM, since - window, ready)
    before = checks.Window.of(data, since - window, since, period)
    after = checks.Window.of(data, since + settle, ready, period)
    outcome = checks.flow_stop(before, after, plan.expect_below_lpm, resolution)
    flow_before = next(
        (float(m["value"]) for m in outcome.measurements if m["name"] == "flow_before"), None
    )
    verdict = evidence_verdict(option.kind, outcome.verdict, flow_before, idle_below_lpm)
    band = flow_band(flow_before) if verdict not in ("idle", "unavailable") else None
    return Done(verdict, band, full, start)


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
    layer: str = "L1"
    rccb: str | None = None  # L2: the RCCB whose circuits are the hypotheses
    expect_drop_ma: float | None = None  # L2: the leakage rise the finding reported


def _localiser(
    task: Task, house: HouseContext, lik: Likelihood, params: LocaliseParams
) -> Localiser:
    if task.layer == "L2":
        assert task.rccb is not None
        return earth_leakage_localiser(house.index, task.rccb, lik, params)
    return Localiser(house.index, lik, params)


def _run(args: tuple[Task, dict[str, Any], dict[str, Any], dict[str, Any]]) -> dict[str, Any]:
    task, lik_table, loc_params, plan_doc = args
    plan = CheckPlan(**plan_doc)
    house = HouseContext.canonical()
    manifest = EpisodeManifest.model_validate_json(
        (Path(task.episode) / "manifest.json").read_text(encoding="utf-8")
    )
    world = world_for(manifest)
    loc = _localiser(task, house, Likelihood(lik_table), LocaliseParams(**loc_params))
    gap = round(plan.slot_min * MINUTE_US)
    if task.detector is None:  # fitting: every check once, the evening after the fault/finding
        after = task.first_seen_us or task.fault_start_us + 86_400 * US
        slot = first_slot(after, house.zone, plan)
        done = []
        for each in loc.options:
            result = do_check(
                world,
                each,
                slot,
                plan,
                loc.params.idle_below_lpm,
                expect_drop_ma=task.expect_drop_ma,
            )
            done.append(_record(each, task.truth, result))
            slot = result.start_us + gap
        return {"task": asdict(task), "checks": done}
    finding = Finding.model_validate_json(task.finding or "{}")
    loc.start_from(finding)
    before = dict(loc.posterior)
    slot = first_slot(task.first_seen_us, house.zone, plan)
    done = []
    while (step := loc.next_check()) is not None:
        option = step[0]
        result = do_check(
            world, option, slot, plan, loc.params.idle_below_lpm, expect_drop_ma=task.expect_drop_ma
        )
        loc.observe(option, result.verdict, result.band)
        done.append(_record(option, task.truth, result))
        slot = result.start_us + gap
    return {
        "task": {**asdict(task), "finding": None},
        "before": _summary(before, task.truth, house),
        "after": _summary(loc.posterior, task.truth, house),
        "checks": done,
    }


def _record(option: CheckOption, truth: str, result: Done) -> dict[str, Any]:
    return {
        "check": option.key,
        "kind": option.kind,
        "behind": truth in option.behind,
        "verdict": result.verdict,
        "band": result.band,
        "tank_full": result.tank_full,
    }


def _rooms(house: HouseContext, element: str) -> set[str]:
    index = house.index
    if not index.exists(element):
        return set()
    try:
        return set(index.rooms_of(element))
    except (KeyError, ValueError, TypeError):
        return set()


def _summary(p: Mapping[str, float], truth: str, house: HouseContext) -> dict[str, Any]:
    ranked = sorted(p.items(), key=lambda kv: (-kv[1], kv[0]))
    names = [h for h, _ in ranked]
    rank = names.index(truth) + 1 if truth in names else None
    return {
        "truth_rank": rank,
        "truth_p": float(p.get(truth, 0.0)),
        "top1": names[0],
        "top1_p": ranked[0][1],
        "room_top1": bool(_rooms(house, names[0]) & _rooms(house, truth)),
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


def _layer(suites: Sequence[Suite]) -> str:
    layers = {layer for s in suites for layer in s.layers if layer in LOCALISED}
    if len(layers) != 1:
        raise ValueError("localise suites of one layer at a time (L1 or L2)")
    return layers.pop()


# ---------------------------------------------------------------------------- fitting


L2_FIT_DETECTOR = "l2_leakage_baseline"


def fit(
    suites: Sequence[Suite],
    *,
    plan: CheckPlan | None = None,
    episodes_root: Path = episodes.DEFAULT_ROOT,
    workers: int | None = None,
    out: Path | None = None,
) -> dict[str, Any]:
    """Estimate the check likelihood on validation splits: every check, once, for every fault.

    Water checks start the first evening a day after the fault starts. Switch-off checks need
    what the finding says (its RCCB and the rise it saw), so they are fitted on the faults the
    deployed leakage baseline found, from the evening after its finding."""
    plan = plan or CheckPlan()
    layer = _layer(suites)
    out = out or LIKELIHOOD_PATHS[layer]
    house = HouseContext.canonical()
    tasks: list[Task] = []
    for suite in suites:
        paths = episodes.ensure(
            manifests(suite, "validation"), episodes_root, workers, suite.streams
        )
        for path in paths:
            if layer == "L2":
                found = _tasks(path, [L2_FIT_DETECTOR], layer, house)
                tasks += [dataclasses.replace(t, detector=None, finding=None) for t in found]
                continue
            faults, _, _ = load_truth(path)
            for f in faults:
                if f.fault_type in LOCALISED[layer] and f.object_ids:
                    tasks.append(Task(str(path), f.fault_type, f.object_ids[0], f.start_us))
    records = _pool(tasks, Likelihood.default(), LocaliseParams(), plan, workers)
    counts: dict[str, dict[str, dict[str, int]]] = {}
    for r in records:
        for c in r["checks"]:
            side = "behind" if c["behind"] else "other"
            keys = [c["kind"]] + ([f"{c['kind']}@{c['band']}"] if c["band"] else [])
            for key in keys:
                bucket = counts.setdefault(key, {}).setdefault(side, {})
                bucket[c["verdict"]] = bucket.get(c["verdict"], 0) + 1
    lik = Likelihood.from_counts(counts)
    flow = [c for r in records for c in r["checks"] if c["kind"] == "flow_stop"]
    doc = {
        "likelihood": lik.as_dict(),
        "counts": counts,
        "check_plan": asdict(plan),
        "fitted_on": {
            "suites": [s.name for s in suites],
            "split": "validation",
            "layer": layer,
            "generator": GENERATOR_VERSION,
            "faults": len(tasks),
            "flow_checks_with_full_tank": None
            if not flow
            else round(sum(bool(c["tank_full"]) for c in flow) / len(flow), 4),
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


def _tasks(path: Path, detectors: Sequence[str], layer: str, house: HouseContext) -> list[Task]:
    built = [registry.build(n, house, registry.parameters(n)) for n in detectors]
    found = replay(path, built)
    out = []
    for name in detectors:
        score = LayerScore()
        match_episode(result_for(path, found[name]), layer, score)
        first: dict[str, Finding] = {}
        for f in found[name]:
            first.setdefault(f.id, f)
        for m in score.matches:
            if m.finding is None or not m.fault.object_ids:
                continue
            if m.fault.fault_type not in LOCALISED[layer]:
                continue
            finding = first[m.finding.id]
            rccb = None
            if layer == "L2":
                rccb = next((o for o in finding.object_ids if o.startswith("rcd_")), None)
                if rccb is None:
                    continue  # a finding that does not name its RCCB cannot start switch-offs
            out.append(
                Task(
                    str(path),
                    m.fault.fault_type,
                    m.fault.object_ids[0],
                    m.fault.start_us,
                    name,
                    finding.model_dump_json(by_alias=True),
                    m.finding.first_seen_us,
                    layer,
                    rccb,
                    expected_drop(finding) if layer == "L2" else None,
                )
            )
    return out


def evaluate(
    suite: Suite,
    split: str,
    detectors: Sequence[str],
    *,
    params: LocaliseParams | None = None,
    likelihood_path: Path | None = None,
    episodes_root: Path = episodes.DEFAULT_ROOT,
    workers: int | None = None,
    sealed_dir: Path = sealing.SEALED_DIR,
    test_log: Path = sealing.TEST_LOG,
    limit: int | None = None,
) -> dict[str, Any]:
    params = params or LocaliseParams()
    layer = _layer([suite])
    lik, plan, lik_source = load_likelihood(likelihood_path or LIKELIHOOD_PATHS[layer])
    items = manifests(suite, split)
    if split == "test":
        sealing.check(suite, items, sealed_dir)
        if limit is not None:
            raise ValueError("test runs use the whole sealed split")
    if limit is not None:
        items = items[:limit]
    paths = episodes.ensure(items, episodes_root, workers, suite.streams)
    house = HouseContext.canonical()
    tasks = [t for path in paths for t in _tasks(path, detectors, layer, house)]
    records = _pool(tasks, lik, params, plan, workers)
    report: dict[str, Any] = {
        "kind": "localisation",
        "suite": suite.name,
        "split": split,
        "layer": layer,
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

    flow = [c for r in records for c in r["checks"] if c["kind"] == "flow_stop"]
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
        "flow_checks_with_full_tank": _rate([bool(c["tank_full"]) for c in flow]),
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
        full = d["all"].get("flow_checks_with_full_tank")
        lines += [
            "",
            "Checks lowered the truth's probability for "
            f"{pct(d['all']['truth_p_fell'])} of faults."
            + (
                ""
                if full is None
                else f" Flow checks began with the tank full {pct(full)} of the time."
            ),
            "",
        ]
    return "\n".join(lines)
