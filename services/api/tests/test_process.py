"""End-to-end with real processes: M4, the simulator, detection and the Core API over HTTP.
A burst injected through /sim/v1 becomes a finding, an urgent case in the app's view and events
on the SSE stream."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

import httpx2
import pytest

from penjaga_sim.testing import START, at

OPERATOR = "process-operator-token-0123456789"
APP = "process-app-token-0123456789"
HOMEOWNER = "process-homeowner-token-0123456789"
TOKENS = {
    "PENJAGA_TOKEN_OPERATOR": OPERATOR,
    "PENJAGA_TOKEN_DEVICE": "process-device-token-0123456789",
    "PENJAGA_TOKEN_DETECTOR": "process-detector-token-0123456789",
    "PENJAGA_TOKEN_APP": APP,
    "PENJAGA_TOKEN_HOMEOWNER": HOMEOWNER,
    "PENJAGA_TOKEN_MANAGEMENT": "process-management-token-0123456789",
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _call(method: str, url: str, body: Any = None, token: str = OPERATOR) -> Any:
    data = None if body is None else json.dumps(body).encode()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def _wait(what: str, check: Callable[[], bool], timeout_s: float = 120.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if check():
                return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.2)
    raise TimeoutError(what)


@contextmanager
def _process(cmd: list[str], log_path: Path, health_url: str) -> Iterator[None]:
    env = {**os.environ, "PYTHONUTF8": "1", **TOKENS}
    with log_path.open("wb") as log:
        proc = subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:

            def healthy() -> bool:
                if proc.poll() is not None:
                    raise RuntimeError(log_path.read_text(encoding="utf-8", errors="replace"))
                with urllib.request.urlopen(health_url, timeout=1) as r:
                    return bool(r.status == 200)

            _wait(f"{cmd[2]} health", healthy)
            yield
        finally:
            proc.terminate()
            proc.wait(timeout=15)


@pytest.fixture
def services(tmp_path: Path) -> Iterator[tuple[str, str, str]]:
    ports = [_free_port() for _ in range(4)]
    stream, sim, detect, api = (f"http://127.0.0.1:{p}" for p in ports)
    with ExitStack() as stack:
        stack.enter_context(
            _process(
                [sys.executable, "-m", "penjaga_stream.cli", "serve", "--port", str(ports[0]),
                 "--data-dir", str(tmp_path / "stream"), "--mode", "accelerated",
                 "--start", at(START, 8.0).isoformat(), "--speed", "600"],
                tmp_path / "stream.log",
                f"{stream}/stream/v1/health",
            )
        )  # fmt: skip
        stack.enter_context(
            _process(
                [sys.executable, "-m", "penjaga_sim.cli", "serve", "--port", str(ports[1]),
                 "--stream-url", stream, "--data-dir", str(tmp_path / "sim"), "--seed", "7"],
                tmp_path / "sim.log",
                f"{sim}/sim/v1/health",
            )
        )  # fmt: skip
        stack.enter_context(
            _process(
                [sys.executable, "-m", "penjaga_detect.cli", "serve", "--port", str(ports[2]),
                 "--stream-url", stream, "--data-dir", str(tmp_path / "detect")],
                tmp_path / "detect.log",
                f"{detect}/detect/v1/health",
            )
        )  # fmt: skip
        stack.enter_context(
            _process(
                [sys.executable, "-m", "penjaga_api.cli", "serve", "--port", str(ports[3]),
                 "--stream-url", stream, "--detect-url", detect,
                 "--data-dir", str(tmp_path / "api")],
                tmp_path / "api.log",
                f"{api}/api/v1/health",
            )
        )  # fmt: skip
        yield sim, detect, api


def test_an_injected_burst_reaches_the_core_api(
    services: tuple[str, str, str], tmp_path: Path
) -> None:
    sim, _, api = services

    def health() -> dict[str, Any]:
        with urllib.request.urlopen(f"{api}/api/v1/health", timeout=5) as r:
            return dict(json.loads(r.read()))

    _wait("the Core API follows detection", lambda: health()["detection_connected"] is True)

    # subscribe to live events before injecting, as the app would
    with httpx2.stream(
        "GET",
        f"{api}/api/v1/events",
        params={"after": 0},
        headers={"Authorization": f"Bearer {OPERATOR}"},
        timeout=httpx2.Timeout(10, read=180),
    ) as events:
        assert events.headers["content-type"].startswith("text/event-stream")
        catalogue = {e["fault_type"]: e for e in _call("GET", f"{sim}/sim/v1/faults/catalogue")}
        _call(
            "POST",
            f"{sim}/sim/v1/faults",
            {
                "fault_type": "W5",
                "location_id": catalogue["W5"]["locations"][0],
                "magnitude": {"value": 15.0, "unit": "L/min"},
                "progression": {"kind": "constant"},
                "start_at": None,
            },
        )
        seen: dict[str, Any] = {}
        case_event: dict[str, Any] = {}
        for line in events.iter_lines():
            if line.startswith("data: "):
                doc = json.loads(line.removeprefix("data: "))
                if doc["kind"] == "finding" and doc["type"] == "burst_pipe_suspected":
                    seen = doc
                if doc["kind"] == "case" and doc["severity"] == "urgent":
                    case_event = doc
                if seen and case_event:
                    break

    assert seen["severity"] == "urgent"

    # the finding is an urgent case in the app's view straight away, charted from real readings
    view = _call("GET", f"{api}/api/v1/view", token=HOMEOWNER)
    (case,) = [c for c in view["cases"] if c["severity"] == "urgent"]
    assert case["id"] == case_event["id"]
    assert case["status"] == "open"
    assert case["title"] == "Water has been flowing nonstop at a high rate"
    assert case["safety"][0].startswith("Turn off the water supply")
    assert case["evidence"]["series"]["unit"] == "L/min"
    assert max(p["v"] for p in case["evidence"]["series"]["points"]) > 6
    assert view["home"]["status"] == "urgent"
    assert set(case["focus"]["object_ids"]) <= set(
        _call("POST", f"{api}/api/v1/house/validate-ids", {"ids": case["focus"]["object_ids"]})[
            "ids"
        ]
    )
    found = _call("GET", f"{api}/api/v1/findings", token=OPERATOR)
    (burst,) = [f for f in found if f["type"] == "burst_pipe_suspected"]
    assert burst["id"] == seen["id"]
    assert burst["safety_guidance_key"] == "sg_burst_pipe"
    ids = _call("POST", f"{api}/api/v1/house/validate-ids", {"ids": burst["object_ids"]})
    assert ids["ids"] == burst["object_ids"]

    # the homeowner may not read raw findings; the operator may
    with pytest.raises(urllib.error.HTTPError) as denied:
        _call("GET", f"{api}/api/v1/findings", token=HOMEOWNER)
    assert denied.value.code == 403
    denied.value.close()  # an HTTPError holds the response open

    log = (tmp_path / "api.log").read_text(encoding="utf-8", errors="replace")
    assert "Traceback" not in log
