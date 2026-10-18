"""Cost of waiting: what a finding's physical impact adds to the monthly bill (PRD §16, §18.2.3).

Always the difference between two bills, ``bill(U + extra) - bill(U)``, never ``rate x extra``:
Malaysian tariffs have cliffs (the electricity protection threshold, the 1,500 kWh step, the EEI
bands, the water blocks), so a small extra use can cost several times the headline rate. ``U`` is
the household's own typical month, measured from its readings before the problem began.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from penjaga_api.tariffs import (
    ElectricityTerms,
    WaterTerms,
    electricity_bill,
    electricity_terms,
    sen,
    water_bill,
    water_terms,
)

DAYS_PER_MONTH = 30  # costs are quoted for a 30-day month
SPREAD_KWH = 50  # usual months vary by at least one EEI band (50 kWh) either side


@dataclass(frozen=True)
class Baseline:
    """The household's typical use per 30-day month, and how many days it was measured over."""

    water_m3: float | None
    elec_kwh: float | None
    days: float


@dataclass(frozen=True)
class CostEstimate:
    kind: str  # "water" | "electricity"
    extra_per_month: float  # m3 or kWh
    unit: str
    baseline_per_month: float
    rm_per_month: Decimal
    how: str
    threshold_note: str | None
    confidence: str
    sources: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "extra_per_month": round(self.extra_per_month, 3),
            "unit": self.unit,
            "baseline_per_month": round(self.baseline_per_month, 3),
            "rm_per_month": float(self.rm_per_month),
            "how": self.how,
            "threshold_note": self.threshold_note,
            "confidence": self.confidence,
            "sources": list(self.sources),
        }


def _rm(value: Decimal) -> str:
    return f"RM{value:,.2f}"


def _num(value: float, digits: int = 0) -> str:
    return f"{value:,.{digits}f}"


def water_cost(extra_l_per_day: float, baseline_m3: float, on: date) -> CostEstimate:
    terms = water_terms(on)
    extra = extra_l_per_day * DAYS_PER_MONTH / 1000
    before = water_bill(baseline_m3, terms)
    after = water_bill(baseline_m3 + extra, terms)
    rm = after - before
    note = _water_note(baseline_m3, baseline_m3 + extra, before, after, terms)
    how = (
        f"Your water bill with this ({_num(baseline_m3 + extra, 1)} m³ a month, {_rm(after)}) "
        f"minus your usual bill ({_num(baseline_m3, 1)} m³, {_rm(before)}), on Air Selangor's "
        f"domestic tariff. {_num(extra_l_per_day)} L a day is {_num(extra, 1)} m³ over "
        f"{DAYS_PER_MONTH} days."
    )
    return CostEstimate(
        "water", extra, "m3", baseline_m3, rm, how, note, terms.confidence, terms.sources
    )


def _water_note(
    before_m3: float, after_m3: float, before: Decimal, after: Decimal, terms: WaterTerms
) -> str | None:
    if after == before == terms.minimum:
        return (
            f"Your bill stays at the {_rm(terms.minimum)} minimum charge, so this costs nothing "
            "extra yet, but the water is still wasted."
        )
    blocks = terms.schedule["blocks"]
    for edge in reversed(terms.block_edges()):
        if before_m3 <= edge < after_m3:
            following = next(
                b["rm_per_m3"] for b in blocks if b["up_to_m3"] is None or b["up_to_m3"] > edge
            )
            return (
                f"This pushes your water use past {_num(edge)} m³ a month, into the "
                f"RM{following:.2f}/m³ block."
            )
    return None


def electricity_cost(extra_kwh_per_day: float, baseline_kwh: float, on: date) -> CostEstimate:
    """Bill difference averaged over usual months ``SPREAD_KWH`` either side of the baseline.

    TNB's charges step on every kWh at each EEI band (every 50 kWh), the protection threshold and
    1,500 kWh. A household's usage moves by more than a band from month to month, so a single
    bill difference at the estimated baseline would swing between almost nothing and several
    ringgit depending on which side of a step the estimate lands. The average of the bill
    differences across that range is the expected cost; the big steps get their own note.
    """
    terms = electricity_terms(on)
    extra = extra_kwh_per_day * DAYS_PER_MONTH
    usual = [baseline_kwh + k for k in range(-SPREAD_KWH, SPREAD_KWH + 1) if baseline_kwh + k >= 0]
    diffs = [
        electricity_bill(u + extra, terms).total - electricity_bill(u, terms).total for u in usual
    ]
    rm = sen(sum(diffs, Decimal("0")) / len(diffs))
    before = electricity_bill(baseline_kwh, terms)
    after = electricity_bill(baseline_kwh + extra, terms)
    low, high = usual[0], usual[-1]
    how = (
        f"{_num(extra_kwh_per_day, 2)} kWh a day is {_num(extra, 1)} kWh over {DAYS_PER_MONTH} "
        f"days. The cost is what that adds to your bill on TNB's domestic tariff for {on:%B %Y}, "
        f"averaged over months between {_num(low)} and {_num(high)} kWh (your usual use is about "
        f"{_num(baseline_kwh)} kWh), because the tariff steps every 50 kWh and your use changes "
        f"from month to month. At exactly your usual use it would be "
        f"{_rm(after.total - before.total)} ({_rm(before.total)} rising to {_rm(after.total)})."
    )
    if terms.afa_note and not after.protected:
        how += f" {terms.afa_note}"
    return CostEstimate(
        "electricity",
        extra,
        "kWh",
        baseline_kwh,
        rm,
        how,
        _electricity_note(baseline_kwh, extra, terms),
        terms.confidence,
        terms.sources,
    )


def _near(level: float, usual: float, extra: float) -> bool:
    """Whether ``extra`` can tip a usual month over ``level`` (usual use varies by SPREAD_KWH)."""
    return usual - SPREAD_KWH <= level < usual + extra + SPREAD_KWH and usual <= level + extra


def _electricity_note(usual: float, extra: float, terms: ElectricityTerms) -> str | None:
    s = terms.schedule
    notes = []
    p = terms.threshold_kwh
    if _near(p, usual, extra):
        notes.append(
            f"Your usual use (about {_num(usual)} kWh a month) is close to the {_num(p)} kWh "
            "protection level. In a month this tips you over it, AFA, the RM10 retail charge and "
            "service tax apply, with AFA on every kWh, so that month costs much more."
        )
    high = s["energy_high_above_kwh"]
    if _near(high, usual, extra):
        notes.append(
            f"You're close to {_num(high)} kWh a month, above which the energy rate rises for "
            "every kWh."
        )
    return " ".join(notes) or None


def estimate(quantities: dict[str, Any], baseline: Baseline, on: date) -> CostEstimate | None:
    """The monthly cost of a finding's ``quantities``, or None when it has no billable quantity
    or the household's usual use isn't known yet."""
    water = quantities.get("water_l_per_day")
    if water is not None and baseline.water_m3 is not None:
        return water_cost(float(water["value"]), baseline.water_m3, on)
    energy = quantities.get("extra_energy_kwh_per_day")
    if energy is not None and baseline.elec_kwh is not None:
        return electricity_cost(float(energy["value"]), baseline.elec_kwh, on)
    return None
