"""/eval/v1 on the tiny suite: a run end to end through the subprocess, sealing rules, restarts."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from penjaga_detect import registry
from penjaga_detect.detector import HouseContext
from penjaga_eval import episodes
from penjaga_eval.api import create_app
from penjaga_eval.sealing import seal
from penjaga_eval.service import EvalConfig, EvalService, detector_layer
from penjaga_eval.suites import Suite, manifests

TOKEN = "eval-operator-token-0123456789"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def config(suites_dir: Path, episodes_root: Path, tiny: Suite, tmp_path: Path) -> EvalConfig:
    episodes.ensure(manifests(tiny, "validation"), episodes_root, workers=1)  # shared cache
    return EvalConfig(
        runs_dir=tmp_path / "runs",
        suites_dir=suites_dir,
        episodes_root=episodes_root,
        sealed_dir=tmp_path / "sealed",
        test_log=tmp_path / "test-log.jsonl",
        workers=1,
    )


@pytest.fixture
def client(config: EvalConfig) -> Iterator[TestClient]:
    with TestClient(create_app(EvalService(config), operator_token=TOKEN)) as c:
        yield c


def wait(client: TestClient, run_id: str, timeout_s: float = 300) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        doc = client.get(f"/eval/v1/runs/{run_id}", headers=AUTH).json()
        if doc["status"] in ("complete", "failed"):
            return dict(doc)
        time.sleep(0.2)
    raise AssertionError(f"run {run_id} did not finish")


def test_detector_layers_follow_the_naming_convention() -> None:
    house = HouseContext.canonical()
    for name in registry.REGISTRY:
        if name == "l1_leak_model":
            continue  # needs a trained artifact; its name carries the layer like the others
        detector = registry.build(name, house, registry.parameters(name, None))
        assert detector.info.layer == detector_layer(name)


def test_suites_and_auth(client: TestClient) -> None:
    assert client.get("/eval/v1/health").json()["status"] == "ok"
    assert client.get("/eval/v1/suites").status_code == 401
    (tiny,) = [s for s in client.get("/eval/v1/suites", headers=AUTH).json() if s["name"] == "tiny"]
    assert tiny["layers"] == ["L1"]
    assert tiny["episodes"] == {"train": 3, "validation": 3, "test": 3}  # 2 faults + 1 free
    assert tiny["sealed"] is False
    assert "l1_mnf_baseline" in tiny["detectors"]
    assert "l2_safety_floor" not in tiny["detectors"]


def test_a_validation_run_end_to_end(client: TestClient) -> None:
    r = client.post("/eval/v1/runs", headers=AUTH, json={"suite": "tiny", "split": "validation"})
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["status"] == "queued"
    assert created["detectors"] == ["l1_mnf_baseline", "l1_burst_rule"]  # deployed, L1
    done = wait(client, created["run_id"])
    assert done["status"] == "complete", done["error"]
    assert done["episodes"] == 3
    mnf = done["results"]["l1_mnf_baseline"]["layers"]["L1"]
    assert mnf["faults"] == 2
    assert mnf["by_fault_type"]["W5"]["recall"] == 1.0
    report = client.get(f"/eval/v1/runs/{created['run_id']}/report", headers=AUTH).json()
    assert report["suite"] == "tiny"
    text = client.get(f"/eval/v1/runs/{created['run_id']}/report.md", headers=AUTH).text
    assert "| W5 |" in text
    listed = client.get("/eval/v1/runs", headers=AUTH).json()
    assert [d["run_id"] for d in listed] == [created["run_id"]]


def test_requests_that_cannot_run(client: TestClient, config: EvalConfig, tiny: Suite) -> None:
    def post(body: dict[str, Any]) -> int:
        return client.post("/eval/v1/runs", headers=AUTH, json=body).status_code

    assert post({"suite": "nope", "split": "validation"}) == 422
    assert post({"suite": "tiny", "split": "train"}) == 422  # tuning data is not for the Lab
    assert post({"suite": "tiny", "split": "validation", "detectors": ["l2_safety_floor"]}) == 422
    assert post({"suite": "tiny", "split": "test"}) == 422  # not sealed
    seal(tiny, config.sealed_dir)
    assert post({"suite": "tiny", "split": "test", "limit": 1}) == 422  # whole split only
    assert client.get("/eval/v1/runs/evr_nope00", headers=AUTH).status_code == 404
    assert client.get("/eval/v1/runs/../../etc", headers=AUTH).status_code == 404


def test_a_restart_requeues_queued_runs_and_fails_interrupted_ones(config: EvalConfig) -> None:
    service = EvalService(config)
    queued = service.create("tiny", "validation", ["l1_mnf_baseline"], 1)
    interrupted = service.create("tiny", "validation", ["l1_mnf_baseline"], 1)
    doc = service.get(interrupted["run_id"])
    doc["status"] = "running"
    path = config.runs_dir / interrupted["run_id"] / "run.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    again = EvalService(config)
    assert again.get(interrupted["run_id"])["status"] == "failed"
    assert "restarted" in again.get(interrupted["run_id"])["error"]
    assert again.get(queued["run_id"])["status"] == "queued"
    assert again._queue.qsize() == 1
