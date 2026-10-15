"""Fan-out of accepted readings to subscribers.

Two kinds of subscriber:

* **Lossless** (detection, role ``detector``): every batch and event, in order. Each has a
  bounded queue; when it is full the publisher waits, which back-pressures the producer, and
  the clock governor slows house time. A subscriber that stays full beyond a timeout is
  disconnected (and can catch up through the raw API) instead of stalling everyone forever.
* **Live** (app and Lab, roles ``app``/``operator``): batches are folded into per-stream
  aggregates and sent as a frame at most ``max_hz`` times per wall second. Events and
  control messages are never aggregated or dropped (PRD §14).
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

import numpy as np

from penjaga_contract.core import FrameMessage, LiveFrame, LiveStreamAggregate
from penjaga_stream.registry import PreparedBatch
from penjaga_stream.timeutil import from_us

SendText = Callable[[str], Awaitable[None]]


class SubscriberTooSlowError(Exception):
    """A lossless subscriber was disconnected for not keeping up."""


@dataclass(eq=False)
class LosslessSubscriber:
    maxsize: int
    put_timeout_s: float
    queue: asyncio.Queue[str] = field(init=False)
    closed: bool = False

    def __post_init__(self) -> None:
        self.queue = asyncio.Queue(maxsize=self.maxsize)

    @property
    def fill(self) -> float:
        return self.queue.qsize() / self.maxsize

    async def put(self, text: str) -> None:
        if self.closed:
            return
        try:
            await asyncio.wait_for(self.queue.put(text), timeout=self.put_timeout_s)
        except TimeoutError:
            self.closed = True
            raise SubscriberTooSlowError from None


@dataclass
class _Agg:
    n: int = 0
    total: float = 0.0
    lo: float = math.inf
    hi: float = -math.inf
    last: float | None = None
    t_last_us: int = 0

    def add(self, batch: PreparedBatch) -> None:
        present = ~np.isnan(batch.values)
        self.t_last_us = batch.last_t_us
        if not present.any():
            return
        vals = batch.values[present]
        self.n += int(vals.size)
        self.total += float(vals.sum())
        self.lo = min(self.lo, float(vals.min()))
        self.hi = max(self.hi, float(vals.max()))
        self.last = float(vals[-1])


@dataclass(eq=False)
class LiveSubscriber:
    """Aggregating subscriber. One sender task owns the socket (no concurrent sends)."""

    streams: frozenset[str] | None
    max_hz: float
    zone: ZoneInfo
    _aggs: dict[str, _Agg] = field(default_factory=dict)
    _urgent: asyncio.Queue[str] = field(default_factory=asyncio.Queue)

    def wants(self, stream: str) -> bool:
        return self.streams is None or stream in self.streams

    def add_batch(self, batch: PreparedBatch) -> None:
        if self.wants(batch.stream):
            self._aggs.setdefault(batch.stream, _Agg()).add(batch)

    def push_urgent(self, text: str) -> None:
        """Events and control messages: queued unbounded, sent before the next frame."""
        self._urgent.put_nowait(text)

    def take_frame(self, house_us: int) -> str:
        streams = {
            sid: LiveStreamAggregate(
                t_last=from_us(a.t_last_us, self.zone),
                n=a.n,
                min=a.lo if a.n else None,
                max=a.hi if a.n else None,
                mean=a.total / a.n if a.n else None,
                last=a.last,
            )
            for sid, a in self._aggs.items()
        }
        self._aggs = {}
        frame = LiveFrame(house_time=from_us(house_us, self.zone), streams=streams)
        return FrameMessage(type="frame", data=frame).model_dump_json()

    async def run_sender(self, send: SendText, house_now: Callable[[], int]) -> None:
        """Send urgent messages immediately and a frame every 1/max_hz wall seconds."""
        interval = 1.0 / self.max_hz
        loop = asyncio.get_running_loop()
        next_frame = loop.time() + interval
        while True:
            timeout = max(0.0, next_frame - loop.time())
            try:
                text = await asyncio.wait_for(self._urgent.get(), timeout=timeout)
            except TimeoutError:
                await send(self.take_frame(house_now()))
                next_frame = loop.time() + interval
                continue
            await send(text)


@dataclass
class Hub:
    lossless_maxsize: int = 256
    lossless_put_timeout_s: float = 10.0
    lossless: set[LosslessSubscriber] = field(default_factory=set)
    live: set[LiveSubscriber] = field(default_factory=set)
    dropped_subscribers: int = 0

    def add_lossless(self) -> LosslessSubscriber:
        sub = LosslessSubscriber(self.lossless_maxsize, self.lossless_put_timeout_s)
        self.lossless.add(sub)
        return sub

    def remove_lossless(self, sub: LosslessSubscriber) -> None:
        self.lossless.discard(sub)

    def add_live(self, sub: LiveSubscriber) -> None:
        self.live.add(sub)

    def remove_live(self, sub: LiveSubscriber) -> None:
        self.live.discard(sub)

    def queue_fill(self) -> float:
        """Fill fraction of the fullest lossless queue (0 when there are none)."""
        return max((s.fill for s in self.lossless if not s.closed), default=0.0)

    async def _to_lossless(self, text: str) -> None:
        for sub in list(self.lossless):
            try:
                await sub.put(text)
            except SubscriberTooSlowError:
                self.lossless.discard(sub)
                self.dropped_subscribers += 1

    async def publish_batch(self, batch: PreparedBatch, text: str) -> None:
        await self._to_lossless(text)
        for sub in self.live:
            sub.add_batch(batch)

    async def publish_event(self, text: str) -> None:
        await self._to_lossless(text)
        for sub in self.live:
            sub.push_urgent(text)

    async def publish_control(self, text: str) -> None:
        """Clock changes and resets go to everyone, never dropped or aggregated."""
        await self._to_lossless(text)
        for sub in self.live:
            sub.push_urgent(text)
