"""Export: IngestMessages for live publishing, and episode directories for M11."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import pytest
from jsonschema import Draft202012Validator

from penjaga_contract import schema_registry
from penjaga_contract.validate import (
    ContractContext,
    validate_event_document,
    validate_reading_batch_document,
)
from penjaga_sim.export import SeqCounters, ingest_messages, run_episode, world_for
from penjaga_sim.testing import START, US, at, fault, manifest, new_world
from penjaga_sim.world import World, WorldError

INGEST = Draft202012Validator(
    {"$ref": "https://penjaga.invalid/contract/core/stream_api.schema.json#/$defs/IngestMessage"},
    registry=schema_registry.registry(),
    format_checker=schema_registry.FORMAT_CHECKER,
)


def us(day_offset: int, hour: float) -> int:
    return int(at(START + timedelta(days=day_offset), hour).timestamp() * US)


@pytest.fixture(scope="module")
def outage_world() -> World:
    """A world with a 30-minute power cut at 09:00 on day 1 (so the window holds events)."""
    world = new_world()
    world.inject(fault("U3", None, 1, at(START, 9.0), duration_s=1800))
    return world


def assert_valid(messages: list[dict[str, Any]], ctx: ContractContext) -> None:
    for m in messages:
        errors = [e.message for e in INGEST.iter_errors(m)]
        assert not errors, errors
        check = validate_reading_batch_document if m["type"] == "batch" else validate_event_document
        report = check(m["data"], ctx)
        assert report.ok, [str(i) for i in report.errors]


# ---------------------------------------------------------------------------- ingest messages


def test_messages_are_valid_contract_documents(outage_world: World, ctx: ContractContext) -> None:
    messages = ingest_messages(outage_world, us(0, 8.75), us(0, 9.75), SeqCounters())
    assert_valid(messages, ctx)
    events = [m["data"] for m in messages if m["type"] == "event"]
    assert [(e["stream"], e["payload"]["state"]) for e in events] == [
        ("elec.supply.state", "lost"),
        ("elec.supply.state", "restored"),
    ]
    batches = [m["data"] for m in messages if m["type"] == "batch"]
    # every stream has a sample in the window (the slowest, hourly weather, at 09:00)
    assert {b["stream"] for b in batches} == set(outage_world.output(START).streams)


def test_windows_are_half_open_and_chunking_invariant(outage_world: World) -> None:
    whole = ingest_messages(outage_world, us(0, 8.75), us(0, 9.75), SeqCounters())
    counters = SeqCounters()
    parts = ingest_messages(outage_world, us(0, 8.75), us(0, 9.25), counters) + ingest_messages(
        outage_world, us(0, 9.25), us(0, 9.75), counters
    )

    def flatten(messages: list[dict[str, Any]]) -> dict[str, list[Any]]:
        out: dict[str, list[Any]] = defaultdict(list)
        for m in messages:
            d = m["data"]
            if m["type"] == "batch":
                q = d["quality"]
                flags = q if isinstance(q, list) else [q] * len(d["values"])
                out[d["stream"]] += list(zip(d["values"], flags, strict=True))
            else:
                out[d["stream"]].append((d["ts"], d["payload"]["state"]))
        return out

    assert flatten(parts) == flatten(whole)
    seqs: dict[str, list[int]] = defaultdict(list)
    for m in parts:
        seqs[m["data"]["stream"]].append(m["data"]["seq"])
    for stream, seq in seqs.items():
        assert seq == list(range(len(seq))), stream  # per-stream, gap-free from 0


def test_midnight_sample_belongs_to_the_day_it_ends(baseline: World) -> None:
    messages = ingest_messages(baseline, us(0, 23.5), us(1, 0.5), SeqCounters())
    flow = [m["data"] for m in messages if m["data"]["stream"] == "water.main.flow_lpm"]
    assert [b["t0"] for b in flow] == [
        at(START, 23.5 + 10 / 3600).isoformat(),
        at(START + timedelta(days=1), 10 / 3600).isoformat(),
    ]
    assert len(flow[0]["values"]) == 180  # 23:30:10 .. 24:00:00
    assert len(flow[1]["values"]) == 180


def test_messages_are_in_time_order(outage_world: World) -> None:
    messages = ingest_messages(outage_world, us(0, 8.75), us(0, 9.75), SeqCounters())
    starts = [m["data"]["t0"] if m["type"] == "batch" else m["data"]["ts"] for m in messages]
    assert starts == sorted(starts)


def test_empty_window_gives_nothing(baseline: World) -> None:
    assert ingest_messages(baseline, us(0, 3), us(0, 3), SeqCounters()) == []


# ---------------------------------------------------------------------------- episodes


def test_episode_directory(tmp_path: Path, location: Any, ctx: ContractContext) -> None:
    spec = fault("W2", location("W2"), 0.3, at(START, 14.0))
    m = manifest(
        start=at(START, 12.0).isoformat(),
        duration_days=0.5,
        faults=[spec.model_dump(mode="json", exclude_none=True)],
    )
    summary = run_episode(m, tmp_path / "ep")
    root = tmp_path / "ep"
    assert sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()) == [
        "manifest.json",
        "observed/events.jsonl",
        "observed/readings.parquet",
        "truth/days.jsonl",
        "truth/truth.jsonl",
    ]
    table = pq.read_table(root / "observed" / "readings.parquet")
    assert table.column_names == ["stream", "ts_us", "value", "quality"]
    bounds = pc.min_max(table.column("ts_us")).as_py()
    assert bounds["min"] > us(0, 12.0)
    assert bounds["max"] == us(1, 0.0)
    counts = pc.value_counts(table.column("stream").cast(pa.string())).to_pylist()
    per_stream = {c["values"]: c["counts"] for c in counts}
    assert set(per_stream) == set(ctx.streams) - {"elec.protection.trip", "elec.supply.state"}
    for sid, n in per_stream.items():
        period = ctx.streams[sid].nominal_period_s
        assert period is not None
        assert n == 43_200 / period, sid
    assert summary["readings"] == table.num_rows
    truth = [json.loads(line) for line in (root / "truth" / "truth.jsonl").read_text().splitlines()]
    assert summary["truth_records"] == len(truth)
    faults = [r for r in truth if r["kind"] == "fault"]
    assert len(faults) == 1
    assert faults[0]["label"] == "W2"
    assert faults[0]["episode_id"] == m.episode_id
    for record in truth:
        schema_registry.validate("lab", "truth_record", record)
    again = json.loads((root / "manifest.json").read_text())
    assert again["episode_id"] == m.episode_id


def test_episode_is_reproducible(tmp_path: Path) -> None:
    m = manifest(duration_days=1)
    run_episode(m, tmp_path / "a")
    run_episode(m, tmp_path / "b")
    a = pq.read_table(tmp_path / "a" / "observed" / "readings.parquet")
    b = pq.read_table(tmp_path / "b" / "observed" / "readings.parquet")
    assert a.equals(b)
    for name in ("truth/truth.jsonl", "truth/days.jsonl", "observed/events.jsonl"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()


def test_episode_refuses_to_overwrite(tmp_path: Path) -> None:
    m = manifest(duration_days=0.25)
    run_episode(m, tmp_path / "ep")
    with pytest.raises(FileExistsError):
        run_episode(m, tmp_path / "ep")
    run_episode(m, tmp_path / "ep", overwrite=True)
    assert not (tmp_path / "ep.partial").exists()


def test_manifest_from_another_generator_or_house_is_refused() -> None:
    with pytest.raises(WorldError, match="would not reproduce"):
        world_for(manifest(generator_version="penjaga-sim/0"))
    with pytest.raises(WorldError, match="manifest house"):
        world_for(manifest(house_id="some_other_house"))


def test_observed_files_carry_no_ground_truth(tmp_path: Path, location: Any) -> None:
    spec = fault("W1", location("W1"), 0.1, at(START, 1.0))
    m = manifest(duration_days=0.25, faults=[spec.model_dump(mode="json", exclude_none=True)])
    run_episode(m, tmp_path / "ep")
    observed = tmp_path / "ep" / "observed"
    events = (observed / "events.jsonl").read_text()
    table = pq.read_table(observed / "readings.parquet")
    strings = set(pc.unique(table.column("stream").cast(pa.string())).to_pylist())
    strings |= set(pc.unique(table.column("quality").cast(pa.string())).to_pylist())
    metadata = repr(pq.read_metadata(observed / "readings.parquet").metadata)
    for leak in ("W1", "flt_", "tr_", location("W1"), "fault"):
        assert leak not in events
        assert leak not in metadata
        assert not any(leak in s for s in strings)


def test_written_documents_validate_with_supply_faults(tmp_path: Path) -> None:
    """Supply faults have a required, null location_id: it must survive serialisation."""
    spec = fault("U3", None, 1, at(START, 2.0), duration_s=600)
    m = manifest(duration_days=0.25, faults=[spec.model_dump(mode="json")])
    run_episode(m, tmp_path / "ep")
    doc = json.loads((tmp_path / "ep" / "manifest.json").read_text())
    schema_registry.validate("lab", "episode_manifest", doc)
    (record,) = [
        json.loads(line)
        for line in (tmp_path / "ep" / "truth" / "truth.jsonl").read_text().splitlines()
        if '"kind": "fault"' in line
    ]
    schema_registry.validate("lab", "fault_spec", record["params"]["spec"])
    assert record["params"]["spec"]["location_id"] is None
