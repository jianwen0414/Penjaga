"""/detect/v1 over HTTP (in-process) against an in-memory M4 fed by a simulated episode."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from penjaga_contract import schema_registry
from penjaga_detect.api import create_app
from penjaga_detect.cli import stream_infos
from penjaga_detect.deployment import Deployment
from penjaga_detect.detector import HouseContext
from penjaga_detect.service import DetectConfig, DetectService
from penjaga_detect.store import FindingStore
from penjaga_sim.testing import START, at, fault, new_world
from penjaga_sim.world import World

TOKEN = "test-app-token-0123456789"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
API = "https://penjaga.invalid/contract/core/"


@pytest.fixture(scope="module")
def world() -> World:
    w = new_world()
    w.inject(fault("U3", None, 1, at(START, 3.0), duration_s=1800))
    w.inject(fault("W5", "pipe_porch_tap", 12.0, at(START, 10.0), duration_s=2 * 3600))
    return w


class Harness:
    def __init__(self, fake: Any, data_dir: Path) -> None:
        self.fake = fake
        house = HouseContext.canonical()
        self.store = FindingStore(data_dir / "findings.sqlite")
        self.service = DetectService(
            self.fake,
            house,
            specs=Deployment.load(params_dir=None).specs(house),
            streams=stream_infos(),
            store=self.store,
            config=DetectConfig(data_dir=data_dir, reconnect_s=0.05, check_poll_s=0.05),
        )
        self.client = TestClient(create_app(self.service, tokens=[TOKEN]))

    def __enter__(self) -> Harness:
        self.client.__enter__()
        return self

    def __exit__(self, *exc: object) -> None:
        self.client.__exit__(*exc)
        self.store.close()

    def publish(self, hours: float) -> None:
        portal = self.client.portal
        assert portal is not None
        portal.call(self.fake.publish, hours)

    def call(self, fn: Callable[..., Any], *args: Any) -> Any:
        portal = self.client.portal
        assert portal is not None
        return portal.call(fn, *args)

    def get(self, path: str, **kw: Any) -> Any:
        return self.client.get(f"/detect/v1{path}", headers=AUTH, **kw)

    def wait_processed(self, until_us: int, timeout_s: float = 120.0) -> None:
        """Wait until detection has processed house time up to the last full step."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            engine = self.service.engine
            if engine is not None and engine.processed_until_us >= until_us:
                return
            time.sleep(0.05)
        raise AssertionError(f"detection stuck: {self.client.get('/detect/v1/health').json()}")


@pytest.fixture
def new_fake(world: World, fake_m4: Any) -> Callable[[], Any]:
    return lambda: fake_m4(world, at(START))


@pytest.fixture
def h(new_fake: Callable[[], Any], tmp_path: Path) -> Iterator[Harness]:
    with Harness(new_fake(), tmp_path) as harness:
        yield harness


def check(instance: Any, ref: str) -> None:
    validator = Draft202012Validator(
        {"$ref": API + ref},
        registry=schema_registry.registry(),
        format_checker=schema_registry.FORMAT_CHECKER,
    )
    errors = [e.message for e in validator.iter_errors(instance)]
    assert errors == [], errors


def us(hours: float) -> int:
    return int(at(START, hours).timestamp()) * 1_000_000


def test_health_is_public_and_the_rest_needs_a_token(h: Harness) -> None:
    body = h.client.get("/detect/v1/health").json()
    check(body, "detect_api.schema.json#/$defs/DetectHealth")
    for path in ("/findings", "/detectors", "/layers", "/diagnostics"):
        assert h.client.get(f"/detect/v1{path}").status_code == 401
        bad = {"Authorization": "Bearer wrong-token-0123456789"}
        assert h.client.get(f"/detect/v1{path}", headers=bad).status_code == 401


def test_findings_flow_from_published_readings(h: Harness) -> None:
    h.publish(24)
    h.wait_processed(us(23.75))
    findings = h.get("/findings").json()
    kinds = {(f["type"], f["status"]) for f in findings}
    assert ("power_supply_interruption", "cleared") in kinds
    assert any(f["type"] == "burst_pipe_suspected" for f in findings)
    for f in findings:
        check(f, "finding.schema.json")
    burst = next(f for f in findings if f["type"] == "burst_pipe_suspected")
    assert burst["severity"] == "urgent"
    assert burst["safety_guidance_key"] == "sg_burst_pipe"
    assert h.get(f"/findings/{burst['id']}").json() == burst
    history = h.get(f"/findings/{burst['id']}/history").json()
    assert history[-1] == burst
    assert [r["revision"] for r in history] == list(range(1, len(history) + 1))
    assert {f["layer"] for f in h.get("/findings", params={"layer": "L5"}).json()} == {"L5"}
    assert h.get("/findings/fnd_nope").status_code == 404
    health = h.client.get("/detect/v1/health").json()
    assert health["status"] == "ok"
    assert health["connected"] is True
    assert health["late_samples"] == 0
    assert health["lag_s"] is not None
    assert health["lag_s"] <= 3600


def test_a_restart_mid_episode_backfills_and_duplicates_nothing(
    new_fake: Callable[[], Any], tmp_path: Path
) -> None:
    with Harness(new_fake(), tmp_path) as first:
        first.publish(24)
        first.wait_processed(us(23.75))
        before = first.get("/findings").json()
        fake = first.fake
    with Harness(fake, tmp_path) as second:  # same store, same M4 episode
        second.wait_processed(us(23.75))
        assert second.get("/findings").json() == before
        assert second.store.conflicts == 0
        assert second.service.counters.revisions == 0  # every revision was already stored
        assert second.service.counters.backfilled_samples > 0


def test_reconnecting_after_a_drop_backfills_the_gap(
    new_fake: Callable[[], Any], tmp_path: Path
) -> None:
    with Harness(new_fake(), tmp_path / "ref") as ref:
        ref.publish(24)
        ref.wait_processed(us(23.75))
        expected = ref.get("/findings").json()
    with Harness(new_fake(), tmp_path / "drop") as h:
        h.publish(6)
        h.wait_processed(us(5.75))
        h.fake.down = True
        h.call(h.fake.drop_subscribers)
        h.publish(18)  # published while detection is disconnected
        h.fake.down = False
        h.wait_processed(us(23.75))
        assert h.get("/findings").json() == expected
        assert h.service.counters.sessions >= 2
        assert h.store.conflicts == 0


def test_layer_consent(h: Harness) -> None:
    h.publish(1)
    h.wait_processed(us(0.75))
    r = h.client.put("/detect/v1/layers/L2", headers=AUTH, json={"enabled": False})
    assert r.status_code == 200
    assert r.json() == {"layer": "L2", "enabled": False}
    layers = {x["layer"]: x["enabled"] for x in h.get("/layers").json()}
    assert layers == {"L1": True, "L2": False, "L3": True, "L5": True}
    detectors = h.get("/detectors").json()
    for d in detectors:
        check(d, "detect_api.schema.json#/$defs/DetectorStatus")
    assert {d["name"]: d["enabled"] for d in detectors}["l2_safety_floor"] is False
    assert "l2_safety_floor" not in h.get("/diagnostics").json()["running"]
    assert (h.service.config.data_dir / "layers.json").exists()
    assert (
        h.client.put("/detect/v1/layers/L4", headers=AUTH, json={"enabled": True}).status_code
        == 404
    )
    assert (
        h.client.put("/detect/v1/layers/L9", headers=AUTH, json={"enabled": True}).status_code
        == 422
    )


def test_on_demand_run(h: Harness) -> None:
    h.publish(12)
    h.wait_processed(us(11.75))
    body = {
        "detector": "l5_supply",
        "from": at(START, 0).isoformat(),
        "to": at(START, 6).isoformat(),
    }
    r = h.client.post("/detect/v1/run", headers=AUTH, json=body)
    assert r.status_code == 200, r.text
    check(r.json(), "detect_api.schema.json#/$defs/RunResponse")
    assert [f["type"] for f in r.json()["findings"]] == ["power_supply_interruption"]
    late = {**body, "to": at(START, 20).isoformat()}
    assert h.client.post("/detect/v1/run", headers=AUTH, json=late).status_code == 422
    unknown = {**body, "detector": "l1_leak_model"}
    assert h.client.post("/detect/v1/run", headers=AUTH, json=unknown).status_code == 404
    naive = {**body, "from": "2026-10-01T00:00:00"}
    assert h.client.post("/detect/v1/run", headers=AUTH, json=naive).status_code == 422


def test_guided_checks(h: Harness) -> None:
    h.publish(11)
    h.wait_processed(us(10.75))
    stop = {
        "kind": "flow_stop",
        "since": at(START, 12.0).isoformat(),  # the burst stops at 12:00
        "settle_s": 120,
        "window_s": 900,
        "expect_below_lpm": 2.0,
    }
    r = h.client.post("/detect/v1/checks", headers=AUTH, json=stop)
    assert r.status_code == 201, r.text
    created = r.json()
    check(created, "detect_api.schema.json#/$defs/Check")
    assert created["status"] == "pending"
    assert created["verdict"] is None
    circuit = {
        "kind": "circuit_load",
        "circuit_id": "circ_sockets_kitchen",
        "since": at(START, 11.0).isoformat(),
        "settle_s": 0,
        "window_s": 600,
    }
    load = h.client.post("/detect/v1/checks", headers=AUTH, json=circuit).json()
    tank = {"kind": "tank_level_trend", "since": at(START, 11.0).isoformat(), "window_s": 1800}
    trend = h.client.post("/detect/v1/checks", headers=AUTH, json=tank).json()
    h.publish(2)
    h.wait_processed(us(12.75))
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        done = h.get(f"/checks/{created['check_id']}").json()
        if done["status"] != "pending":
            break
        time.sleep(0.05)
    check(done, "detect_api.schema.json#/$defs/Check")
    assert done["status"] == "complete"
    assert done["verdict"] in {"stopped", "reduced"}
    change = {m["name"]: m["value"] for m in done["measurements"]}["flow_change"]
    assert change < -8.0  # the 12 L/min burst stopped
    for c in (load, trend):
        result = h.get(f"/checks/{c['check_id']}").json()
        check(result, "detect_api.schema.json#/$defs/Check")
        assert result["status"] == "complete"
    bad = {**circuit, "circuit_id": "app_fridge_kitchen"}
    assert h.client.post("/detect/v1/checks", headers=AUTH, json=bad).status_code == 422
    assert h.get("/checks/chk_000000").status_code == 404


def test_nothing_to_serve_before_an_episode(new_fake: Callable[[], Any], tmp_path: Path) -> None:
    fake = new_fake()
    fake.down = True
    with Harness(fake, tmp_path) as h:
        assert h.get("/findings").status_code == 503
        health = h.client.get("/detect/v1/health").json()
        assert health["status"] == "degraded"
        assert health["connected"] is False


def test_openapi_documents_the_service(h: Harness) -> None:
    paths = h.client.get("/detect/v1/openapi.json").json()["paths"]
    for p in (
        "/detect/v1/findings",
        "/detect/v1/findings/stream",
        "/detect/v1/run",
        "/detect/v1/checks",
    ):
        assert p in paths


def test_day_two_continues_the_same_run(h: Harness) -> None:
    h.publish(30)
    h.wait_processed(us(29.75))
    assert h.service.run is not None
    assert h.service.run.start_us == us(0)
    assert h.service.engine is not None
    assert h.service.engine.processed_until_us >= us(29.75)
    assert timedelta(hours=29.75) <= timedelta(
        microseconds=h.service.engine.processed_until_us - us(0)
    )


def test_a_restarted_m4_starts_a_new_run_even_with_the_same_episode_number(
    new_fake: Callable[[], Any], tmp_path: Path
) -> None:
    with Harness(new_fake(), tmp_path) as first:
        first.publish(12)
        first.wait_processed(us(11.75))
        assert first.get("/findings").json()
    restarted = new_fake()
    restarted.episode_id_override = "epi_00000000000000ff"  # same episode number and start
    with Harness(restarted, tmp_path) as second:
        second.publish(1)
        second.wait_processed(us(0.75))
        assert second.service.run is not None
        assert second.service.run.key == "epi_00000000000000ff"
        assert second.get("/findings").json() == []  # nothing from the previous M4 run


def _wait_check(h: Harness, check_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        doc: dict[str, Any] = h.get(f"/checks/{check_id}").json()
        if doc["status"] != "pending":
            return doc
        time.sleep(0.05)
    raise AssertionError("check still pending")


def test_localise_a_burst_with_a_valve_check(h: Harness) -> None:
    h.publish(11)
    h.wait_processed(us(10.75))
    burst = next(f for f in h.get("/findings").json() if f["type"] == "burst_pipe_suspected")
    r = h.client.post(
        "/detect/v1/localise", headers=AUTH, json={"finding_id": burst["id"], "check_ids": []}
    )
    assert r.status_code == 200, r.text
    first = r.json()
    check(first, "detect_api.schema.json#/$defs/LocaliseResponse")
    assert first["layer"] == "L1"
    assert first["next_check"] is not None
    assert first["next_check"]["action"] == "close_valve"
    # close a bathroom branch valve while the porch-tap burst runs: the meter keeps flowing
    stop = {
        "kind": "flow_stop", "valve_id": "valve_bath2_branch",
        "since": at(START, 11.0).isoformat(), "settle_s": 120, "window_s": 600,
        "expect_below_lpm": 0.02,
    }  # fmt: skip
    created = h.client.post("/detect/v1/checks", headers=AUTH, json=stop).json()
    h.publish(1)
    h.wait_processed(us(11.5))
    done = _wait_check(h, created["check_id"])
    assert done["verdict"] == "unchanged"
    body = {"finding_id": burst["id"], "check_ids": [created["check_id"]]}
    after = h.client.post("/detect/v1/localise", headers=AUTH, json=body).json()
    check(after, "detect_api.schema.json#/$defs/LocaliseResponse")
    assert after["checks_used"] == [{"check_id": created["check_id"], "evidence": "unchanged"}]
    assert after["entropy_bits"] < first["entropy_bits"]
    p_before = {c["object_id"]: c["p"] for c in first["candidates"]}
    p_after = {c["object_id"]: c["p"] for c in after["candidates"]}
    # the closed valve's branch lost probability (it fell out of the top 10, or below where it was)
    bath2 = "fix_bath2_wc_cistern"
    assert bath2 in p_before
    assert p_after.get(bath2, 0.0) < p_before[bath2]


def test_localise_refuses_what_it_cannot_localise(h: Harness) -> None:
    h.publish(4)
    h.wait_processed(us(3.75))
    cut = next(f for f in h.get("/findings").json() if f["type"] == "power_supply_interruption")
    body = {"finding_id": cut["id"], "check_ids": []}
    assert h.client.post("/detect/v1/localise", headers=AUTH, json=body).status_code == 422
    missing = {"finding_id": "fnd_" + "0" * 20, "check_ids": []}
    assert h.client.post("/detect/v1/localise", headers=AUTH, json=missing).status_code == 404


def test_residual_drop_checks(h: Harness) -> None:
    h.publish(2)
    h.wait_processed(us(1.75))
    req = {
        "kind": "residual_drop", "rccb_id": "rcd_sockets", "circuit_id": "circ_sockets_kitchen",
        "since": at(START, 2.5).isoformat(), "settle_s": 60, "window_s": 600,
    }  # fmt: skip
    r = h.client.post("/detect/v1/checks", headers=AUTH, json=req)
    assert r.status_code == 201, r.text
    wrong = {**req, "rccb_id": "rcd_wh_bath2"}
    assert h.client.post("/detect/v1/checks", headers=AUTH, json=wrong).status_code == 422
    h.publish(2)
    h.wait_processed(us(3.0))
    done = _wait_check(h, r.json()["check_id"])
    check(done, "detect_api.schema.json#/$defs/Check")
    # nobody switched the circuit off in this world: the check says so rather than guessing
    assert done["status"] in {"complete", "unavailable"}
    if done["status"] == "unavailable":
        assert "not switched off" in done["reason"]
