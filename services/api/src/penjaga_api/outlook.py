"""What waiting would do: a case's own measurements carried forward (the Lab's "what if").

Only what the lead finding measured, at the rate measured (or along the trend measured): never a
guess about damage or failure. Each case says which of these it is:

- ``water``: litres a day (``quantities.water_l_per_day``) and its cost of waiting (``costs.py``,
  the bill difference on the household's own usual month): after ``d`` days, ``d`` times the
  litres a day and ``d/30`` times the ringgit a month;
- ``energy``: extra kWh a day and its cost; when detection measured the efficiency gap growing
  (``efficiency_gap_trend_pct_per_week``), the gap (and so the extra energy) grows along it;
- ``leakage`` (``earth_leakage_rising``): today's daily median leakage and its measured trend
  (mA/day), with the safety switch's two levels: half its rating, the non-operating limit above
  which it may trip at any moment (IEC 61008-1), and its rating, where it trips;
- ``tank`` (a water supply cut): litres left in the roof tank and the hours they last at the
  current draw.

Cases with nothing to carry forward (an overload, repeated trips: a risk now, not something that
accumulates) say why instead. Operator only: a Lab view, not something the homeowner app shows.
"""

from __future__ import annotations

from typing import Any

from penjaga_api.cases import CaseState
from penjaga_api.costs import CostEstimate

# of the rated residual current: IEC 61008-1's non-operating limit (detection uses the same)
NON_OPERATING = 0.5
ACTIVE = frozenset({"open", "monitoring", "action_pending"})


def _evidence(doc: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for item in doc.get("evidence", []):
        value = item.get("value")
        if isinstance(value, int | float) and not isinstance(value, bool):
            out[str(item["name"])] = float(value)
    return out


def _qty(doc: dict[str, Any], name: str) -> float | None:
    item = doc.get("quantities", {}).get(name)
    return None if item is None else float(item["value"])


def _cost(cost: CostEstimate | str | None) -> dict[str, Any]:
    if isinstance(cost, CostEstimate):
        return {
            "rm_per_month": float(cost.rm_per_month),
            "how": cost.how,
            "note": cost.threshold_note,
            "confidence": cost.confidence,
        }
    return {"rm_per_month": None, "why_not": cost or "No ringgit figure yet."}


def outlook(
    state: CaseState, case: dict[str, Any], cost: CostEstimate | str | None
) -> dict[str, Any]:
    """The projection inputs for one case (``case`` is its rendered app document)."""
    lead = state.lead
    base: dict[str, Any] = {
        "case_id": case["id"],
        "title": case["title"],
        "location": case["location"],
        "layer": case["layer"],
        "severity": case["severity"],
        "status": case["status"],
        "focus": case["focus"],
        "finding_type": lead["type"],
        "measured_at": lead.get("updated_at") or lead.get("first_seen"),
    }
    ev = _evidence(lead)
    if case.get("contained"):
        return {
            **base,
            "kind": None,
            "projectable": False,
            "why_not": "The owner shut the supply off: nothing more is lost while it stays off, "
            "so there is nothing to carry forward until it is turned back on.",
        }
    # rising leakage: the case may hold findings from both L2 detectors; only the trend detector
    # measures a rate (mA a day), and only a rate can be carried forward
    rising = [t.latest for t in state.active_tracks if t.latest["type"] == "earth_leakage_rising"]
    trended = next(
        (e for e in map(_evidence, rising) if {"daily_median_residual", "trend"} <= set(e)), None
    )
    if rising and trended is None:
        seen = _evidence(rising[0])
        before, after = seen.get("baseline_direct_leakage"), seen.get("recent_direct_leakage")
        rose = f" (from {before:g} to {after:g} mA)" if before is not None and after else ""
        return {
            **base,
            "kind": "leakage",
            "projectable": False,
            "why_not": f"Detection saw the leakage rise{rose} but hasn't measured how fast yet, so "
            "there is no rate to carry forward. It will once the daily trend is established.",
        }
    if trended is not None:
        ev = trended
        rated = ev.get("rated_residual", 30.0)
        now, slope = ev["daily_median_residual"], ev["trend"]
        limit = NON_OPERATING * rated

        def days_to(level: float) -> float | None:
            return None if slope <= 0 else max(0.0, (level - now) / slope)

        to_rated = days_to(rated)
        return {
            **base,
            "kind": "leakage",
            "projectable": slope > 0,
            "why_not": None if slope > 0 else "The leakage isn't rising any more.",
            "leakage": {
                "now_ma": now,
                "trend_ma_per_day": slope,
                "limit_ma": limit,
                "rated_ma": rated,
                "days_to_limit": days_to(limit),
                "days_to_rated": to_rated,
            },
            "horizon": {"unit": "day", "max": min(365, max(14, round((to_rated or 60) * 1.4)))},
            "basis": "Today's leakage carried forward along the trend detection measured.",
        }
    tank_l, tank_h = _qty(lead, "tank_water_l"), _qty(lead, "tank_hours_left")
    if tank_l is not None and tank_h is not None:
        return {
            **base,
            "kind": "tank",
            "projectable": tank_h > 0,
            "why_not": None if tank_h > 0 else "The roof tank is already empty.",
            "tank": {"litres": tank_l, "hours_left": tank_h},
            "horizon": {"unit": "hour", "max": max(2, round(tank_h * 1.3))},
            "basis": "The roof tank's water at the household's current draw, as measured.",
        }
    litres = _qty(lead, "water_l_per_day")
    if litres is not None:
        return {
            **base,
            "kind": "water",
            "projectable": litres > 0,
            "why_not": None if litres > 0 else "No water is being lost now.",
            "water": {"litres_per_day": litres},
            "cost": _cost(cost),
            "horizon": {"unit": "day", "max": 365},
            "basis": "The flow measured at your meter, carried forward at the same rate.",
        }
    kwh = _qty(lead, "extra_energy_kwh_per_day")
    if kwh is not None:
        return {
            **base,
            "kind": "energy",
            "projectable": kwh > 0,
            "why_not": None if kwh > 0 else "No extra energy is being used now.",
            "energy": {
                "kwh_per_day": kwh,
                "gap_pct": _qty(lead, "efficiency_gap_pct"),
                "gap_trend_pct_per_week": _qty(lead, "efficiency_gap_trend_pct_per_week"),
            },
            "cost": _cost(cost),
            "horizon": {"unit": "day", "max": 365},
            "basis": "The extra energy measured, carried forward"
            + (
                " along the measured growth of the gap."
                if _qty(lead, "efficiency_gap_trend_pct_per_week")
                else " at the same rate."
            ),
        }
    return {
        **base,
        "kind": None,
        "projectable": False,
        "why_not": (
            "This is a risk right now, not something that adds up over time: "
            "there is nothing to carry forward."
        ),
    }
