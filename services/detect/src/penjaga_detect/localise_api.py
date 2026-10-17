"""``POST /detect/v1/localise``: a finding's location after the guided checks done for it.

Pure functions over contract documents, so the service only fetches the finding and the checks
(D-031). The answer is rebuilt from scratch on every call: the finding's prior, then each
completed check in the order given, then the most informative next check. Nothing is stored.

A check counts as evidence only if it is complete, of a kind the finding's localiser knows, and
names its target: ``valve_id`` for flow and tank checks (optional in the request, contract
1.5.0), ``circuit_id`` and the finding's RCCB for ``residual_drop``. Anything else is reported
as ``ignored``, never guessed.

Likelihoods and check timings come from ``<params_dir>/l1_localiser.json`` and
``l2_localiser.json``, written by the evaluation harness from validation episodes (M11). Without
them the cautious defaults apply.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from penjaga_contract.core import Finding
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

L1_TYPES = frozenset(
    {
        "night_flow_unexplained",
        "dripping_fixture_suspected",
        "running_cistern_suspected",
        "tank_overflow_suspected",
        "concealed_leak_suspected",
        "burst_pipe_suspected",
    }
)
L2_TYPES = frozenset({"earth_leakage_rising", "earth_leakage_elevated"})
MAX_CANDIDATES = 10


@dataclass(frozen=True)
class Timing:
    """How a suggested check is done (mirrors the harness's CheckPlan)."""

    flow_window_s: float = 600.0
    flow_settle_s: float = 120.0
    expect_below_lpm: float = 0.02
    tank_window_s: float = 1800.0
    residual_window_s: float = 120.0
    residual_settle_s: float = 30.0


@dataclass(frozen=True)
class LocaliserSetup:
    likelihood: dict[str, Likelihood] = field(
        default_factory=lambda: {"L1": Likelihood.default(), "L2": Likelihood.default()}
    )
    timing: Timing = field(default_factory=Timing)
    params: dict[str, LocaliseParams] = field(
        default_factory=lambda: {"L1": LocaliseParams(), "L2": LocaliseParams()}
    )

    @staticmethod
    def load(params_dir: Path | None) -> LocaliserSetup:
        """Fitted likelihoods, timings and stopping rules from the params directory, defaults
        otherwise."""
        likelihood = {"L1": Likelihood.default(), "L2": Likelihood.default()}
        params = {"L1": LocaliseParams(), "L2": LocaliseParams()}
        timing: dict[str, Any] = {}
        if params_dir is not None:
            for layer, name in (("L1", "l1_localiser.json"), ("L2", "l2_localiser.json")):
                path = params_dir / name
                if path.exists():
                    doc = json.loads(path.read_text(encoding="utf-8"))
                    likelihood[layer] = Likelihood(doc["likelihood"])
                    params[layer] = LocaliseParams(**doc.get("localise_params", {}))
                    plan = doc.get("check_plan", {})
                    timing.update({k: v for k, v in plan.items() if _timing_layer(k) == layer})
        return LocaliserSetup(likelihood, Timing(**timing), params)


def _timing_layer(key: str) -> str | None:
    """Which layer's fit sets a timing: switch-off checks are L2's, water checks L1's."""
    if key not in Timing.__dataclass_fields__:
        return None
    return "L2" if key.startswith("residual_") else "L1"


class LocaliseError(ValueError):
    """The finding cannot be localised (422)."""


def localiser_for(finding: Finding, house: HouseContext, setup: LocaliserSetup) -> Localiser:
    kind = finding.type.value
    if kind in L1_TYPES:
        loc = Localiser(house.index, setup.likelihood["L1"], setup.params["L1"])
    elif kind in L2_TYPES:
        rccbs = [o for o in finding.object_ids if o.startswith("rcd_")]
        if not rccbs:
            raise LocaliseError("an earth-leakage finding needs its RCCB in object_ids")
        loc = earth_leakage_localiser(
            house.index, rccbs[0], setup.likelihood["L2"], setup.params["L2"]
        )
    else:
        raise LocaliseError(f"{kind} findings are not localised by guided checks")
    loc.start_from(finding)
    return loc


def _measurement(check: Mapping[str, Any], name: str) -> float | None:
    for m in check.get("measurements", []):
        if m.get("name") == name:
            return float(m["value"])
    return None


def _option_for(loc: Localiser, check: Mapping[str, Any]) -> CheckOption | None:
    request = check["request"]
    kind = request["kind"]
    if kind == "residual_drop":
        option = loc.option(kind, request["circuit_id"])
        if option is None or option.rccb_id != request.get("rccb_id"):
            return None
        return option
    if kind in ("flow_stop", "tank_level_trend") and "valve_id" in request:
        return loc.option(kind, request["valve_id"])
    return None


def _band(
    option: CheckOption, verdict: str, flow_before: float | None, check: Mapping[str, Any]
) -> str | None:
    if option.kind == "residual_drop":
        return load_band(_measurement(check, "circuit_before"))
    return flow_band(flow_before) if verdict != "idle" else None


def expected_drop(finding: Finding) -> float | None:
    """The leakage rise an earth-leakage finding reported (rise_on_direct_circuits, mA)."""
    for item in finding.evidence:
        if item.name == "rise_on_direct_circuits" and isinstance(item.value, int | float):
            return float(item.value)
    return None


def _suggestion(
    option: CheckOption,
    gain: float,
    setup: LocaliserSetup,
    has_tank: bool,
    expect_drop_ma: float | None = None,
) -> dict[str, Any]:
    t = setup.timing
    out: dict[str, Any] = {"kind": option.kind, "expected_gain_bits": round(gain, 4)}
    if option.kind == "residual_drop":
        assert option.rccb_id is not None
        out.update(
            action="switch_off_circuit",
            circuit_id=option.target_id,
            rccb_id=option.rccb_id,
            settle_s=t.residual_settle_s,
            window_s=t.residual_window_s,
            hold_water_use=False,
        )
        if expect_drop_ma is not None:
            out["expect_drop_ma"] = round(expect_drop_ma, 3)
    elif option.kind == "flow_stop":
        out.update(
            action="close_valve",
            valve_id=option.target_id,
            settle_s=t.flow_settle_s,
            window_s=t.flow_window_s,
            expect_below_lpm=t.expect_below_lpm,
            hold_water_use=True,
            wait_for_full_tank=has_tank,
        )
    else:
        out.update(
            action="close_valve",
            valve_id=option.target_id,
            settle_s=0.0,
            window_s=t.tank_window_s,
            hold_water_use=True,
        )
    return out


def localise(
    finding: Finding,
    checks: Sequence[Mapping[str, Any]],
    house: HouseContext,
    setup: LocaliserSetup,
) -> dict[str, Any]:
    """The LocaliseResponse document for ``finding`` after ``checks`` (in the order done)."""
    loc = localiser_for(finding, house, setup)
    used = []
    for check in checks:
        option = _option_for(loc, check)
        if check.get("status") == "complete" and option is not None:
            before = _measurement(check, "flow_before")
            verdict = evidence_verdict(
                option.kind, check.get("verdict"), before, loc.params.idle_below_lpm
            )
            loc.observe(option, verdict, _band(option, verdict, before, check))
            used.append({"check_id": check["check_id"], "evidence": verdict})
        elif check.get("status") == "unavailable" and option is not None:
            loc.observe(option, "unavailable")
            used.append({"check_id": check["check_id"], "evidence": "unavailable"})
        else:
            used.append({"check_id": check["check_id"], "evidence": "ignored"})
    ranked = loc.ranked()
    top = [(h, round(p, 4)) for h, p in ranked[:MAX_CANDIDATES] if p > 0]
    shown = [{"object_id": h, "p": p} for h, p in top]
    nxt = loc.next_check()
    has_tank = bool(house.index.house.water.tanks)
    return {
        "finding_id": finding.id,
        "layer": "L1" if finding.type.value in L1_TYPES else "L2",
        "candidates": shown,
        "other_p": round(max(0.0, 1.0 - sum(p for _, p in top)), 4),
        "entropy_bits": round(entropy_bits(loc.posterior), 4),
        "checks_used": used,
        "next_check": None
        if nxt is None
        else _suggestion(nxt[0], nxt[1], setup, has_tank, expected_drop(finding)),
    }
