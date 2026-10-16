"""The live engine gives exactly the findings the evaluation replay gives, however data arrives."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from datetime import timedelta
from typing import Any

import numpy as np
import pytest

from penjaga_contract.core import Finding
from penjaga_contract.validate import load_context
from penjaga_detect import registry
from penjaga_detect.chunk import (
    Chunk,
    EventRecord,
    Series,
    batch_samples,
    chunk_from_messages,
    event_record,
)
from penjaga_detect.detector import DetectorInfo, HouseContext
from penjaga_detect.engine import DetectorSpec, Engine
from penjaga_sim.export import SeqCounters, ingest_messages
from penjaga_sim.testing import START, at, fault, new_world
from penjaga_sim.world import World

US = 1_000_000
H = 3600 * US
DETECTORS = ["l1_mnf_baseline", "l1_burst_rule", "l2_safety_floor", "l5_supply"]
DAYS = 3


def specs(names: Sequence[str] = DETECTORS) -> list[DetectorSpec]:
    house = HouseContext.canonical()
    out = []
    for name in names:
        params = registry.parameters(name, None)
        layer = registry.build(name, house, params).info.layer

        def build(name: str = name, params: Any = params) -> Any:
            return registry.build(name, house, params)

        out.append(DetectorSpec(name, layer, build))
    return out


def periods() -> dict[str, float]:
    return {
        sid: float(s.nominal_period_s)
        for sid, s in load_context().streams.items()
        if s.nominal_period_s is not None
    }


@pytest.fixture(scope="module")
def world() -> World:
    w = new_world()
    w.inject(fault("W2", "fix_bath2_wc_cistern", 0.3, at(START, 0.5)))
    w.inject(fault("U3", None, 1, at(START, 9.0), duration_s=1800))
    w.inject(fault("E1", "circ_sockets_kitchen", 25.0, at(START + timedelta(days=1), 19.0)))
    w.inject(fault("W5", "pipe_porch_tap", 12.0, at(START + timedelta(days=2), 14.0)))
    return w


@pytest.fixture(scope="module")
def messages(world: World) -> list[dict[str, Any]]:
    """The whole episode as M4 relays it, in 7-minute publishing steps (events first)."""
    counters = SeqCounters()
    out: list[dict[str, Any]] = []
    t, end = world.day_start_us(START), world.day_start_us(START + timedelta(days=DAYS))
    while t < end:
        t1 = min(end, t + 7 * 60 * US)
        out += ingest_messages(world, t, t1, counters)
        t = t1
    return out


def by_detector(pairs: Iterable[tuple[str, Finding]], end_us: int) -> dict[str, list[Any]]:
    out: dict[str, list[Any]] = defaultdict(list)
    for name, f in pairs:
        if int(f.updated_at.timestamp() * US) <= end_us:
            out[name].append(f.model_dump(mode="json", exclude_none=True))
    return dict(out)


def replay_reference(world: World, messages: list[dict[str, Any]]) -> dict[str, list[Any]]:
    """What the evaluation harness computes: 24-hour chunks over the same messages."""
    built = {s.name: s.build() for s in specs()}
    pairs: list[tuple[str, Finding]] = []
    t, end = world.day_start_us(START), world.day_start_us(START + timedelta(days=DAYS))
    while t < end:
        t1 = t + 24 * H
        chunk = chunk_from_messages(world.model.index.house_id, t, t1, messages)
        for name, d in built.items():
            pairs += [(name, f) for f in d.update(chunk)]
        t = t1
    return by_detector(pairs, end)


def new_engine(world: World, **kw: Any) -> Engine:
    return Engine(
        world.model.index.house_id, specs(), periods(), start_us=world.day_start_us(START), **kw
    )


@pytest.fixture(scope="module")
def reference(world: World, messages: list[dict[str, Any]]) -> dict[str, list[Any]]:
    ref = replay_reference(world, messages)
    assert set(ref) == set(DETECTORS)  # the scenario exercises every detector
    return ref


def test_live_equals_replay_advancing_after_every_message(
    world: World, messages: list[dict[str, Any]], reference: dict[str, list[Any]]
) -> None:
    engine = new_engine(world)
    pairs: list[tuple[str, Finding]] = []
    for m in messages:
        engine.ingest(m)
        pairs += engine.advance()  # never runs ahead of data still in flight
    end = world.day_start_us(START + timedelta(days=DAYS))
    assert engine.processed_until_us >= end - 7 * 60 * US
    assert engine.late_samples == engine.late_events == 0
    assert by_detector(pairs, end - 7 * 60 * US) == {
        k: [d for d in v if _stamp(d) <= end - 7 * 60 * US] for k, v in reference.items()
    }


def _stamp(doc: dict[str, Any]) -> int:
    from datetime import datetime

    return int(datetime.fromisoformat(doc["updated_at"]).timestamp() * US)


def _store(messages: list[dict[str, Any]], until_us: int) -> tuple[dict[str, Any], list[Any]]:
    """What M4's store would hold up to ``until_us``: samples per stream, events with seq."""
    parts: dict[str, list[Any]] = defaultdict(list)
    events: list[tuple[EventRecord, int]] = []
    for m in messages:
        if m["type"] == "batch":
            ts, values, quality = batch_samples(m["data"])
            keep = ts <= until_us
            parts[m["data"]["stream"]].append((ts[keep], values[keep], quality[keep]))
        else:
            event = event_record(m["data"])
            if event.ts_us <= until_us:
                events.append((event, int(m["data"]["seq"])))
    series = {
        sid: tuple(np.concatenate([p[i] for p in ps]) for i in range(3))
        for sid, ps in parts.items()
    }
    return series, events


def test_backfill_then_live_with_overlap_equals_replay(
    world: World, messages: list[dict[str, Any]], reference: dict[str, list[Any]]
) -> None:
    """A service joining mid-episode: the store up to day 1 18:00, then the live subscription
    from two hours earlier (the overlap is dropped as duplicates)."""
    cut = world.day_start_us(START + timedelta(days=1)) + 18 * H
    engine = new_engine(world)
    series, events = _store(messages, cut)
    engine.backfill(series, events)
    pairs = engine.advance()
    for m in messages:
        data = m["data"]
        first = batch_samples(data)[0][0] if m["type"] == "batch" else event_record(data).ts_us
        if first > cut - 2 * H:
            engine.ingest(m)
            pairs += engine.advance()
    end = world.day_start_us(START + timedelta(days=DAYS)) - 7 * 60 * US
    assert engine.duplicate_samples > 0
    assert engine.late_samples == engine.late_events == 0
    assert by_detector(pairs, end) == {
        k: [d for d in v if _stamp(d) <= end] for k, v in reference.items()
    }


# ---------------------------------------------------------------------------- unit behaviour


class _Probe:
    """A detector that records the chunks it sees (and can be told to fail)."""

    def __init__(self, layer: str, streams: frozenset[str], events: frozenset[str]) -> None:
        self.info = DetectorInfo(
            "probe_" + layer.lower(), layer, "rule", "1.0.0", None, None, streams, events
        )
        self.chunks: list[Chunk] = []
        self.fail = False

    def update(self, chunk: Chunk) -> list[Finding]:
        if self.fail:
            raise RuntimeError("boom")
        self.chunks.append(chunk)
        return []


A, B, EV = "water.main.flow_lpm", "water.main.pressure_bar", "elec.supply.state"
T0 = 1_700_000_000 * US


def batch(
    stream: str, t0_s: float, values: Sequence[float | None], period_s: float
) -> dict[str, Any]:
    from datetime import UTC, datetime

    t0 = datetime.fromtimestamp(T0 / US + t0_s, tz=UTC).isoformat()
    return {
        "type": "batch",
        "data": {
            "stream": stream,
            "t0": t0,
            "period_s": period_s,
            "values": list(values),
            "quality": "ok",
        },
    }


def event(ts_s: float, seq: int) -> dict[str, Any]:
    from datetime import UTC, datetime

    ts = datetime.fromtimestamp(T0 / US + ts_s, tz=UTC).isoformat()
    return {
        "type": "event",
        "data": {
            "ts": ts,
            "stream": EV,
            "seq": seq,
            "payload": {"type": "supply_state", "mains": "lost"},
        },
    }


def _same(probe: _Probe) -> Callable[[], _Probe]:
    return lambda: probe


def probe_engine(**kw: Any) -> tuple[Engine, dict[str, _Probe]]:
    probes = {
        "L1": _Probe("L1", frozenset({A, B}), frozenset({EV})),
        "L2": _Probe("L2", frozenset({A}), frozenset()),
    }
    specs_ = [DetectorSpec(p.info.name, layer, _same(p)) for layer, p in probes.items()]
    engine = Engine("h", specs_, {A: 10.0, B: 10.0}, start_us=T0, **kw)
    return engine, probes


def test_processing_waits_for_every_stream_and_never_passes_the_samples() -> None:
    engine, probes = probe_engine()
    engine.ingest(event(5, 1))  # events first (protocol rule)
    engine.ingest(batch(A, 10, [1, 2, 3], 10))  # A covers up to 39.999999 s
    assert engine.advance() == []
    assert engine.processed_until_us == T0 + 10 * US - 1  # B not seen: at most one period
    engine.ingest(batch(B, 10, [1], 10))  # B covers up to 19.999999 s
    engine.advance()
    assert engine.processed_until_us == T0 + 20 * US - 1
    engine.ingest(batch(B, 20, [2, 3], 10))  # B now covers up to 39.999999 s
    engine.advance()
    assert engine.processed_until_us == T0 + 30 * US  # never past the last sample seen
    chunks = probes["L1"].chunks
    assert sum(len(c.events) for c in chunks) == 1
    assert sum(len(c.get(A)) for c in chunks) == 3
    assert sum(len(c.get(B)) for c in chunks) == 3


def test_duplicates_and_late_samples_are_counted_not_processed() -> None:
    engine, _ = probe_engine()
    engine.ingest(batch(A, 10, [1, 2, 3], 10))
    engine.ingest(batch(B, 10, [1, 2, 3], 10))
    engine.advance()
    engine.ingest(batch(A, 20, [2, 3, 4], 10))  # 20 and 30 seen before; 40 is new
    assert engine.duplicate_samples == 2
    engine.ingest(event(1, 7))  # an event for time already processed
    assert engine.late_events == 1
    engine.ingest(event(1, 7))  # the same event again: a duplicate, not late again
    assert engine.late_events == 1


def test_a_stalled_stream_stops_holding_processing_back() -> None:
    engine, _ = probe_engine(stall_s=60)
    engine.ingest(batch(B, 10, [1], 10))
    engine.ingest(batch(A, 10, [float(i) for i in range(30)], 10))  # A runs 300 s ahead
    assert engine.stalled() == [B]
    engine.advance()
    assert engine.processed_until_us == T0 + 300 * US


def test_an_absent_stream_holds_processing_only_until_it_stalls() -> None:
    engine, _ = probe_engine(stall_s=60)
    engine.ingest(batch(A, 10, [1.0] * 5, 10))  # B (an absent sensor) never arrives
    engine.advance()
    assert engine.processed_until_us == T0 + 10 * US - 1
    engine.ingest(batch(A, 60, [1.0] * 10, 10))  # A is now more than 60 s ahead
    assert engine.stalled() == [B]
    engine.advance()
    assert engine.processed_until_us == T0 + 150 * US
    engine.ingest(batch(B, 20, [1.0], 10))  # B comes back for time already processed
    assert engine.late_samples == 1


def test_a_failing_detector_is_isolated() -> None:
    engine, probes = probe_engine()
    probes["L2"].fail = True
    engine.ingest(batch(A, 10, [1, 2], 10))
    engine.ingest(batch(B, 10, [1, 2], 10))
    engine.advance()
    assert "boom" in engine.failed["probe_l2"]
    assert engine.running == ["probe_l1"]
    assert probes["L1"].chunks


def test_layer_consent_drops_and_restarts_detectors() -> None:
    engine, _ = probe_engine()
    engine.set_layer("L1", False)
    assert engine.running == ["probe_l2"]
    assert engine.wanted_streams == [A]
    assert engine.wanted_event_streams == []
    engine.set_layer("L1", True)
    assert sorted(engine.running) == ["probe_l1", "probe_l2"]
    with pytest.raises(KeyError):
        engine.set_layer("L3", True)


def test_series_between_is_half_open() -> None:
    s = Series(np.array([1, 2, 3], dtype=np.int64), np.zeros(3), np.zeros(3, dtype=np.uint8))
    assert list(s.between(1, 3).ts_us) == [2, 3]
