"""The follower files detection's revisions under the right episode, resumes after its cursor and
never misfiles a revision from an earlier run."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from penjaga_api.follower import FindingFollower
from penjaga_api.store import ApiStore
from penjaga_api.testing import FakeUpstream, load_findings, revision

EPISODE_START = "2026-10-17T00:00:00+08:00"


def _upstream() -> FakeUpstream:
    return FakeUpstream(episode_id="epi_aaaaaaaaaaaaaaaa", episode_start=EPISODE_START)


async def _until(check: Callable[[], bool], timeout_s: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while not check():
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError
        await asyncio.sleep(0.01)


def test_backfill_then_live_revisions_are_stored_and_announced(store: ApiStore) -> None:
    up = _upstream()
    (leak,) = load_findings("l1_leak")
    up.emit(revision(leak, 1, "2026-10-19T04:00:00+08:00"))
    burst = load_findings("l1_burst")
    changes: list[int] = []

    async def scenario() -> None:
        follower = FindingFollower(store, up, on_change=lambda: changes.append(1))
        stop = asyncio.Event()
        task = asyncio.create_task(follower.run(stop))
        await _until(lambda: follower.connected)
        for doc in [leak, *burst]:
            up.emit(doc)
        await _until(lambda: follower.cursor == 4)
        stop.set()
        await task

    asyncio.run(scenario())
    latest = {d["id"]: d for d in store.latest_findings("epi_aaaaaaaaaaaaaaaa")}
    assert latest[leak["id"]]["revision"] == leak["revision"]
    assert {d["type"] for d in latest.values()} >= {"burst_pipe_suspected"}
    kinds = [e.kind for e in store.events_after(0)]
    assert kinds[0] == "episode"
    assert kinds.count("finding") == 4  # leak r1 (backfill + replay, stored once), leak r7, 2 burst
    assert changes


def test_a_reconnect_resumes_after_the_stored_cursor(store: ApiStore) -> None:
    # these findings were captured in a run that started on 2025-11-26
    up = FakeUpstream(episode_id="epi_aaaaaaaaaaaaaaaa", episode_start="2025-11-26T00:00:00+08:00")
    docs = load_findings("l2_elderly")

    async def scenario() -> None:
        follower = FindingFollower(store, up, backoff_s=(0.01, 0.02))
        stop = asyncio.Event()
        task = asyncio.create_task(follower.run(stop))
        await _until(lambda: follower.connected)
        up.emit(docs[0])
        up.emit(docs[1])
        await _until(lambda: follower.cursor == 2)
        up.end_streams()  # detection drops the stream
        await _until(lambda: len(up.event_calls) == 2)
        up.emit(docs[2])
        await _until(lambda: follower.cursor == 3)
        stop.set()
        await task

    asyncio.run(scenario())
    assert up.event_calls == [0, 2]
    assert len(store.latest_findings("epi_aaaaaaaaaaaaaaaa")) == 3


def test_detection_being_down_is_retried_not_fatal(store: ApiStore) -> None:
    up = _upstream()
    up.down = True

    async def scenario() -> FindingFollower:
        follower = FindingFollower(store, up, backoff_s=(0.01, 0.02))
        stop = asyncio.Event()
        task = asyncio.create_task(follower.run(stop))
        await _until(lambda: follower.last_error is not None)
        assert not follower.connected
        up.down = False
        await _until(lambda: follower.connected)
        stop.set()
        await task
        return follower

    follower = asyncio.run(scenario())
    assert follower.last_error is None


def test_a_revision_older_than_the_episode_is_dropped_not_misfiled(store: ApiStore) -> None:
    up = FakeUpstream(episode_id="epi_bbbbbbbbbbbbbbbb", episode_start="2026-10-30T00:00:00+08:00")
    (leak,) = load_findings("l1_leak")  # first seen 2026-10-19: from an earlier run

    async def scenario() -> FindingFollower:
        follower = FindingFollower(store, up)
        stop = asyncio.Event()
        task = asyncio.create_task(follower.run(stop))
        await _until(lambda: follower.connected)
        up.emit(leak)
        await _until(lambda: follower.cursor == 1)
        stop.set()
        await task
        return follower

    follower = asyncio.run(scenario())
    assert follower.dropped == 1
    assert store.latest_findings("epi_bbbbbbbbbbbbbbbb") == []


def test_a_reset_starts_a_new_episode(store: ApiStore) -> None:
    up = _upstream()
    (leak,) = load_findings("l1_leak")
    water_cut = load_findings("l5_water")

    async def scenario() -> None:
        follower = FindingFollower(store, up, clock_refresh_s=0.0)
        stop = asyncio.Event()
        task = asyncio.create_task(follower.run(stop))
        await _until(lambda: follower.connected)
        up.emit(leak)
        await _until(lambda: follower.cursor == 1)
        up.reset("epi_cccccccccccccccc", "2025-11-29T00:00:00+08:00")
        up.emit(water_cut[0])
        await _until(lambda: follower.cursor == 2)
        stop.set()
        await task

    asyncio.run(scenario())
    assert [d["layer"] for d in store.latest_findings("epi_aaaaaaaaaaaaaaaa")] == ["L1"]
    assert [d["layer"] for d in store.latest_findings("epi_cccccccccccccccc")] == ["L5"]
    assert store.current_episode() == ("epi_cccccccccccccccc", "2025-11-29T00:00:00+08:00")
    assert [e.kind for e in store.events_after(0)].count("episode") == 2
