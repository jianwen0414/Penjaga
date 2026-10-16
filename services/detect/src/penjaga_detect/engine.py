"""The live detection engine: M4's lossless messages in, chunks through the detectors, findings out.

Pure and synchronous, so it is tested without sockets; ``service.py`` drives it.

**Ordering and completeness.**
- A stream's samples are complete up to just before its next expected sample: the last
  timestamp received + period - 1 us. Samples are stamped at the end of their period.
- Events have no period. Ingest protocol rule (``docs/design/interfaces.md``): a producer sends
  each publishing step's events before its batches, and M4 relays and stores messages in
  arrival order. So any sample at time t proves that every event up to t has arrived, whether
  it came live or from M4's store (backfill fetches events after samples).
- The engine processes house time up to the smallest of these bounds. It never runs a chunk
  whose readings are still in flight.

**Stalls, duplicates and late data.**
- A stream not yet seen counts as complete only up to the time the engine started waiting
  for it. Absent sensors therefore hold processing back until they stall.
- A stream more than ``stall_s`` (or two of its periods) behind the furthest stream is
  "stalled" and no longer holds processing back. Its later samples for time already processed
  are counted as late and dropped.
- Samples and events seen before (by timestamp, or by ``seq`` for events) are duplicates. They
  come from overlaps between a backfill and the live subscription, and are dropped silently.

**Detector failures.** A detector that raises is switched off for the rest of the episode and
reported. The others keep running.

**Layer consent.** A disabled layer's detectors are dropped. Re-enabling builds them afresh
from the current house time, so their baselines commission again.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt

from penjaga_contract.core import Finding
from penjaga_detect.chunk import US, Chunk, EventRecord, Series, batch_samples, event_record
from penjaga_detect.detector import Detector

log = logging.getLogger(__name__)

I64 = npt.NDArray[np.int64]
F64 = npt.NDArray[np.float64]
U8 = npt.NDArray[np.uint8]


@dataclass(frozen=True)
class DetectorSpec:
    """A deployed detector: how to build a fresh instance, and its layer."""

    name: str
    layer: str
    build: Callable[[], Detector]


@dataclass
class _Buffer:
    period_us: int
    waiting_since_us: int  # when the engine started waiting for this stream
    last_ts: int | None = None
    parts: list[tuple[I64, F64, U8]] = field(default_factory=list)

    @property
    def covered_until(self) -> int:
        if self.last_ts is None:
            return self.waiting_since_us
        return self.last_ts + self.period_us - 1

    def take(self, t0: int, t1: int) -> Series:
        """Samples in (t0, t1]; everything at or before t1 leaves the buffer."""
        if not self.parts:
            return Series.empty()
        ts = np.concatenate([p[0] for p in self.parts])
        values = np.concatenate([p[1] for p in self.parts])
        quality = np.concatenate([p[2] for p in self.parts])
        split = int(np.searchsorted(ts, t1, side="right"))
        self.parts = [(ts[split:], values[split:], quality[split:])] if split < ts.size else []
        lo = int(np.searchsorted(ts[:split], t0, side="right"))
        return Series(ts[lo:split], values[lo:split], quality[lo:split])


@dataclass(frozen=True)
class EngineStats:
    processed_until_us: int
    late_samples: int
    late_events: int
    duplicate_samples: int
    stalled: tuple[str, ...]
    failed: Mapping[str, str]


class Engine:
    """One episode of live detection over a fixed set of deployed detectors."""

    def __init__(
        self,
        house_id: str,
        specs: Sequence[DetectorSpec],
        periods_s: Mapping[str, float],
        *,
        start_us: int,
        enabled_layers: Iterable[str] | None = None,
        max_chunk_s: float = 3600.0,
        stall_s: float = 7200.0,
    ) -> None:
        names = [s.name for s in specs]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate detectors in {names}")
        self.house_id = house_id
        self.specs = {s.name: s for s in specs}
        self._periods_us = {k: round(v * US) for k, v in periods_s.items()}
        self._max_chunk_us = round(max_chunk_s * US)
        self._stall_us = round(stall_s * US)
        layers = {s.layer for s in specs}
        self.enabled_layers = set(layers if enabled_layers is None else enabled_layers) & layers
        self.processed_until_us = start_us
        self._detectors: dict[str, Detector] = {}
        self.failed: dict[str, str] = {}
        self._buffers: dict[str, _Buffer] = {}
        self._events: list[EventRecord] = []
        self._last_event_seq: dict[str, int] = {}
        self._event_bound_us: int | None = None  # every event up to here has arrived
        self.late_samples = self.late_events = self.duplicate_samples = 0
        for spec in specs:
            if spec.layer in self.enabled_layers:
                self._detectors[spec.name] = spec.build()
        self._refresh_wanted()

    # ------------------------------------------------------------------ configuration

    @property
    def running(self) -> list[str]:
        return list(self._detectors)

    @property
    def wanted_streams(self) -> list[str]:
        """Sampled streams the running detectors read."""
        return sorted(self._buffers)

    @property
    def wanted_event_streams(self) -> list[str]:
        return sorted(self._event_streams)

    def _refresh_wanted(self) -> None:
        streams: set[str] = set()
        everything = False
        events: set[str] = set()
        for d in self._detectors.values():
            if d.info.streams is None:
                everything = True
            else:
                streams |= d.info.streams
            events |= d.info.event_streams
        wanted = set(self._periods_us) if everything else streams & set(self._periods_us)
        for sid in wanted:
            if sid not in self._buffers:
                self._buffers[sid] = _Buffer(self._periods_us[sid], self.processed_until_us)
        for sid in set(self._buffers) - wanted:
            del self._buffers[sid]
        self._event_streams = events
        self._events = [e for e in self._events if e.stream in events]

    def set_layer(self, layer: str, enabled: bool) -> None:
        if layer not in {s.layer for s in self.specs.values()}:
            raise KeyError(f"no deployed detector in layer {layer}")
        if enabled and layer not in self.enabled_layers:
            self.enabled_layers.add(layer)
            for spec in self.specs.values():
                if spec.layer == layer and spec.name not in self.failed:
                    self._detectors[spec.name] = spec.build()
        elif not enabled and layer in self.enabled_layers:
            self.enabled_layers.discard(layer)
            for spec in self.specs.values():
                if spec.layer == layer:
                    self._detectors.pop(spec.name, None)
        self._refresh_wanted()

    # ------------------------------------------------------------------ input

    def ingest(self, message: Mapping[str, Any]) -> None:
        """An IngestMessage-shaped document ({type: batch|event, data}) relayed by M4."""
        kind, data = message["type"], message["data"]
        if kind == "batch":
            ts, values, quality = batch_samples(data)
            if ts.size:
                self._certify_events(int(ts[-1]))
            self._add_samples(data["stream"], ts, values, quality)
        elif kind == "event":
            self._add_event(event_record(data), int(data["seq"]))
        else:
            raise ValueError(f"not a reading message: {kind!r}")

    def backfill(
        self,
        series: Mapping[str, tuple[I64, F64, U8]],
        events: Iterable[tuple[EventRecord, int]],
    ) -> None:
        """Readings fetched from M4's store, the events fetched *after* the samples (so they
        include every event the samples certify)."""
        for sid, (ts, values, quality) in series.items():
            if ts.size:
                self._certify_events(int(ts[-1]))
            self._add_samples(sid, ts, values, quality)
        for event, seq in sorted(events, key=lambda e: (e[0].ts_us, e[0].stream, e[1])):
            self._add_event(event, seq)

    def _certify_events(self, ts_us: int) -> None:
        if self._event_bound_us is None or ts_us > self._event_bound_us:
            self._event_bound_us = ts_us

    def _add_samples(self, stream: str, ts: I64, values: F64, quality: U8) -> None:
        buf = self._buffers.get(stream)
        if buf is None or ts.size == 0:
            return
        if buf.last_ts is not None:
            fresh = ts > buf.last_ts
            self.duplicate_samples += int(ts.size - np.count_nonzero(fresh))
            ts, values, quality = ts[fresh], values[fresh], quality[fresh]
            if ts.size == 0:
                return
        buf.last_ts = int(ts[-1])
        on_time = ts > self.processed_until_us
        self.late_samples += int(ts.size - np.count_nonzero(on_time))
        if np.any(on_time):
            buf.parts.append((ts[on_time], values[on_time], quality[on_time]))

    def _add_event(self, event: EventRecord, seq: int) -> None:
        if event.stream not in self._event_streams:
            return
        last = self._last_event_seq.get(event.stream)
        if last is not None and seq <= last:
            return  # seen before (backfill overlap)
        self._last_event_seq[event.stream] = seq
        if event.ts_us <= self.processed_until_us:
            self.late_events += 1
            return
        self._events.append(event)

    # ------------------------------------------------------------------ processing

    def stalled(self) -> list[str]:
        if not self._buffers:
            return []
        front = max(b.covered_until for b in self._buffers.values())
        return sorted(
            s
            for s, b in self._buffers.items()
            if b.covered_until < front - max(self._stall_us, 2 * b.period_us)
        )

    def complete_until(self) -> int:
        """House time up to which every reading the detectors need has arrived."""
        if self._event_bound_us is None:
            return self.processed_until_us
        bound = self._event_bound_us
        stalled = set(self.stalled())
        for sid, buf in self._buffers.items():
            if sid not in stalled:
                bound = min(bound, buf.covered_until)
        return max(bound, self.processed_until_us)

    def advance(self) -> list[tuple[str, Finding]]:
        """Run every complete chunk; the finding revisions produced, in order."""
        out: list[tuple[str, Finding]] = []
        target = self.complete_until()
        while self.processed_until_us < target:
            t0 = self.processed_until_us
            t1 = min(target, t0 + self._max_chunk_us)
            events = tuple(e for e in self._events if t0 < e.ts_us <= t1)
            self._events = [e for e in self._events if e.ts_us > t1]
            chunk = Chunk(
                self.house_id,
                t0,
                t1,
                {sid: buf.take(t0, t1) for sid, buf in self._buffers.items()},
                events,
            )
            for name, detector in list(self._detectors.items()):
                try:
                    out += [(name, f) for f in detector.update(chunk)]
                except Exception as exc:  # isolate: one broken detector must not stop the rest
                    log.exception("detector %s failed; switched off for this episode", name)
                    self.failed[name] = f"{type(exc).__name__}: {exc}"[:200]
                    del self._detectors[name]
            self.processed_until_us = t1
        return out

    def stats(self) -> EngineStats:
        return EngineStats(
            self.processed_until_us,
            self.late_samples,
            self.late_events,
            self.duplicate_samples,
            tuple(self.stalled()),
            dict(self.failed),
        )
