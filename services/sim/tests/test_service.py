"""Live publishing against an in-memory M4: windows, faults at 'now', retries, resets, truth."""

from __future__ import annotations

import asyncio
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest

from penjaga_contract import schema_registry
from penjaga_contract.lab import FaultSpec
from penjaga_sim.service import NoWorldError, SimConfig, SimService, WorldReset
from penjaga_sim.stream_client import StreamUnavailableError
from penjaga_sim.testing import START, FakeStream, at, fault, new_world
from penjaga_sim.world import WorldConflictError, WorldError

pytestmark = pytest.mark.anyio


def make(tmp_path: Path, **config: Any) -> tuple[SimService, FakeStream]:
    stream = FakeStream(at(START, 8.0))
    service = SimService(SimConfig(data_dir=tmp_path, seed=7, **config), stream)
    return service, stream


def values_by_stream(messages: list[dict[str, Any]]) -> dict[str, list[Any]]:
    out: dict[str, list[Any]] = defaultdict(list)
    for m in messages:
        if m["type"] == "batch":
            out[m["data"]["stream"]] += m["data"]["values"]
    return out


async def test_first_step_joins_the_episode_then_publishes_new_time(tmp_path: Path) -> None:
    service, stream = make(tmp_path)
    with pytest.raises(NoWorldError):
        service.require_world()
    assert await service.step(stream) == 0  # adopts episode 1 at 08:00, nothing to send yet
    assert service.episode == 1
    stream.advance(600)
    sent = await service.step(stream)
    assert sent == len(stream.sent) > 0
    flow = values_by_stream(stream.sent)["water.main.flow_lpm"]
    assert len(flow) == 60  # (08:00, 08:10] at 10 s
    seqs = [m["data"]["seq"] for m in stream.sent if m["data"]["stream"] == "water.main.flow_lpm"]
    assert seqs == [0]
    assert service.status()["published_until"] == at(START, 8 + 10 / 60).isoformat()


async def test_published_readings_equal_the_offline_world(tmp_path: Path) -> None:
    """Live publishing in steps gives the same readings as generating the world directly."""
    service, stream = make(tmp_path, max_chunk_s=1800)
    await service.step(stream)
    stream.advance(2 * 3600)
    for _ in range(4):
        await service.step(stream)
    live = values_by_stream(stream.sent)
    world = new_world()
    out = world.output(START)
    for sid, values in live.items():
        sd = out.streams[sid]
        start = (8 * 3600) // (sd.period_us // 1_000_000)
        window = sd.values[start : start + len(values)]
        expected = [None if math.isnan(v) else float(v) for v in window]
        assert values == expected, sid
    assert len(live["water.main.flow_lpm"]) == 720  # two hours, all published within 4 steps


async def test_fault_injected_now_starts_after_published_time(tmp_path: Path) -> None:
    service, stream = make(tmp_path)
    await service.step(stream)
    stream.advance(60)
    await service.step(stream)
    spec = fault("U3", None, 1, at(START), duration_s=600).model_copy(update={"start_at": None})
    f = await service.inject(spec)
    assert f.start_us == service.require_world().published_until_us + 1_000_000
    assert [r for r in service.truth_records() if r["kind"] == "fault"] == []  # not yet published
    assert service.faults()[0]["state"] == "scheduled"
    stream.advance(1200)
    await service.step(stream)
    events = [m["data"] for m in stream.sent if m["type"] == "event"]
    assert [e["payload"]["state"] for e in events] == ["lost", "restored"]
    assert service.faults()[0]["state"] == "ended"
    (record,) = [r for r in service.truth_records() if r["kind"] == "fault"]
    assert record["episode_id"] == "ep_live_0001"
    lines = service.truth.path(1).read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in lines] == service.truth_records()


async def test_changes_to_published_time_are_refused(tmp_path: Path, location: Any) -> None:
    service, stream = make(tmp_path)
    await service.step(stream)
    stream.advance(3600)
    await service.step(stream)
    with pytest.raises(WorldConflictError):
        await service.inject(fault("W1", location("W1"), 0.05, at(START, 8.5)))
    f = await service.inject(fault("W1", location("W1"), 0.05, at(START, 10.0)))
    await service.clear(f.fault_id)  # cleared before it started: cancelled
    with pytest.raises(WorldConflictError, match="cancelled"):
        await service.clear(f.fault_id)
    stream.advance(3 * 3600)
    for _ in range(3):
        await service.step(stream)
    assert [r for r in service.truth_records() if r["kind"] == "fault"] == []


async def test_failed_send_is_retried_with_identical_messages(tmp_path: Path) -> None:
    service, stream = make(tmp_path)
    await service.step(stream)
    stream.advance(300)
    stream.fail_sends = 1
    with pytest.raises(StreamUnavailableError):
        await service.step(stream)
    assert stream.sent == []
    await service.step(stream)
    first = list(stream.sent)
    reference, ref_stream = make(tmp_path / "ref")
    await reference.step(ref_stream)
    ref_stream.advance(300)
    await reference.step(ref_stream)
    assert first == ref_stream.sent  # same windows, same seqs: no gap, no duplicate


async def test_reset_starts_a_new_episode_and_world(tmp_path: Path) -> None:
    service, stream = make(tmp_path)
    await service.step(stream)
    clock = await service.reset(WorldReset(start=at(START, 0.0), speed=600, seed=11))
    assert clock.episode == 2
    assert stream.resets[0].speed == 600
    assert service.status()["seed"] == 11
    assert service.require_world().faults == []
    stream.advance(60)
    await service.step(stream)
    assert stream.sent[0]["data"]["seq"] == 0
    assert service.truth.path(2).exists()


async def test_bad_reset_changes_nothing(tmp_path: Path) -> None:
    service, stream = make(tmp_path)
    await service.step(stream)
    with pytest.raises(WorldError, match="unknown archetype"):
        await service.reset(WorldReset(start=at(START), archetype="robots", seed=99))
    assert stream.resets == []
    assert service.status()["seed"] == 7
    assert service.status()["archetype"] == "working_family"


async def test_reset_by_another_operator_is_followed(tmp_path: Path) -> None:
    service, stream = make(tmp_path)
    await service.step(stream)
    stream.episode = 5
    stream.now = at(START, 20.0)
    assert await service.step(stream) == 0
    assert service.episode == 5
    stream.advance(30)
    await service.step(stream)
    assert stream.sent[0]["data"]["t0"].startswith("2026-10-01T20:00")


async def test_surprise_faults_are_reproducible(tmp_path: Path) -> None:
    specs = []
    for name in ("a", "b"):
        service, stream = make(tmp_path / name)
        await service.step(stream)
        f = await service.inject_random("W2", None)
        specs.append(f.spec.model_dump(exclude={"fault_id"}))
    assert specs[0] == specs[1]
    FaultSpec.model_validate(specs[0])


async def test_run_loop_survives_failures(tmp_path: Path) -> None:
    service, stream = make(tmp_path, poll_interval_s=0.01, retry_s=0.01)
    stream.fail_sends = 2
    stop = asyncio.Event()
    task = asyncio.create_task(service.run(stop))
    for _ in range(200):
        stream.advance(10)
        await asyncio.sleep(0.01)
        if stream.sent:
            break
    stop.set()
    await task
    assert stream.sent
    for m in stream.sent:
        name = "reading_batch" if m["type"] == "batch" else "event"
        schema_registry.validate("core", name, m["data"])
