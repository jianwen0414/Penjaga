"""Turn generated days into contract documents and episode files.

``ingest_messages`` gives the IngestMessage documents ({type, data}) for one window of house
time, as the live runner publishes them to M4 (D-016). ``run_episode`` writes one
EpisodeManifest to a directory laid out so that what detection may see and what it must never
see are separate trees:

    manifest.json               the manifest, as given
    observed/readings.parquet   stream, ts_us, value, quality: exactly what the sensors reported
    observed/events.jsonl       Event documents
    truth/truth.jsonl           TruthRecord documents (validated against the lab schema)
    truth/days.jsonl            per-day physical totals (true water use, energy, overflow)

An episode covers house time (start, start + duration_days]: a sample is included when its
timestamp (the end of its period) falls in that window.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import numpy.typing as npt
import pyarrow as pa
import pyarrow.parquet as pq

from penjaga_contract import schema_registry
from penjaga_contract.lab import EpisodeManifest
from penjaga_contract.validate import load_context
from penjaga_sim import GENERATOR_VERSION
from penjaga_sim.faults import spec_document
from penjaga_sim.sensors import QUALITY_CODES, StreamDay
from penjaga_sim.timeutil import to_us
from penjaga_sim.world import DayOutput, SimEvent, World, WorldConfig, WorldError

US = 1_000_000
QUALITY_NAMES = np.array([q.value for q in QUALITY_CODES], dtype=object)
# stream and quality are dictionary-encoded (as Parquet stores them); readers get strings back
READINGS_SCHEMA = pa.schema(
    [
        ("stream", pa.dictionary(pa.int16(), pa.string())),
        ("ts_us", pa.int64()),
        ("value", pa.float64()),
        ("quality", pa.dictionary(pa.int8(), pa.string())),
    ]
)


@dataclass
class SeqCounters:
    """Per-stream sequence numbers for batches and events (each starts at 0)."""

    batches: dict[str, int] = field(default_factory=dict)
    events: dict[str, int] = field(default_factory=dict)

    @staticmethod
    def _next(counter: dict[str, int], stream: str) -> int:
        n = counter.get(stream, -1) + 1
        counter[stream] = n
        return n

    def next_batch(self, stream: str) -> int:
        return self._next(self.batches, stream)

    def next_event(self, stream: str) -> int:
        return self._next(self.events, stream)


def _iso(ts_us: int, zone: ZoneInfo) -> str:
    return datetime.fromtimestamp(ts_us / US, tz=zone).isoformat()


def _house_id(world: World) -> str:
    return world.model.index.house_id


def _timestamps(sd: StreamDay) -> npt.NDArray[np.int64]:
    return sd.t0_us + np.arange(sd.values.size, dtype=np.int64) * sd.period_us


def _days(world: World, t0_us: int, t1_us: int) -> Iterator[DayOutput]:
    """Days holding samples with t0 < ts <= t1 (a day's last sample is stamped at midnight)."""
    day = max(world.day_of(t0_us), world.config.start)
    last = world.day_of(t1_us - 1)
    while day <= last:
        yield world.output(day)
        day += timedelta(days=1)


def event_document(world: World, ev: SimEvent, counters: SeqCounters) -> dict[str, Any]:
    return {
        "ts": _iso(ev.ts_us, world.zone),
        "house_id": _house_id(world),
        "stream": ev.stream,
        "seq": counters.next_event(ev.stream),
        "payload": dict(ev.payload),
    }


def ingest_messages(
    world: World, t0_us: int, t1_us: int, counters: SeqCounters
) -> list[dict[str, Any]]:
    """IngestMessages for samples and events with t0 < ts <= t1.

    One batch per stream per day touched; within a stream, batches and events are in time
    order, and so are the messages overall (ordered by first timestamp).
    """
    if t1_us <= t0_us:
        return []
    units = {s.id: s.unit.value for s in world.streams}
    messages: list[tuple[int, int, dict[str, Any]]] = []
    for out in _days(world, t0_us, t1_us):
        for sid, sd in out.streams.items():
            ts = _timestamps(sd)
            sel = np.flatnonzero((ts > t0_us) & (ts <= t1_us))
            if not sel.size:
                continue
            raw = sd.values[sel]
            values: list[float | None] = raw.tolist()
            for i in np.flatnonzero(np.isnan(raw)):
                values[i] = None
            codes = sd.quality[sel]
            uniform = bool(np.all(codes == codes[0]))
            quality: Any = QUALITY_NAMES[codes[0]] if uniform else QUALITY_NAMES[codes].tolist()
            doc = {
                "house_id": _house_id(world),
                "stream": sid,
                "t0": _iso(int(ts[sel[0]]), world.zone),
                "period_s": sd.period_us / US,
                "unit": units[sid],
                "values": values,
                "quality": quality,
                "seq": counters.next_batch(sid),
            }
            messages.append((int(ts[sel[0]]), 0, {"type": "batch", "data": doc}))
        for ev in out.events:
            if t0_us < ev.ts_us <= t1_us:
                doc = event_document(world, ev, counters)
                messages.append((ev.ts_us, 1, {"type": "event", "data": doc}))
    messages.sort(key=lambda m: (m[0], m[1]))
    return [m for _, _, m in messages]


def _day_table(out: DayOutput, t0_us: int, t1_us: int, stream_ids: list[str]) -> pa.Table:
    """One day's samples in (t0, t1]; ``stream_ids`` is the fixed stream dictionary."""
    code = {sid: i for i, sid in enumerate(stream_ids)}
    streams, ts, values, quality = [], [], [], []
    for sid, sd in out.streams.items():
        t = _timestamps(sd)
        keep = (t > t0_us) & (t <= t1_us)
        n = int(keep.sum())
        if not n:
            continue
        streams.append(np.full(n, code[sid], dtype=np.int16))
        ts.append(t[keep])
        values.append(sd.values[keep])
        quality.append(sd.quality[keep].astype(np.int8))
    if not streams:
        return READINGS_SCHEMA.empty_table()
    v = np.concatenate(values)
    return pa.table(
        {
            "stream": pa.DictionaryArray.from_arrays(
                pa.array(np.concatenate(streams)), pa.array(stream_ids, pa.string())
            ),
            "ts_us": pa.array(np.concatenate(ts), pa.int64()),
            "value": pa.array(v, pa.float64(), mask=np.isnan(v)),
            "quality": pa.DictionaryArray.from_arrays(
                pa.array(np.concatenate(quality)), pa.array(list(QUALITY_NAMES), pa.string())
            ),
        },
        schema=READINGS_SCHEMA,
    )


def world_for(manifest: EpisodeManifest) -> World:
    """A world configured, faulted and anomalied as the manifest says."""
    if manifest.generator_version != GENERATOR_VERSION:
        raise WorldError(
            f"manifest was made by generator {manifest.generator_version!r}; this is "
            f"{GENERATOR_VERSION!r}, which would not reproduce it"
        )
    world = World(
        WorldConfig(
            seed=manifest.seed,
            archetype=manifest.household_archetype,
            start=first_day(manifest),
            enabled_sensors=frozenset(manifest.enabled_sensor_ids),
        )
    )
    if manifest.house_id != _house_id(world):
        raise WorldError(f"manifest house {manifest.house_id!r} != {_house_id(world)!r}")
    t0, _ = episode_window(manifest)
    for fault in manifest.faults:
        # the episode covers (start, end]: an effect at the start instant would not be in it
        if fault.start_at is None or to_us(fault.start_at) <= t0:
            raise WorldError("every manifest fault needs a start_at after the episode start")
        world.inject(fault)
    for anomaly in manifest.anomalies:
        world.impose(anomaly)
    return world


def first_day(manifest: EpisodeManifest) -> date:
    """The house day holding the manifest's start (the world is generated from its midnight)."""
    return manifest.start.astimezone(ZoneInfo(load_context().house.location.timezone)).date()


def manifest_document(manifest: EpisodeManifest) -> dict[str, Any]:
    """The manifest as a contract JSON document (validated against the lab schema)."""
    doc = manifest.model_dump(mode="json", exclude_none=True)
    doc["faults"] = [spec_document(f) for f in manifest.faults]
    schema_registry.validate("lab", "episode_manifest", doc)
    return doc


def episode_window(manifest: EpisodeManifest) -> tuple[int, int]:
    t0 = to_us(manifest.start)
    return t0, t0 + round(manifest.duration_days * 86_400 * US)


def run_episode(
    manifest: EpisodeManifest, out_dir: Path, *, overwrite: bool = False
) -> dict[str, Any]:
    """Generate an episode into ``out_dir`` (written to a sibling then renamed, so a directory
    that exists is always complete). Returns summary statistics."""
    if out_dir.exists():
        if not overwrite:
            raise FileExistsError(f"{out_dir} exists")
        shutil.rmtree(out_dir)
    partial = out_dir.with_name(out_dir.name + ".partial")
    if partial.exists():
        shutil.rmtree(partial)
    (partial / "observed").mkdir(parents=True)
    (partial / "truth").mkdir()
    world = world_for(manifest)
    t0, t1 = episode_window(manifest)
    counters = SeqCounters()
    stream_ids = sorted(s.id for s in world.streams)
    days: list[dict[str, Any]] = []
    n_readings = n_events = 0
    with (
        pq.ParquetWriter(
            partial / "observed" / "readings.parquet", READINGS_SCHEMA, compression="zstd"
        ) as writer,
        (partial / "observed" / "events.jsonl").open("w", encoding="utf-8", newline="\n") as ev_fh,
    ):
        for out in _days(world, t0, t1):
            table = _day_table(out, t0, t1, stream_ids)
            writer.write_table(table)
            n_readings += table.num_rows
            for ev in out.events:
                if t0 < ev.ts_us <= t1:
                    ev_fh.write(json.dumps(event_document(world, ev, counters)) + "\n")
                    n_events += 1
            days.append({"day": out.day.isoformat(), **out.summary})
            world.forget_before(out.day)
    truth = [{**r, "episode_id": manifest.episode_id} for r in world.truth_records(t1)]
    with (partial / "truth" / "truth.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
        for record in truth:
            schema_registry.validate("lab", "truth_record", record)
            fh.write(json.dumps(record) + "\n")
    with (partial / "truth" / "days.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
        for d in days:
            fh.write(json.dumps(d) + "\n")
    (partial / "manifest.json").write_text(
        json.dumps(manifest_document(manifest), indent=2) + "\n", encoding="utf-8"
    )
    partial.rename(out_dir)
    return {
        "episode_id": manifest.episode_id,
        "days": len(days),
        "readings": n_readings,
        "events": n_events,
        "truth_records": len(truth),
    }
