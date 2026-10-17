"""Where is the problem? Narrowing locations with guided checks (PRD §18.2, D-029, D-031).

A meter at the boundary cannot say where water is lost, and an RCCB cannot say which of its
circuits leaks to earth. The household can: closing a valve and watching the meter tells
whether a leak is behind that valve; switching one circuit off at its MCB and watching the
RCCB's residual current tells whether that circuit leaks. This module keeps a probability for
every place the problem could be, proposes the check that is expected to teach the most, and
updates the probabilities with each check's measured verdict (``checks``).

**Water (L1).** Hypotheses: every contract element where a water fault can occur (taps,
cisterns, the float valve, every pipe but the overflow). Checks use the valves the owner may
operate (``owner_operable``):
- ``flow_stop`` after closing a valve: a leak behind it stops (``stopped``), any other keeps the
  meter running (``unchanged``). Behind means what ``HouseIndex.isolated_by`` says: an angle
  valve isolates its fixture; any other valve its pipe and what is downstream of it, never
  across the roof tank. A leak on a valve's own pipe counts as behind it.
- ``tank_level_trend`` after closing the main stop valve: no water reaches the tank, so a leak
  behind the tank keeps draining it (``falling``); leaks on the main side and the float valve
  stop with everything else (``steady``).

**Earth leakage (L2).** Hypotheses: the circuits an RCCB protects directly (the leakage
baseline's candidates). Checks: ``residual_drop`` after switching one of them off at its MCB:
the RCCB's residual current falls if that circuit leaks (``dropped``).

**Prior.** From a finding: its certain ``object_ids`` (when they are hypotheses), then its
``candidates`` with their probabilities; the rest (``candidates_other_p``) is spread evenly over
the remaining hypotheses. A finding that names no location gives a uniform prior.

**Measured verdicts are noisy**: other flows and loads continue, windows are short, small drips
sit near the meter's starting flow. The update therefore uses ``Likelihood``, P(verdict | the
problem is or is not behind the action), estimated from validation episodes by the evaluation
harness (M11), never from test episodes. For flow checks it can depend on how much was flowing
before the valve closed (``flow_band``): a burst's verdict is more reliable than a drip's.

**No flow, no evidence.** ``checks.flow_stop`` says ``stopped`` whenever the meter is still
after the valve closes, including when it was already still before: a drip below the meter's
starting flow, or a cistern between phantom refills. ``evidence_verdict`` turns that into
``idle`` (flow before the closing below ``idle_below_lpm``).

**Next check**: the one with the largest expected reduction in entropy (bits), computed with
the flow-independent likelihood since the flow is not known before the check. Stop when the
most likely location reaches ``confident_p``, no check is expected to gain ``min_gain_bits``, or
after ``max_checks``.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
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

CheckKind = Literal["flow_stop", "tank_level_trend", "residual_drop"]
VERDICTS: dict[str, tuple[str, ...]] = {
    "flow_stop": ("stopped", "reduced", "unchanged", "idle", "unavailable"),
    "tank_level_trend": ("falling", "steady", "rising", "unavailable"),
    "residual_drop": ("dropped", "unchanged", "rose", "unavailable"),
}
OPERABLE_KINDS = frozenset({ValveKind.stop, ValveKind.angle, ValveKind.gate})
FLOW_BANDS: tuple[tuple[str, float], ...] = (("low", 0.2), ("mid", 2.0), ("high", math.inf))


@dataclass(frozen=True)
class CheckOption:
    kind: CheckKind
    target_id: str  # the valve to close, or the circuit to switch off
    behind: frozenset[str]  # hypotheses that would give the positive verdict
    rccb_id: str | None = None  # residual_drop: the RCCB whose residual current is watched

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.target_id}"

    @property
    def valve_id(self) -> str:
        return self.target_id


def flow_band(flow_before_lpm: float | None) -> str | None:
    """Which flow band a flow check's "before" flow falls in (None when not measured)."""
    if flow_before_lpm is None:
        return None
    return next(name for name, upper in FLOW_BANDS if flow_before_lpm < upper)


@dataclass(frozen=True)
class Likelihood:
    """P(verdict | behind) and P(verdict | not behind), per check kind; a kind may also have
    tables per flow band, keyed ``"flow_stop@high"``, used when the check's band is known."""

    table: Mapping[str, Mapping[str, Mapping[str, float]]]  # key -> "behind"/"other" -> verdict

    def p(self, kind: str, behind: bool, verdict: str, band: str | None = None) -> float:
        key = f"{kind}@{band}" if band is not None and f"{kind}@{band}" in self.table else kind
        return float(self.table[key]["behind" if behind else "other"][verdict])

    @staticmethod
    def default() -> Likelihood:
        """A cautious prior table, used until the harness has estimated one (M11)."""
        return Likelihood(
            {
                "flow_stop": {
                    "behind": {
                        "stopped": 0.6, "reduced": 0.15, "unchanged": 0.05, "idle": 0.18,
                        "unavailable": 0.02,
                    },
                    "other": {
                        "stopped": 0.03, "reduced": 0.07, "unchanged": 0.7, "idle": 0.18,
                        "unavailable": 0.02,
                    },
                },
                "tank_level_trend": {
                    "behind": {"falling": 0.7, "steady": 0.25, "rising": 0.03, "unavailable": 0.02},
                    "other": {"falling": 0.05, "steady": 0.85, "rising": 0.08, "unavailable": 0.02},
                },
                "residual_drop": {
                    "behind": {
                        "dropped": 0.7, "unchanged": 0.25, "rose": 0.02, "unavailable": 0.03,
                    },
                    "other": {
                        "dropped": 0.05, "unchanged": 0.85, "rose": 0.07, "unavailable": 0.03,
                    },
                },
            }
        )  # fmt: skip

    @staticmethod
    def from_counts(
        counts: Mapping[str, Mapping[str, Mapping[str, int]]],
        alpha: float = 1.0,
        min_n: int = 20,
    ) -> Likelihood:
        """Estimate from verdict counts with add-``alpha`` smoothing. Kinds without counts keep
        the default table; a flow band gets its own table only when both of its sides have at
        least ``min_n`` checks (otherwise the kind's pooled table answers for it)."""
        default = Likelihood.default().table
        table: dict[str, dict[str, dict[str, float]]] = {}

        def estimate(seen: Mapping[str, int], verdicts: tuple[str, ...]) -> dict[str, float]:
            total = sum(seen.get(v, 0) for v in verdicts)
            return {v: (seen.get(v, 0) + alpha) / (total + alpha * len(verdicts)) for v in verdicts}

        for kind, verdicts in VERDICTS.items():
            table[kind] = {}
            for side in ("behind", "other"):
                seen = counts.get(kind, {}).get(side, {})
                if sum(seen.get(v, 0) for v in verdicts) == 0:
                    table[kind][side] = dict(default[kind][side])
                else:
                    table[kind][side] = estimate(seen, verdicts)
        for key, sides in counts.items():
            kind, _, band = key.partition("@")
            if not band or kind not in VERDICTS:
                continue
            verdicts = VERDICTS[kind]
            if all(sum(sides.get(s, {}).values()) >= min_n for s in ("behind", "other")):
                table[key] = {s: estimate(sides[s], verdicts) for s in ("behind", "other")}
        return Likelihood(table)

    def as_dict(self) -> dict[str, Any]:
        return {k: {s: dict(v) for s, v in sides.items()} for k, sides in self.table.items()}


@dataclass(frozen=True)
class LocaliseParams:
    max_checks: int = 4
    confident_p: float = 0.8
    min_gain_bits: float = 0.05
    idle_below_lpm: float = 0.02  # flow before closing a valve below this: no evidence


def evidence_verdict(
    kind: str, verdict: str | None, flow_before_lpm: float | None, idle_below_lpm: float
) -> str:
    """The localiser's verdict for a check's measured outcome (see "No flow, no evidence")."""
    if verdict is None:
        return "unavailable"
    if kind == "flow_stop" and flow_before_lpm is not None and flow_before_lpm < idle_below_lpm:
        return "idle"
    return verdict


@dataclass
class Localiser:
    """The probabilities of one problem's location, updated check by check. Without explicit
    hypotheses and options it localises a water leak in ``index``'s house."""

    index: HouseIndex
    likelihood: Likelihood = field(default_factory=Likelihood.default)
    params: LocaliseParams = field(default_factory=LocaliseParams)
    hypotheses: tuple[str, ...] = ()
    options: list[CheckOption] = field(default_factory=list)
    posterior: dict[str, float] = field(default_factory=dict)
    done: list[tuple[CheckOption, str, str | None]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.hypotheses:
            self.hypotheses = hypotheses(self.index)
            self.options = self.options or check_options(self.index)
        if not self.posterior:
            self.posterior = uniform(self.hypotheses)

    def start_from(self, finding: Finding | None) -> None:
        self.posterior = prior(finding, self.hypotheses)
        self.done = []

    def ranked(self) -> list[tuple[str, float]]:
        return sorted(self.posterior.items(), key=lambda kv: (-kv[1], kv[0]))

    def option(self, kind: str, target_id: str) -> CheckOption | None:
        return next((o for o in self.options if o.kind == kind and o.target_id == target_id), None)

    def next_check(self) -> tuple[CheckOption, float] | None:
        """The most informative check not yet done and its expected gain (bits), or None."""
        if len(self.done) >= self.params.max_checks:
            return None
        if self.ranked()[0][1] >= self.params.confident_p:
            return None
        used = {o.key for o, _, _ in self.done}
        best, gain = None, self.params.min_gain_bits
        for option in self.options:
            if option.key in used:
                continue
            g = expected_gain(self.posterior, option, self.likelihood)
            if g > gain:
                best, gain = option, g
        return None if best is None else (best, gain)

    def observe(self, option: CheckOption, verdict: str, band: str | None = None) -> None:
        self.posterior = update(self.posterior, option, verdict, self.likelihood, band)
        self.done.append((option, verdict, band))


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
    """The water checks the household can do with the valves it may operate."""
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


def direct_circuits(index: HouseIndex, rccb_id: str) -> tuple[str, ...]:
    """Circuits whose nearest RCCB is ``rccb_id`` (what its residual current sees directly)."""
    return tuple(
        sorted(
            c.id
            for c in index.house.electrical.circuits
            if index.rccb_chain(c.id)[:1] == (rccb_id,)
        )
    )


def earth_leakage_localiser(
    index: HouseIndex,
    rccb_id: str,
    likelihood: Likelihood | None = None,
    params: LocaliseParams | None = None,
) -> Localiser:
    """A localiser over the circuits ``rccb_id`` protects directly, with one switch-off check
    per circuit."""
    circuits = direct_circuits(index, rccb_id)
    if not circuits:
        raise ValueError(f"{rccb_id!r} protects no circuit directly")
    options = [CheckOption("residual_drop", c, frozenset({c}), rccb_id) for c in circuits]
    return Localiser(
        index,
        likelihood or Likelihood.default(),
        params or LocaliseParams(),
        hypotheses=circuits,
        options=options,
    )


# ---------------------------------------------------------------------------- probability


def uniform(hyps: Iterable[str]) -> dict[str, float]:
    items = list(hyps)
    return {h: 1.0 / len(items) for h in items}


def prior(finding: Finding | None, hyps: Sequence[str]) -> dict[str, float]:
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
    posterior: Mapping[str, float],
    option: CheckOption,
    verdict: str,
    lik: Likelihood,
    band: str | None = None,
) -> dict[str, float]:
    if verdict not in VERDICTS[option.kind]:
        raise ValueError(f"{verdict!r} is not a {option.kind} verdict")
    return _normalised(
        {h: p * lik.p(option.kind, h in option.behind, verdict, band) for h, p in posterior.items()}
    )


def entropy_bits(p: Mapping[str, float]) -> float:
    return -sum(v * math.log2(v) for v in p.values() if v > 0)


def expected_gain(posterior: Mapping[str, float], option: CheckOption, lik: Likelihood) -> float:
    """Expected entropy reduction (bits) from doing ``option`` (flow-independent likelihood)."""
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
