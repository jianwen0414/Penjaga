"""The stream service core (M4), independent of HTTP: ingest, clock control, queries.

``api.py`` maps it onto HTTP and WebSocket routes; tests drive it directly.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import anyio
from pydantic import TypeAdapter, ValidationError

from penjaga_contract.core import (
    BatchMessage,
    ClockCommand,
    ClockMessage,
    ClockMode,
    ClockState,
    Event,
    EventMessage,
    IngestMessage,
    RawResponse,
    RawSample,
    RejectedMessage,
    Rejection,
    ResetCommand,
    ResetMessage,
    ResetNotice,
    StreamKind,
    StreamStatus,
    SummaryPoint,
    SummaryResponse,
)
from penjaga_contract.validate import ContractContext, load_context
from penjaga_stream.clock import ClockError, Governor, HouseClock
from penjaga_stream.config import StreamConfig
from penjaga_stream.hub import Hub
from penjaga_stream.registry import QUALITY_CODES, StreamRegistry
from penjaga_stream.store import ReadingStore
from penjaga_stream.timeutil import US_PER_S, from_us, seconds_to_us, to_us


class QueryError(ValueError):
    """A query the API must refuse (maps to HTTP 422/404)."""

    def __init__(self, message: str, *, not_found: bool = False) -> None:
        super().__init__(message)
        self.not_found = not_found


@dataclass(frozen=True)
class IngestResult:
    accepted: bool
    rejection: str | None = None  # RejectedMessage JSON when refused


_INGEST: TypeAdapter[BatchMessage | EventMessage] = TypeAdapter(IngestMessage)


class StreamService:
    def __init__(
        self,
        config: StreamConfig,
        *,
        ctx: ContractContext | None = None,
        clock: HouseClock | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        wall_now_us: Callable[[], int] = lambda: time.time_ns() // 1000,
    ) -> None:
        self.config = config
        self.ctx = ctx or load_context()
        self.zone = ZoneInfo(self.ctx.house.location.timezone)
        self.registry = StreamRegistry(self.ctx)
        self.clock = clock or HouseClock(
            start_us=wall_now_us(),
            mode=ClockMode.real,
            speed=1.0,
            zone=self.zone,
            monotonic=monotonic,
        )
        self.hub = Hub(config.lossless_queue_size, config.lossless_put_timeout_s)
        self.governor = Governor()
        self.store = ReadingStore(self._store_path(self.clock.episode))
        self.rejected = 0

    def _store_path(self, episode: int) -> Path:
        return self.config.data_dir / f"episode-{episode:04d}" / "readings.duckdb"

    # ------------------------------------------------------------------ ingest

    async def ingest(self, text: str) -> IngestResult:
        try:
            message = _INGEST.validate_json(text)
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(p) for p in first.get("loc", ()))
            return self._reject(["schema.invalid"], f"{where}: {first.get('msg')}")
        if isinstance(message, BatchMessage):
            batch = message.data
            codes, prepared = self.registry.check_batch(batch)
            if prepared is None:
                return self._reject(codes, "batch refused", batch.stream, batch.seq)
            self.registry.accept_batch(prepared)
            self.store.append_batch(prepared)
            await self.hub.publish_batch(prepared, text)
            return IngestResult(True)
        event = message.data
        codes, prepared_event = self.registry.check_event(event)
        if prepared_event is None:
            return self._reject(codes, "event refused", event.stream, event.seq)
        self.registry.accept_event(prepared_event)
        self.store.append_event(prepared_event)
        await self.hub.publish_event(text)
        return IngestResult(True)

    def _reject(
        self, codes: list[str], message: str, stream: str | None = None, seq: int | None = None
    ) -> IngestResult:
        self.rejected += 1
        rejection = Rejection(stream=stream, seq=seq, codes=codes, message=message)
        text = RejectedMessage(type="rejected", data=rejection).model_dump_json(exclude_none=True)
        return IngestResult(False, text)

    # ------------------------------------------------------------------ clock

    def clock_state(self) -> ClockState:
        return self.clock.state()

    def clock_message(self) -> str:
        return ClockMessage(type="clock", data=self.clock.state()).model_dump_json()

    async def command_clock(self, command: ClockCommand) -> ClockState:
        """Apply an operator command atomically (all fields are checked before any change)."""
        if command.speed is None and command.paused is None and command.jump_to is None:
            # The schema's minProperties: 1 is not enforced by the generated model.
            raise QueryError("a clock command needs at least one of speed, paused, jump_to")
        if self.clock.mode is ClockMode.real:
            raise ClockError("the clock is in real mode; reset to accelerated mode first")
        jump_us = None if command.jump_to is None else to_us(command.jump_to)
        if jump_us is not None and jump_us < self.clock.now_us():
            raise ClockError("house time is monotonic: jump_to must not be in the past")
        if command.jump_to is not None and jump_us is not None:
            self.clock.jump_to(jump_us)
        if command.speed is not None:
            self.clock.set_speed(command.speed)
        if command.paused is not None:
            self.clock.set_paused(command.paused)
        await self.hub.publish_control(self.clock_message())
        return self.clock.state()

    async def reset(self, command: ResetCommand) -> ClockState:
        """Start a new episode: new clock epoch, new store, cleared continuity."""
        start_us = to_us(command.start)
        if command.mode is ClockMode.real and command.speed != 1.0:
            raise ClockError("a real-mode clock runs at speed 1")
        old = self.store
        self.clock.reset(start_us=start_us, mode=command.mode, speed=command.speed)
        self.registry.reset()
        self.store = ReadingStore(self._store_path(self.clock.episode))
        await anyio.to_thread.run_sync(old.close)
        notice = ResetNotice(episode=self.clock.episode, start=from_us(start_us, self.zone))
        await self.hub.publish_control(ResetMessage(type="reset", data=notice).model_dump_json())
        await self.hub.publish_control(self.clock_message())
        return self.clock.state()

    async def governor_step(self) -> None:
        """One governor update: slow house time if detection or the producer lags."""
        if self.clock.mode is ClockMode.real or self.clock.paused:
            return
        position = self.registry.producer_position_us()
        lag_house_s = 0.0 if position is None else max(0, self.clock.now_us() - position) / US_PER_S
        new_limit = self.governor.step(
            requested=self.clock.speed,
            current_limit=self.clock.governor_limit,
            queue_fill=self.hub.queue_fill(),
            data_lag_wall_s=lag_house_s / self.clock.speed,
        )
        if new_limit != self.clock.governor_limit:
            self.clock.set_governor_limit(new_limit)
            await self.hub.publish_control(self.clock_message())

    # ------------------------------------------------------------------ queries

    def _sampled_index(self, stream: str) -> int:
        definition = self.ctx.streams.get(stream)
        if definition is None:
            raise QueryError(f"unknown stream {stream!r}", not_found=True)
        if definition.kind is not StreamKind.sampled:
            raise QueryError(f"{stream!r} is an event stream; use /events")
        return self.registry.index[stream]

    @staticmethod
    def _window(start: datetime, end: datetime) -> tuple[int, int]:
        from_us_, to_us_ = to_us(start), to_us(end)
        if to_us_ <= from_us_:
            raise QueryError("'to' must be after 'from'")
        return from_us_, to_us_

    async def summary(
        self, stream: str, start: datetime, end: datetime, bucket_s: float
    ) -> SummaryResponse:
        index = self._sampled_index(stream)
        from_us_, to_us_ = self._window(start, end)
        if bucket_s <= 0:
            raise QueryError("bucket_s must be positive")
        bucket_us = seconds_to_us(bucket_s)
        points = math.ceil((to_us_ - from_us_) / bucket_us)
        limit = self.config.max_summary_points
        if points > limit:
            minimum = math.ceil((to_us_ - from_us_) / limit / US_PER_S)
            raise QueryError(f"{points} buckets exceed {limit}; use bucket_s >= {minimum}")
        store = self.store
        rows = await anyio.to_thread.run_sync(store.summary, index, from_us_, to_us_, bucket_us)
        unit = self.ctx.streams[stream].unit.value
        return SummaryResponse(
            stream=stream,
            unit=unit,
            from_=from_us(from_us_, self.zone),
            to=from_us(to_us_, self.zone),
            bucket_s=bucket_s,
            points=[
                SummaryPoint(
                    t=from_us(from_us_ + r.bucket * bucket_us, self.zone),
                    n=r.n,
                    min=r.min,
                    max=r.max,
                    mean=r.mean,
                    last=r.last,
                )
                for r in rows
            ],
        )

    async def raw(self, stream: str, start: datetime, end: datetime) -> RawResponse:
        index = self._sampled_index(stream)
        from_us_, to_us_ = self._window(start, end)
        store = self.store
        count = await anyio.to_thread.run_sync(store.count_raw, index, from_us_, to_us_)
        if count > self.config.max_raw_samples:
            raise QueryError(
                f"{count} samples exceed {self.config.max_raw_samples}; narrow the window"
            )
        rows = await anyio.to_thread.run_sync(
            store.raw, index, from_us_, to_us_, self.config.max_raw_samples
        )
        return RawResponse(
            stream=stream,
            unit=self.ctx.streams[stream].unit.value,
            from_=from_us(from_us_, self.zone),
            to=from_us(to_us_, self.zone),
            samples=[
                RawSample(
                    t=from_us(r.ts_us, self.zone), value=r.value, quality=QUALITY_CODES[r.quality]
                )
                for r in rows
            ],
        )

    async def events(
        self, streams: list[str] | None, start: datetime, end: datetime
    ) -> list[Event]:
        from_us_, to_us_ = self._window(start, end)
        indices = None
        if streams is not None:
            indices = []
            for sid in streams:
                definition = self.ctx.streams.get(sid)
                if definition is None or definition.kind is not StreamKind.event:
                    raise QueryError(f"{sid!r} is not an event stream")
                indices.append(self.registry.index[sid])
        store = self.store
        rows = await anyio.to_thread.run_sync(
            store.events, indices, from_us_, to_us_, self.config.max_events
        )
        return [
            Event.model_validate(
                {
                    "ts": from_us(r.ts_us, self.zone),
                    "house_id": self.ctx.house_id,
                    "stream": self.registry.ids[r.stream_index],
                    "seq": r.seq,
                    "payload": r.payload,
                }
            )
            for r in rows
        ]

    def status(self) -> list[StreamStatus]:
        return self.registry.status(self.ctx.house.location.timezone)

    # ------------------------------------------------------------------ lifecycle

    async def run_background(self) -> None:
        """Periodic store flush and governor steps until cancelled."""
        loop = asyncio.get_running_loop()
        next_gov = loop.time()
        while True:
            store = self.store
            await anyio.to_thread.run_sync(store.flush)
            if loop.time() >= next_gov:
                await self.governor_step()
                next_gov = loop.time() + self.config.governor_interval_s
            await asyncio.sleep(self.config.store_flush_interval_s)

    async def close(self) -> None:
        with contextlib.suppress(RuntimeError):
            await anyio.to_thread.run_sync(self.store.close)
