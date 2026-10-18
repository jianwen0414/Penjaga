"""Follows detection's findings into the Core API's store.

On every (re)connect it:
1. reads the clock and notes the current episode (a reset starts a new one);
2. backfills the latest revision of every current finding (``GET /detect/v1/findings``), which also
   covers a detection store that was wiped and restarted its cursor;
3. resumes the SSE stream after the last cursor it stored, so no revision is lost across restarts.

Writes are idempotent, so overlap between the backfill and the stream is harmless.

Detection's stream only carries the current run's revisions and doesn't say which run. The
follower tags them with the episode from the clock, refreshed at most once per
``clock_refresh_s``. A revision that began before the tagged episode started belongs to an earlier
run (a reset raced the event), so it is dropped and counted rather than misfiled.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime
from typing import Any

from penjaga_api.store import ApiStore
from penjaga_api.upstream import Upstream, UpstreamUnavailableError

log = logging.getLogger("penjaga_api.follower")

CURSOR_KEY = "detect_cursor"


class FindingFollower:
    def __init__(
        self,
        store: ApiStore,
        upstream: Upstream,
        *,
        on_change: Callable[[], None] = lambda: None,
        clock_refresh_s: float = 1.0,
        backoff_s: tuple[float, float] = (0.5, 10.0),
    ) -> None:
        self.store = store
        self.upstream = upstream
        self.on_change = on_change
        self.clock_refresh_s = clock_refresh_s
        self.backoff_s = backoff_s
        self.connected = False
        self.dropped = 0
        self.last_error: str | None = None
        self._clock_at = float("-inf")
        self._episode: tuple[str, str] | None = store.current_episode()

    @property
    def cursor(self) -> int:
        value = self.store.get_value(CURSOR_KEY)
        return int(value) if value is not None else 0

    @property
    def episode(self) -> tuple[str, str] | None:
        """(episode_id, episode_start) the follower is filing findings under."""
        return self._episode

    async def run(self, stop: asyncio.Event) -> None:
        delay = self.backoff_s[0]
        while not stop.is_set():
            try:
                await _until_stopped(self.sync(), stop)
                delay = self.backoff_s[0]
            except UpstreamUnavailableError as exc:
                self.last_error = str(exc)
                log.warning("detection unavailable: %s", exc)
            finally:
                self.connected = False
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), delay)
            delay = min(self.backoff_s[1], delay * 2)

    async def sync(self) -> None:
        """One connection: backfill, then follow the stream until it ends or fails."""
        await self._refresh_clock(force=True)
        self._file(await self.upstream.findings())
        self.connected = True
        self.last_error = None
        async for cursor, doc in self.upstream.finding_events(self.cursor):
            await self._refresh_clock()
            self._file([doc])
            self.store.set_value(CURSOR_KEY, str(cursor))

    async def _refresh_clock(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._clock_at < self.clock_refresh_s:
            return
        clock = await self.upstream.clock()
        self._clock_at = now
        episode = (str(clock["episode_id"]), str(clock["episode_start"]))
        if self.store.note_episode(*episode):
            self.store.publish(
                "episode",
                str(clock["house_time"]),
                {"episode_id": episode[0], "episode_start": episode[1]},
            )
            self.on_change()
        self._episode = episode

    def _file(self, docs: Iterable[dict[str, Any]]) -> None:
        if self._episode is None:
            return
        episode_id, episode_start = self._episode
        start = datetime.fromisoformat(episode_start)
        current: list[dict[str, Any]] = []
        for doc in docs:
            if datetime.fromisoformat(doc["first_seen"]) < start:
                self.dropped += 1
                continue
            current.append(doc)
        added = self.store.add_findings(episode_id, current)
        for doc in added:
            self.store.publish(
                "finding",
                str(doc["updated_at"]),
                {
                    "id": doc["id"],
                    "revision": doc["revision"],
                    "layer": doc["layer"],
                    "type": doc["type"],
                    "severity": doc["severity"],
                    "status": doc["status"],
                },
            )
        if added:
            self.on_change()


async def _until_stopped(work: Awaitable[None], stop: asyncio.Event) -> None:
    """Run ``work`` until it finishes or ``stop`` is set (then cancel it)."""
    task = asyncio.ensure_future(work)
    stopper = asyncio.ensure_future(stop.wait())
    try:
        await asyncio.wait({task, stopper}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for pending in (task, stopper):
            if not pending.done():
                pending.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pending
    if task.done() and not task.cancelled():
        task.result()
