"""Cost of waiting, the knowledge tools, safety sources and the illustrative vendor directory."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest

from penjaga_api.cases import build_cases
from penjaga_api.costs import Baseline, electricity_cost, estimate, water_cost
from penjaga_api.knowledge import (
    estimate_cost,
    get_house_record,
    get_safety_text,
    get_tariff,
    get_vendors,
)
from penjaga_api.names import Names
from penjaga_api.safety import guidance_keys, safety_text
from penjaga_api.templates import render
from penjaga_api.testing import finding
from penjaga_api.vendors import CATEGORIES, JOB_FOR_FINDING, directory, job
from penjaga_contract.validate import load_context

INDEX = load_context().index()
OCT = date(2026, 10, 20)
NOW = "2026-10-20T12:00:00+08:00"


# ---------------------------------------------------------------------- costs


def test_water_cost_is_a_bill_difference_not_rate_times_volume() -> None:
    cost = water_cost(1000.0, 15.0, OCT)  # 30 m3 extra on top of 15 m3
    # 45 m3 = 13.00 + 15 x 1.62 + 10 x 3.51 = RM72.40; 15 m3 = RM9.75
    assert cost.rm_per_month == Decimal("62.65")
    assert cost.extra_per_month == pytest.approx(30.0)
    assert "35 m³" in (cost.threshold_note or "")
    assert cost.confidence == "official"
    assert "minus your usual bill" in cost.how


def test_a_leak_under_the_minimum_charge_costs_nothing_yet() -> None:
    cost = water_cost(50.0, 3.0, OCT)  # 4.5 m3 a month in all: still the RM6.50 minimum
    assert cost.rm_per_month == 0
    assert "minimum charge" in (cost.threshold_note or "")


def test_electricity_cost_near_the_protection_level_says_so() -> None:
    near = electricity_cost(1.0, 790.0, OCT)
    far = electricity_cost(1.0, 400.0, OCT)
    assert near.rm_per_month > far.rm_per_month
    assert "800 kWh protection level" in (near.threshold_note or "")
    assert far.threshold_note is None


def test_a_tiny_appliance_rise_costs_little_wherever_the_baseline_falls() -> None:
    """Averaging over usual months keeps an EEI band edge from turning sen into ringgit."""
    for baseline in (399.0, 400.0, 401.0, 449.9):
        assert electricity_cost(0.011, baseline, OCT).rm_per_month < Decimal("1.00")


def test_estimate_needs_a_billable_quantity_and_a_baseline() -> None:
    known = Baseline(water_m3=15.0, elec_kwh=400.0, days=10)
    unknown = Baseline(water_m3=None, elec_kwh=None, days=0.2)
    water = {"water_l_per_day": {"value": 500.0, "unit": "L/day"}}
    assert estimate(water, known, OCT) is not None
    assert estimate(water, unknown, OCT) is None
    assert estimate({"tank_water_l": {"value": 900.0, "unit": "L"}}, known, OCT) is None
    as_tool = estimate_cost(water, known, OCT)
    assert as_tool is not None
    assert json.dumps(as_tool)  # plain JSON for the agent
    assert as_tool["sources"]


def test_a_case_with_a_cost_shows_ringgit_and_how_it_was_worked_out() -> None:
    doc = finding("running_cistern_suspected")
    (state,) = build_cases("run", [doc], [], INDEX)
    cost = water_cost(605.0, 15.0, OCT)
    case = render(state, INDEX, Names(INDEX), NOW, {doc["id"]: cost}).case
    assert f"RM{cost.rm_per_month:,.2f} a month" in case["impact"]["headline"]
    assert case["impact"]["lines"][0].startswith("Over a year: about RM")
    assert "minus your usual bill" in case["impact"]["how"]
    without = render(state, INDEX, Names(INDEX), NOW).case
    assert "RM" not in without["impact"]["headline"]
    assert "at least a day" in without["impact"]["how"]


def test_book_vendor_actions_show_a_typical_price() -> None:
    (state,) = build_cases("run", [finding("running_cistern_suspected")], [], INDEX)
    case = render(state, INDEX, Names(INDEX), NOW).case
    (book,) = [a for a in case["actions"] if a["kind"] == "book_vendor"]
    assert book["detail"] == (
        f"Cistern flush or fill valve · typically {job('cistern_valve')['range']}"
    )


def test_an_aircon_case_offers_an_aircon_service() -> None:
    (state,) = build_cases("run", [finding("aircon_efficiency_decline")], [], INDEX)
    case = render(state, INDEX, Names(INDEX), NOW).case
    (book,) = [a for a in case["actions"] if a["kind"] == "book_vendor"]
    assert book["vendor_category"] == "aircon_service"
    assert book["primary"] is True


# ---------------------------------------------------------------------- knowledge tools


def test_house_record_excerpts_are_validated() -> None:
    record = get_house_record(INDEX, ["app_ac_master", "circ_ac_master"])
    assert record["app_ac_master"]["kind"] == "appliance"
    assert record["app_ac_master"]["rooms"] == ["room_bed_master"]
    with pytest.raises(ValueError, match="not in the house contract"):
        get_house_record(INDEX, ["app_ac_garage"])


def test_tariff_tool_names_its_sources() -> None:
    electricity = get_tariff("electricity", OCT)
    assert electricity["protection_threshold_kwh"] == 800
    assert electricity["sources"]
    assert get_tariff("water", OCT)["schedule"]["minimum_rm_per_month"] == 6.5
    with pytest.raises(KeyError):
        get_tariff("gas", OCT)


def test_every_safety_line_has_a_source_and_unsourced_ones_say_so() -> None:
    assert guidance_keys()
    for key in guidance_keys():
        lines = get_safety_text(key)["lines"]
        assert lines == safety_text(key)
        for line in lines:
            assert line["source"]
            if line["url"] is None:
                assert line["confidence"] == "unconfirmed"
                assert "own wording" in line["source"] or "trade practice" in line["source"]
            else:
                assert line["url"].startswith("https://")


def test_repeated_trips_guidance_includes_the_monthly_rcd_test() -> None:
    texts = [line["text"] for line in safety_text("sg_repeated_trips")]
    assert any("once a month" in t and "'T'" in t for t in texts)


# ---------------------------------------------------------------------- vendors


def test_vendors_carry_the_right_licences_and_a_quote_reminder() -> None:
    doc = directory()
    assert "written quote" in doc["label"]
    assert doc["licences"]["electrician"].startswith("ST-registered electrical contractor")
    assert doc["licences"]["plumber"].startswith("SPAN-registered plumber")
    for category in CATEGORIES:
        listed = get_vendors(category, NOW)
        assert listed["vendors"], category
        for v in listed["vendors"]:
            assert v["licence"] == doc["licences"][category]
            assert len(v["next_slots"]) == 3
            assert all(slot > 0 for slot in v["next_slots"])


def test_every_job_has_a_range_a_confidence_and_a_source() -> None:
    for job_id in {*JOB_FOR_FINDING.values(), *directory()["jobs"]}:
        j = job(job_id)
        assert j["range"].startswith(("RM", "about RM"))
        assert j["confidence"] in ("official", "secondary", "unconfirmed")
        assert j["sources"]


def test_unknown_vendor_category() -> None:
    with pytest.raises(KeyError):
        get_vendors("roofer", NOW)


def test_a_case_says_why_it_has_no_ringgit_figure() -> None:
    doc = finding("running_cistern_suspected")
    (state,) = build_cases("run", [doc], [], INDEX)
    reason = "There's no official tariff on record for August 2025, so no ringgit figure."
    case = render(state, INDEX, Names(INDEX), NOW, {doc["id"]: reason}).case
    assert case["impact"]["how"].endswith(
        f"{reason} Average use per person: SPAN (2023), via Air Selangor."
    )
    assert "at least a day" not in case["impact"]["how"]
