"""Official tariffs as versioned data (``data/tariffs/*.json``) and the bills they produce.

Every record carries ``effective_from``/``effective_to``, a ``confidence`` (``official`` |
``secondary`` | ``unconfirmed``), the date it was ``checked`` and its sources; nothing date-bound
lives in code (PRD §16). The bill functions are plain arithmetic: the LLM never does tariff maths.

Electricity follows the Energy Commission's sample bill (ST infographic Q16, RM380.99 for 801 kWh):
above the protection threshold AFA applies to every kWh, SST applies to the share above the
threshold plus the retail charge, and the KWTBB levy applies to energy + capacity + network less
the EEI (not AFA, not the retail charge). Each bill line is rounded to the sen.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from functools import cache
from pathlib import Path
from typing import Any

DATA = Path(__file__).resolve().parent / "data" / "tariffs"
CONFIDENCE = ("official", "secondary", "unconfirmed")  # best first


class NoTariffError(LookupError):
    """No tariff record covers the requested date."""


def sen(value: float | Decimal) -> Decimal:
    """Round to the sen, half up, as bills do."""
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def weakest(levels: list[str]) -> str:
    """The least certain of several confidence levels."""
    return max(levels, key=CONFIDENCE.index) if levels else "unconfirmed"


@cache
def _load(name: str) -> dict[str, Any]:
    doc: dict[str, Any] = json.loads((DATA / f"{name}.json").read_text(encoding="utf-8"))
    return doc


def tariff_documents() -> dict[str, dict[str, Any]]:
    return {path.stem: _load(path.stem) for path in sorted(DATA.glob("*.json"))}


def _covering(records: list[dict[str, Any]], on: date) -> dict[str, Any] | None:
    """The record in force on ``on``; when several are, the one that started last (a temporary
    rule overrides the standing one)."""
    live = [
        r
        for r in records
        if date.fromisoformat(r["effective_from"]) <= on
        and (r["effective_to"] is None or on <= date.fromisoformat(r["effective_to"]))
    ]
    return max(live, key=lambda r: r["effective_from"]) if live else None


# ---------------------------------------------------------------------- electricity


@dataclass(frozen=True)
class ElectricityTerms:
    """Everything that sets a TNB domestic bill on one date."""

    on: date
    schedule: dict[str, Any]
    threshold_kwh: float
    afa_sen: float
    afa_month: str  # the month whose AFA is used (may be earlier than ``on``; see afa_note)
    afa_note: str | None
    confidence: str
    sources: tuple[str, ...]

    def eei_sen(self, kwh: float) -> float:
        for band in self.schedule["eei_bands"]:
            if kwh <= band["up_to_kwh"]:
                return float(band["sen_per_kwh"])
        return 0.0

    def energy_sen(self, kwh: float) -> float:
        s = self.schedule
        high = kwh > s["energy_high_above_kwh"]
        return float(s["energy_high_sen_per_kwh"] if high else s["energy_sen_per_kwh"])


def electricity_terms(on: date) -> ElectricityTerms:
    doc = _load("tnb_domestic")
    schedule = _covering(doc["schedules"], on)
    threshold = _covering(doc["protection_thresholds"], on)
    if schedule is None or threshold is None:
        raise NoTariffError(f"no TNB domestic tariff in force on {on}")
    afa = _covering(doc["afa"], on)
    note = None
    if afa is None:
        # Not yet published (rates come out monthly): use the latest earlier month, and say so.
        earlier = [r for r in doc["afa"] if date.fromisoformat(r["effective_from"]) <= on]
        if not earlier:
            raise NoTariffError(f"no AFA rate on or before {on}")
        afa = max(earlier, key=lambda r: r["effective_from"])
        note = (
            f"The AFA rate for {on:%B %Y} isn't published yet, so this uses "
            f"{date.fromisoformat(afa['effective_from']):%B %Y}'s."
        )
    records = [schedule, threshold, afa]
    return ElectricityTerms(
        on=on,
        schedule=schedule,
        threshold_kwh=float(threshold["kwh"]),
        afa_sen=float(afa["sen_per_kwh"]),
        afa_month=str(afa["effective_from"])[:7],
        afa_note=note,
        confidence=weakest([r["confidence"] for r in records]),
        sources=tuple(dict.fromkeys(s["url"] for r in records for s in r["sources"])),
    )


@dataclass(frozen=True)
class ElectricityBill:
    kwh: float
    lines: dict[str, Decimal]  # energy, afa, capacity, network, retail, eei (negative)
    sst: Decimal
    kwtbb: Decimal
    total: Decimal
    protected: bool  # at or below the protection threshold


def electricity_bill(kwh: float, terms: ElectricityTerms) -> ElectricityBill:
    """One month's TNB domestic bill for ``kwh``, line by line as on the bill."""
    s = terms.schedule
    protected = kwh <= terms.threshold_kwh
    rates = {
        "energy": terms.energy_sen(kwh),
        "afa": 0.0 if protected else terms.afa_sen,
        "capacity": float(s["capacity_sen_per_kwh"]),
        "network": float(s["network_sen_per_kwh"]),
        "eei": -terms.eei_sen(kwh),
    }
    lines = {name: sen(kwh * rate / 100) for name, rate in rates.items()}
    retail = Decimal("0.00") if protected else sen(s["retail_rm_per_month"])
    lines["retail"] = retail
    charges = sum(lines.values(), Decimal("0"))
    sst = Decimal("0.00")
    if not protected:
        taxed_kwh = kwh - terms.threshold_kwh
        taxed = sum((sen(taxed_kwh * rate / 100) for rate in rates.values()), Decimal("0"))
        sst = sen((taxed + retail) * Decimal(str(s["sst_rate"])))
    kwtbb = Decimal("0.00")
    if kwh > s["kwtbb_exempt_up_to_kwh"]:
        base = lines["energy"] + lines["capacity"] + lines["network"] + lines["eei"]
        kwtbb = sen(base * Decimal(str(s["kwtbb_rate"])))
    return ElectricityBill(kwh, lines, sst, kwtbb, charges + sst + kwtbb, protected)


# ---------------------------------------------------------------------- water


@dataclass(frozen=True)
class WaterTerms:
    on: date
    schedule: dict[str, Any]
    confidence: str
    sources: tuple[str, ...] = field(default=())

    @property
    def minimum(self) -> Decimal:
        return sen(self.schedule["minimum_rm_per_month"])

    def block_edges(self) -> list[float]:
        return [float(b["up_to_m3"]) for b in self.schedule["blocks"] if b["up_to_m3"] is not None]


def water_terms(on: date) -> WaterTerms:
    schedule = _covering(_load("air_selangor_domestic")["schedules"], on)
    if schedule is None:
        raise NoTariffError(f"no Air Selangor domestic tariff in force on {on}")
    return WaterTerms(
        on=on,
        schedule=schedule,
        confidence=str(schedule["confidence"]),
        sources=tuple(s["url"] for s in schedule["sources"]),
    )


def water_bill(m3: float, terms: WaterTerms) -> Decimal:
    """One month's Air Selangor domestic bill for ``m3`` (progressive blocks, minimum charge)."""
    cost = Decimal("0")
    lower = 0.0
    for block in terms.schedule["blocks"]:
        upper = block["up_to_m3"]
        top = m3 if upper is None else min(m3, float(upper))
        if top > lower:
            cost += Decimal(str(top - lower)) * Decimal(str(block["rm_per_m3"]))
        if upper is None or m3 <= upper:
            break
        lower = float(upper)
    return max(sen(cost), terms.minimum)
