"""ReadingStore against an independent numpy reference."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq
import pytest

from penjaga_contract.core import Event, ReadingBatch
from penjaga_contract.validate import ContractContext
from penjaga_stream.registry import StreamRegistry
from penjaga_stream.store import ReadingStore
from penjaga_stream.testing import at, batch_doc, trip_event_doc
from penjaga_stream.timeutil import US_PER_S, to_us

CURRENT = "elec.circuit.circ_ac_master.current_a"
OTHER = "elec.circuit.circ_ac_bed2.current_a"


@pytest.fixture
def store(tmp_path: Path):
    s = ReadingStore(tmp_path / "ep" / "readings.duckdb")
    yield s
    s.close()


def ingest(reg: StreamRegistry, store: ReadingStore, doc: dict[str, Any]) -> None:
    codes, prepared = reg.check_batch(ReadingBatch.model_validate(doc))
    assert codes == []
    assert prepared is not None
    reg.accept_batch(prepared)
    store.append_batch(prepared)


def test_summary_matches_numpy_reference(ctx: ContractContext, store: ReadingStore):
    reg = StreamRegistry(ctx)
    rng = np.random.default_rng(1)
    values = np.round(rng.uniform(0, 10, 600), 2).tolist()
    values[37] = None  # one missing sample
    values[120:180] = [None] * 60  # a whole missing minute
    ingest(reg, store, batch_doc(CURRENT, at(0), values, seq=0))
    ingest(reg, store, batch_doc(OTHER, at(0), [50.0] * 600, seq=0))  # must not leak in
    from_us, to_us_ = to_us(at(0)), to_us(at(600))
    rows = store.summary(reg.index[CURRENT], from_us, to_us_, 60 * US_PER_S)
    assert [r.bucket for r in rows] == list(range(10))
    arr = np.array([np.nan if v is None else v for v in values]).reshape(10, 60)
    for r in rows:
        bucket = arr[r.bucket]
        present = bucket[~np.isnan(bucket)]
        assert r.n == present.size
        if present.size == 0:
            assert r.min is None
            assert r.max is None
            assert r.mean is None
            assert r.last is None
        else:
            assert r.min == pytest.approx(present.min())
            assert r.max == pytest.approx(present.max())
            assert r.mean == pytest.approx(present.mean())
            assert r.last == pytest.approx(present[-1])


def test_window_is_half_open_and_buckets_align_to_from(ctx: ContractContext, store: ReadingStore):
    reg = StreamRegistry(ctx)
    ingest(reg, store, batch_doc(CURRENT, at(0), [float(i) for i in range(10)], seq=0))
    rows = store.summary(reg.index[CURRENT], to_us(at(3)), to_us(at(7)), 2 * US_PER_S)
    assert [(r.bucket, r.n, r.min, r.max) for r in rows] == [(0, 2, 3.0, 4.0), (1, 2, 5.0, 6.0)]


def test_raw_and_count(ctx: ContractContext, store: ReadingStore):
    reg = StreamRegistry(ctx)
    ingest(reg, store, batch_doc(CURRENT, at(0), [1.0, None, 3.0], seq=0))
    rows = store.raw(reg.index[CURRENT], to_us(at(0)), to_us(at(10)), limit=100)
    assert [(r.ts_us, r.value, r.quality) for r in rows] == [
        (to_us(at(0)), 1.0, 0),
        (to_us(at(1)), None, 1),
        (to_us(at(2)), 3.0, 0),
    ]
    assert store.count_raw(reg.index[CURRENT], to_us(at(1)), to_us(at(10))) == 2
    assert len(store.raw(reg.index[CURRENT], to_us(at(0)), to_us(at(10)), limit=2)) == 2


def test_events_roundtrip(ctx: ContractContext, store: ReadingStore):
    reg = StreamRegistry(ctx)
    for i, (device, state) in enumerate([("rcd_wh_bath2", "tripped"), ("rcd_wh_bath2", "reset")]):
        codes, prepared = reg.check_event(
            Event.model_validate(trip_event_doc(device, at(i), seq=i, state=state))
        )
        assert prepared is not None, codes
        reg.accept_event(prepared)
        store.append_event(prepared)
    rows = store.events([reg.index["elec.protection.trip"]], to_us(at(0)), to_us(at(10)), 100)
    assert [(r.seq, r.payload["state"]) for r in rows] == [(0, "tripped"), (1, "reset")]
    assert store.events([reg.index["elec.supply.state"]], to_us(at(0)), to_us(at(10)), 100) == []
    assert len(store.events(None, to_us(at(0)), to_us(at(1)), 100)) == 1


def test_parquet_export(ctx: ContractContext, store: ReadingStore, tmp_path: Path):
    reg = StreamRegistry(ctx)
    ingest(reg, store, batch_doc(CURRENT, at(0), [1.0, 2.0, None], seq=0))
    out = tmp_path / "export" / "readings.parquet"
    store.export_parquet(out)
    table = pq.read_table(out)
    assert table.num_rows == 3
    assert table.column("value").null_count == 1


def test_closed_store_refuses_writes(ctx: ContractContext, tmp_path: Path):
    s = ReadingStore(tmp_path / "x" / "r.duckdb")
    s.close()
    s.close()  # idempotent
    reg = StreamRegistry(ctx)
    _, prepared = reg.check_batch(ReadingBatch.model_validate(batch_doc(CURRENT, at(0), [1.0])))
    assert prepared is not None
    with pytest.raises(RuntimeError):
        s.append_batch(prepared)
