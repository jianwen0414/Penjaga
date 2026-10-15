"""MNF baseline on simulated houses, fed through the live (IngestMessage) path."""

from __future__ import annotations

from datetime import timedelta

import pytest

from penjaga_contract.core import Finding
from penjaga_contract.validate import load_context, validate_finding_document
from penjaga_detect.baselines.mnf import MnfBaseline, MnfParams
from penjaga_detect.chunk import chunk_from_messages
from penjaga_detect.detector import HouseContext
from penjaga_sim.export import SeqCounters, ingest_messages
from penjaga_sim.testing import START, at, fault, new_world
from penjaga_sim.world import World

US = 1_000_000
DAYS = 4


def run(world: World, step_h: float, params: MnfParams | None = None) -> list[Finding]:
    detector = MnfBaseline(HouseContext.canonical(), params)
    counters = SeqCounters()
    t = world.day_start_us(START)
    end = world.day_start_us(START + timedelta(days=DAYS))
    step = round(step_h * 3600 * US)
    out: list[Finding] = []
    while t < end:
        t1 = min(end, t + step)
        chunk = chunk_from_messages(
            world.model.index.house_id, t, t1, ingest_messages(world, t, t1, counters)
        )
        out += detector.update(chunk)
        t = t1
    return out


@pytest.fixture(scope="module")
def cistern_world() -> World:
    world = new_world()
    world.inject(fault("W2", "fix_bath2_wc_cistern", 0.3, at(START, 0.5)))
    return world


def test_no_finding_in_a_fault_free_house() -> None:
    assert run(new_world(), 6) == []


def test_running_cistern_is_flagged_after_two_nights(cistern_world: World) -> None:
    findings = run(cistern_world, 6)
    assert findings
    first = findings[0]
    assert first.type.value == "night_flow_unexplained"
    assert first.first_seen == at(START + timedelta(days=1), 4.0)  # end of the second night
    assert first.source.kind.value == "baseline"
    ctx = load_context()
    for f in findings:
        doc = f.model_dump(mode="json", by_alias=True, exclude_none=True)
        assert validate_finding_document(doc, ctx).ok


def test_findings_do_not_depend_on_chunking(cistern_world: World) -> None:
    reference = run(cistern_world, 24)
    for step in (1, 5.5):
        assert run(cistern_world, step) == reference


def test_threshold_above_the_leak_stays_quiet(cistern_world: World) -> None:
    assert run(cistern_world, 24, MnfParams(threshold_lpm=1.0)) == []
