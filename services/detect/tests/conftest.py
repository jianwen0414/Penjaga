"""An in-memory M4 for the detection service, fed by a simulated episode."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Sequence
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pytest

from penjaga_contract.core import ClockMode, ClockState
from penjaga_detect.chunk import US, EventRecord, batch_samples, event_record
from penjaga_detect.m4 import M4UnavailableError, Samples
from penjaga_sim.export import SeqCounters, ingest_messages
from penjaga_sim.world import World

_END = object()


class FakeM4:
    """Publishes a world's messages on demand. Subscribers get a clock message, then everything
    published while they are connected; the store answers raw and event queries."""

    def __init__(self, world: World, start: datetime, *, episode: int = 1) -> None:
        self.world = world
        self.start = start
        self.episode = episode
        self.published_us = int(start.timestamp()) * US
        self.counters = SeqCounters()
        self.stored: list[dict[str, Any]] = []
        self._subscribers: list[asyncio.Queue[Any]] = []
        self.down = False
        self.raw_calls = 0

    def house_time(self) -> datetime:
        return datetime.fromtimestamp(self.published_us / US, tz=self.start.tzinfo)

    def clock_state(self) -> ClockState:
        return ClockState(
            house_time=self.house_time(),
            mode=ClockMode.accelerated,
            speed=60.0,
            effective_speed=0.0,  # the test drives house time; no extrapolation
            paused=True,
            episode=self.episode,
            episode_start=self.start,
        )

    async def publish(self, hours: float, step_h: float = 0.25) -> None:
        """Publish ``hours`` of house time in steps (events first within each step)."""
        end = self.published_us + round(hours * 3600 * US)
        while self.published_us < end:
            t1 = min(end, self.published_us + round(step_h * 3600 * US))
            messages = ingest_messages(self.world, self.published_us, t1, self.counters)
            self.published_us = t1
            self.stored += messages
            for q in self._subscribers:
                for m in messages:
                    await q.put(m)

    async def drop_subscribers(self) -> None:
        """Close every subscription, as M4 does to a slow subscriber (1013)."""
        for q in self._subscribers:
            await q.put(_END)
        self._subscribers.clear()

    async def clock(self) -> ClockState:
        return self.clock_state()

    @contextlib.asynccontextmanager
    async def subscribe(self) -> AsyncIterator[Any]:
        if self.down:
            raise M4UnavailableError("M4 is down")
        q: asyncio.Queue[Any] = asyncio.Queue()
        await q.put({"type": "clock", "data": self.clock_state().model_dump(mode="json")})
        self._subscribers.append(q)

        async def messages() -> AsyncIterator[dict[str, Any]]:
            while True:
                m = await q.get()
                if m is _END:
                    return
                yield m

        try:
            yield messages()
        finally:
            with contextlib.suppress(ValueError):
                self._subscribers.remove(q)

    async def raw(self, stream: str, from_us: int, to_us: int, period_s: float) -> Samples:
        self.raw_calls += 1
        parts = [
            batch_samples(m["data"])
            for m in self.stored
            if m["type"] == "batch" and m["data"]["stream"] == stream
        ]
        if not parts:
            return np.empty(0, np.int64), np.empty(0), np.empty(0, np.uint8)
        ts, values, quality = (np.concatenate([p[i] for p in parts]) for i in range(3))
        keep = (ts >= from_us) & (ts < to_us)
        return ts[keep], values[keep], quality[keep]

    async def events(
        self, streams: Sequence[str], from_us: int, to_us: int
    ) -> list[tuple[EventRecord, int]]:
        out = []
        for m in self.stored:
            if m["type"] == "event" and m["data"]["stream"] in streams:
                e = event_record(m["data"])
                if from_us <= e.ts_us < to_us:
                    out.append((e, int(m["data"]["seq"])))
        return out


def day_us(start: datetime, days: float) -> int:
    return int((start + timedelta(days=days)).timestamp()) * US


@pytest.fixture
def fake_m4() -> type[FakeM4]:
    """The FakeM4 class (test modules cannot import conftest under --import-mode=importlib)."""
    return FakeM4
