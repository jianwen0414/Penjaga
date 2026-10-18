"""The knowledge base the agent and the case builder read (PRD §16): the house record, tariffs,
the cost calculator, reviewed safety texts and the illustrative vendor directory.

These functions are the agent's tools in step 6. Each returns plain JSON-able data with its
sources and confidence, so nothing the agent says has to be guessed. Document search over
manuals (pgvector) isn't needed yet: maintenance rules come as structured data in step 8.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from penjaga_api.costs import Baseline, estimate
from penjaga_api.ids import validate_ids
from penjaga_api.safety import safety_text
from penjaga_api.tariffs import electricity_terms, water_terms
from penjaga_api.vendors import directory, list_vendors
from penjaga_contract.house import HouseIndex

TARIFF_KINDS = ("electricity", "water")


def get_house_record(index: HouseIndex, ids: list[str]) -> dict[str, dict[str, Any]]:
    """Record excerpts for contract IDs (each one validated): kind, name and where it is."""
    out: dict[str, dict[str, Any]] = {}
    for object_id in validate_ids(index, ids):
        element = index.element(object_id)
        record = element.model.model_dump(mode="json", exclude_none=True)
        record["kind"] = element.kind.value
        record["rooms"] = list(index.rooms_of(object_id))
        out[object_id] = record
    return out


def get_tariff(kind: str, on: date) -> dict[str, Any]:
    """The tariff in force on ``on``, with its sources and confidence."""
    if kind == "electricity":
        t = electricity_terms(on)
        return {
            "kind": kind,
            "on": on.isoformat(),
            "schedule": t.schedule,
            "protection_threshold_kwh": t.threshold_kwh,
            "afa_sen_per_kwh": t.afa_sen,
            "afa_month": t.afa_month,
            "afa_note": t.afa_note,
            "confidence": t.confidence,
            "sources": list(t.sources),
        }
    if kind == "water":
        w = water_terms(on)
        return {
            "kind": kind,
            "on": on.isoformat(),
            "schedule": w.schedule,
            "confidence": w.confidence,
            "sources": list(w.sources),
        }
    raise KeyError(f"unknown tariff kind {kind!r}; one of {', '.join(TARIFF_KINDS)}")


def estimate_cost(
    quantities: dict[str, Any], baseline: Baseline, on: date
) -> dict[str, Any] | None:
    """The monthly cost of a finding's quantities against the household's usual use."""
    found = estimate(quantities, baseline, on)
    return None if found is None else found.as_dict()


def get_safety_text(key: str) -> dict[str, Any]:
    return {"key": key, "lines": safety_text(key)}


def get_vendors(category: str, now: str) -> dict[str, Any]:
    return {
        "label": directory()["label"],
        "vendors": list_vendors(category, now),
    }
