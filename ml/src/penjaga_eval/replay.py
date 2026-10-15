"""Replay an episode's observed readings through detectors, exactly as the live service would.

Replay opens only ``<episode>/observed/`` (the leakage canary runs it with ``truth/`` and the
manifest deleted). It loads the streams the detectors declare, cuts house time into chunks
(t0, t1] and feeds every detector the same chunks in order.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from penjaga_contract.core import Finding
from penjaga_detect.chunk import QUALITY_CODES, Chunk, EventRecord, Series
from penjaga_detect.detector import Detector

US = 1_000_000


@dataclass(frozen=True)
class Observed:
    house_id: str
    t0_us: int
    t1_us: int
    series: dict[str, Series]
    events: list[EventRecord]


def _us(text: str) -> int:
    moment = datetime.fromisoformat(text)
    return int(moment.replace(microsecond=0).timestamp()) * US + moment.microsecond


def load_observed(
    episode: Path, streams: set[str] | None, event_streams: set[str] | None = None
) -> Observed:
    observed = episode / "observed"
    meta = json.loads((observed / "meta.json").read_text(encoding="utf-8"))
    wanted = sorted(set(meta["streams"]) if streams is None else streams & set(meta["streams"]))
    table = pq.read_table(observed / "readings.parquet", filters=[("stream", "in", wanted)])

    def codes(column: str, names: list[str]) -> np.ndarray:
        found = pc.index_in(table.column(column).cast(pa.string()), value_set=pa.array(names))
        return np.asarray(found.to_numpy(zero_copy_only=False), dtype=np.int64)

    stream_code = codes("stream", wanted)
    quality = codes("quality", list(QUALITY_CODES)).astype(np.uint8)
    ts = np.asarray(table.column("ts_us").to_numpy(), dtype=np.int64)
    values = np.asarray(table.column("value").to_numpy(zero_copy_only=False), dtype=np.float64)
    order = np.lexsort((ts, stream_code))  # by stream, then time
    bounds = np.searchsorted(stream_code[order], np.arange(len(wanted) + 1))
    series: dict[str, Series] = {}
    for i, stream in enumerate(wanted):
        sel = order[bounds[i] : bounds[i + 1]]
        series[stream] = Series(ts[sel], values[sel], quality[sel])
    events = []
    with (observed / "events.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            doc = json.loads(line)
            if event_streams is None or doc["stream"] in event_streams:
                events.append(EventRecord(_us(doc["ts"]), doc["stream"], doc["payload"]))
    window = meta["window"]
    return Observed(meta["house_id"], _us(window["from"]), _us(window["to"]), series, events)


def replay(
    episode: Path, detectors: Sequence[Detector], chunk_h: float = 24.0
) -> dict[str, list[Finding]]:
    """All finding revisions each detector produced over the episode, in order."""
    streams: set[str] | None = set()
    event_streams: set[str] = set()
    for d in detectors:
        if d.info.streams is None:
            streams = None
        elif streams is not None:
            streams |= d.info.streams
        event_streams |= d.info.event_streams
    obs = load_observed(episode, streams, event_streams)
    out: dict[str, list[Finding]] = {d.info.name: [] for d in detectors}
    step = round(chunk_h * 3600 * US)
    t = obs.t0_us
    while t < obs.t1_us:
        t1 = min(obs.t1_us, t + step)
        chunk = Chunk(
            obs.house_id,
            t,
            t1,
            {s: v.between(t, t1) for s, v in obs.series.items()},
            tuple(e for e in obs.events if t < e.ts_us <= t1),
        )
        for d in detectors:
            out[d.info.name] += d.update(chunk)
        t = t1
    return out
