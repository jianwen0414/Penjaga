"""What a detector sees: a window of house time with each stream's samples and the events in it.

A ``Chunk`` covers (t0, t1]. The live service builds small chunks from the M4 lossless
subscription; the evaluation harness builds large ones from episode files. Detectors must give
the same findings however the time is chunked (property-tested), so both paths agree.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np
import numpy.typing as npt

from penjaga_contract.core import Quality

US = 1_000_000
QUALITY_CODES = {q.value: i for i, q in enumerate(Quality)}
OK = QUALITY_CODES[Quality.ok.value]
MISSING = QUALITY_CODES[Quality.missing.value]


@dataclass(frozen=True)
class Series:
    """Samples of one stream in time order; ``values`` is NaN where the sample is missing."""

    ts_us: npt.NDArray[np.int64]
    values: npt.NDArray[np.float64]
    quality: npt.NDArray[np.uint8]

    @staticmethod
    def empty() -> Series:
        return Series(np.empty(0, np.int64), np.empty(0, np.float64), np.empty(0, np.uint8))

    def __len__(self) -> int:
        return int(self.ts_us.size)

    def between(self, t0_us: int, t1_us: int) -> Series:
        """Samples stamped in (t0, t1]."""
        lo = int(np.searchsorted(self.ts_us, t0_us, side="right"))
        hi = int(np.searchsorted(self.ts_us, t1_us, side="right"))
        return Series(self.ts_us[lo:hi], self.values[lo:hi], self.quality[lo:hi])


@dataclass(frozen=True)
class EventRecord:
    ts_us: int
    stream: str
    payload: Mapping[str, str]


@dataclass(frozen=True)
class Chunk:
    house_id: str
    t0_us: int  # exclusive
    t1_us: int  # inclusive
    series: Mapping[str, Series] = field(default_factory=dict)
    events: tuple[EventRecord, ...] = ()

    def get(self, stream: str) -> Series:
        return self.series.get(stream) or Series.empty()


def _us(text: str) -> int:
    moment = datetime.fromisoformat(text)
    return int(moment.replace(microsecond=0).timestamp()) * US + moment.microsecond


def batch_samples(
    data: Mapping[str, Any],
) -> tuple[npt.NDArray[np.int64], npt.NDArray[np.float64], npt.NDArray[np.uint8]]:
    """Timestamps, values (NaN where missing) and quality codes of a ReadingBatch document."""
    n = len(data["values"])
    period = round(float(data["period_s"]) * US)
    ts = _us(data["t0"]) + np.arange(n, dtype=np.int64) * period
    values = np.fromiter(
        (np.nan if v is None else v for v in data["values"]), dtype=np.float64, count=n
    )
    flags = data["quality"]
    if isinstance(flags, list):
        codes = np.fromiter((QUALITY_CODES[f] for f in flags), dtype=np.uint8, count=n)
    else:
        codes = np.full(n, QUALITY_CODES[flags], dtype=np.uint8)
    return ts, values, codes


def event_record(data: Mapping[str, Any]) -> EventRecord:
    """An Event document as the detectors see it."""
    return EventRecord(_us(data["ts"]), data["stream"], dict(data["payload"]))


def chunk_from_messages(
    house_id: str, t0_us: int, t1_us: int, messages: Iterable[Mapping[str, Any]]
) -> Chunk:
    """A chunk from IngestMessage-shaped documents ({type, data}), as M4 relays them."""
    parts: dict[
        str, list[tuple[npt.NDArray[np.int64], npt.NDArray[np.float64], npt.NDArray[np.uint8]]]
    ] = {}
    events: list[EventRecord] = []
    for m in messages:
        data = m["data"]
        if m["type"] == "event":
            event = event_record(data)
            if t0_us < event.ts_us <= t1_us:
                events.append(event)
            continue
        parts.setdefault(data["stream"], []).append(batch_samples(data))
    series: dict[str, Series] = {}
    for stream, pieces in parts.items():
        ts = np.concatenate([p[0] for p in pieces])
        values = np.concatenate([p[1] for p in pieces])
        quality = np.concatenate([p[2] for p in pieces])
        order = np.argsort(ts, kind="stable")
        whole = Series(ts[order], values[order], quality[order])
        series[stream] = whole.between(t0_us, t1_us)
    events.sort(key=lambda e: (e.ts_us, e.stream))
    return Chunk(house_id, t0_us, t1_us, series, tuple(events))
