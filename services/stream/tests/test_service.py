from __future__ import annotations

import json
from typing import Any

import pytest

from penjaga_contract.core import ClockCommand, ClockMode, ResetCommand
from penjaga_stream.clock import ClockError
from penjaga_stream.hub import LiveSubscriber
from penjaga_stream.service import QueryError, StreamService
from penjaga_stream.testing import FakeMonotonic, at, batch_doc, trip_event_doc

pytestmark = pytest.mark.anyio

CURRENT = "elec.circuit.circ_sockets_kitchen.current_a"


def batch_message(doc: dict[str, Any]) -> str:
    return json.dumps({"type": "batch", "data": doc})


def event_message(doc: dict[str, Any]) -> str:
    return json.dumps({"type": "event", "data": doc})


async def test_ingest_fans_out_and_stores(make_service):
    service: StreamService = make_service()
    sub = service.hub.add_lossless()
    live = LiveSubscriber(streams=None, max_hz=2, zone=service.zone)
    service.hub.add_live(live)
    text = batch_message(batch_doc(CURRENT, at(0), [1.0, 2.0, 3.0]))
    result = await service.ingest(text)
    assert result.accepted
    assert sub.queue.get_nowait() == text  # forwarded verbatim, in order
    frame = json.loads(live.take_frame(service.clock.now_us()))
    agg = frame["data"]["streams"][CURRENT]
    assert (agg["n"], agg["min"], agg["max"], agg["mean"], agg["last"]) == (3, 1.0, 3.0, 2.0, 3.0)
    summary = await service.summary(CURRENT, at(0), at(60), 60)
    assert [(p.n, p.mean) for p in summary.points] == [(3, 2.0)]


async def test_ingest_rejections_are_reported(make_service):
    service: StreamService = make_service()
    bad_json = await service.ingest("{not json")
    assert not bad_json.accepted
    assert bad_json.rejection is not None
    assert json.loads(bad_json.rejection)["data"]["codes"] == ["schema.invalid"]
    leak = batch_doc(CURRENT, at(0), [1.0]) | {"fault_type": "W2"}
    assert json.loads((await service.ingest(batch_message(leak))).rejection or "")["data"][
        "codes"
    ] == ["schema.invalid"]
    ok = await service.ingest(batch_message(batch_doc(CURRENT, at(0), [1.0, 2.0], seq=0)))
    assert ok.accepted
    overlap = await service.ingest(batch_message(batch_doc(CURRENT, at(1), [1.0], seq=1)))
    rejection = json.loads(overlap.rejection or "")["data"]
    assert rejection == {
        "stream": CURRENT,
        "seq": 1,
        "codes": ["batch.time_overlap"],
        "message": "batch refused",
    }
    assert service.rejected == 3


async def test_events_reach_live_subscribers_unaggregated(make_service):
    service: StreamService = make_service()
    live = LiveSubscriber(streams=frozenset({CURRENT}), max_hz=2, zone=service.zone)
    service.hub.add_live(live)
    text = event_message(trip_event_doc("mcb_sockets_kitchen", at(5), seq=0))
    assert (await service.ingest(text)).accepted
    assert live._urgent.get_nowait() == text
    events = await service.events(["elec.protection.trip"], at(0), at(10))
    assert [e.payload.state.value for e in events] == ["tripped"]


async def test_clock_commands_are_atomic(make_service, mono: FakeMonotonic):
    service: StreamService = make_service(speed=600)
    mono.advance(1)
    with pytest.raises(ClockError):
        await service.command_clock(ClockCommand(speed=60, jump_to=at(0)))  # backwards jump
    assert service.clock.speed == 600  # nothing applied
    state = await service.command_clock(ClockCommand(jump_to=at(86_400), speed=60, paused=True))
    assert state.house_time == at(86_400)
    assert state.speed == 60
    assert state.paused


async def test_real_clock_refuses_commands(make_service):
    service: StreamService = make_service(mode=ClockMode.real)
    with pytest.raises(ClockError):
        await service.command_clock(ClockCommand(speed=10))


async def test_reset_starts_a_clean_episode(make_service, config):
    service: StreamService = make_service()
    sub = service.hub.add_lossless()
    await service.ingest(batch_message(batch_doc(CURRENT, at(0), [1.0], seq=0)))
    first_store = service.store.path
    state = await service.reset(ResetCommand(start=at(-3600), mode=ClockMode.accelerated, speed=60))
    assert state.episode == 1
    assert state.house_time == at(-3600)
    assert service.store.path != first_store
    assert service.store.path.parent.name == "episode-0001"
    assert service.registry.state[CURRENT].last_seq is None
    sub.queue.get_nowait()  # the batch
    messages = [json.loads(sub.queue.get_nowait())["type"] for _ in range(2)]
    assert messages == ["reset", "clock"]
    # same seq/time as before the reset is accepted again: continuity restarted
    assert (await service.ingest(batch_message(batch_doc(CURRENT, at(0), [1.0], seq=0)))).accepted
    summary = await service.summary(CURRENT, at(0), at(60), 60)
    assert [p.n for p in summary.points] == [1]  # the old episode's data is not mixed in


async def test_a_restarted_service_starts_its_episode_empty(make_service):
    # A restart numbers episodes from 0 again, but it is a new episode (a new id, a new run of the
    # house): the file an earlier run left at episode-0000 must not be mixed into its readings,
    # or every summary averages two runs over the same house times.
    first: StreamService = make_service()
    await first.ingest(batch_message(batch_doc(CURRENT, at(0), [16.0, 16.0], seq=0)))
    await first.close()
    second: StreamService = make_service()
    assert second.store.path == first.store.path
    assert second.clock.episode_id != first.clock.episode_id
    await second.ingest(batch_message(batch_doc(CURRENT, at(0), [1.0, 1.0], seq=0)))
    summary = await second.summary(CURRENT, at(0), at(60), 60)
    assert [(p.n, p.mean) for p in summary.points] == [(2, 1.0)]


async def test_governor_slows_clock_when_detection_lags(make_service):
    service: StreamService = make_service(speed=600)
    sub = service.hub.add_lossless()
    for i in range(int(sub.maxsize * 0.6)):
        sub.queue.put_nowait(f"m{i}")
    await service.governor_step()
    assert service.clock.effective_speed() == 300
    while not sub.queue.empty():
        sub.queue.get_nowait()
    for _ in range(10):
        await service.governor_step()
    assert service.clock.governor_limit is None
    assert service.clock.effective_speed() == 600


async def test_governor_slows_clock_when_producer_lags(make_service, mono: FakeMonotonic):
    service: StreamService = make_service(speed=600)
    await service.ingest(batch_message(batch_doc(CURRENT, at(0), [1.0] * 10, seq=0)))
    mono.advance(3)  # house time 1800 s ahead of the data: 3 wall-seconds of lag at 600x
    await service.governor_step()
    assert service.clock.governor_limit == 300


async def test_query_limits(make_service):
    service: StreamService = make_service()
    with pytest.raises(QueryError, match="bucket_s >= 173"):
        await service.summary(CURRENT, at(0), at(86_400), 60)  # 1,440 buckets
    with pytest.raises(QueryError) as unknown:
        await service.summary("water.bath9.flow_lpm", at(0), at(60), 60)
    assert unknown.value.not_found
    with pytest.raises(QueryError, match="event stream"):
        await service.summary("elec.protection.trip", at(0), at(60), 60)
    with pytest.raises(QueryError, match="after"):
        await service.summary(CURRENT, at(60), at(0), 60)
    with pytest.raises(QueryError, match="not an event stream"):
        await service.events([CURRENT], at(0), at(60))


async def test_raw_limit(make_service, config):
    service: StreamService = make_service()
    small = type(config)(
        data_dir=config.data_dir / "small", tokens=config.tokens, max_raw_samples=5
    )
    service.config = small
    await service.ingest(batch_message(batch_doc(CURRENT, at(0), [1.0] * 10, seq=0)))
    with pytest.raises(QueryError, match="exceed 5"):
        await service.raw(CURRENT, at(0), at(10))
    raw = await service.raw(CURRENT, at(0), at(5))
    assert [s.value for s in raw.samples] == [1.0] * 5
