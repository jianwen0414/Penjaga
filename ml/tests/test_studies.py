"""Calibration, localisation after guided checks and the roof-tank outlook (M11 studies)."""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from penjaga_contract.validate import load_context
from penjaga_detect.detector import HouseContext
from penjaga_detect.localise import (
    Likelihood,
    LocaliseParams,
    check_options,
    direct_circuits,
    hypotheses,
)
from penjaga_eval import calibration, episodes, localisation, outlook
from penjaga_eval.localisation import CheckPlan, Task, first_slot
from penjaga_eval.scoring import TruthFault, load_truth
from penjaga_eval.suites import Suite, load_suite, manifests
from penjaga_sim.actions import isolation

US = 1_000_000

# ---------------------------------------------------------------------------- calibration


def test_a_calibrated_forecast_has_a_small_ece_and_an_overconfident_one_does_not() -> None:
    r = np.random.default_rng(0)
    p = r.uniform(0, 1, 20_000)
    y = (r.uniform(0, 1, p.size) < p).astype(np.float64)
    assert calibration.ece(p, y) < 0.02
    sharp = np.where(p > 0.5, 1.0, 0.0) * 0.98 + 0.01
    assert calibration.ece(sharp, y) > 0.2
    assert calibration.brier_skill(np.full(p.size, y.mean()), y) == pytest.approx(0.0)
    skill = calibration.brier_skill(p, y)
    assert skill is not None
    assert skill > 0.2


def test_reliability_bins_cover_every_night() -> None:
    p = np.linspace(0, 1, 101)
    y = (p > 0.5).astype(np.float64)
    for equal_mass in (False, True):
        table = calibration.reliability(p, y, equal_mass=equal_mass)
        assert sum(b["n"] for b in table) == p.size
        assert all(b["p_low"] <= b["mean_predicted"] <= b["p_high"] for b in table)


def test_calibration_metrics_from_class_probabilities() -> None:
    proba = np.array([[0.9, 0.1], [0.2, 0.8], [0.6, 0.4], [0.1, 0.9]])
    groups = np.arange(4, dtype=np.int64)
    m = calibration.metrics(proba, ["none", "W2"], ["none", "W2", "W2", "W2"], groups)
    assert m["nights"] == 4
    assert m["leak_nights"] == 3
    assert m["top_label_accuracy"] == pytest.approx(0.75)
    assert json.loads(json.dumps(m)) == m


# ---------------------------------------------------------------------------- localisation


def test_detection_and_the_simulator_agree_on_what_a_valve_isolates() -> None:
    index = HouseContext.canonical().index
    known = set(hypotheses(index))
    for option in check_options(index):
        iso = isolation(index, option.valve_id)
        if option.kind == "flow_stop":
            assert option.behind == iso.elements & known, option.key
        else:
            assert iso.stops_inlet, option.key


def _us(moment: datetime) -> int:
    return int(moment.timestamp()) * US


def test_checks_start_on_the_first_evening_after_the_finding() -> None:
    zone = HouseContext.canonical().zone
    plan = CheckPlan()
    morning = _us(datetime(2026, 10, 2, 5, 0, tzinfo=zone))
    late = _us(datetime(2026, 10, 2, 18, 45, tzinfo=zone))
    assert first_slot(morning, zone, plan) == _us(datetime(2026, 10, 2, 19, tzinfo=zone))
    assert first_slot(late, zone, plan) == _us(datetime(2026, 10, 3, 19, tzinfo=zone))


@pytest.fixture(scope="module")
def cistern_episode(tiny: Suite, episodes_root: Path) -> tuple[Path, TruthFault]:
    paths = episodes.ensure(manifests(tiny, "validation"), episodes_root, workers=1)
    for path in paths:
        faults, _, _ = load_truth(path)
        for f in faults:
            if f.fault_type == "W2":
                return path, f
    raise AssertionError("the tiny suite has a W2 episode")


def test_every_check_on_a_running_cistern(cistern_episode: tuple[Path, TruthFault]) -> None:
    path, fault = cistern_episode
    task = Task(str(path), fault.fault_type, fault.object_ids[0], fault.start_us)
    record = localisation._run((task, Likelihood.default().as_dict(), {}, {}))
    assert len(record["checks"]) == len(check_options(HouseContext.canonical().index))
    valves = [c for c in record["checks"] if c["behind"] and c["kind"] == "flow_stop"]
    assert len(valves) == 2  # the cistern's angle valve and its bathroom's branch valve
    # a cistern leak stops, or at least falls, behind its closed valve
    # (idle: the meter was still before closing, as between a cistern's phantom refills)
    assert all(c["verdict"] in ("stopped", "reduced", "idle") for c in valves)


def test_fitting_writes_a_likelihood_from_validation(
    tiny: Suite, episodes_root: Path, tmp_path: Path
) -> None:
    out = tmp_path / "l1_localiser.json"
    doc = localisation.fit([tiny], episodes_root=episodes_root, workers=1, out=out)
    assert doc["fitted_on"]["split"] == "validation"
    lik, plan, source = localisation.load_likelihood(out)
    assert plan == CheckPlan()
    assert source == out.as_posix()
    verdicts = ("stopped", "reduced", "unchanged", "idle", "unavailable")
    assert sum(lik.p("flow_stop", True, v) for v in verdicts) == pytest.approx(1.0)


def test_localisation_report(tiny: Suite, episodes_root: Path, tmp_path: Path) -> None:
    report = localisation.evaluate(
        tiny,
        "validation",
        ["l1_mnf_baseline"],
        episodes_root=episodes_root,
        workers=1,
        likelihood_path=tmp_path / "absent.json",
        params=LocaliseParams(max_checks=3),
    )
    d = report["detectors"]["l1_mnf_baseline"]
    assert d["all"]["n"] >= 1
    assert d["all"]["checks_mean"] <= 3
    assert report["likelihood"] == "default (not fitted)"
    assert "| all |" in localisation.render(report)


# ---------------------------------------------------------------------------- tank outlook

TANK = """
name = "tank"
layers = ["L5"]
days = 4
fault_types = ["U1"]
fault_start_days = [2.5, 2.8]
anomaly_prob = 0.0
anomaly_types = []
anomaly_days = [1, 1]
sensors = ["sensor_water_meter_main", "sensor_water_pressure_inlet", "sensor_tank_level"]

[split.train]
years = [2023]
archetypes = ["large_family"]
episodes_per_fault_type = 1
fault_free_episodes = 0

[split.validation]
years = [2024]
archetypes = ["large_family"]
episodes_per_fault_type = 2
fault_free_episodes = 0

[split.test]
years = [2025]
archetypes = ["large_family"]
episodes_per_fault_type = 1
fault_free_episodes = 0
"""


@pytest.fixture(scope="module")
def tank_suite(tmp_path_factory: pytest.TempPathFactory) -> Suite:
    directory = tmp_path_factory.mktemp("tank_suite")
    (directory / "tank.toml").write_text(TANK, encoding="utf-8")
    return load_suite("tank", directory)


def test_outlook_is_scored_against_the_tank_truth(tank_suite: Suite, episodes_root: Path) -> None:
    cases = outlook.cases_for(
        tank_suite,
        "validation",
        {"outlook_method": "daily_mean"},
        episodes_root=episodes_root,
        workers=1,
    )
    assert len(cases) == 2
    capacity = float(load_context().house.water.tanks[0].capacity_l)
    for c in cases:
        assert 0 <= c["true_litres"] <= capacity
        assert abs(c["litres"] - c["true_litres"]) < 0.02 * capacity  # the level sensor is good
        assert c["true_rate"] is not None
    m = outlook.score(cases)
    assert m["cuts"] == 2
    assert m["coverage"] == 1.0
    assert m["rate_factor"] >= 1.0


def test_outlook_selection_writes_its_choice(
    tank_suite: Suite, episodes_root: Path, tmp_path: Path
) -> None:
    record = outlook.select(
        tank_suite,
        {"outlook_method": ["slope_30min", "daily_mean"]},
        episodes_root=episodes_root,
        workers=1,
        params_dir=tmp_path,
        min_coverage=0.0,
    )
    assert record["tuned_on"]["split"] == "validation"
    assert record["params"]["outlook_method"] in ("slope_30min", "daily_mean")
    assert len(record["candidates"]) == 2
    assert (tmp_path / "l5_supply.json").exists()


def _cut(litres: float, true_litres: float, hours: float | None, **kw: Any) -> dict[str, Any]:
    return {"litres": litres, "true_litres": true_litres, "hours": hours, **kw}


def test_outlook_score_handles_censored_and_dry_cuts() -> None:
    cases = [
        _cut(1000, 990, 20, ran_dry=False, true_hours=None, cut_hours=6, rate=50, true_rate=40),
        _cut(400, 410, 5, ran_dry=True, true_hours=8, cut_hours=12, rate=80, true_rate=50),
        _cut(300, 300, None, ran_dry=False, true_hours=None, cut_hours=3, rate=None, true_rate=30),
    ]
    m = outlook.score(cases)
    assert m["coverage"] == pytest.approx(2 / 3)
    assert m["litres_mae"] == pytest.approx(20 / 3)
    assert m["hours_mae_dry"] == pytest.approx(3.0)
    assert m["hours_rel_error_dry"] == pytest.approx(3 / 8)
    assert m["hours_late_dry"] == 0.0  # 5 h said, 8 h true: it ran out later than said
    assert m["censored_consistent"] == 1.0
    assert m["rate_factor"] == pytest.approx(np.exp(np.median([np.log(50 / 40), np.log(80 / 50)])))


# ---------------------------------------------------------------------------- earth leakage (L2)

LEAK = """
name = "leak"
layers = ["L2"]
days = 3
fault_types = ["E2"]
fault_start_days = [0.3, 0.5]
anomaly_prob = 0.0
anomaly_types = []
anomaly_days = [1, 1]
sensors = ["sensor_db_monitor", "sensor_weather_feed"]

[split.train]
years = [2023]
archetypes = ["working_family"]
episodes_per_fault_type = 1
fault_free_episodes = 0

[split.validation]
years = [2024]
archetypes = ["working_family"]
episodes_per_fault_type = 1
fault_free_episodes = 0

[split.test]
years = [2025]
archetypes = ["working_family"]
episodes_per_fault_type = 1
fault_free_episodes = 0
"""


@pytest.fixture(scope="module")
def leak_suite(tmp_path_factory: pytest.TempPathFactory) -> Suite:
    directory = tmp_path_factory.mktemp("leak_suite")
    (directory / "leak.toml").write_text(LEAK, encoding="utf-8")
    return load_suite("leak", directory)


@pytest.fixture(scope="module")
def leak_episode(leak_suite: Suite, episodes_root: Path) -> tuple[Path, TruthFault]:
    paths = episodes.ensure(
        manifests(leak_suite, "validation"), episodes_root, workers=1, streams=leak_suite.streams
    )
    for path in paths:
        faults, _, _ = load_truth(path)
        for f in faults:
            if f.fault_type == "E2":
                return path, f
    raise AssertionError("the leak suite has an E2 episode")


def direct_circuits_of(task: Task) -> tuple[str, ...]:
    assert task.rccb is not None
    return direct_circuits(HouseContext.canonical().index, task.rccb)


def _leak_task(path: Path, fault: TruthFault) -> Task:
    """A switch-off task as a leakage finding would start it: its RCCB and the rise it saw."""
    rccb = HouseContext.canonical().index.rccb_chain(fault.object_ids[0])[0]
    return Task(
        str(path),
        fault.fault_type,
        fault.object_ids[0],
        fault.start_us,
        layer="L2",
        rccb=rccb,
        expect_drop_ma=fault.magnitude,
    )


def test_switch_off_checks_find_the_leaking_circuit(
    leak_episode: tuple[Path, TruthFault],
) -> None:
    record = localisation._run((_leak_task(*leak_episode), Likelihood.default().as_dict(), {}, {}))
    behind = [c["verdict"] for c in record["checks"] if c["behind"]]
    assert behind == ["dropped"]  # the leaking circuit, switched off
    # a healthy circuit with nothing running leaks only through its wiring: at most "reduced"
    # (one with an appliance running loses that appliance's leakage too: the "loaded" band)
    idle = [c["verdict"] for c in record["checks"] if not c["behind"] and c["band"] == "unloaded"]
    assert idle
    assert "dropped" not in idle
    assert all(c["band"] in ("loaded", "unloaded") for c in record["checks"])


def test_l2_fitting_starts_from_the_leakage_findings(
    leak_suite: Suite,
    leak_episode: tuple[Path, TruthFault],
    episodes_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, fault = leak_episode
    found = dataclasses.replace(
        _leak_task(path, fault),
        detector=localisation.L2_FIT_DETECTOR,
        finding="{}",
        first_seen_us=fault.start_us + 6 * 3600 * US,
    )
    asked: list[tuple[str, ...]] = []

    def tasks(at: Path, detectors: list[str], layer: str, house: HouseContext) -> list[Task]:
        asked.append(tuple(detectors))
        assert layer == "L2"
        return [found] if Path(at) == path else []

    monkeypatch.setattr(localisation, "_tasks", tasks)
    out = tmp_path / "l2_localiser.json"
    doc = localisation.fit([leak_suite], episodes_root=episodes_root, workers=1, out=out)
    assert set(asked) == {(localisation.L2_FIT_DETECTOR,)}
    assert doc["fitted_on"]["layer"] == "L2"
    assert doc["fitted_on"]["faults"] == 1
    counts = doc["counts"]["residual_drop"]
    assert counts["behind"] == {"dropped": 1}
    assert sum(counts["other"].values()) == len(direct_circuits_of(found)) - 1
    assert doc["counts"]["residual_drop@unloaded"]["other"].get("dropped", 0) == 0
    lik, plan, _ = localisation.load_likelihood(out)
    assert plan.residual_window_s == 120.0
    assert lik.p("residual_drop", True, "dropped") > lik.p("residual_drop", False, "dropped")


def test_flow_checks_wait_for_a_full_tank(cistern_episode: tuple[Path, TruthFault]) -> None:
    path, fault = cistern_episode
    task = Task(str(path), fault.fault_type, fault.object_ids[0], fault.start_us)
    record = localisation._run((task, Likelihood.default().as_dict(), {}, {}))
    flow = [c for c in record["checks"] if c["kind"] == "flow_stop"]
    assert flow
    assert all(c["tank_full"] in (True, False) for c in flow)
    assert all(c["band"] in (None, "low", "mid", "high") for c in flow)
