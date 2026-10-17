"""The burst rule counts only water the roof tank did not store (v1.1.0, D-030)."""

from __future__ import annotations

import numpy as np
import pytest

from penjaga_contract.core import Finding
from penjaga_detect.chunk import Chunk, Series
from penjaga_detect.detector import HouseContext
from penjaga_detect.l1_burst import FLOW, TANK, BurstParams, L1BurstRule, rise_rate

US = 1_000_000
HOUSE = HouseContext.canonical()
TANK_L = float(HOUSE.index.house.water.tanks[0].capacity_l)
T0 = 1_790_000_000 * US
HOURS = 4


def _series(period_s: int, values: np.ndarray) -> Series:
    ts = T0 + (np.arange(values.size, dtype=np.int64) + 1) * period_s * US
    return Series(ts, values.astype(np.float64), np.zeros(values.size, np.uint8))


def _house_data(lost_lpm: float, tank_rise_lpm: float) -> dict[str, Series]:
    """A tank refilling at ``tank_rise_lpm`` from 5% until full, plus ``lost_lpm`` that leaves
    the house (a burst); the meter sees both."""
    full_after_min = 0.95 * TANK_L / tank_rise_lpm if tank_rise_lpm > 0 else 0.0
    seconds = (np.arange(HOURS * 360) + 1) * 10.0
    refilling = seconds / 60.0 <= full_after_min
    flow = lost_lpm + np.where(refilling, tank_rise_lpm, 0.0)
    minutes = np.arange(1, HOURS * 60 + 1)
    level = np.minimum(100.0, 5.0 + 100.0 * tank_rise_lpm * minutes / TANK_L)
    return {FLOW: _series(10, flow), TANK: _series(60, level)}


def _run(data: dict[str, Series], step_h: float) -> list[Finding]:
    detector = L1BurstRule(HOUSE, BurstParams())
    out: list[Finding] = []
    t = T0
    end = T0 + HOURS * 3600 * US
    while t < end:
        t1 = min(end, t + round(step_h * 3600 * US))
        out += detector.update(
            Chunk(HOUSE.house_id, t, t1, {s: v.between(t, t1) for s, v in data.items()})
        )
        t = t1
    return out


def test_a_slow_night_refill_is_not_a_burst() -> None:
    # 8 L/min for over two hours, all of it going into the tank (a low-pressure night refill)
    assert _run(_house_data(0.0, 8.0), 24) == []


def test_flow_the_tank_does_not_store_is_a_burst() -> None:
    (opened,) = _run(_house_data(8.0, 0.0), 24)
    assert opened.type.value == "burst_pipe_suspected"
    assert opened.severity.value == "urgent"
    evidence = {e.name: e.value for e in opened.evidence}
    assert evidence["tank_fill"] == 0.0


def test_a_burst_during_a_refill_is_still_found() -> None:
    (opened,) = _run(_house_data(12.0, 8.0), 24)
    evidence = {e.name: e.value for e in opened.evidence}
    assert evidence["tank_fill"] == pytest.approx(8.0, abs=0.2)


def test_without_a_tank_reading_all_flow_counts() -> None:
    data = _house_data(0.0, 8.0)
    del data[TANK]
    opened = _run(data, 24)[0]
    assert opened.type.value == "burst_pipe_suspected"


@pytest.mark.parametrize("step_h", [0.25, 1.5, 7.0])
def test_chunking_does_not_change_the_findings(step_h: float) -> None:
    for lost, rise in ((8.0, 0.0), (12.0, 8.0), (0.0, 8.0)):
        data = _house_data(lost, rise)
        assert _run(data, step_h) == _run(data, 24)


def test_rise_rate_uses_the_readings_inside_the_window() -> None:
    ts = np.array([60, 120, 180, 240], dtype=np.int64) * US
    level = np.array([10.0, np.nan, 30.0, 40.0])
    at = np.array([60, 180, 240, 400], dtype=np.int64) * US
    out = rise_rate(ts, level, at, 200 * US)
    assert np.isnan(out[0])  # one reading spans nothing
    assert out[1] == pytest.approx(20.0 / 2.0)  # 10 -> 30 over 2 minutes (NaN skipped)
    assert out[2] == pytest.approx(30.0 / 3.0)
    assert np.isnan(out[3])  # only 240 s remains in (200, 400]: under half the window
