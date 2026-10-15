"""Live simulation: keep the world in step with M4's house clock and run the Lab's commands.

``SimService.step`` is one publishing step:
1. read M4's clock;
2. if M4 is on a new episode (a reset, by us or by another operator), start a fresh world at
   the clock's house time;
3. otherwise generate the readings and events for (published_until, now] (at most
   ``max_chunk_s`` of house time), send them to M4's ingest socket, then advance
   ``published_until``.

Steps and Lab commands hold one lock, so a fault can never be injected between generating a
window and publishing it. Generation is deterministic and sequence numbers are only committed
after a successful send, so a failed step is retried with exactly the same messages; M4 refuses
any it already has (at-least-once delivery, deduplicated by seq).

Ground truth for published time is written to ``<data_dir>/episode-NNNN/truth.jsonl`` (the
truth store). It never goes to M4.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import anyio

from penjaga_contract import schema_registry
from penjaga_contract.core import ClockMode, ClockState, ResetCommand
from penjaga_contract.lab import AnomalySpec, FaultSpec
from penjaga_contract.validate import load_context
from penjaga_sim.export import SeqCounters, ingest_messages
from penjaga_sim.faults import ActiveFault, random_spec, spec_document
from penjaga_sim.rng import rng
from penjaga_sim.stream_client import Ingest, StreamClient
from penjaga_sim.timeutil import US, to_us
from penjaga_sim.world import World, WorldConfig, WorldError

log = logging.getLogger("penjaga_sim.service")
NEWLINE = "\n"


@dataclass(frozen=True)
class SimConfig:
    data_dir: Path
    seed: int = 0
    archetype: str = "working_family"
    enabled_sensors: frozenset[str] | None = None
    poll_interval_s: float = 0.25  # wall seconds between steps
    max_chunk_s: float = 3600.0  # house seconds published per step at most
    retry_s: float = 2.0  # wall seconds before reconnecting after a failure


@dataclass(frozen=True)
class WorldReset:
    """A Lab request for a new episode (M4 is reset to the same start)."""

    start: datetime
    mode: ClockMode = ClockMode.accelerated
    speed: float = 60.0
    seed: int | None = None
    archetype: str | None = None
    enabled_sensors: frozenset[str] | None = None


class NoWorldError(WorldError):
    """No episode yet: the service has not reached M4's clock."""


@dataclass
class TruthStore:
    """Ground truth per episode, rewritten atomically whenever it changes."""

    root: Path
    _last: tuple[int, str] | None = field(default=None, repr=False)

    def path(self, episode: int) -> Path:
        return self.root / f"episode-{episode:04d}" / "truth.jsonl"

    def write(self, episode: int, records: list[dict[str, Any]]) -> None:
        text = "".join(json.dumps(r, sort_keys=True) + NEWLINE for r in records)
        if self._last == (episode, text):
            return
        for record in records:
            schema_registry.validate("lab", "truth_record", record)
        target = self.path(episode)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8", newline=NEWLINE)
        os.replace(tmp, target)
        self._last = (episode, text)


def house_date(moment: datetime) -> date:
    """The house-local date of an instant (whatever offset the timestamp carries)."""
    return moment.astimezone(ZoneInfo(load_context().house.location.timezone)).date()


def episode_id(episode: int) -> str:
    return f"ep_live_{episode:04d}"


class SimService:
    def __init__(self, config: SimConfig, client: StreamClient) -> None:
        self.config = config
        self.client = client
        self.lock = asyncio.Lock()
        self.truth = TruthStore(config.data_dir)
        self.seed = config.seed
        self.archetype = config.archetype
        self.enabled_sensors = config.enabled_sensors
        self.world: World | None = None
        self.episode: int | None = None
        self.counters = SeqCounters()
        self.sent = 0
        self.random_draws = 0
        self.last_error: str | None = None

    # ------------------------------------------------------------------ episodes

    def _new_world(self, start: date) -> World:
        return World(WorldConfig(self.seed, self.archetype, start, self.enabled_sensors))

    def _adopt(self, clock: ClockState) -> None:
        """A fresh world for M4's current episode, starting at its house time."""
        now = to_us(clock.house_time)
        world = self._new_world(house_date(clock.house_time))
        # The episode covers (now, ...]: like an episode manifest, everything starts after it.
        world.published_until_us = now
        self.world, self.episode, self.counters = world, clock.episode, SeqCounters()
        self.random_draws = 0
        log.info("episode %d: world from %s, seed %d", clock.episode, clock.house_time, self.seed)
        self._write_truth()

    async def reset(self, request: WorldReset) -> ClockState:
        """Reset M4 to a new episode and start a matching world."""
        async with self.lock:
            previous = (self.seed, self.archetype, self.enabled_sensors)
            if request.seed is not None:
                self.seed = request.seed
            if request.archetype is not None:
                self.archetype = request.archetype
            self.enabled_sensors = request.enabled_sensors
            try:
                # check the configuration before touching M4, so a bad request changes nothing
                self._new_world(house_date(request.start))
                clock = await self.client.reset(
                    ResetCommand(start=request.start, mode=request.mode, speed=request.speed)
                )
            except Exception:
                self.seed, self.archetype, self.enabled_sensors = previous
                raise
            self._adopt(clock)
            return clock

    # ------------------------------------------------------------------ publishing

    async def step(self, ingest: Ingest) -> int:
        """One publishing step. Returns the number of messages sent."""
        clock = await self.client.clock()
        async with self.lock:
            if self.world is None or clock.episode != self.episode:
                self._adopt(clock)
                return 0
            world = self.world
            t0 = world.published_until_us
            t1 = min(to_us(clock.house_time), t0 + round(self.config.max_chunk_s * US))
            if t1 <= t0:
                return 0
            counters = SeqCounters(dict(self.counters.batches), dict(self.counters.events))
            messages = await anyio.to_thread.run_sync(ingest_messages, world, t0, t1, counters)
            await ingest.send(messages)
            # committed only after a successful send
            self.counters = counters
            world.published_until_us = t1
            world.forget_before(world.day_of(t1) - timedelta(days=1))
            self.sent += len(messages)
            self._write_truth()
            return len(messages)

    async def run(self, stop: asyncio.Event) -> None:
        """Publish until ``stop`` is set, reconnecting after failures."""
        while not stop.is_set():
            try:
                async with self.client.ingest() as ingest:
                    self.last_error = None
                    while not stop.is_set():
                        await self.step(ingest)
                        await _sleep_or_stop(stop, self.config.poll_interval_s)
            except Exception as exc:  # keep the loop alive; the cause is reported in status()
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.warning("publishing paused: %s", self.last_error)
                await _sleep_or_stop(stop, self.config.retry_s)

    # ------------------------------------------------------------------ Lab commands

    def require_world(self) -> World:
        if self.world is None or self.episode is None:
            raise NoWorldError("no episode yet: the simulator has not reached the stream service")
        return self.world

    def _now_us(self, world: World) -> int:
        """Earliest time a change can take effect: one second after what has been published."""
        return world.published_until_us + US

    async def inject(self, spec: FaultSpec) -> ActiveFault:
        async with self.lock:
            world = self.require_world()
            fault = world.inject(spec, now_us=self._now_us(world))
            self._write_truth()
            return fault

    async def inject_random(self, fault_type: str, start: datetime | None) -> ActiveFault:
        """'Surprise me': a plausible fault of this type, drawn reproducibly per episode."""
        async with self.lock:
            world = self.require_world()
            r = rng(self.seed, "surprise", self.episode or 0, self.random_draws)
            when = start or datetime.fromtimestamp(self._now_us(world) / US, tz=world.zone)
            fault = world.inject(random_spec(world.model, fault_type, when, r))
            self.random_draws += 1
            self._write_truth()
            return fault

    async def clear(self, fault_id: str) -> ActiveFault:
        async with self.lock:
            world = self.require_world()
            fault = world.clear(fault_id, self._now_us(world))
            self._write_truth()
            return fault

    async def impose(self, spec: AnomalySpec) -> str:
        async with self.lock:
            world = self.require_world()
            record_id = world.impose(spec)
            self._write_truth()
            return record_id

    def faults(self) -> list[dict[str, Any]]:
        world = self.require_world()
        return [fault_view(world, f, world.published_until_us) for f in world.faults]

    def truth_records(self) -> list[dict[str, Any]]:
        """Ground truth for house time already published (labels never run ahead of data)."""
        world = self.require_world()
        ep = episode_id(self.episode or 0)
        until = world.published_until_us + 1
        return [{**r, "episode_id": ep} for r in world.truth_records(until)]

    def _write_truth(self) -> None:
        if self.world is not None and self.episode is not None:
            self.truth.write(self.episode, self.truth_records())

    def status(self) -> dict[str, Any]:
        world = self.world
        sensors = None if self.enabled_sensors is None else sorted(self.enabled_sensors)
        return {
            "episode": self.episode,
            "seed": self.seed,
            "archetype": self.archetype,
            "enabled_sensors": sensors,
            "published_until": None if world is None else _published_iso(world),
            "messages_sent": self.sent,
            "faults": 0 if world is None else len(world.faults),
            "last_error": self.last_error,
        }


def _published_iso(world: World) -> str:
    """House time up to which readings have been sent, rounded up to the second."""
    whole = -(-world.published_until_us // US) * US
    return datetime.fromtimestamp(whole / US, tz=world.zone).isoformat()


def fault_view(world: World, fault: ActiveFault, published_us: int) -> dict[str, Any]:
    """Operator view of a fault: its spec, schedule and state relative to published time."""
    if published_us < fault.start_us:
        state = "scheduled"
    elif fault.end_us is not None and fault.end_us <= published_us:
        state = "ended"
    else:
        state = "active"

    def iso(us: int | None) -> str | None:
        return None if us is None else datetime.fromtimestamp(us / US, tz=world.zone).isoformat()

    return {
        "fault_id": fault.fault_id,
        "spec": spec_document(fault.spec),
        "start": iso(fault.start_us),
        "end": iso(fault.end_us),
        "state": state,
    }


async def _sleep_or_stop(stop: asyncio.Event, seconds: float) -> None:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=seconds)
