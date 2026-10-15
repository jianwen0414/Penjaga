"""Stream registry: the catalogue in memory, fast ingest validation, per-stream continuity.

The checks mirror ``penjaga_contract.validate.validate_reading_batch_document`` and
``validate_event_document`` (same issue codes; an equivalence test keeps them aligned) but
run on already-parsed Pydantic models with numpy, fast enough for 600x house time.
On top of the contract rules, M4 enforces continuity per stream: batch numbers and time must
not go backwards or overlap. Forward gaps are accepted and counted, because real sensors drop
out.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

from penjaga_contract.core import (
    Event,
    ProtectionDeviceState,
    Quality,
    ReadingBatch,
    StreamDef,
    StreamKind,
    StreamStatus,
    StreamStatusKind,
)
from penjaga_contract.validate import ContractContext
from penjaga_stream.timeutil import from_us, house_zone, seconds_to_us, to_us

QUALITY_CODES: tuple[Quality, ...] = (
    Quality.ok,
    Quality.missing,
    Quality.estimated,
    Quality.out_of_range,
    Quality.stale,
)
QUALITY_INDEX: dict[Quality, int] = {q: i for i, q in enumerate(QUALITY_CODES)}
MISSING = QUALITY_INDEX[Quality.missing]
OUT_OF_RANGE = QUALITY_INDEX[Quality.out_of_range]


@dataclass
class StreamState:
    """Continuity and counters for one stream within the current episode."""

    last_seq: int | None = None
    next_t_us: int | None = None
    last_at_us: int | None = None
    samples: int = 0
    gaps: int = 0


@dataclass(frozen=True)
class PreparedBatch:
    """A validated batch in columnar form, ready for fan-out and storage."""

    stream: str
    stream_index: int
    seq: int
    t0_us: int
    period_us: int
    values: npt.NDArray[np.float64]  # NaN where missing
    quality: npt.NDArray[np.uint8]

    @property
    def n(self) -> int:
        return int(self.values.size)

    @property
    def end_us(self) -> int:
        """Exclusive end: timestamp the next contiguous batch must start at."""
        return self.t0_us + self.n * self.period_us

    @property
    def last_t_us(self) -> int:
        return self.t0_us + (self.n - 1) * self.period_us

    def timestamps_us(self) -> npt.NDArray[np.int64]:
        return self.t0_us + np.arange(self.n, dtype=np.int64) * self.period_us


@dataclass(frozen=True)
class PreparedEvent:
    stream: str
    stream_index: int
    seq: int
    ts_us: int
    event: Event


@dataclass
class StreamRegistry:
    ctx: ContractContext
    ids: list[str] = field(init=False)
    index: dict[str, int] = field(init=False)
    state: dict[str, StreamState] = field(init=False)

    def __post_init__(self) -> None:
        self.ids = [s.id for s in self.ctx.catalogue.streams]
        self.index = {sid: i for i, sid in enumerate(self.ids)}
        self.reset()

    def reset(self) -> None:
        """Forget continuity and counters (new episode)."""
        self.state = {sid: StreamState() for sid in self.ids}

    @property
    def house_id(self) -> str:
        return self.ctx.house_id

    def definition(self, stream: str) -> StreamDef | None:
        return self.ctx.streams.get(stream)

    # ------------------------------------------------------------------ batches

    def check_batch(self, batch: ReadingBatch) -> tuple[list[str], PreparedBatch | None]:
        """Validate a parsed batch. Returns (issue codes, prepared batch if accepted)."""
        codes: list[str] = []
        if batch.house_id != self.house_id:
            codes.append("batch.house")
        definition = self.ctx.streams.get(batch.stream)
        if definition is None:
            return [*codes, "batch.unknown_stream"], None
        if definition.kind is not StreamKind.sampled:
            return [*codes, "batch.not_sampled"], None
        if batch.unit != definition.unit.value:
            codes.append("batch.unit")
        if batch.period_s != definition.nominal_period_s:
            codes.append("batch.period")
        n = len(batch.values)
        if isinstance(batch.quality, list):
            if len(batch.quality) != n:
                return [*codes, "batch.quality_length"], None
            quality = np.fromiter((QUALITY_INDEX[q] for q in batch.quality), np.uint8, n)
        else:
            quality = np.full(n, QUALITY_INDEX[batch.quality], np.uint8)
        values = np.array([np.nan if v is None else v for v in batch.values], dtype=np.float64)
        is_null = np.isnan(values)
        consistent = is_null == (quality == MISSING)
        if not consistent.all():
            codes.append("batch.null_quality")
        lo, hi = definition.value_min, definition.value_max
        if lo is not None and hi is not None:
            # Like the contract validator: range is judged only where value and flag agree.
            checked = consistent & ~is_null & (quality != OUT_OF_RANGE)
            if np.any(checked & ((values < lo) | (values > hi))):
                codes.append("batch.range")
        t0_us = to_us(batch.t0)
        state = self.state[batch.stream]
        if state.last_seq is not None and batch.seq <= state.last_seq:
            codes.append("batch.seq_regression")
        if state.next_t_us is not None and t0_us < state.next_t_us:
            codes.append("batch.time_overlap")
        if codes:
            return codes, None
        return [], PreparedBatch(
            stream=batch.stream,
            stream_index=self.index[batch.stream],
            seq=batch.seq,
            t0_us=t0_us,
            period_us=seconds_to_us(batch.period_s),
            values=values,
            quality=quality,
        )

    def accept_batch(self, prepared: PreparedBatch) -> None:
        state = self.state[prepared.stream]
        seq_gap = state.last_seq is not None and prepared.seq != state.last_seq + 1
        time_gap = state.next_t_us is not None and prepared.t0_us != state.next_t_us
        if seq_gap or time_gap:
            state.gaps += 1
        state.last_seq = prepared.seq
        state.next_t_us = prepared.end_us
        state.last_at_us = prepared.last_t_us
        state.samples += prepared.n

    # ------------------------------------------------------------------ events

    def check_event(self, event: Event) -> tuple[list[str], PreparedEvent | None]:
        codes: list[str] = []
        if event.house_id != self.house_id:
            codes.append("event.house")
        definition = self.ctx.streams.get(event.stream)
        if definition is None:
            return [*codes, "event.unknown_stream"], None
        if definition.kind is not StreamKind.event or definition.event_payload is None:
            return [*codes, "event.not_event"], None
        if event.payload.type != definition.event_payload.value:
            codes.append("event.payload_type")
        payload = event.payload
        if (
            isinstance(payload, ProtectionDeviceState)
            and payload.device_id not in self.ctx.protective_device_ids
        ):
            codes.append("event.unknown_device")
        ts_us = to_us(event.ts)
        state = self.state[event.stream]
        if state.last_seq is not None and event.seq <= state.last_seq:
            codes.append("event.seq_regression")
        if state.last_at_us is not None and ts_us < state.last_at_us:
            codes.append("event.time_regression")
        if codes:
            return codes, None
        return [], PreparedEvent(event.stream, self.index[event.stream], event.seq, ts_us, event)

    def accept_event(self, prepared: PreparedEvent) -> None:
        state = self.state[prepared.stream]
        if state.last_seq is not None and prepared.seq != state.last_seq + 1:
            state.gaps += 1
        state.last_seq = prepared.seq
        state.last_at_us = prepared.ts_us
        state.samples += 1

    # ------------------------------------------------------------------ status

    def producer_position_us(self) -> int | None:
        """Oldest 'next expected time' across active sampled streams: how far data has arrived."""
        positions = [
            s.next_t_us
            for sid, s in self.state.items()
            if s.next_t_us is not None and self.ctx.streams[sid].kind is StreamKind.sampled
        ]
        return min(positions) if positions else None

    def status(self, zone_name: str) -> list[StreamStatus]:
        zone = house_zone(zone_name)
        out = []
        for sid in self.ids:
            s = self.state[sid]
            out.append(
                StreamStatus(
                    stream=sid,
                    kind=StreamStatusKind(self.ctx.streams[sid].kind.value),
                    last_at=None if s.last_at_us is None else from_us(s.last_at_us, zone),
                    samples=s.samples,
                    gaps=s.gaps,
                    last_seq=s.last_seq,
                )
            )
        return out
