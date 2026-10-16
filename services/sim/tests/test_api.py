"""/sim/v1 over HTTP (in-process), with an in-memory M4."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from penjaga_contract import schema_registry
from penjaga_sim.api import ContractViolationError, create_app, document
from penjaga_sim.service import SimConfig, SimService
from penjaga_sim.stream_client import StreamUnavailableError
from penjaga_sim.testing import START, FakeStream, at, fault

TOKEN = "test-operator-token-0123456789"
LAB = "https://penjaga.invalid/contract/lab/"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class Harness:
    def __init__(self, client: TestClient, service: SimService, stream: FakeStream) -> None:
        self.client, self.service, self.stream = client, service, stream

    def step(self, advance_s: float = 0.0) -> None:
        """Advance M4's clock and run one publishing step on the app's event loop."""
        self.stream.advance(advance_s)
        portal = self.client.portal
        assert portal is not None
        portal.call(self.service.step, self.stream)

    def get(self, path: str, **kw: Any) -> Any:
        return self.client.get(f"/sim/v1{path}", headers=AUTH, **kw)

    def post(self, path: str, body: Any) -> Any:
        return self.client.post(f"/sim/v1{path}", headers=AUTH, json=body)


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    stream = FakeStream(at(START, 8.0))
    service = SimService(SimConfig(data_dir=tmp_path, seed=7), stream)
    app = create_app(service, operator_token=TOKEN, autostart=False)
    with TestClient(app) as client:
        yield Harness(client, service, stream)


def spec_doc(spec: Any) -> dict[str, Any]:
    return {**spec.model_dump(mode="json", exclude_none=True), "location_id": spec.location_id}


def check(instance: Any, ref: str) -> None:
    """Full JSON Schema validation (the generated models are laxer, e.g. on nulls)."""
    validator = Draft202012Validator(
        {"$ref": LAB + ref},
        registry=schema_registry.registry(),
        format_checker=schema_registry.FORMAT_CHECKER,
    )
    errors = [e.message for e in validator.iter_errors(instance)]
    assert errors == [], (ref, errors)


def test_responses_follow_the_contract(h: Harness, location: Any) -> None:
    api = "sim_api.schema.json#/$defs/"
    check(h.client.get("/sim/v1/health").json(), api + "SimHealth")
    check(h.get("/status").json(), api + "SimStatus")  # before the first step: nulls
    h.step()
    check(h.get("/status").json(), api + "SimStatus")
    catalogue = h.get("/faults/catalogue").json()
    assert catalogue
    for entry in catalogue:
        check(entry, api + "CatalogueEntry")
    r = h.post("/faults", spec_doc(fault("W2", location("W2"), 0.3, at(START, 9.0))))
    check(r.json(), api + "FaultView")
    r = h.post("/faults/random", {"fault_type": "U3", "start_at": None})
    assert r.status_code == 201, r.text
    check(r.json(), api + "FaultView")
    for view in h.get("/faults").json():
        check(view, api + "FaultView")
    h.step(2 * 3600)
    for record in h.get("/truth").json():
        check(record, "truth_record.schema.json")
    r = h.post("/world/reset", {"start": at(START, 0.0).isoformat(), "mode": None, "speed": None})
    assert r.status_code == 200, r.text
    check(r.json(), api + "ResetResponse")
    assert r.json()["clock"]["mode"] == "accelerated"


def test_a_response_that_breaks_the_contract_is_a_server_bug() -> None:
    with pytest.raises(ContractViolationError, match="AnomalyCreated"):
        document({"record_id": ""}, "sim_api", "AnomalyCreated")
    with pytest.raises(ContractViolationError):
        document([{"record_id": "tr_1"}, {}], "sim_api", "AnomalyCreated", many=True)
    assert document({"record_id": "tr_1"}, "sim_api", "AnomalyCreated").status_code == 200


def test_health_is_public_and_reveals_nothing(h: Harness) -> None:
    body = h.client.get("/sim/v1/health").json()
    assert body["status"] == "ok"
    assert "seed" not in body
    assert "faults" not in body


def test_everything_else_needs_the_operator_token(h: Harness) -> None:
    for path in ("/status", "/faults", "/faults/catalogue", "/truth"):
        assert h.client.get(f"/sim/v1{path}").status_code == 401
        wrong = {"Authorization": "Bearer someone-elses-token-0123"}
        assert h.client.get(f"/sim/v1{path}", headers=wrong).status_code == 401
    assert h.client.post("/sim/v1/faults", json={}).status_code == 401


def test_no_episode_yet_is_503(h: Harness) -> None:
    assert h.get("/faults").status_code == 503
    h.step()
    assert h.get("/faults").json() == []
    assert len(h.get("/faults/catalogue").json()) == 15


def test_inject_list_and_clear(h: Harness, location: Any) -> None:
    h.step()
    h.step(60)
    spec = fault("W5", location("W5"), 10.0, at(START, 9.0))
    r = h.post("/faults", spec_doc(spec))
    assert r.status_code == 201, r.text
    view = r.json()
    assert view["state"] == "scheduled"
    assert view["start"] == at(START, 9.0).isoformat()
    assert [f["fault_id"] for f in h.get("/faults").json()] == [view["fault_id"]]
    h.step(3600)
    assert h.get("/faults").json()[0]["state"] == "active"
    r = h.client.delete(f"/sim/v1/faults/{view['fault_id']}", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["end"] is not None
    assert h.client.delete("/sim/v1/faults/flt_000000000000", headers=AUTH).status_code == 404


def test_invalid_and_conflicting_requests(h: Harness, location: Any) -> None:
    h.step()
    h.step(3600)
    past = fault("W1", location("W1"), 0.05, at(START, 8.5))
    assert h.post("/faults", spec_doc(past)).status_code == 409
    wrong_place = fault("W1", "circ_lights_gf", 0.05, at(START, 10.0))
    assert h.post("/faults", spec_doc(wrong_place)).status_code == 422
    assert h.post("/faults", {"fault_type": "W1"}).status_code == 422  # schema
    no_cycle = spec_doc(fault("W4", location("W4"), 0.4, at(START, 10.0)))
    no_cycle["progression"] = {"kind": "intermittent"}
    assert h.post("/faults", no_cycle).status_code == 422  # conditional rule


def test_surprise_me(h: Harness) -> None:
    h.step()
    r = h.post("/faults/random", {"fault_type": "E2"})
    assert r.status_code == 201, r.text
    assert r.json()["spec"]["progression"]["kind"] == "worsening"
    assert h.post("/faults/random", {"fault_type": "Z9"}).status_code == 422


def test_anomaly(h: Harness) -> None:
    h.step()
    body = {"anomaly_type": "guests", "start_at": at(START, 18.0).isoformat(), "duration_s": 3600}
    assert h.post("/anomalies", body).status_code == 409  # whole days: today has begun
    body["start_at"] = at(START, 30.0).isoformat()
    r = h.post("/anomalies", body)
    assert r.status_code == 201
    assert r.json()["record_id"].startswith("tr_")


def test_reset(h: Harness) -> None:
    h.step()
    r = h.post(
        "/world/reset",
        {"start": at(START, 0.0).isoformat(), "speed": 600, "seed": 3, "archetype": "young_couple"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["clock"]["episode"] == 2
    assert body["status"]["seed"] == 3
    assert body["status"]["archetype"] == "young_couple"
    bad = h.post("/world/reset", {"start": at(START).isoformat(), "enabled_sensor_ids": ["x"]})
    assert bad.status_code == 422
    assert h.post("/world/reset", {"start": "2026-10-01T00:00:00"}).status_code == 422  # no offset


def test_stream_failure_is_502(h: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    h.step()

    async def broken(_: Any) -> Any:
        raise StreamUnavailableError("M4 is down")

    monkeypatch.setattr(h.stream, "reset", broken)
    r = h.post("/world/reset", {"start": at(START).isoformat()})
    assert r.status_code == 502
    assert "M4 is down" in r.json()["detail"]


def test_truth_only_covers_published_time(h: Harness, location: Any) -> None:
    h.step()
    spec = fault("W2", location("W2"), 0.3, at(START, 9.0))
    h.post("/faults", spec_doc(spec))
    assert [r for r in h.get("/truth").json() if r["kind"] == "fault"] == []
    h.step(2 * 3600)
    (record,) = [r for r in h.get("/truth").json() if r["kind"] == "fault"]
    assert record["label"] == "W2"


def test_openapi_documents_the_control_plane(h: Harness) -> None:
    paths = h.client.get("/sim/v1/openapi.json").json()["paths"]
    for path in ("/sim/v1/faults", "/sim/v1/faults/random", "/sim/v1/world/reset", "/sim/v1/truth"):
        assert path in paths
