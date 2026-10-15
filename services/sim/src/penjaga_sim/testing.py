"""Helpers for tests of the simulator and of code that consumes it (M11 harness tests)."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from penjaga_contract.core import ClockMode, ClockState, ResetCommand
from penjaga_contract.lab import AnomalySpec, EpisodeManifest, FaultSpec
from penjaga_contract.validate import (
    load_context,
    validate_event_document,
    validate_reading_batch_document,
)
from penjaga_sim import GENERATOR_VERSION
from penjaga_sim.faults import UNIT
from penjaga_sim.stream_client import StreamUnavailableError
from penjaga_sim.world import DayOutput, World, WorldConfig

SEED = 7
ARCHETYPE = "working_family"
START = date(2026, 10, 1)
US = 1_000_000


def zone() -> ZoneInfo:
    return ZoneInfo(load_context().house.location.timezone)


def at(day: date, hour: float = 0.0) -> datetime:
    """House-local time ``hour`` hours after midnight of ``day``."""
    return datetime.combine(day, datetime.min.time(), tzinfo=zone()) + timedelta(hours=hour)


def new_world(**config: Any) -> World:
    return World(WorldConfig(**{"seed": SEED, "archetype": ARCHETYPE, "start": START, **config}))


def fault(
    fault_type: str,
    location: str | None,
    magnitude: float,
    start: datetime,
    *,
    duration_s: float | None = None,
    progression: dict[str, Any] | None = None,
) -> FaultSpec:
    doc: dict[str, Any] = {
        "fault_type": fault_type,
        "location_id": location,
        "magnitude": {"value": magnitude, "unit": UNIT[fault_type]},
        "progression": progression or {"kind": "constant"},
        "start_at": start.isoformat(),
    }
    if duration_s is not None:
        doc["duration_s"] = duration_s
    return FaultSpec.model_validate(doc)


def anomaly(kind: str, start: datetime, duration_s: float, **params: Any) -> AnomalySpec:
    return AnomalySpec.model_validate(
        {
            "anomaly_type": kind,
            "start_at": start.isoformat(),
            "duration_s": duration_s,
            "params": params,
        }
    )


def manifest(**overrides: Any) -> EpisodeManifest:
    """A valid one-day, fault-free manifest for the canonical house; override any field."""
    ctx = load_context()
    doc: dict[str, Any] = {
        "schema_version": "1.0",
        "episode_id": "ep_test_001",
        "split": "train",
        "seed": SEED,
        "house_id": ctx.house_id,
        "household_archetype": ARCHETYPE,
        "start": at(START).isoformat(),
        "duration_days": 1,
        "enabled_sensor_ids": sorted(s.id for s in ctx.house.sensors),
        "faults": [],
        "anomalies": [],
        "generator_version": GENERATOR_VERSION,
    }
    doc.update(overrides)
    return EpisodeManifest.model_validate(doc)


def same_output(a: DayOutput, b: DayOutput) -> bool:
    """True when two generated days carry identical readings, quality flags and events."""
    if a.streams.keys() != b.streams.keys() or a.events != b.events:
        return False
    for sid, sa in a.streams.items():
        sb = b.streams[sid]
        if (sa.t0_us, sa.period_us) != (sb.t0_us, sb.period_us):
            return False
        if not np.array_equal(sa.values, sb.values, equal_nan=True):
            return False
        if not np.array_equal(sa.quality, sb.quality):
            return False
    return True


def window(out: DayOutput, stream: str, h0: float, h1: float) -> Any:
    """Values of ``stream`` stamped in (h0, h1] hours after the day's midnight."""
    sd = out.streams[stream]
    ts = sd.t0_us + np.arange(sd.values.size) * sd.period_us
    t0, t1 = out.start_us + int(h0 * 3600 * US), out.start_us + int(h1 * 3600 * US)
    return sd.values[(ts > t0) & (ts <= t1)]


def meter_litres(out: DayOutput) -> float:
    """Volume the water meter reported over the day."""
    flow = out.streams["water.main.flow_lpm"]
    return float(np.nansum(flow.values) * flow.period_us / US / 60.0)


class FakeStream:
    """In-memory stand-in for M4: a settable clock, resets, and a recording ingest socket.

    Every message sent is checked against the contract (schema plus semantic rules), so a test
    using it also proves the simulator only emits valid documents.
    """

    def __init__(self, start: datetime, *, episode: int = 1, speed: float = 60.0) -> None:
        self.now = start
        self.episode = episode
        self.speed = speed
        self.sent: list[dict[str, Any]] = []
        self.fail_sends = 0  # the next n sends raise, as a dropped connection would
        self.resets: list[ResetCommand] = []

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)

    async def clock(self) -> ClockState:
        return ClockState(
            house_time=self.now,
            mode=ClockMode.accelerated,
            speed=self.speed,
            effective_speed=self.speed,
            paused=False,
            episode=self.episode,
        )

    async def reset(self, command: ResetCommand) -> ClockState:
        self.resets.append(command)
        self.episode += 1
        self.now = command.start
        self.speed = command.speed
        return await self.clock()

    @contextlib.asynccontextmanager
    async def ingest(self) -> AsyncIterator[FakeStream]:
        yield self

    async def send(self, messages: list[dict[str, Any]]) -> None:
        if self.fail_sends:
            self.fail_sends -= 1
            raise StreamUnavailableError("simulated dropped connection")
        ctx = load_context()
        for m in messages:
            check = (
                validate_reading_batch_document if m["type"] == "batch" else validate_event_document
            )
            check(m["data"], ctx).raise_for_errors(m["type"])
        self.sent.extend(messages)
