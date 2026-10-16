"""L2 leakage baseline: rises against each RCCB's own normal, nesting, switching loads, chunking."""

from __future__ import annotations

from collections.abc import Callable, Sequence

import numpy as np
import pytest

from penjaga_contract.core import Finding
from penjaga_detect.chunk import Chunk, Series, chunk_from_messages
from penjaga_detect.detector import HouseContext
from penjaga_detect.l2_leakage import L2LeakageBaseline, LeakageParams
from penjaga_sim.export import SeqCounters, ingest_messages
from penjaga_sim.testing import START, at, fault, new_world

US = 1_000_000
H = 3600
T0 = 1_759_248_000 * US  # 2025-10-01 00:00 +08:00, on an hour boundary
HOUSE = HouseContext.canonical()
ALL = sorted(L2LeakageBaseline(HOUSE).devices)

Profile = Callable[[np.ndarray], np.ndarray]  # seconds since T0 -> residual mA


def run(
    profiles: dict[str, Profile],
    hours: int,
    step_h: float = 6.0,
    params: LeakageParams | None = None,
) -> list[Finding]:
    """Feed every RCCB 1 s residual current from ``profiles`` (default 0.1 mA)."""
    detector = L2LeakageBaseline(HOUSE, params)
    out: list[Finding] = []
    t = 0
    end = hours * H
    rng = np.random.default_rng(1)
    while t < end:
        t1 = min(end, t + round(step_h * H))
        secs = np.arange(t + 1, t1 + 1)
        series = {}
        for device in ALL:
            base = profiles.get(device, lambda s: np.full(s.size, 0.1))(secs)
            values = np.round(base + rng.normal(0, 0.02, secs.size), 1)
            series[f"elec.rcd.{device}.residual_ma"] = Series(
                T0 + secs * US, values, np.zeros(secs.size, dtype=np.uint8)
            )
        out += detector.update(Chunk(HOUSE.house_id, T0 + t * US, T0 + t1 * US, series))
        t = t1
    return out


def step_at(hour: float, before: float, after: float) -> Profile:
    return lambda s: np.where(s > hour * H, after, before)


def opened(findings: Sequence[Finding]) -> list[str]:
    return [f.object_ids[0] for f in findings if f.revision == 1]


def test_quiet_without_change() -> None:
    assert run({}, 7 * 24) == []


def test_a_step_rise_is_found_at_its_rccb_with_its_circuits_as_candidates() -> None:
    findings = run({"rcd_sockets": step_at(96, 0.6, 1.4)}, 7 * 24)
    assert opened(findings) == ["rcd_sockets"]  # the main RCCB's rise is explained by it
    first = findings[0]
    assert first.type.value == "earth_leakage_rising"
    assert first.severity.value == "heads_up"
    hours_late = (first.first_seen.timestamp() * US - T0) / (H * US) - 96
    assert 0 < hours_late <= 24
    candidates = [c.object_id for c in first.candidates]
    assert candidates == sorted(HOUSE.index.circuits_under("rcd_sockets"))
    assert sum(c.p for c in first.candidates) + first.candidates_other_p == pytest.approx(1.0)


def test_a_rise_on_the_main_rccb_alone_names_the_main_rccb() -> None:
    findings = run({"rcd_main": step_at(96, 1.2, 2.0)}, 7 * 24)
    assert opened(findings) == ["rcd_main"]


def test_switching_loads_do_not_alarm() -> None:
    """Appliances add leakage while they run: evenings at +0.8 mA, the floor unchanged."""

    def evenings(s: np.ndarray) -> np.ndarray:
        hour = (s // H) % 24
        busy = (hour >= 19) | (hour < 6)
        days = s // (24 * H)
        some_nights = (days % 3) != 1  # not every night, as with aircon on warm nights
        return np.where(busy & some_nights, 1.9, 1.1)

    assert run({"rcd_main": evenings}, 10 * 24) == []


def test_a_rise_that_falls_back_clears() -> None:
    def profile(s: np.ndarray) -> np.ndarray:
        return np.where((s > 96 * H) & (s <= 130 * H), 2.0, 0.6)

    findings = run({"rcd_sockets": profile}, 10 * 24)
    assert findings[0].status.value == "active"
    assert findings[-1].status.value == "cleared"


def test_chunking_does_not_change_findings() -> None:
    profiles = {"rcd_sockets": step_at(80, 0.6, 1.6)}
    reference = run(profiles, 6 * 24, step_h=24)
    assert reference
    assert run(profiles, 6 * 24, step_h=1.75) == reference


def test_simulated_rising_leak_is_found_quickly() -> None:
    world = new_world()
    world.inject(
        fault(
            "E2",
            "circ_sockets_living",
            1.0,
            at(START, 80.0),
            progression={"kind": "worsening", "growth_per_day": 0.1},
        )
    )
    detector = L2LeakageBaseline(HOUSE)
    counters = SeqCounters()
    t, end = world.day_start_us(START), world.day_start_us(START) + 6 * 24 * H * US
    found: list[Finding] = []
    while t < end:
        t1 = t + 12 * H * US
        msgs = ingest_messages(world, t, t1, counters)
        found += detector.update(chunk_from_messages(HOUSE.house_id, t, t1, msgs))
        t = t1
    first = [f for f in found if f.revision == 1]
    assert [f.object_ids[0] for f in first] == ["rcd_sockets"]
    assert first[0].first_seen <= at(START, 80.0 + 30)
