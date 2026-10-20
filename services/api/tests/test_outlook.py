"""``GET /api/v1/outlook``: each open case's own measurements, carried forward for the Lab's
"what if they wait?" view (operator only); a case with nothing that adds up says why."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from penjaga_api.api import create_app
from penjaga_api.service import ApiService, Role
from penjaga_api.store import ApiStore
from penjaga_api.testing import FakeUpstream, finding

TOKENS = {
    Role.homeowner: "homeowner-token-0123456789",
    Role.management: "management-token-0123456789",
    Role.operator: "operator-token-0123456789",
}


def _h(role: Role) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[role]}"}


class Harness:
    def __init__(self, tmp_path: Path) -> None:
        self.up = FakeUpstream(episode_start="2025-11-01T00:00:00+08:00")
        self.up.house_time = "2025-11-03T12:00:00+08:00"
        self.store = ApiStore(tmp_path / "api.sqlite")
        self.service = ApiService(self.store, self.up)
        self.client = TestClient(create_app(self.service, tokens=TOKENS, autostart=False))

    def follow(self, *docs: dict[str, Any]) -> None:
        for doc in docs:
            self.up.emit(doc)

        async def run() -> None:
            async def end_soon() -> None:
                await asyncio.sleep(0.05)
                self.up.end_streams()

            await asyncio.gather(self.service.follower.sync(), end_soon())

        asyncio.run(run())

    def outlook(self) -> dict[str, dict[str, Any]]:
        r = self.client.get("/api/v1/outlook", headers=_h(Role.operator))
        assert r.status_code == 200, r.text
        return {o["finding_type"]: o for o in r.json()}


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    harness = Harness(tmp_path)
    yield harness
    harness.store.close()


def test_only_the_lab_sees_it(h: Harness) -> None:
    h.follow(finding("running_cistern_suspected"))
    for role in (Role.homeowner, Role.management):
        assert h.client.get("/api/v1/outlook", headers=_h(role)).status_code == 403
    assert h.client.get("/api/v1/outlook").status_code == 401
    assert h.client.get("/api/v1/outlook", headers=_h(Role.operator)).status_code == 200


def test_a_leak_carries_its_litres_a_day_and_its_cost(h: Harness) -> None:
    h.follow(finding("running_cistern_suspected"))
    o = h.outlook()["running_cistern_suspected"]
    assert o["kind"] == "water"
    assert o["projectable"] is True
    assert o["water"] == {"litres_per_day": 605.0}
    assert o["horizon"] == {"unit": "day", "max": 365}
    # the same cost of waiting the case shows: a figure, or the reason there isn't one yet
    assert o["cost"]["rm_per_month"] is not None or o["cost"]["why_not"]
    assert o["focus"]["object_ids"]
    assert o["case_id"].startswith("case_")


def test_rising_leakage_reaches_the_safety_switch_levels_along_its_trend(h: Harness) -> None:
    evidence = [
        {"name": "daily_median_residual", "value": 9.0, "unit": "mA"},
        {"name": "trend", "value": 0.3, "unit": "mA/day"},
        {"name": "days_to_non_operating_limit", "value": 20.0, "unit": "day"},
        {"name": "rated_residual", "value": 30, "unit": "mA"},
    ]
    h.follow(finding("earth_leakage_rising", evidence=evidence))
    o = h.outlook()["earth_leakage_rising"]
    assert o["kind"] == "leakage"
    assert o["projectable"] is True
    leak = o["leakage"]
    assert leak["now_ma"] == 9.0
    assert leak["limit_ma"] == 15.0  # half the rating: may trip from here
    assert leak["rated_ma"] == 30.0
    assert leak["days_to_limit"] == pytest.approx(20.0)  # agrees with detection's own figure
    assert leak["days_to_rated"] == pytest.approx(70.0)
    assert o["horizon"]["max"] == 98  # 70 days and a margin


def test_leakage_that_stopped_rising_has_nothing_to_carry(h: Harness) -> None:
    evidence = [
        {"name": "daily_median_residual", "value": 9.0, "unit": "mA"},
        {"name": "trend", "value": -0.1, "unit": "mA/day"},
        {"name": "rated_residual", "value": 30, "unit": "mA"},
    ]
    h.follow(finding("earth_leakage_rising", evidence=evidence))
    o = h.outlook()["earth_leakage_rising"]
    assert o["projectable"] is False
    assert o["leakage"]["days_to_rated"] is None


def test_leakage_seen_rising_without_a_rate_is_not_projected(h: Harness) -> None:
    # the baseline detector (CUSUM) says the leakage rose, but not how fast: no rate, no projection
    evidence = [
        {"name": "baseline_direct_leakage", "value": 1.9, "unit": "mA"},
        {"name": "recent_direct_leakage", "value": 2.1, "unit": "mA"},
        {"name": "cusum", "value": 8.0, "threshold": 4.0},
        {"name": "rated_residual", "value": 30.0, "unit": "mA"},
    ]
    h.follow(finding("earth_leakage_rising", evidence=evidence))
    o = h.outlook()["earth_leakage_rising"]
    assert o["kind"] == "leakage"
    assert o["projectable"] is False
    assert "from 1.9 to 2.1 mA" in o["why_not"]
    assert "leakage" not in o


def test_an_appliance_carries_its_extra_energy_and_the_gaps_growth(h: Harness) -> None:
    quantities = {
        "efficiency_gap_pct": {"value": 18.0, "unit": "%"},
        "extra_energy_kwh_per_day": {"value": 0.6, "unit": "kWh/day"},
        "efficiency_gap_trend_pct_per_week": {"value": 2.5, "unit": "%/week"},
    }
    h.follow(finding("aircon_efficiency_decline", quantities=quantities))
    o = h.outlook()["aircon_efficiency_decline"]
    assert o["kind"] == "energy"
    assert o["energy"] == {"kwh_per_day": 0.6, "gap_pct": 18.0, "gap_trend_pct_per_week": 2.5}
    assert "growth" in o["basis"]


def test_a_supply_cut_carries_the_tank(h: Harness) -> None:
    quantities = {
        "tank_water_l": {"value": 900.0, "unit": "L"},
        "tank_hours_left": {"value": 7.5, "unit": "h"},
    }
    h.follow(finding("water_supply_interruption", quantities=quantities))
    o = h.outlook()["water_supply_interruption"]
    assert o["kind"] == "tank"
    assert o["tank"] == {"litres": 900.0, "hours_left": 7.5}
    assert o["horizon"] == {"unit": "hour", "max": 10}


def test_an_overload_is_a_risk_now_with_nothing_to_project(h: Harness) -> None:
    h.follow(finding("circuit_overload"))
    o = h.outlook()["circuit_overload"]
    assert o["kind"] is None
    assert o["projectable"] is False
    assert "risk right now" in o["why_not"]


def test_resolved_cases_are_left_out(h: Harness) -> None:
    h.follow(
        finding(
            "running_cistern_suspected", status="cleared", updated_at="2025-11-03T06:00:00+08:00"
        )
    )
    assert h.outlook() == {}
