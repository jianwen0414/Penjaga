"""JSON Schema validation against the contract schemas.

All schemas are registered under their ``$id`` so relative ``$ref`` values resolve offline
(the ``penjaga.invalid`` base URI is reserved and never fetched). Format assertion is
switched on with a strict RFC 3339 checker: ``date-time`` values must carry an explicit UTC
offset, which is what makes a value valid house time.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import date, datetime
from functools import cache
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from penjaga_contract.resources import read_json, schema_path, schema_paths

_RFC3339_DATE_TIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})$"
)
_RFC3339_FULL_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

FORMAT_CHECKER = FormatChecker(formats=())


@FORMAT_CHECKER.checks("date-time", raises=ValueError)
def is_rfc3339_date_time(instance: object) -> bool:
    """RFC 3339 ``date-time``: explicit offset required; field ranges checked by datetime."""
    if not isinstance(instance, str):
        return True
    if not _RFC3339_DATE_TIME.match(instance):
        return False
    parsed = datetime.fromisoformat(instance.upper())
    return parsed.tzinfo is not None


@FORMAT_CHECKER.checks("date", raises=ValueError)
def is_rfc3339_full_date(instance: object) -> bool:
    """RFC 3339 ``full-date`` (YYYY-MM-DD), with calendar validity."""
    if not isinstance(instance, str):
        return True
    if not _RFC3339_FULL_DATE.match(instance):
        return False
    date.fromisoformat(instance)
    return True


@cache
def registry() -> Registry[Any]:
    """Registry of every contract schema keyed by its ``$id``."""
    pairs = []
    for path in schema_paths():
        contents = read_json(path)
        schema_id = contents.get("$id")
        if not isinstance(schema_id, str):
            raise ValueError(f"{path} has no string $id")
        pairs.append((schema_id, Resource.from_contents(contents, DRAFT202012)))
    return Registry().with_resources(pairs)


@cache
def validator(namespace: str, name: str) -> Draft202012Validator:
    """Validator for ``<namespace>/<name>.schema.json`` with offline refs and format assertion."""
    schema = read_json(schema_path(namespace, name))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, registry=registry(), format_checker=FORMAT_CHECKER)


def iter_errors(namespace: str, name: str, instance: Any) -> Iterator[ValidationError]:
    """All schema violations of ``instance``, deepest-first ordering left to jsonschema."""
    return validator(namespace, name).iter_errors(instance)


def error_messages(namespace: str, name: str, instance: Any) -> list[str]:
    """Human-readable schema violations, each prefixed with its JSON path."""
    messages = []
    for err in sorted(iter_errors(namespace, name, instance), key=lambda e: list(e.path)):
        messages.append(f"{err.json_path}: {err.message}")
    return messages


def validate(namespace: str, name: str, instance: Any) -> None:
    """Raise ``jsonschema.ValidationError`` (the most relevant one) if ``instance`` is invalid."""
    validator(namespace, name).validate(instance)
