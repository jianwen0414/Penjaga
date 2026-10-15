"""Raw reading and event store for one episode (DuckDB native file; decision D-010, spike S4).

The stream service is the only process that opens the file. Every database call holds one
lock, which serialises access the way DuckDB requires. Appends only queue the batch (lock-free),
and a periodic flush (run off the event loop) writes queued batches as one Arrow insert.
Queries flush first, so they always see everything accepted so far.

Timestamps are stored as UTC ``TIMESTAMP`` and returned as epoch microseconds (DuckDB needs
pytz to return ``TIMESTAMPTZ`` values to Python, and we avoid that dependency).
"""

from __future__ import annotations

import json
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import duckdb
import numpy as np
import pyarrow as pa

from penjaga_stream.registry import PreparedBatch, PreparedEvent

_SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    stream USMALLINT NOT NULL,
    ts TIMESTAMP NOT NULL,
    value DOUBLE,
    quality UTINYINT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    stream USMALLINT NOT NULL,
    ts TIMESTAMP NOT NULL,
    seq UBIGINT NOT NULL,
    payload VARCHAR NOT NULL
);
"""


@dataclass(frozen=True)
class SummaryRow:
    bucket: int
    n: int
    min: float | None
    max: float | None
    mean: float | None
    last: float | None


@dataclass(frozen=True)
class RawRow:
    ts_us: int
    value: float | None
    quality: int


@dataclass(frozen=True)
class EventRow:
    stream_index: int
    ts_us: int
    seq: int
    payload: dict[str, object]


class ReadingStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._con = duckdb.connect(str(path))
        self._con.execute(_SCHEMA)
        # Appends never take the lock: deque.append/popleft are atomic, so ingest on the event
        # loop is never blocked by a flush or query running on another thread.
        self._pending_batches: deque[PreparedBatch] = deque()
        self._pending_events: deque[PreparedEvent] = deque()
        self._closed = False

    # ------------------------------------------------------------------ writing

    def append_batch(self, batch: PreparedBatch) -> None:
        self._check_open()
        self._pending_batches.append(batch)

    def append_event(self, event: PreparedEvent) -> None:
        self._check_open()
        self._pending_events.append(event)

    def pending_rows(self) -> int:
        return sum(b.n for b in list(self._pending_batches))

    def flush(self) -> None:
        with self._lock:
            if not self._closed:
                self._flush_locked()

    def _flush_locked(self) -> None:
        batches: list[PreparedBatch] = []
        while self._pending_batches:
            batches.append(self._pending_batches.popleft())
        if batches:
            values = np.concatenate([b.values for b in batches])
            table = pa.table(
                {
                    "stream": pa.array(
                        np.concatenate([np.full(b.n, b.stream_index, np.uint16) for b in batches]),
                        pa.uint16(),
                    ),
                    "ts": pa.array(
                        np.concatenate([b.timestamps_us() for b in batches]), pa.timestamp("us")
                    ),
                    "value": pa.array(values, pa.float64(), mask=np.isnan(values)),
                    "quality": pa.array(np.concatenate([b.quality for b in batches]), pa.uint8()),
                }
            )
            self._con.from_arrow(table).insert_into("readings")
        events: list[PreparedEvent] = []
        while self._pending_events:
            events.append(self._pending_events.popleft())
        if events:
            table = pa.table(
                {
                    "stream": pa.array([e.stream_index for e in events], pa.uint16()),
                    "ts": pa.array([e.ts_us for e in events], pa.timestamp("us")),
                    "seq": pa.array([e.seq for e in events], pa.uint64()),
                    "payload": pa.array(
                        [e.event.payload.model_dump_json() for e in events], pa.string()
                    ),
                }
            )
            self._con.from_arrow(table).insert_into("events")

    def _check_open(self) -> None:
        if self._closed:
            raise RuntimeError(f"store {self.path} is closed")

    # ------------------------------------------------------------------ reading

    def summary(
        self, stream_index: int, from_us: int, to_us: int, bucket_us: int
    ) -> list[SummaryRow]:
        sql = """
            SELECT (epoch_us(ts) - $from) // $bucket AS b,
                   count(value), min(value), max(value), avg(value),
                   arg_max(value, ts) FILTER (WHERE value IS NOT NULL)
            FROM readings
            WHERE stream = $stream AND ts >= make_timestamp($from) AND ts < make_timestamp($to)
            GROUP BY b ORDER BY b
        """
        params = {"from": from_us, "to": to_us, "bucket": bucket_us, "stream": stream_index}
        with self._lock:
            self._check_open()
            self._flush_locked()
            rows = self._con.execute(sql, params).fetchall()
        return [SummaryRow(int(b), int(n), lo, hi, mean, last) for b, n, lo, hi, mean, last in rows]

    def raw(self, stream_index: int, from_us: int, to_us: int, limit: int) -> list[RawRow]:
        sql = """
            SELECT epoch_us(ts), value, quality FROM readings
            WHERE stream = $stream AND ts >= make_timestamp($from) AND ts < make_timestamp($to)
            ORDER BY ts LIMIT $limit
        """
        params = {"stream": stream_index, "from": from_us, "to": to_us, "limit": limit}
        with self._lock:
            self._check_open()
            self._flush_locked()
            rows = self._con.execute(sql, params).fetchall()
        return [RawRow(int(t), v, int(q)) for t, v, q in rows]

    def count_raw(self, stream_index: int, from_us: int, to_us: int) -> int:
        sql = """
            SELECT count(*) FROM readings
            WHERE stream = $stream AND ts >= make_timestamp($from) AND ts < make_timestamp($to)
        """
        with self._lock:
            self._check_open()
            self._flush_locked()
            row = self._con.execute(
                sql, {"stream": stream_index, "from": from_us, "to": to_us}
            ).fetchone()
        return int(row[0]) if row else 0

    def events(
        self, stream_indices: list[int] | None, from_us: int, to_us: int, limit: int
    ) -> list[EventRow]:
        where = "ts >= make_timestamp($from) AND ts < make_timestamp($to)"
        params: dict[str, object] = {"from": from_us, "to": to_us, "limit": limit}
        if stream_indices is not None:
            where += " AND list_contains($streams, stream)"
            params["streams"] = stream_indices
        sql = f"""
            SELECT stream, epoch_us(ts), seq, payload FROM events
            WHERE {where} ORDER BY ts, stream, seq LIMIT $limit
        """  # only fixed SQL fragments are interpolated; all values are bound parameters
        with self._lock:
            self._check_open()
            self._flush_locked()
            rows = self._con.execute(sql, params).fetchall()
        return [EventRow(int(s), int(t), int(q), json.loads(p)) for s, t, q, p in rows]

    def export_parquet(self, path: Path) -> None:
        """Write all readings of this episode to a zstd Parquet file (training/evaluation)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._check_open()
            self._flush_locked()
            self._con.execute(
                "COPY (SELECT * FROM readings ORDER BY stream, ts) TO $path "
                "(FORMAT parquet, COMPRESSION zstd)",
                {"path": path.as_posix()},
            )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._flush_locked()
            self._con.close()
            self._closed = True
