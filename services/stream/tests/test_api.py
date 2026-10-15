from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from penjaga_stream.api import create_app
from penjaga_stream.config import Role
from penjaga_stream.service import StreamService
from penjaga_stream.testing import at, batch_doc, trip_event_doc

TOKENS = {role: f"test-token-{role.value}-0123456789" for role in Role}
CURRENT = "elec.circuit.circ_sockets_kitchen.current_a"


def auth(role: Role) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[role]}"}


@pytest.fixture
def client(make_service: Any) -> Iterator[TestClient]:
    service: StreamService = make_service()
    with TestClient(create_app(service)) as c:
        yield c


def window(start: float, end: float, **extra: Any) -> dict[str, Any]:
    return {"from": at(start).isoformat(), "to": at(end).isoformat(), **extra}


def test_health_needs_no_token(client: TestClient):
    body = client.get("/stream/v1/health").json()
    assert body["status"] == "ok"
    assert body["episode"] == 0


def test_authentication_and_roles(client: TestClient):
    assert client.get("/stream/v1/clock").status_code == 401
    assert (
        client.get("/stream/v1/clock", headers={"Authorization": "Bearer nope"}).status_code == 401
    )
    for role in Role:
        assert client.get("/stream/v1/clock", headers=auth(role)).status_code == 200
    assert (
        client.post("/stream/v1/clock", json={"speed": 60}, headers=auth(Role.app)).status_code
        == 403
    )
    assert (
        client.get(
            "/stream/v1/readings/raw", params=window(0, 1, stream=CURRENT), headers=auth(Role.app)
        ).status_code
        == 403
    )


def test_clock_control(client: TestClient):
    state = client.post(
        "/stream/v1/clock", json={"speed": 60, "paused": True}, headers=auth(Role.operator)
    ).json()
    assert state["speed"] == 60
    assert state["paused"]
    assert state["effective_speed"] == 0
    back = client.post(
        "/stream/v1/clock", json={"jump_to": at(-10).isoformat()}, headers=auth(Role.operator)
    )
    assert back.status_code == 409
    assert client.post("/stream/v1/clock", json={}, headers=auth(Role.operator)).status_code == 422
    reset = client.post(
        "/stream/v1/reset",
        json={"start": at(0).isoformat(), "mode": "accelerated", "speed": 600},
        headers=auth(Role.operator),
    ).json()
    assert reset["episode"] == 1


def test_ingest_subscribe_and_query(client: TestClient):
    with client.websocket_connect("/stream/v1/subscribe", headers=auth(Role.detector)) as detector:
        assert detector.receive_json()["type"] == "clock"
        with client.websocket_connect("/stream/v1/ingest", headers=auth(Role.device)) as producer:
            doc = batch_doc(CURRENT, at(0), [1.0, 2.0, 3.0])
            producer.send_text(json.dumps({"type": "batch", "data": doc}))
            event = trip_event_doc("mcb_sockets_kitchen", at(2), seq=0)
            producer.send_text(json.dumps({"type": "event", "data": event}))
            producer.send_text(json.dumps({"type": "batch", "data": doc}))  # duplicate -> refused
            rejection = producer.receive_json()
            assert rejection["type"] == "rejected"
            assert "batch.seq_regression" in rejection["data"]["codes"]
        got = [detector.receive_json(), detector.receive_json()]
        assert [m["type"] for m in got] == ["batch", "event"]
        assert got[0]["data"]["values"] == [1.0, 2.0, 3.0]
    summary = client.get(
        "/stream/v1/readings/summary",
        params=window(0, 60, stream=CURRENT, bucket_s=60),
        headers=auth(Role.app),
    )
    assert summary.status_code == 200
    body = summary.json()
    assert body["from"] == at(0).isoformat()
    assert body["points"][0]["n"] == 3
    raw = client.get(
        "/stream/v1/readings/raw", params=window(0, 60, stream=CURRENT), headers=auth(Role.detector)
    ).json()
    assert [s["value"] for s in raw["samples"]] == [1.0, 2.0, 3.0]
    events = client.get("/stream/v1/events", params=window(0, 60), headers=auth(Role.app)).json()
    assert events[0]["payload"]["device_id"] == "mcb_sockets_kitchen"
    status = {
        s["stream"]: s for s in client.get("/stream/v1/streams", headers=auth(Role.app)).json()
    }
    assert status[CURRENT]["samples"] == 3
    assert status["elec.protection.trip"]["samples"] == 1


def test_query_errors(client: TestClient):
    too_many = client.get(
        "/stream/v1/readings/summary",
        params=window(0, 86_400, stream=CURRENT, bucket_s=1),
        headers=auth(Role.app),
    )
    assert too_many.status_code == 422
    assert "bucket_s >=" in too_many.json()["detail"]
    unknown = client.get(
        "/stream/v1/readings/summary",
        params=window(0, 60, stream="x.y", bucket_s=60),
        headers=auth(Role.app),
    )
    assert unknown.status_code == 404
    naive = client.get(
        "/stream/v1/readings/summary",
        params={
            "stream": CURRENT,
            "from": "2026-10-16T00:00:00",
            "to": at(60).isoformat(),
            "bucket_s": 60,
        },
        headers=auth(Role.app),
    )
    assert naive.status_code == 422


def test_websocket_roles_are_enforced(client: TestClient):
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("/stream/v1/ingest", headers=auth(Role.app)) as ws,
    ):
        ws.receive_text()
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("/stream/v1/subscribe", headers=auth(Role.app)) as ws,
    ):
        ws.receive_text()
    token_in_query = f"/stream/v1/live?token={TOKENS[Role.app]}&max_hz=10"
    with client.websocket_connect(token_in_query) as ws:
        assert ws.receive_json()["type"] == "clock"


def test_live_feed_frames_and_events(client: TestClient):
    with client.websocket_connect(
        f"/stream/v1/live?streams={CURRENT}&max_hz=10", headers=auth(Role.app)
    ) as live:
        assert live.receive_json()["type"] == "clock"
        with client.websocket_connect("/stream/v1/ingest", headers=auth(Role.device)) as producer:
            producer.send_text(
                json.dumps({"type": "batch", "data": batch_doc(CURRENT, at(0), [4.0, 6.0])})
            )
            producer.send_text(
                json.dumps(
                    {"type": "event", "data": trip_event_doc("mcb_sockets_kitchen", at(1), seq=0)}
                )
            )
        seen: dict[str, Any] = {}
        for _ in range(20):
            msg = live.receive_json()
            if msg["type"] == "frame" and CURRENT in msg["data"]["streams"]:
                seen["frame"] = msg["data"]["streams"][CURRENT]
            if msg["type"] == "event":
                seen["event"] = msg["data"]
            if len(seen) == 2:
                break
        assert seen["frame"]["mean"] == 5.0
        assert seen["frame"]["n"] == 2
        assert seen["event"]["payload"]["state"] == "tripped"


def test_live_feed_rejects_bad_parameters(client: TestClient):
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("/stream/v1/live?max_hz=100", headers=auth(Role.app)) as ws,
    ):
        ws.receive_text()
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("/stream/v1/live?streams=a.b", headers=auth(Role.app)) as ws,
    ):
        ws.receive_text()
