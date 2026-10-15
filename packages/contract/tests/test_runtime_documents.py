"""Findings, readings and events: schema + semantic validation, and label-leak canaries."""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import ValidationError

from penjaga_contract import examples
from penjaga_contract.core import Event, Finding, Reading, ReadingBatch
from penjaga_contract.validate import (
    validate_event_document,
    validate_finding_document,
    validate_reading_batch_document,
)


def _codes(report) -> set[str]:
    return {i.code for i in report.errors}


@pytest.mark.parametrize("factory", [examples.example_finding, examples.example_floor_finding])
def test_example_findings_are_valid(ctx, factory):
    doc = factory()
    assert validate_finding_document(doc, ctx).issues == []
    Finding.model_validate(doc)


def test_example_batch_reading_event_are_valid(ctx):
    assert validate_reading_batch_document(examples.example_reading_batch(), ctx).issues == []
    assert validate_event_document(examples.example_trip_event(), ctx).issues == []
    ReadingBatch.model_validate(examples.example_reading_batch())
    Reading.model_validate(examples.example_reading())
    Event.model_validate(examples.example_trip_event())


Edit = Callable[[dict[str, Any]], None]

FINDING_CASES: list[tuple[str, Callable[[], dict[str, Any]], Edit, str]] = [
    (
        "urgent without guidance",
        examples.example_floor_finding,
        lambda d: d.pop("safety_guidance_key"),
        "schema.invalid",
    ),
    (
        "rule confidence below 1",
        examples.example_floor_finding,
        lambda d: d.update(confidence=0.9),
        "schema.invalid",
    ),
    (
        "floor with model confidence",
        examples.example_floor_finding,
        lambda d: d.update(confidence_kind="calibrated_probability", confidence=0.9),
        "schema.invalid",
    ),
    (
        "layer/type mismatch",
        examples.example_finding,
        lambda d: d.update(type="circuit_overload"),
        "schema.invalid",
    ),
    ("L4 finding", examples.example_finding, lambda d: d.update(layer="L4"), "schema.invalid"),
    (
        "model without model card",
        examples.example_finding,
        lambda d: d["source"].pop("model_card"),
        "schema.invalid",
    ),
    (
        "superseded without successor",
        examples.example_finding,
        lambda d: d.update(status="superseded"),
        "schema.invalid",
    ),
    (
        "cleared without time",
        examples.example_finding,
        lambda d: d.update(status="cleared"),
        "schema.invalid",
    ),
    (
        "naive timestamp",
        examples.example_finding,
        lambda d: d.update(first_seen="2026-10-13T05:00:00"),
        "schema.invalid",
    ),
    ("bad id", examples.example_finding, lambda d: d.update(id="finding-1"), "schema.invalid"),
    ("no evidence", examples.example_finding, lambda d: d.update(evidence=[]), "schema.invalid"),
    (
        "key layer mismatch",
        examples.example_finding,
        lambda d: d.update(key="L2:running_cistern_suspected:tank_fed"),
        "finding.key_layer",
    ),
    (
        "unknown object",
        examples.example_finding,
        lambda d: d["candidates"][0].update(object_id="fix_bath9_wc_cistern"),
        "finding.unknown_object",
    ),
    (
        "duplicate candidates",
        examples.example_finding,
        lambda d: d["candidates"][1].update(object_id="fix_bath2_wc_cistern"),
        "finding.candidate_duplicate",
    ),
    (
        "probabilities above one",
        examples.example_finding,
        lambda d: d.update(candidates_other_p=0.5),
        "finding.candidates_sum",
    ),
    (
        "time order",
        examples.example_finding,
        lambda d: d.update(last_seen="2026-10-12T05:00:00+08:00"),
        "finding.time_order",
    ),
    (
        "unknown stream",
        examples.example_finding,
        lambda d: d["series_refs"][0].update(stream="water.bath2.flow_lpm"),
        "finding.unknown_stream",
    ),
    (
        "floor from a model",
        examples.example_floor_finding,
        lambda d: d["source"].update(kind="model", model="m", model_card="docs/models/m.md"),
        "finding.floor_source",
    ),
    (
        "quantity name",
        examples.example_finding,
        lambda d: d["quantities"].update({"Water L/day": {"value": 1.0, "unit": "L/day"}}),
        "finding.quantity_name",
    ),
    (
        "wrong house",
        examples.example_finding,
        lambda d: d.update(house_id="other_house"),
        "finding.house",
    ),
]


@pytest.mark.parametrize(
    ("name", "factory", "edit", "code"), FINDING_CASES, ids=[c[0] for c in FINDING_CASES]
)
def test_invalid_findings_are_rejected(ctx, name, factory, edit, code):
    doc = factory()
    edit(doc)
    assert code in _codes(validate_finding_document(doc, ctx)), name


LABEL_FIELDS = ["fault_id", "fault_type", "label", "truth", "injected", "episode_id", "scenario"]


@pytest.mark.parametrize("field", LABEL_FIELDS)
@pytest.mark.parametrize(
    ("factory", "model"),
    [
        (examples.example_reading, Reading),
        (examples.example_reading_batch, ReadingBatch),
        (examples.example_trip_event, Event),
        (examples.example_finding, Finding),
    ],
)
def test_runtime_documents_cannot_carry_labels(ctx, factory, model, field):
    """Canary for PRD §8.1: no reading, event or finding can smuggle ground truth."""
    doc = factory()
    doc[field] = "W2"
    with pytest.raises(ValidationError):
        model.model_validate(doc)


def test_event_payload_cannot_carry_cause():
    doc = examples.example_trip_event()
    doc["payload"]["cause"] = "residual_current"
    with pytest.raises(ValidationError):
        Event.model_validate(doc)


BATCH_CASES: list[tuple[str, Edit, str]] = [
    ("unknown stream", lambda d: d.update(stream="water.bath2.flow_lpm"), "batch.unknown_stream"),
    ("event stream", lambda d: d.update(stream="elec.protection.trip"), "batch.not_sampled"),
    ("wrong unit", lambda d: d.update(unit="L/h"), "batch.unit"),
    ("wrong period", lambda d: d.update(period_s=5.0), "batch.period"),
    ("quality length", lambda d: d.update(quality=["ok"]), "batch.quality_length"),
    ("null marked ok", lambda d: d["quality"].__setitem__(3, "ok"), "batch.null_quality"),
    (
        "value marked missing",
        lambda d: d["quality"].__setitem__(0, "missing"),
        "batch.null_quality",
    ),
    ("out of range unflagged", lambda d: d["values"].__setitem__(0, 500.0), "batch.range"),
    ("wrong house", lambda d: d.update(house_id="other_house"), "batch.house"),
    ("label field", lambda d: d.update(fault_id="flt_abcdef"), "schema.invalid"),
]


@pytest.mark.parametrize(("name", "edit", "code"), BATCH_CASES, ids=[c[0] for c in BATCH_CASES])
def test_invalid_batches_are_rejected(ctx, name, edit, code):
    doc = examples.example_reading_batch()
    edit(doc)
    assert code in _codes(validate_reading_batch_document(doc, ctx)), name


def test_out_of_range_value_is_allowed_when_flagged(ctx):
    doc = examples.example_reading_batch()
    doc["values"][0] = 500.0
    doc["quality"][0] = "out_of_range"
    assert validate_reading_batch_document(doc, ctx).ok


EVENT_CASES: list[tuple[str, Edit, str]] = [
    (
        "unknown device",
        lambda d: d["payload"].update(device_id="rcd_garage"),
        "event.unknown_device",
    ),
    (
        "payload/stream mismatch",
        lambda d: d.update(stream="elec.supply.state"),
        "event.payload_type",
    ),
    ("sampled stream", lambda d: d.update(stream="water.main.flow_lpm"), "event.not_event"),
    ("unknown stream", lambda d: d.update(stream="elec.protection.cause"), "event.unknown_stream"),
]


@pytest.mark.parametrize(("name", "edit", "code"), EVENT_CASES, ids=[c[0] for c in EVENT_CASES])
def test_invalid_events_are_rejected(ctx, name, edit, code):
    doc = copy.deepcopy(examples.example_trip_event())
    edit(doc)
    assert code in _codes(validate_event_document(doc, ctx)), name
