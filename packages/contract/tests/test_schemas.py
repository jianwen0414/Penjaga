"""The schema files themselves: valid, canonical, uniquely identified, resolvable offline."""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from jsonschema import Draft202012Validator

from penjaga_contract import schema_registry
from penjaga_contract.resources import SCHEMAS_DIR, dump_json, read_json, schema_paths


@pytest.mark.parametrize("path", schema_paths(), ids=lambda p: f"{p.parent.name}/{p.name}")
def test_schema_is_valid_draft_2020_12(path):
    Draft202012Validator.check_schema(read_json(path))


@pytest.mark.parametrize("path", schema_paths(), ids=lambda p: f"{p.parent.name}/{p.name}")
def test_schema_is_canonically_formatted(path):
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert text == dump_json(json.loads(text)), "run json canonicalisation (2-space indent, LF)"


def test_schema_ids_are_unique_and_match_paths():
    ids = {}
    for path in schema_paths():
        schema_id = read_json(path)["$id"]
        rel = path.relative_to(SCHEMAS_DIR).as_posix()
        assert schema_id == f"https://penjaga.invalid/contract/{rel}"
        assert schema_id not in ids
        ids[schema_id] = path


def test_every_ref_resolves_offline():
    registry = schema_registry.registry()
    for path in schema_paths():
        schema = read_json(path)
        resolver = registry.resolver(base_uri=schema["$id"])
        stack: list[Any] = [schema]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                ref = node.get("$ref")
                if isinstance(ref, str):
                    resolver.lookup(ref)  # raises if unresolvable
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)


def test_core_schemas_never_reference_lab():
    for path in schema_paths():
        if path.parent.name == "core":
            assert "/lab/" not in path.read_text(encoding="utf-8")
            assert "../lab" not in path.read_text(encoding="utf-8")


@given(st.datetimes(timezones=st.sampled_from([UTC, timezone(timedelta(hours=8))])))
def test_aware_rfc3339_datetimes_are_accepted(dt: datetime):
    value = dt.isoformat()
    assert schema_registry.is_rfc3339_date_time(value)


@given(st.datetimes())
def test_naive_datetimes_are_rejected(dt: datetime):
    assert not schema_registry.is_rfc3339_date_time(dt.isoformat())


@pytest.mark.parametrize(
    "value",
    [
        "2026-10-16 05:00:00+08:00",
        "2026-10-16T05:00+08:00",
        "2026-13-16T05:00:00Z",
        "2026-10-16T25:00:00Z",
        "yesterday",
    ],
)
def test_malformed_datetimes_are_rejected(value: str):
    # Out-of-range fields surface as ValueError, which the format checker treats as invalid.
    with contextlib.suppress(ValueError):
        assert not schema_registry.is_rfc3339_date_time(value)


@pytest.mark.parametrize("value", ["2024-02-30", "2024-1-01", "01/02/2024"])
def test_malformed_dates_are_rejected(value: str):
    with contextlib.suppress(ValueError):
        assert not schema_registry.is_rfc3339_full_date(value)


def test_validator_for_a_definition():
    assert (
        schema_registry.error_messages("lab", "sim_api", {"record_id": "x"}, "AnomalyCreated") == []
    )
    assert schema_registry.error_messages("lab", "sim_api", {}, "AnomalyCreated")
    with pytest.raises(KeyError):
        schema_registry.validator("lab", "sim_api", "NoSuchType")
