"""Where is the leak? Narrowing L1 locations with guided checks (PRD §18.2, D-029).

A meter at the boundary cannot say where water is lost. The household can: closing a valve and
watching the meter tells whether the leak is behind that valve. This module keeps a
probability for every place a leak could be, proposes the check that is expected to teach the
most, and updates the probabilities with each check's measured verdict (``checks``).

**Hypotheses.** Every contract element where a water fault can occur: taps, cisterns, the
float valve and every pipe except the overflow.

**Prior.** From a finding: its certain ``object_ids``, then its ``candidates`` with their
probabilities; the rest (``candidates_other_p``) is spread evenly over the remaining
hypotheses. A finding that names no location (the minimum-night-flow baseline) gives a uniform
prior.

**Checks** use the valves the owner can operate (``owner_operable``):
- ``flow_stop`` after closing a valve: a leak behind it stops (expected verdict ``stopped``),
  any other keeps the meter running (``unchanged``). Behind means what
  ``HouseIndex.isolated_by`` says: an angle valve isolates its fixture; any other valve its pipe
  and what is downstream of it, never across the roof tank. A leak on a valve's own pipe counts
  as behind it.
- ``tank_level_trend`` after closing the main stop valve: no water reaches the tank, so a leak
  behind the tank keeps draining it (``falling``); leaks on the main side and the float valve
  stop with everything else (``steady``). Closing the main stop valve and watching the meter
  would say nothing, since all flow stops.

**Measured verdicts are noisy**: other flows (the purifier refilling, the tank topping up)
continue, windows are short, small drips sit near the meter's starting flow. The update
therefore uses ``Likelihood``, P(verdict | the leak is or is not behind the valve), estimated
from validation episodes by the evaluation harness (M11), never from test episodes.

**Next check**: the one with the largest expected reduction in entropy (bits). Stop when the
most likely location reaches ``confident_p``, no check is expected to gain ``min_gain_bits``,
or after ``max_checks``.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from penjaga_contract.core import (
    Finding,
    FixtureKind,
    PipeKind,
    Valve,
    ValveKind,
)
from penjaga_contract.house import ElementKind, HouseIndex

CheckKind = Literal["flow_stop", "tank_level_trend"]
VERDICTS: dict[str, tuple[str, ...]] = {
    "flow_stop": ("stopped", "reduced", "unchanged", "unavailable"),
    "tank_level_trend": ("falling", "steady", "rising", "unavailable"),
}
OPERABLE_KINDS = frozenset({ValveKind.stop, ValveKind.angle, ValveKind.gate})


@dataclass(frozen=True)
class CheckOption:
    kind: CheckKind
    valve_id: str
    behind: frozenset[str]  # hypotheses that would give the positive verdict

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.valve_id}"


@dataclass(frozen=True)
class Likelihood:
    """P(verdict | leak behind the valve) and P(verdict | not behind), per check kind."""

    table: Mapping[str, Mapping[str, Mapping[str, float]]]  # kind -> "behind"/"other" -> verdict

    def p(self, kind: str, behind: bool, verdict: str) -> float:
        return float(self.table[kind]["behind" if behind else "other"][verdict])

    @staticmethod
    def default() -> Likelihood:
        """A cautious prior table, used until the harness has estimated one (M11)."""
        return Likelihood(
            {
                "flow_stop": {
                    "behind": {
                        "stopped": 0.7, "reduced": 0.2, "unchanged": 0.08, "unavailable": 0.02,
                    },
                    "other": {
                        "stopped": 0.04, "reduced": 0.08, "unchanged": 0.86, "unavailable": 0.02,
                    },
                },
                "tank_level_trend": {
                    "behind": {"falling": 0.7, "steady": 0.25, "rising": 0.03, "unavailable": 0.02},
                    "other": {"falling": 0.05, "steady": 0.85, "rising": 0.08, "unavailable": 0.02},
                },
            }
        )  # fmt: skip

    @staticmethod
    def from_counts(
        counts: Mapping[str, Mapping[str, Mapping[str, int]]], alpha: float = 1.0
    ) -> Likelihood:
        """Estimate from verdict counts with add-``alpha`` smoothing; kinds without counts keep
        the default table."""
        default = Likelihood.default().table
        table: dict[str, dict[str, dict[str, float]]] = {}
        for kind, verdicts in VERDICTS.items():
            table[kind] = {}
            for side in ("behind", "other"):
                seen = counts.get(kind, {}).get(side, {})
                total = sum(seen.get(v, 0) for v in verdicts)
                if total == 0:
                    table[kind][side] = dict(default[kind][side])
                    continue
                denominator = total + alpha * len(verdicts)
                table[kind][side] = {v: (seen.get(v, 0) + alpha) / denominator for v in verdicts}
        return Likelihood(table)

    def as_dict(self) -> dict[str, Any]:
        return {k: {s: dict(v) for s, v in sides.items()} for k, sides in self.table.items()}


@dataclass(frozen=True)
class LocaliseParams:
    max_checks: int = 4
    confident_p: float = 0.8
    min_gain_bits: float = 0.05


@dataclass
class Localiser:
    """The probabilities of one leak's location, updated check by check."""

    index: HouseIndex
    likelihood: Likelihood = field(default_factory=Likelihood.default)
    params: LocaliseParams = field(default_factory=LocaliseParams)
    posterior: dict[str, float] = field(default_factory=dict)
    done: list[tuple[CheckOption, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.hypotheses = hypotheses(self.index)
        self.options = check_options(self.index)
        if not self.posterior:
            self.posterior = uniform(self.hypotheses)

    def start_from(self, finding: Finding | None) -> None:
        self.posterior = prior(finding, self.hypotheses)
        self.done = []

    def ranked(self) -> list[tuple[str, float]]:
        return sorted(self.posterior.items(), key=lambda kv: (-kv[1], kv[0]))

    def next_check(self) -> CheckOption | None:
        if len(self.done) >= self.params.max_checks:
            return None
        if self.ranked()[0][1] >= self.params.confident_p:
            return None
        used = {o.key for o, _ in self.done}
        best, gain = None, self.params.min_gain_bits
        for option in self.options:
            if option.key in used:
                continue
            g = expected_gain(self.posterior, option, self.likelihood)
            if g > gain:
                best, gain = option, g
        return best

    def observe(self, option: CheckOption, verdict: str) -> None:
        self.posterior = update(self.posterior, option, verdict, self.likelihood)
        self.done.append((option, verdict))


# ---------------------------------------------------------------------------- house topology


def hypotheses(index: HouseIndex) -> tuple[str, ...]:
    """Every element where a water leak can be, in contract order."""
    water = index.house.water
    taps_and_cisterns = [f.id for f in water.fixtures if f.kind is not FixtureKind.water_purifier]
    floats = [v.id for v in water.valves if v.kind is ValveKind.float]
    pipes = [p.id for p in water.pipes if p.kind is not PipeKind.overflow]
    return tuple(dict.fromkeys([*taps_and_cisterns, *floats, *pipes]))


def _behind_valve(index: HouseIndex, valve: Valve) -> frozenset[str]:
    if valve.kind is ValveKind.angle:
        return frozenset(index.isolated_by(valve.id))
    nodes = index.network_downstream(valve.pipe_id, through_tanks=False)
    pipes = {n for n in nodes if index.kind_of(n) is not ElementKind.tank}
    fixtures = set(index.fixtures_fed_by(valve.pipe_id, through_tanks=False))
    return frozenset(pipes | fixtures)


def _behind_tank(index: HouseIndex) -> frozenset[str]:
    """Elements fed through the roof tank (they keep drawing stored water)."""
    out: set[str] = set()
    for tank in index.house.water.tanks:
        for node in index.network_downstream(tank.id):
            if index.kind_of(node) is not ElementKind.tank:
                out.add(node)
        out |= set(index.fixtures_fed_by(tank.id))
    return frozenset(out)


def check_options(index: HouseIndex) -> list[CheckOption]:
    """The checks the household can do with the valves it may operate."""
    known = set(hypotheses(index))
    out: list[CheckOption] = []
    for valve in index.house.water.valves:
        if not valve.owner_operable or valve.kind not in OPERABLE_KINDS:
            continue
        if index.stops_tank_refill(valve.id):
            behind = _behind_tank(index) & known
            out.append(CheckOption("tank_level_trend", valve.id, frozenset(behind)))
        else:
            behind = _behind_valve(index, valve) & known
            out.append(CheckOption("flow_stop", valve.id, frozenset(behind)))
    return out


# ---------------------------------------------------------------------------- probability


def uniform(hyps: Iterable[str]) -> dict[str, float]:
    items = list(hyps)
    return {h: 1.0 / len(items) for h in items}


def prior(finding: Finding | None, hyps: tuple[str, ...]) -> dict[str, float]:
    """The finding's location belief over the hypotheses (uniform if it names none)."""
    if finding is None:
        return uniform(hyps)
    known = set(hyps)
    named: dict[str, float] = {}
    certain = [o for o in finding.object_ids if o in known]
    candidates = [(c.object_id, c.p) for c in finding.candidates if c.object_id in known]
    if certain:
        share = 1.0 / len(certain)
        named = dict.fromkeys(certain, share)
    elif candidates:
        for object_id, p in candidates:
            named[object_id] = named.get(object_id, 0.0) + float(p)
    if not named:
        return uniform(hyps)
    rest = [h for h in hyps if h not in named]
    other = 0.0 if certain else float(finding.candidates_other_p or 0.0)
    total_named = sum(named.values())
    if not rest or other <= 0.0:
        return _normalised({h: named.get(h, 0.0) for h in hyps})
    out = {h: named[h] / total_named * (1.0 - other) for h in named}
    out.update(dict.fromkeys(rest, other / len(rest)))
    return _normalised({h: out[h] for h in hyps})


def _normalised(p: Mapping[str, float]) -> dict[str, float]:
    total = sum(p.values())
    if total <= 0:
        return uniform(p)
    return {h: v / total for h, v in p.items()}


def update(
    posterior: Mapping[str, float], option: CheckOption, verdict: str, lik: Likelihood
) -> dict[str, float]:
    if verdict not in VERDICTS[option.kind]:
        raise ValueError(f"{verdict!r} is not a {option.kind} verdict")
    return _normalised(
        {h: p * lik.p(option.kind, h in option.behind, verdict) for h, p in posterior.items()}
    )


def entropy_bits(p: Mapping[str, float]) -> float:
    return -sum(v * math.log2(v) for v in p.values() if v > 0)


def expected_gain(posterior: Mapping[str, float], option: CheckOption, lik: Likelihood) -> float:
    """Expected entropy reduction (bits) from doing ``option``."""
    p_behind = sum(p for h, p in posterior.items() if h in option.behind)
    before = entropy_bits(posterior)
    after = 0.0
    for verdict in VERDICTS[option.kind]:
        p_verdict = p_behind * lik.p(option.kind, True, verdict) + (1 - p_behind) * lik.p(
            option.kind, False, verdict
        )
        if p_verdict <= 0:
            continue
        after += p_verdict * entropy_bits(update(posterior, option, verdict, lik))
    return before - after
