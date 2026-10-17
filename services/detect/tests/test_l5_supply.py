"""L5 supply interruptions on simulated houses."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta

import pytest

from penjaga_contract.core import Finding
from penjaga_contract.lab import FaultSpec
from penjaga_detect.chunk import chunk_from_messages
from penjaga_detect.detector import HouseContext
from penjaga_detect.l5_supply import L5SupplyDetector, SupplyParams
from penjaga_sim.export import SeqCounters, ingest_messages
from penjaga_sim.testing import START, at, fault, new_world

US = 1_000_000


def run(
    faults: Sequence[FaultSpec],
    step_h: float = 6.0,
    days: int = 1,
    params: SupplyParams | None = None,
) -> list[Finding]:
    world = new_world()
    for f in faults:
        world.inject(f)
    detector = L5SupplyDetector(HouseContext.canonical(), params)
    counters = SeqCounters()
    t = world.day_start_us(START)
    end = world.day_start_us(START + timedelta(days=days))
    out: list[Finding] = []
    while t < end:
        t1 = min(end, t + round(step_h * 3600 * US))
        msgs = ingest_messages(world, t, t1, counters)
        out += detector.update(chunk_from_messages(world.model.index.house_id, t, t1, msgs))
        t = t1
    return out


def test_quiet_without_interruptions() -> None:
    assert run([]) == []


def test_water_cut_is_found_with_the_tank_outlook() -> None:
    findings = run([fault("U1", None, 1, at(START, 18.0), duration_s=3 * 3600)])
    opened, cleared = findings
    assert opened.type.value == "water_supply_interruption"
    minutes_late = (opened.first_seen - at(START, 18.0)).total_seconds() / 60
    assert 5 <= minutes_late <= 15  # the 10-minute mean falls below 0.5 bar
    assert "tank_water_l" in opened.quantities
    assert cleared.status.value == "cleared"
    assert cleared.cleared_at is not None
    assert at(START, 21.0) < cleared.cleared_at <= at(START, 21.5)


def test_power_cut_opens_and_restoration_clears() -> None:
    findings = run([fault("U3", None, 1, at(START, 9.0), duration_s=1800)])
    assert [(f.type.value, f.status.value) for f in findings] == [
        ("power_supply_interruption", "active"),
        ("power_supply_interruption", "cleared"),
    ]
    assert findings[0].first_seen == at(START, 9.0)
    assert findings[1].cleared_at == at(START, 9.5)


def test_chunking_does_not_change_findings() -> None:
    faults = [
        fault("U1", None, 1, at(START, 18.0), duration_s=3 * 3600),
        fault("U3", None, 1, at(START, 9.0), duration_s=1800),
    ]
    reference = run(faults, step_h=24)
    assert run(faults, step_h=1.5) == reference


HISTORY = [SupplyParams(outlook_method=m) for m in ("daily_mean", "hour_profile")]


@pytest.mark.parametrize("params", HISTORY, ids=lambda p: p.outlook_method)
def test_history_outlooks_need_history_and_ignore_chunking(params: SupplyParams) -> None:
    cut = [fault("U1", None, 1, at(START + timedelta(days=3), 18.0), duration_s=3 * 3600)]
    reference = run(cut, step_h=24, days=4, params=params)
    opened = reference[0]
    hours = opened.quantities["tank_hours_left"].value
    litres = opened.quantities["tank_water_l"].value
    assert 3 < hours < 96  # a family drains a mostly full 1,364 L tank in hours to days
    assert litres > 500
    assert run(cut, step_h=1.5, days=4, params=params) == reference


def test_history_outlook_says_nothing_without_a_day_of_history() -> None:
    cut = [fault("U1", None, 1, at(START, 8.0), duration_s=3 * 3600)]  # 8 hours of history
    opened = run(cut, params=SupplyParams(outlook_method="daily_mean"))[0]
    assert "tank_water_l" in opened.quantities
    assert "tank_hours_left" not in opened.quantities  # under 12 of the last 24 hours known


def test_unknown_outlook_method_is_refused() -> None:
    with pytest.raises(ValueError, match="outlook_method"):
        L5SupplyDetector(HouseContext.canonical(), SupplyParams(outlook_method="guess"))
