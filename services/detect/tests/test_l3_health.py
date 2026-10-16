"""L3 health baseline on synthetic appliance signals."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from penjaga_contract.core import Finding
from penjaga_contract.validate import load_context, validate_finding_document
from penjaga_detect.chunk import Chunk, Series
from penjaga_detect.detector import HouseContext
from penjaga_detect.l3_health import TEMP, L3HealthBaseline

US = 1_000_000
T0 = 1_790_784_000 * US  # 2026-10-01T00:00:00+08:00
DAY = 86_400 * US
FRIDGE = "elec.appliance.app_fridge_kitchen.power_w"
AC = "elec.circuit.circ_ac_master.energy_wh"


def fridge_series(
    days: int,
    on_w: Callable[[int], float],
    period_s: Callable[[int], int],
    duty: Callable[[int], float],
) -> Series:
    """1 s fridge power: on for duty * period of every period, at on_w; idle at 2 W."""
    ts = T0 + (np.arange(days * 86_400, dtype=np.int64) + 1) * US
    day = (ts - T0 - 1) // DAY
    sec = (ts - T0) // US
    period = np.array([period_s(int(d)) for d in range(days)])[day]
    frac = np.array([duty(int(d)) for d in range(days)])[day]
    on = (sec % period) < frac * period
    watts = np.where(on, np.array([on_w(int(d)) for d in range(days)])[day], 2.0)
    return Series(ts, watts.astype(np.float64), np.zeros(ts.size, np.uint8))


def ac_series(days: int, per_hour_kwh: Callable[[int], float]) -> Series:
    """Minute energy: running 22:00-06:00 at the day's kWh per running hour."""
    ts = T0 + (np.arange(days * 1440, dtype=np.int64) + 1) * 60 * US
    day = (ts - T0 - 1) // DAY
    minute = ((ts - T0) // (60 * US)) % 1440
    running = (minute >= 22 * 60) | (minute < 6 * 60)
    wh = np.where(
        running, np.array([per_hour_kwh(int(d)) for d in range(days)])[day] * 1000 / 60, 0.0
    )
    return Series(ts, wh, np.zeros(ts.size, np.uint8))


def temp_series(days: int) -> Series:
    ts = T0 + (np.arange(days * 24, dtype=np.int64) + 1) * 3600 * US
    hour = ((ts - T0) // (3600 * US)) % 24
    values = 27.0 + 3.0 * np.sin((hour - 9) / 24 * 2 * np.pi)
    return Series(ts, values.astype(np.float64), np.zeros(ts.size, np.uint8))


def run(streams: dict[str, Series], days: int, step_h: float = 24.0) -> list[Finding]:
    detector = L3HealthBaseline(HouseContext.canonical())
    out: list[Finding] = []
    t, end = T0, T0 + days * DAY
    while t < end:
        t1 = min(end, t + round(step_h * 3600 * US))
        out += detector.update(
            Chunk("elmina_g6_demo", t, t1, {k: v.between(t, t1) for k, v in streams.items()})
        )
        t = t1
    return out


def steady(_: int) -> float:
    return 1.0


def test_quiet_when_nothing_changes() -> None:
    rng = np.random.default_rng(0)
    noise = rng.normal(0, 0.01, 40)
    streams = {
        FRIDGE: fridge_series(
            40, lambda d: 100.0 + 2 * noise[d], lambda d: 2400, lambda d: 0.45 + noise[d]
        ),
        AC: ac_series(40, lambda d: 0.15 * (1 + noise[d])),
        TEMP: temp_series(40),
    }
    assert run(streams, 40) == []


def test_door_seal_raises_the_duty_cycle() -> None:
    streams = {
        FRIDGE: fridge_series(
            30,
            lambda d: 100.0,
            lambda d: 2400,
            lambda d: 0.45 + 0.004 * (d % 3) + (0.15 if d >= 15 else 0),
        ),
        TEMP: temp_series(30),
    }
    findings = run(streams, 30)
    assert findings
    first = findings[0]
    assert first.type.value == "fridge_duty_cycle_rise"
    assert first.first_seen.day == 17  # the first day after onset already exceeds h: a big step
    assert first.object_ids == ["app_fridge_kitchen"]
    ctx = load_context()
    for f in findings:
        assert validate_finding_document(
            f.model_dump(mode="json", by_alias=True, exclude_none=True), ctx
        ).ok


def test_compressor_wear_shows_as_power_and_short_runs() -> None:
    streams = {
        FRIDGE: fridge_series(
            30,
            lambda d: 100.0 + (d % 2) + (20 if d >= 15 else 0),
            lambda d: 2400 if d < 15 else 900,
            lambda d: 0.45 + 0.004 * (d % 3),
        ),
        TEMP: temp_series(30),
    }
    types = {f.type.value for f in run(streams, 30)}
    assert types == {"fridge_compressor_wear"}


def test_aircon_efficiency_decline_is_measured() -> None:
    def per_hour(d: int) -> float:
        return 0.15 * (1 + 0.02 * ((d % 4) - 1.5)) * (1.25 if d >= 20 else 1.0)

    streams = {AC: ac_series(40, per_hour), TEMP: temp_series(40)}
    findings = run(streams, 40)
    assert findings
    assert {f.type.value for f in findings} == {"aircon_efficiency_decline"}
    gap = findings[-1].quantities["efficiency_gap_pct"].value
    assert 20 <= gap <= 30


def test_chunking_does_not_change_findings() -> None:
    streams = {
        FRIDGE: fridge_series(
            25,
            lambda d: 100.0,
            lambda d: 2400,
            lambda d: 0.45 + 0.004 * (d % 3) + (0.15 if d >= 15 else 0),
        ),
        TEMP: temp_series(25),
    }
    reference = run(streams, 25, 24)
    assert reference
    assert run(streams, 25, 7.5) == reference
