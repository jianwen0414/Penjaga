"""The illustrative vendor directory (``data/vendors.json``): fictional businesses with typical
Klang Valley price ranges, always labelled as illustrative (PRD §16). Booking them is mocked in
step 7; nothing here contacts a real business."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from functools import cache
from pathlib import Path
from typing import Any

from penjaga_api.cases import epoch_ms

DATA = Path(__file__).resolve().parent / "data" / "vendors.json"
CATEGORIES = ("plumber", "electrician", "aircon_service")

# The job a booking from each finding type is for, shown as a typical price on the action.
JOB_FOR_FINDING: dict[str, str] = {
    "night_flow_unexplained": "leak_detection",
    "dripping_fixture_suspected": "basin_tap",
    "running_cistern_suspected": "cistern_valve",
    "tank_overflow_suspected": "tank_float_valve",
    "concealed_leak_suspected": "leak_detection",
    "burst_pipe_suspected": "pipe_in_wall",
    "circuit_overload": "trip_fault_finding",
    "earth_leakage_elevated": "insulation_test",
    "earth_leakage_rising": "insulation_test",
    "protective_device_trip": "trip_fault_finding",
    "repeated_trips": "trip_fault_finding",
    "aircon_efficiency_decline": "aircon_chemical",
}

# Visit slots offered: the next three days at these local hours (illustrative availability).
SLOT_HOURS = ((10, 15), (11, 16), (9, 14))


@cache
def directory() -> dict[str, Any]:
    doc: dict[str, Any] = json.loads(DATA.read_text(encoding="utf-8"))
    return doc


def job(job_id: str) -> dict[str, Any]:
    found: dict[str, Any] = directory()["jobs"][job_id]
    return found


def job_price(finding_type: str) -> str | None:
    """The typical price of the job a finding type's booking is for, e.g. "Cistern flush or fill
    valve · typically RM100 to 250"."""
    job_id = JOB_FOR_FINDING.get(finding_type)
    if job_id is None:
        return None
    j = job(job_id)
    return f"{j['label']} · typically {j['range']}"


def list_vendors(category: str, now: str) -> list[dict[str, Any]]:
    """Vendors of ``category`` in the app's ``Vendor`` shape, with visit slots after ``now``
    (house time)."""
    doc = directory()
    if category not in CATEGORIES:
        raise KeyError(f"unknown vendor category {category!r}")
    start = datetime.fromisoformat(now).replace(hour=0, minute=0, second=0, microsecond=0)
    out = []
    for i, v in enumerate(v for v in doc["vendors"] if v["category"] == category):
        hours = SLOT_HOURS[i % len(SLOT_HOURS)]
        slots = [
            epoch_ms((start + timedelta(days=1 + d // 2, hours=hours[d % 2])).isoformat())
            for d in range(3)
        ]
        headline = job(v["headline_job"])
        out.append(
            {
                "id": v["id"],
                "name": v["name"],
                "category": category,
                "rating": v["rating"],
                "jobs": v["jobs"],
                "price": f"{headline['range']} · {headline['label'].lower()}",
                "licence": doc["licences"][category],
                "area": v["area"],
                "next_slots": slots,
            }
        )
    return out
