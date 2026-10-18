"""Tariff data and bills: the official sample bill, the published bills and the tariff cliffs."""

from __future__ import annotations

import itertools
from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from penjaga_api.tariffs import (
    CONFIDENCE,
    ElectricityTerms,
    NoTariffError,
    electricity_bill,
    electricity_terms,
    tariff_documents,
    water_bill,
    water_terms,
)

OCT_2026 = date(2026, 10, 9)


def d(value: str) -> Decimal:
    return Decimal(value)


def published_terms() -> (
    ElectricityTerms
):  # AFA 0 and the standing 600 kWh threshold, as the published bills assume
    return replace(electricity_terms(date(2025, 7, 15)), afa_sen=0.0, threshold_kwh=600.0)


# ---------------------------------------------------------------------- data files


def _records(doc: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for key in ("schedules", "protection_thresholds", "afa"):
        out += doc.get(key, [])
    return out


def test_every_tariff_record_has_sources_a_date_checked_confidence_and_period() -> None:
    docs = tariff_documents()
    assert set(docs) == {"tnb_domestic", "air_selangor_domestic"}
    for name, doc in docs.items():
        records = _records(doc)
        assert records, name
        for record in records:
            assert record["confidence"] in CONFIDENCE, record
            assert date.fromisoformat(record["checked"])
            start = date.fromisoformat(record["effective_from"])
            if record["effective_to"] is not None:
                assert date.fromisoformat(record["effective_to"]) >= start
            assert record["sources"], record
            for source in record["sources"]:
                assert source["url"].startswith("https://"), source
                assert source["confidence"] in CONFIDENCE


def test_afa_months_do_not_overlap() -> None:
    afa = tariff_documents()["tnb_domestic"]["afa"]
    spans = sorted((r["effective_from"], r["effective_to"]) for r in afa)
    for (_, end), (start, _) in itertools.pairwise(spans):
        assert end < start


def test_the_temporary_800_kwh_threshold_applies_only_in_its_window() -> None:
    assert electricity_terms(date(2026, 8, 31)).threshold_kwh == 600
    assert electricity_terms(date(2026, 9, 1)).threshold_kwh == 800
    assert electricity_terms(date(2026, 12, 31)).threshold_kwh == 800
    assert electricity_terms(date(2027, 1, 1)).threshold_kwh == 600


def test_an_unpublished_afa_month_uses_the_latest_and_says_so() -> None:
    terms = electricity_terms(date(2026, 11, 5))
    assert terms.afa_month == "2026-10"
    assert terms.afa_sen == pytest.approx(3.61)
    assert terms.afa_note is not None
    assert "November 2026" in terms.afa_note


def test_no_tariff_before_rp4() -> None:
    with pytest.raises(NoTariffError):
        electricity_terms(date(2025, 6, 30))
    with pytest.raises(NoTariffError):
        water_terms(date(2025, 8, 31))


# ---------------------------------------------------------------------- electricity bills


def test_the_energy_commission_sample_bill_to_the_sen() -> None:
    """ST infographic Q16: 801 kWh, October 2026 AFA +3.61 sen, threshold 800 kWh."""
    bill = electricity_bill(801, electricity_terms(OCT_2026))
    assert bill.lines == {
        "energy": d("216.51"),
        "afa": d("28.92"),
        "capacity": d("36.45"),
        "network": d("102.93"),
        "eei": d("-20.03"),
        "retail": d("10.00"),
    }
    assert bill.sst == d("0.84")
    assert bill.kwtbb == d("5.37")
    assert bill.total == d("380.99")


@pytest.mark.parametrize(
    ("kwh", "total"),
    [(300, "65.79"), (600, "215.98"), (1200, "573.82"), (1500, "719.90"), (2000, "1177.78")],
)
def test_published_bills_at_afa_zero(kwh: int, total: str) -> None:
    assert electricity_bill(kwh, published_terms()).total == d(total)


def test_published_900_kwh_bill_within_a_sen() -> None:
    assert abs(electricity_bill(900, published_terms()).total - d("418.35")) <= d("0.01")


def test_at_or_below_the_threshold_there_is_no_afa_retail_or_sst() -> None:
    bill = electricity_bill(800, electricity_terms(OCT_2026))
    assert bill.protected
    assert bill.lines["afa"] == bill.lines["retail"] == bill.sst == 0


def test_kwtbb_starts_above_300_kwh_and_excludes_afa() -> None:
    terms = electricity_terms(OCT_2026)
    assert electricity_bill(300, terms).kwtbb == 0
    assert electricity_bill(301, terms).kwtbb > 0
    high = electricity_bill(1200, terms)
    base = high.lines["energy"] + high.lines["capacity"] + high.lines["network"] + high.lines["eei"]
    assert high.kwtbb == (base * d("0.016")).quantize(d("0.01"))


@pytest.mark.parametrize(
    ("before", "after", "threshold", "at_least"),
    [
        (600, 601, 600.0, "30"),  # protection threshold: AFA, retail and SST on the whole bill
        (800, 801, 800.0, "30"),  # the raised threshold, Sep to Dec 2026
        (1500, 1501, 600.0, "100"),  # 37.03 sen on every kWh above 1,500
    ],
)
def test_tariff_cliffs_cost_far_more_than_one_kwh(
    before: int, after: int, threshold: float, at_least: str
) -> None:
    terms = replace(electricity_terms(OCT_2026), threshold_kwh=threshold)
    jump = electricity_bill(after, terms).total - electricity_bill(before, terms).total
    assert jump > d(at_least)


def test_the_eei_band_applies_to_every_kwh() -> None:
    terms = electricity_terms(OCT_2026)
    assert terms.eei_sen(200) == 25.0
    assert terms.eei_sen(201) == 24.5
    assert terms.eei_sen(1000) == 0.5
    assert terms.eei_sen(1001) == 0.0
    assert electricity_bill(201, terms).lines["eei"] == d("-49.25")


# ---------------------------------------------------------------------- water bills


@pytest.mark.parametrize(
    ("m3", "total"),
    [(0, "6.50"), (10, "6.50"), (20, "13.00"), (25, "21.10"), (35, "37.30"), (40, "54.85")],
)
def test_water_blocks_and_minimum(m3: float, total: str) -> None:
    assert water_bill(m3, water_terms(OCT_2026)) == d(total)
