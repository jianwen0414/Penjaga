"""Fast ingest validation: same verdicts as the contract validator, plus continuity."""

from __future__ import annotations

import copy
from typing import Any

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from penjaga_contract.core import Event, ReadingBatch
from penjaga_contract.validate import (
    ContractContext,
    validate_event_document,
    validate_reading_batch_document,
)
from penjaga_stream.registry import StreamRegistry
from penjaga_stream.testing import at, batch_doc, trip_event_doc

CURRENT = "elec.circuit.circ_sockets_kitchen.current_a"  # 1 s, A, 0..100
FLOW = "water.main.flow_lpm"  # 10 s, L/min, 0..52.08


def check(registry: StreamRegistry, doc: dict[str, Any]) -> list[str]:
    codes, _ = registry.check_batch(ReadingBatch.model_validate(doc))
    return codes


def test_valid_batch_is_prepared(ctx: ContractContext):
    reg = StreamRegistry(ctx)
    codes, prepared = reg.check_batch(
        ReadingBatch.model_validate(batch_doc(CURRENT, at(0), [1.0, None, 3.5]))
    )
    assert codes == []
    assert prepared is not None
    assert prepared.n == 3
    assert np.isnan(prepared.values[1])
    assert prepared.quality.tolist() == [0, 1, 0]
    assert prepared.end_us - prepared.t0_us == 3_000_000
    assert prepared.timestamps_us().tolist() == [prepared.t0_us + i * 1_000_000 for i in range(3)]


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"house_id": "other_house"}, "batch.house"),
        ({"stream": "water.bath2.flow_lpm"}, "batch.unknown_stream"),
        ({"stream": "elec.protection.trip"}, "batch.not_sampled"),
        ({"unit": "mA"}, "batch.unit"),
        ({"period_s": 2.0}, "batch.period"),
        ({"quality": ["ok"]}, "batch.quality_length"),
        ({"values": [1.0, None, 500.0], "quality": ["ok", "missing", "ok"]}, "batch.range"),
        ({"quality": ["ok", "ok", "ok"]}, "batch.null_quality"),
        ({"quality": ["missing", "missing", "ok"]}, "batch.null_quality"),
    ],
)
def test_defects_are_rejected(ctx: ContractContext, change: dict[str, Any], code: str):
    doc = batch_doc(CURRENT, at(0), [1.0, None, 3.5])
    doc.update(change)
    assert code in check(StreamRegistry(ctx), doc)


def test_out_of_range_allowed_when_flagged(ctx: ContractContext):
    doc = batch_doc(CURRENT, at(0), [500.0], quality=["out_of_range"])
    assert check(StreamRegistry(ctx), doc) == []


def test_continuity(ctx: ContractContext):
    reg = StreamRegistry(ctx)

    def accept(doc: dict[str, Any]) -> None:
        codes, prepared = reg.check_batch(ReadingBatch.model_validate(doc))
        assert codes == []
        assert prepared is not None
        reg.accept_batch(prepared)

    accept(batch_doc(CURRENT, at(0), [1.0, 2.0], seq=0))
    accept(batch_doc(CURRENT, at(2), [1.0], seq=1))  # contiguous
    assert reg.state[CURRENT].gaps == 0
    assert "batch.seq_regression" in check(reg, batch_doc(CURRENT, at(3), [1.0], seq=1))
    assert "batch.time_overlap" in check(reg, batch_doc(CURRENT, at(2), [1.0], seq=2))
    accept(
        batch_doc(CURRENT, at(10), [1.0], seq=5)
    )  # forward gap in time and seq: accepted, counted
    state = reg.state[CURRENT]
    assert state.gaps == 1
    assert state.samples == 4
    assert state.last_seq == 5
    assert reg.producer_position_us() == state.next_t_us
    reg.reset()
    assert reg.state[CURRENT].last_seq is None


def test_event_checks_and_continuity(ctx: ContractContext):
    reg = StreamRegistry(ctx)
    good = trip_event_doc("rcd_wh_bath2", at(5), seq=0)
    codes, prepared = reg.check_event(Event.model_validate(good))
    assert codes == []
    assert prepared is not None
    reg.accept_event(prepared)
    later = trip_event_doc("rcd_wh_bath2", at(4), seq=1, state="reset")
    assert reg.check_event(Event.model_validate(later))[0] == ["event.time_regression"]
    bad_device = trip_event_doc("rcd_garage", at(6), seq=1)
    assert "event.unknown_device" in reg.check_event(Event.model_validate(bad_device))[0]
    wrong_stream = copy.deepcopy(good) | {"stream": "elec.supply.state", "seq": 1}
    assert "event.payload_type" in reg.check_event(Event.model_validate(wrong_stream))[0]
    sampled = copy.deepcopy(good) | {"stream": CURRENT}
    assert reg.check_event(Event.model_validate(sampled))[0] == ["event.not_event"]


# ---------------------------------------------------------------- equivalence with the contract

CONTRACT_BATCH_CODES = {
    "batch.house",
    "batch.unknown_stream",
    "batch.not_sampled",
    "batch.unit",
    "batch.period",
    "batch.quality_length",
    "batch.null_quality",
    "batch.range",
}


@st.composite
def batch_documents(draw: st.DrawFn, streams: list[dict[str, Any]]) -> dict[str, Any]:
    stream = draw(st.sampled_from(streams))
    n = draw(st.integers(1, 8))
    lo, hi = stream["value_min"], stream["value_max"]
    value = st.one_of(
        st.none(),
        st.floats(lo, hi, allow_nan=False),
        st.floats(hi + 1, hi * 10 + 10, allow_nan=False),
    )
    values = draw(st.lists(value, min_size=n, max_size=n))
    flag = st.sampled_from(["ok", "missing", "estimated", "out_of_range", "stale"])
    quality: Any = draw(
        st.one_of(
            st.lists(flag, min_size=n, max_size=n),
            st.lists(flag, min_size=1, max_size=n + 2),
            flag,
        )
    )
    return {
        "house_id": draw(st.sampled_from(["elmina_g6_demo", "other_house"])),
        "stream": draw(
            st.sampled_from([stream["id"], "elec.protection.trip", "water.bath9.flow_lpm"])
        ),
        "t0": "2026-10-16T00:00:00+08:00",
        "period_s": draw(st.sampled_from([stream["nominal_period_s"], 2.5])),
        "unit": draw(st.sampled_from([stream["unit"], "kg"])),
        "values": values,
        "quality": quality,
        "seq": 0,
    }


def _sampled_streams(ctx: ContractContext) -> list[dict[str, Any]]:
    return [s.model_dump() for s in ctx.catalogue.streams if s.kind.value == "sampled"]


@settings(
    max_examples=300, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(data=st.data())
def test_registry_agrees_with_contract_validator(ctx: ContractContext, data: st.DataObject):
    doc = data.draw(batch_documents(_sampled_streams(ctx)))
    contract = {i.code for i in validate_reading_batch_document(doc, ctx).errors}
    if "schema.invalid" in contract:
        return  # the stream service rejects these at parse time
    fast = set(check(StreamRegistry(ctx), doc)) & CONTRACT_BATCH_CODES
    assert fast == contract, doc


def test_events_agree_with_contract_validator(ctx: ContractContext):
    docs = [
        trip_event_doc("rcd_wh_bath2", at(1), seq=0),
        trip_event_doc("rcd_garage", at(1), seq=0),
        trip_event_doc("rcd_wh_bath2", at(1), seq=0) | {"stream": "elec.supply.state"},
        trip_event_doc("rcd_wh_bath2", at(1), seq=0) | {"house_id": "other_house"},
    ]
    for doc in docs:
        contract = {i.code for i in validate_event_document(doc, ctx).errors}
        fast = set(StreamRegistry(ctx).check_event(Event.model_validate(doc))[0])
        assert fast == contract, doc
