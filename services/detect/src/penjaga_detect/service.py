"""The live detection service (M5): follows M4, runs the deployed detectors, keeps findings.

**One run per M4 episode.** A reset message (or a clock message naming another episode) starts
a new run: fresh detectors, and processing from the episode start.

**Joining or rejoining.** When the service joins an episode already under way, it backfills the
episode from M4's store before following live data. This happens at a start-up, and on a
reconnect after M4 closed a slow subscriber (1013).
- **Order:** the subscription is registered before the store is read, so nothing falls between
  the two. The overlap is dropped as duplicates.
- **Slices:** the backfill runs in slices of house time, so memory stays bounded.

**Back-pressure.** Messages pass through a bounded queue. While detection is behind, the
socket is not read, so M4's lossless queue fills and its clock governor slows house time.
Data is never dropped.

**Concurrency.** One consumer task owns the engine. The CPU work (detectors, SQLite writes)
runs in a worker thread while the consumer waits. API changes to the engine (layer consent)
take the same lock.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import anyio
from websockets.exceptions import WebSocketException

from penjaga_contract.core import ClockState, Finding, ResetNotice
from penjaga_contract.house import ElementKind
from penjaga_detect import checks
from penjaga_detect.chunk import US, Chunk, Series
from penjaga_detect.detector import HouseContext
from penjaga_detect.engine import DetectorSpec, Engine
from penjaga_detect.m4 import M4, M4UnavailableError, iso
from penjaga_detect.store import FindingStore, StoredRevision

log = logging.getLogger("penjaga_detect.service")

LAYERS = ("L1", "L2", "L3", "L5")


class NoRunError(RuntimeError):
    """No episode is being followed yet (503)."""


class RequestError(ValueError):
    """The request cannot be served as asked (422)."""


class NotFoundError(LookupError):
    """Unknown finding, check, detector or layer (404)."""


def to_us(moment: datetime) -> int:
    if moment.tzinfo is None:
        raise RequestError("house times need an explicit UTC offset")
    return int(moment.replace(microsecond=0).timestamp()) * US + moment.microsecond


def finding_document(finding: Finding) -> dict[str, Any]:
    return finding.model_dump(mode="json", by_alias=True, exclude_none=True)


@dataclass(frozen=True)
class StreamInfo:
    period_s: float
    resolution: float


@dataclass(frozen=True)
class DetectConfig:
    data_dir: Path
    max_chunk_s: float = 3600.0
    stall_s: float = 7200.0
    backfill_slice_s: float = 6 * 3600.0
    queue_size: int = 256
    drain_batch: int = 512  # messages handled between two engine advances
    reconnect_s: float = 2.0
    check_poll_s: float = 1.0
    max_run_days: float = 7.0


@dataclass(frozen=True)
class Run:
    episode_id: str  # unique per M4 episode, across M4 restarts too
    episode: int
    start_us: int

    @property
    def key(self) -> str:
        return self.episode_id


@dataclass
class _Clock:
    state: ClockState
    received_mono: float

    def house_us(self) -> int:
        """House time now, extrapolated from the last clock message."""
        now = to_us(self.state.house_time)
        if self.state.paused:
            return now
        elapsed = time.monotonic() - self.received_mono
        return now + round(elapsed * self.state.effective_speed * US)


@dataclass
class Counters:
    sessions: int = 0
    backfilled_samples: int = 0
    revisions: int = 0


class DetectService:
    def __init__(
        self,
        m4: M4,
        house: HouseContext,
        *,
        specs: Sequence[DetectorSpec],
        streams: Mapping[str, StreamInfo],
        store: FindingStore,
        config: DetectConfig,
    ) -> None:
        self.m4 = m4
        self.house = house
        self.specs = list(specs)
        self.streams = dict(streams)
        self.store = store
        self.config = config
        self.deployed_layers = sorted({s.layer for s in self.specs})
        self.enabled_layers = self._load_layers()
        self.engine: Engine | None = None
        self.run: Run | None = None
        self.connected = False
        self.last_error: str | None = None
        self.counters = Counters()
        self._clock: _Clock | None = None
        self._lock = asyncio.Lock()
        self._changed = asyncio.Condition()
        self._infos = {s.name: s.build().info for s in self.specs}

    # ------------------------------------------------------------------ layer consent

    @property
    def _layers_path(self) -> Path:
        return self.config.data_dir / "layers.json"

    def _load_layers(self) -> set[str]:
        path = self._layers_path
        if not path.exists():
            return set(self.deployed_layers)
        saved = json.loads(path.read_text(encoding="utf-8"))
        return {layer for layer in self.deployed_layers if saved.get(layer, True)}

    async def set_layer(self, layer: str, enabled: bool) -> None:
        if layer not in self.deployed_layers:
            raise NotFoundError(f"no deployed detector in layer {layer}")
        async with self._lock:
            if enabled:
                self.enabled_layers.add(layer)
            else:
                self.enabled_layers.discard(layer)
            state = {lay: lay in self.enabled_layers for lay in self.deployed_layers}
            self.config.data_dir.mkdir(parents=True, exist_ok=True)
            self._layers_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
            if self.engine is not None:
                self.engine.set_layer(layer, enabled)

    def detector_status(self) -> list[dict[str, Any]]:
        out = []
        for spec in self.specs:
            info = self._infos[spec.name]
            doc: dict[str, Any] = {
                "name": info.name,
                "layer": info.layer,
                "kind": info.kind,
                "version": info.version,
                "enabled": spec.layer in self.enabled_layers,
            }
            if info.model is not None:
                doc["model"] = info.model
            if info.model_card is not None:
                doc["model_card"] = info.model_card
            out.append(doc)
        return out

    # ------------------------------------------------------------------ main loop

    async def serve(self, stop: asyncio.Event) -> None:
        checker = asyncio.create_task(self._check_loop(stop))
        try:
            while not stop.is_set():
                try:
                    await self._session(stop)
                except (M4UnavailableError, WebSocketException, OSError) as exc:
                    self.last_error = f"M4: {exc}"[:300]
                    log.warning("M4 connection: %s", exc)
                finally:
                    self.connected = False
                await _sleep_or_stop(stop, self.config.reconnect_s)
        finally:
            checker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await checker

    async def _session(self, stop: asyncio.Event) -> None:
        async with self.m4.subscribe() as subscription:
            self.connected = True
            self.counters.sessions += 1
            queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(self.config.queue_size)

            async def reader() -> None:
                try:
                    async for message in subscription:
                        await queue.put(message)
                finally:
                    await queue.put(None)

            task = asyncio.create_task(reader())
            first = True
            try:
                while not stop.is_set():
                    try:
                        message = await asyncio.wait_for(queue.get(), timeout=0.5)
                    except TimeoutError:
                        continue
                    handled = 0
                    while True:
                        if message is None:
                            await self._advance()
                            return  # M4 closed the socket: reconnect (and backfill the gap)
                        await self._handle(message, first)
                        first = False
                        handled += 1
                        if queue.empty() or handled >= self.config.drain_batch:
                            break
                        message = queue.get_nowait()
                    await self._advance()
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

    async def _handle(self, message: dict[str, Any], first_of_session: bool) -> None:
        kind = message.get("type")
        if kind == "clock":
            clock = ClockState.model_validate(message["data"])
            self._clock = _Clock(clock, time.monotonic())
            run = Run(clock.episode_id, clock.episode, to_us(clock.episode_start))
            if run != self.run:
                await self._start(run, backfill=True)
            elif first_of_session:
                await self._backfill()  # the gap while disconnected
        elif kind == "reset":
            notice = ResetNotice.model_validate(message["data"])
            run = Run(notice.episode_id, notice.episode, to_us(notice.start))
            if run != self.run:
                await self._start(run, backfill=False)  # a new episode has no data yet
        elif kind in ("batch", "event"):
            if self.engine is not None:
                async with self._lock:
                    self.engine.ingest(message)

    async def _start(self, run: Run, *, backfill: bool) -> None:
        async with self._lock:
            self.run = run
            self.engine = Engine(
                self.house.house_id,
                self.specs,
                {sid: s.period_s for sid, s in self.streams.items()},
                start_us=run.start_us,
                enabled_layers=self.enabled_layers,
                max_chunk_s=self.config.max_chunk_s,
                stall_s=self.config.stall_s,
            )
        log.info("following episode %s (start %s)", run.episode, run.start_us)
        if backfill:
            await self._backfill()

    async def _backfill(self) -> None:
        """Feed the engine from M4's store, from where it stands to house time now."""
        engine, clock = self.engine, self._clock
        if engine is None or clock is None:
            return
        end = clock.house_us() + US  # nothing can be stamped after house time
        slice_us = round(self.config.backfill_slice_s * US)
        t = engine.processed_until_us
        while t < end:
            t1 = min(end, t + slice_us)
            series = {}
            for sid in engine.wanted_streams:
                # (t, t1] as M4's [from, to)
                data = await self.m4.raw(sid, t + 1, t1 + 1, self.streams[sid].period_s)
                series[sid] = data
                self.counters.backfilled_samples += int(data[0].size)
            events = await self.m4.events(engine.wanted_event_streams, t + 1, t1 + 1)
            async with self._lock:
                if self.engine is not engine:
                    return  # a reset arrived meanwhile
                engine.backfill(series, events)
            await self._advance()
            t = t1

    async def _advance(self) -> None:
        engine, run = self.engine, self.run
        if engine is None or run is None:
            return
        async with self._lock:
            added = await anyio.to_thread.run_sync(self._advance_sync, engine, run)
        if added:
            self.counters.revisions += len(added)
            async with self._changed:
                self._changed.notify_all()

    def _advance_sync(self, engine: Engine, run: Run) -> list[StoredRevision]:
        produced = engine.advance()
        docs = []
        for _, finding in produced:
            doc = finding_document(finding)
            docs.append((doc, to_us(finding.updated_at)))
        return self.store.add(run.key, docs) if docs else []

    # ------------------------------------------------------------------ findings

    def require_run(self) -> Run:
        if self.run is None:
            raise NoRunError("not following an episode yet (is M4 reachable?)")
        return self.run

    def findings(
        self, status: str | None, layer: str | None, since: datetime | None
    ) -> list[dict[str, Any]]:
        run = self.require_run()
        since_us = None if since is None else to_us(since)
        return self.store.latest(run.key, status=status, layer=layer, since_us=since_us)

    def finding(self, finding_id: str) -> list[dict[str, Any]]:
        history = self.store.history(self.require_run().key, finding_id)
        if not history:
            raise NotFoundError(f"no finding {finding_id!r} in the current episode")
        return history

    async def wait_for_revisions(self, after_seq: int, timeout_s: float) -> list[StoredRevision]:
        """Revisions stored after ``after_seq``, waiting up to ``timeout_s`` for some."""
        found = self.store.after(after_seq)
        if found:
            return found
        async with self._changed:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._changed.wait(), timeout_s)
        return self.store.after(after_seq)

    # ------------------------------------------------------------------ on-demand runs

    async def run_detector(self, name: str, from_: datetime, to: datetime) -> dict[str, Any]:
        """Run one deployed detector afresh over stored readings; nothing is stored."""
        run = self.require_run()
        spec = next((s for s in self.specs if s.name == name), None)
        if spec is None:
            raise NotFoundError(f"{name!r} is not a deployed detector")
        t0, t1 = to_us(from_), to_us(to)
        if t1 <= t0:
            raise RequestError("'to' must be after 'from'")
        if t1 - t0 > self.config.max_run_days * 86400 * US:
            raise RequestError(f"at most {self.config.max_run_days:g} days per run")
        if t0 < run.start_us:
            raise RequestError("'from' is before the episode start")
        engine = self.engine
        limit = run.start_us if engine is None else engine.processed_until_us
        if t1 > limit:
            raise RequestError("'to' is after the house time detection has reached")
        detector = spec.build()
        streams = sorted(
            set(self.streams)
            if detector.info.streams is None
            else detector.info.streams & set(self.streams)
        )
        series = {
            sid: Series(*(await self.m4.raw(sid, t0 + 1, t1 + 1, self.streams[sid].period_s)))
            for sid in streams
        }
        events = await self.m4.events(sorted(detector.info.event_streams), t0 + 1, t1 + 1)
        records = tuple(e for e, _ in events)

        def replay() -> list[Finding]:
            latest: dict[str, Finding] = {}
            step = round(self.config.max_chunk_s * US)
            t = t0
            while t < t1:
                u = min(t1, t + step)
                chunk = Chunk(
                    self.house.house_id,
                    t,
                    u,
                    {sid: s.between(t, u) for sid, s in series.items()},
                    tuple(e for e in records if t < e.ts_us <= u),
                )
                for f in detector.update(chunk):
                    latest[f.id] = f
                t = u
            return list(latest.values())

        found = await anyio.to_thread.run_sync(replay)
        return {
            "detector": name,
            "from": iso(t0, self.house.zone),
            "to": iso(t1, self.house.zone),
            "findings": [finding_document(f) for f in found],
        }

    # ------------------------------------------------------------------ guided checks

    async def create_check(self, request: dict[str, Any]) -> dict[str, Any]:
        run = self.require_run()
        kind = request["kind"]
        since = to_us(datetime.fromisoformat(request["since"]))
        if since < run.start_us:
            raise RequestError("'since' is before the episode start")
        if kind == "tank_level_trend":
            ready = since + round(float(request["window_s"]) * US)
            stream = checks.TANK_STREAM
        else:
            ready = since + round((float(request["settle_s"]) + float(request["window_s"])) * US)
            if kind == "flow_stop":
                stream = checks.FLOW_STREAM
            else:
                circuit = request["circuit_id"]
                index = self.house.index
                if not index.exists(circuit) or index.kind_of(circuit) is not ElementKind.circuit:
                    raise RequestError(f"{circuit!r} is not a circuit of this house")
                stream = checks.circuit_stream(circuit)
        doc: dict[str, Any] = {
            "check_id": f"chk_{secrets.token_hex(6)}",
            "request": request,
            "status": "pending",
            "ready_at": iso(ready, self.house.zone),
            "measurements": [],
            "verdict": None,
        }
        if stream not in self.streams:
            doc.update(status="unavailable", reason=f"the house has no {stream} stream")
        self.store.put_check(run.key, doc)
        return doc

    def check(self, check_id: str) -> dict[str, Any]:
        found = self.store.get_check(check_id)
        if found is None:
            raise NotFoundError(f"no check {check_id!r}")
        return found[1]

    async def _check_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.evaluate_due_checks()
            except (M4UnavailableError, OSError) as exc:
                log.warning("guided checks: %s", exc)
            await _sleep_or_stop(stop, self.config.check_poll_s)

    async def evaluate_due_checks(self) -> int:
        run, engine = self.run, self.engine
        if run is None or engine is None:
            return 0
        done = 0
        for doc in self.store.pending_checks(run.key):
            ready = to_us(datetime.fromisoformat(doc["ready_at"]))
            if engine.processed_until_us < ready:
                continue
            outcome = await self._measure(doc["request"], ready)
            doc.update(
                status=outcome.status,
                measurements=outcome.measurements,
                verdict=outcome.verdict,
            )
            if outcome.reason is not None:
                doc["reason"] = outcome.reason
            self.store.put_check(run.key, doc)
            done += 1
        return done

    async def _measure(self, request: Mapping[str, Any], ready: int) -> checks.Outcome:
        kind = request["kind"]
        since = to_us(datetime.fromisoformat(request["since"]))
        window = round(float(request["window_s"]) * US)
        if kind == "tank_level_trend":
            info = self.streams[checks.TANK_STREAM]
            data = Series(
                *(await self.m4.raw(checks.TANK_STREAM, since + 1, ready + 1, info.period_s))
            )
            return checks.tank_level_trend(
                checks.Window.of(data, since, ready, info.period_s), info.resolution
            )
        stream = (
            checks.FLOW_STREAM
            if kind == "flow_stop"
            else checks.circuit_stream(request["circuit_id"])
        )
        info = self.streams[stream]
        settle = round(float(request["settle_s"]) * US)
        data = Series(*(await self.m4.raw(stream, since - window + 1, ready + 1, info.period_s)))
        before = checks.Window.of(data, since - window, since, info.period_s)
        after = checks.Window.of(data, since + settle, ready, info.period_s)
        if kind == "flow_stop":
            return checks.flow_stop(
                before, after, float(request["expect_below_lpm"]), info.resolution
            )
        return checks.circuit_load(before, after, info.resolution)

    # ------------------------------------------------------------------ health

    def health(self) -> dict[str, Any]:
        engine, clock = self.engine, self._clock
        stats = None if engine is None else engine.stats()
        processed = None if stats is None else stats.processed_until_us
        lag = None
        if processed is not None and clock is not None:
            lag = round(max(0, clock.house_us() - processed) / US, 3)
        stalled = [] if stats is None else list(stats.stalled)
        failed = {} if stats is None else dict(stats.failed)
        degraded = not self.connected or bool(stalled) or bool(failed) or self.store.conflicts > 0
        detectors = []
        for spec in self.specs:
            info = self._infos[spec.name]
            item = {"name": info.name, "version": info.version}
            if info.model is not None:
                item["model"] = info.model
            detectors.append(item)
        return {
            "status": "degraded" if degraded else "ok",
            "connected": self.connected,
            "episode": None if self.run is None else self.run.episode,
            "processed_until": None if processed is None else iso(processed, self.house.zone),
            "lag_s": lag,
            "late_samples": 0 if stats is None else stats.late_samples + stats.late_events,
            "stalled_streams": stalled,
            "detectors": detectors,
        }

    def diagnostics(self) -> dict[str, Any]:
        """Operator detail beyond the contract's health document."""
        stats = None if self.engine is None else self.engine.stats()
        return {
            "failed_detectors": {} if stats is None else dict(stats.failed),
            "duplicate_samples": 0 if stats is None else stats.duplicate_samples,
            "store_conflicts": self.store.conflicts,
            "last_error": self.last_error,
            "sessions": self.counters.sessions,
            "backfilled_samples": self.counters.backfilled_samples,
            "revisions": self.counters.revisions,
            "running": [] if self.engine is None else self.engine.running,
            "enabled_layers": sorted(self.enabled_layers),
        }


async def _sleep_or_stop(stop: asyncio.Event, seconds: float) -> None:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=seconds)
