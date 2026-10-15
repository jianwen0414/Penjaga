"""Score findings against ground truth (PRD §21): the only place truth meets findings.

Matching rules, per layer:
- a fault expects findings of its layer with one of the accepted types (EXPECTED);
- a finding detects a fault when its first revision's ``first_seen`` lies in
  [fault start, fault end + GRACE[layer]]; each finding detects at most one fault, and each
  fault is credited to its earliest matching finding (greedy, by fault start);
- every other finding of the layer is a false alarm. False alarms are attributed to any
  legitimate anomaly whose window (plus grace) contains them, to explain where they come from.

Time to detect is first_seen minus fault start. Localisation compares the truth's object ids
(and their rooms) with the finding's ranked locations: certain ``object_ids`` first, then
``candidates`` by probability. Intervals are 95% bootstrap intervals over episodes.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from penjaga_contract.core import Finding
from penjaga_contract.house import HouseIndex

US = 1_000_000
DAY_US = 86_400 * US
HOUSE_MONTH_DAYS = 30.4375

_LEAK = "night_flow_unexplained"  # the generic L1 type accepted for every water fault
_TRIPS = ("protective_device_trip", "repeated_trips")
# fault type -> (layer, the specific finding type, other accepted finding types)
_ROWS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "W1": ("L1", "dripping_fixture_suspected", (_LEAK,)),
    "W2": ("L1", "running_cistern_suspected", (_LEAK,)),
    "W3": ("L1", "tank_overflow_suspected", (_LEAK,)),
    "W4": ("L1", "concealed_leak_suspected", (_LEAK,)),
    "W5": ("L1", "burst_pipe_suspected", (_LEAK,)),
    "E1": ("L2", "circuit_overload", _TRIPS),
    "E2": ("L2", "earth_leakage_rising", ("earth_leakage_elevated", *_TRIPS)),
    "E3": ("L2", "repeated_trips", ("protective_device_trip",)),
    "A1": ("L3", "aircon_efficiency_decline", ()),
    "A2": ("L3", "aircon_efficiency_decline", ()),
    "A3": ("L3", "fridge_duty_cycle_rise", ("fridge_compressor_wear",)),
    "A4": ("L3", "fridge_compressor_wear", ("fridge_duty_cycle_rise",)),
    "U1": ("L5", "water_supply_interruption", ()),
    "U2": ("L5", "water_supply_interruption", ()),
    "U3": ("L5", "power_supply_interruption", ()),
}
# fault type -> (layer, accepted finding types, the specific finding type)
EXPECTED: dict[str, tuple[str, frozenset[str], str]] = {
    t: (layer, frozenset({specific, *others}), specific)
    for t, (layer, specific, others) in _ROWS.items()
}
GRACE_US = {"L1": 2 * DAY_US, "L2": DAY_US, "L3": 7 * DAY_US, "L5": 3600 * US}


def _us(text: str) -> int:
    moment = datetime.fromisoformat(text)
    return int(moment.replace(microsecond=0).timestamp()) * US + moment.microsecond


@dataclass(frozen=True)
class TruthFault:
    record_id: str
    fault_type: str
    layer: str
    start_us: int
    end_us: int  # the episode end when the fault never ended
    object_ids: tuple[str, ...]
    magnitude: float


@dataclass(frozen=True)
class TruthAnomaly:
    label: str
    start_us: int
    end_us: int


@dataclass(frozen=True)
class FindingSummary:
    """A finding as scored: its first revision (when and where it was first raised)."""

    id: str
    layer: str
    type: str
    first_seen_us: int
    ranked: tuple[str, ...]  # object_ids, then candidates by probability


@dataclass
class EpisodeResult:
    episode_id: str
    days: float
    faults: list[TruthFault]
    anomalies: list[TruthAnomaly]
    findings: list[FindingSummary]


def summarise(findings: Iterable[Finding]) -> list[FindingSummary]:
    """One summary per finding id, from its first revision."""
    first: dict[str, Finding] = {}
    for f in findings:
        first.setdefault(f.id, f)
    out = []
    for f in first.values():
        candidates = sorted(f.candidates, key=lambda c: -c.p)
        ranked = tuple(dict.fromkeys([*f.object_ids, *(c.object_id for c in candidates)]))
        out.append(
            FindingSummary(f.id, f.layer.value, f.type.value, _us(f.first_seen.isoformat()), ranked)
        )
    return sorted(out, key=lambda s: (s.first_seen_us, s.id))


def load_truth(episode: Path) -> tuple[list[TruthFault], list[TruthAnomaly], float]:
    """The episode's truth (scorer only) and its length in days."""
    meta = json.loads((episode / "observed" / "meta.json").read_text(encoding="utf-8"))
    t1 = _us(meta["window"]["to"])
    t0 = _us(meta["window"]["from"])
    faults, anomalies = [], []
    with (episode / "truth" / "truth.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            start = _us(r["start"])
            end = t1 if r["end"] is None else min(t1, _us(r["end"]))
            if r["kind"] == "fault":
                layer = EXPECTED[r["label"]][0]
                magnitude = float(r["params"].get("magnitude", 0.0))
                faults.append(
                    TruthFault(
                        r["record_id"],
                        r["label"],
                        layer,
                        start,
                        end,
                        tuple(r["object_ids"]),
                        magnitude,
                    )
                )
            else:
                anomalies.append(TruthAnomaly(r["label"], start, end))
    return faults, anomalies, (t1 - t0) / DAY_US


@dataclass
class Match:
    episode_id: str
    fault: TruthFault
    finding: FindingSummary | None


@dataclass
class LayerScore:
    matches: list[Match] = field(default_factory=list)
    false_alarms: list[tuple[str, FindingSummary, str | None]] = field(default_factory=list)
    days: float = 0.0
    findings: int = 0


def match_episode(result: EpisodeResult, layer: str, score: LayerScore) -> None:
    faults = sorted((f for f in result.faults if f.layer == layer), key=lambda f: f.start_us)
    findings = [f for f in result.findings if f.layer == layer]
    used: set[str] = set()
    for fault in faults:
        accepted = EXPECTED[fault.fault_type][1]
        window_end = fault.end_us + GRACE_US[layer]
        hits = [
            f
            for f in findings
            if f.id not in used
            and f.type in accepted
            and fault.start_us <= f.first_seen_us <= window_end
        ]
        best = min(hits, key=lambda f: f.first_seen_us) if hits else None
        if best is not None:
            used.add(best.id)
        score.matches.append(Match(result.episode_id, fault, best))
    for f in findings:
        if f.id in used:
            continue
        cause = next(
            (
                a.label
                for a in result.anomalies
                if a.start_us <= f.first_seen_us <= a.end_us + GRACE_US[layer]
            ),
            None,
        )
        score.false_alarms.append((result.episode_id, f, cause))
    score.days += result.days
    score.findings += len(findings)


def _rooms(index: HouseIndex, ids: Sequence[str]) -> set[str]:
    rooms: set[str] = set()
    for i in ids:
        if index.exists(i):
            rooms |= set(index.rooms_of(i))
    return rooms


def _bootstrap(
    episodes: list[str], stat: Any, reps: int = 1000, seed: int = 0
) -> list[float] | None:
    if not episodes:
        return None
    r = np.random.default_rng(seed)
    values = []
    for _ in range(reps):
        sample = [episodes[i] for i in r.integers(0, len(episodes), len(episodes))]
        v = stat(sample)
        if v is not None:
            values.append(v)
    if not values:
        return None
    lo, hi = np.percentile(values, [2.5, 97.5])
    return [round(float(lo), 4), round(float(hi), 4)]


def share(flags: Sequence[bool]) -> float | None:
    return None if not flags else sum(flags) / len(flags)


def summarise_layer(score: LayerScore, index: HouseIndex, episodes: list[str]) -> dict[str, Any]:
    """Metrics for one layer: per fault type and overall."""
    by_type: dict[str, list[Match]] = {}
    for m in score.matches:
        by_type.setdefault(m.fault.fault_type, []).append(m)

    def recall_of(ms: list[Match]) -> float | None:
        return None if not ms else sum(m.finding is not None for m in ms) / len(ms)

    types: dict[str, Any] = {}
    for fault_type, ms in sorted(by_type.items()):
        detected = [m for m in ms if m.finding is not None]
        hours = sorted(
            (m.finding.first_seen_us - m.fault.start_us) / 3600 / US for m in detected if m.finding
        )
        specific = EXPECTED[fault_type][2]
        top1 = [
            bool(m.finding and m.finding.ranked[:1] and m.finding.ranked[0] in m.fault.object_ids)
            for m in detected
        ]
        top3 = [
            bool(m.finding and set(m.finding.ranked[:3]) & set(m.fault.object_ids))
            for m in detected
        ]
        room = [
            bool(
                m.finding
                and _rooms(index, m.finding.ranked[:1]) & _rooms(index, m.fault.object_ids)
            )
            for m in detected
        ]
        ep_ids = sorted({m.episode_id for m in ms})

        def stat(sample: list[str], ms: list[Match] = ms) -> float | None:
            chosen = [m for e in sample for m in ms if m.episode_id == e]
            return recall_of(chosen)

        magnitudes = sorted(m.fault.magnitude for m in ms)
        bins = []
        if len(ms) >= 6:
            edges = np.quantile(magnitudes, [0, 1 / 3, 2 / 3, 1])
            for k, (lo, hi) in enumerate(itertools.pairwise(edges)):
                # (lo, hi], and the first bin also takes the smallest magnitude
                part = [
                    m
                    for m in ms
                    if lo < m.fault.magnitude <= hi or (k == 0 and m.fault.magnitude == lo)
                ]
                bins.append(
                    {
                        "magnitude": [round(float(lo), 4), round(float(hi), 4)],
                        "n": len(part),
                        "recall": recall_of(part),
                    }
                )
        types[fault_type] = {
            "n": len(ms),
            "detected": len(detected),
            "recall": recall_of(ms),
            "recall_ci95": _bootstrap(ep_ids, stat),
            "ttd_hours_median": None if not hours else round(float(np.median(hours)), 2),
            "ttd_hours_p90": None if not hours else round(float(np.percentile(hours, 90)), 2),
            "specific_type_rate": share(
                [m.finding is not None and m.finding.type == specific for m in detected]
            ),
            "localisation_top1": share(top1),
            "localisation_top3": share(top3),
            "room_top1": share(room),
            "recall_by_magnitude": bins,
        }  # fmt: skip
    tp = sum(m.finding is not None for m in score.matches)
    fp = len(score.false_alarms)
    months = score.days / HOUSE_MONTH_DAYS
    causes: dict[str, int] = {}
    for _, _, cause in score.false_alarms:
        causes[cause or "none"] = causes.get(cause or "none", 0) + 1

    def precision_stat(sample: list[str]) -> float | None:
        t = sum(1 for e in sample for m in score.matches if m.episode_id == e and m.finding)
        f = sum(1 for e in sample for x in score.false_alarms if x[0] == e)
        return None if t + f == 0 else t / (t + f)

    return {
        "faults": len(score.matches),
        "detected": tp,
        "recall": None if not score.matches else tp / len(score.matches),
        "findings": score.findings,
        "false_alarms": fp,
        "precision": None if tp + fp == 0 else tp / (tp + fp),
        "precision_ci95": _bootstrap(episodes, precision_stat),
        "house_months": round(months, 3),
        "false_alarms_per_house_month": None if months == 0 else round(fp / months, 4),
        "false_alarms_by_cause": causes,
        "by_fault_type": types,
    }


def score_episodes(
    results: Sequence[EpisodeResult], layers: Sequence[str], index: HouseIndex
) -> dict[str, Any]:
    episodes = [r.episode_id for r in results]
    out: dict[str, Any] = {}
    for layer in layers:
        score = LayerScore()
        for r in results:
            match_episode(r, layer, score)
        out[layer] = summarise_layer(score, index, episodes)
    return out


def result_for(episode: Path, findings: Iterable[Finding]) -> EpisodeResult:
    faults, anomalies, days = load_truth(episode)
    return EpisodeResult(episode.name, days, faults, anomalies, summarise(findings))
