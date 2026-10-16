"""End-to-end with real processes: M4, the simulator and detection over HTTP and WebSocket.
A burst injected through /sim/v1 becomes an urgent finding on /detect/v1 and its SSE stream."""

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
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2
import pytest

from penjaga_sim.testing import START, at

OPERATOR = "process-operator-token-0123456789"
APP = "process-app-token-0123456789"
TOKENS = {
    "PENJAGA_TOKEN_OPERATOR": OPERATOR,
    "PENJAGA_TOKEN_DEVICE": "process-device-token-0123456789",
    "PENJAGA_TOKEN_DETECTOR": "process-detector-token-0123456789",
    "PENJAGA_TOKEN_APP": APP,
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
    ports = [_free_port() for _ in range(3)]
    stream, sim, detect = (f"http://127.0.0.1:{p}" for p in ports)
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
        yield stream, sim, detect


def test_an_injected_burst_becomes_an_urgent_finding(
    services: tuple[str, str, str], tmp_path: Path
) -> None:
    _, sim, detect = services

    def health() -> dict[str, Any]:
        with urllib.request.urlopen(f"{detect}/detect/v1/health", timeout=5) as r:
            return dict(json.loads(r.read()))

    _wait("detection follows the episode", lambda: health()["processed_until"] is not None)
    catalogue = {e["fault_type"]: e for e in _call("GET", f"{sim}/sim/v1/faults/catalogue")}
    spec = {
        "fault_type": "W5",
        "location_id": catalogue["W5"]["locations"][0],
        "magnitude": {"value": 15.0, "unit": "L/min"},
        "progression": {"kind": "constant"},
        "start_at": None,
    }
    start = datetime.fromisoformat(_call("POST", f"{sim}/sim/v1/faults", spec)["start"])

    def bursts() -> list[dict[str, Any]]:
        found = _call("GET", f"{detect}/detect/v1/findings", token=APP)
        return [f for f in found if f["type"] == "burst_pipe_suspected"]

    _wait("burst finding", lambda: bool(bursts()), timeout_s=180)
    (burst,) = bursts()
    assert burst["severity"] == "urgent"
    assert burst["safety_guidance_key"] == "sg_burst_pipe"
    first_seen = datetime.fromisoformat(burst["first_seen"])
    assert start < first_seen <= start + timedelta(hours=2)

    # the same revision arrives on the SSE stream (replayed from the start of the store)
    with httpx2.stream(
        "GET",
        f"{detect}/detect/v1/findings/stream",
        params={"after": 0},
        headers={"Authorization": f"Bearer {APP}"},
        timeout=30,
    ) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        seen: dict[str, Any] = {}
        for line in r.iter_lines():
            if line.startswith("data: "):
                doc = json.loads(line.removeprefix("data: "))
                if doc["type"] == "burst_pipe_suspected":
                    seen = doc
                    break
    assert seen["id"] == burst["id"]

    state = health()
    assert state["connected"] is True
    assert state["late_samples"] == 0
    assert state["stalled_streams"] == []
    log = (tmp_path / "detect.log").read_text(encoding="utf-8", errors="replace")
    assert "Traceback" not in log
