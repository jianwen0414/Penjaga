"""End-to-end: real M4 and simulator processes; a fault injected through /sim/v1 shows up in M4."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from penjaga_sim.testing import START, at

OPERATOR = "process-operator-token-0123456789"
DEVICE = "process-device-token-0123456789"
TOKENS = {
    "PENJAGA_TOKEN_OPERATOR": OPERATOR,
    "PENJAGA_TOKEN_DEVICE": DEVICE,
    "PENJAGA_TOKEN_DETECTOR": "process-detector-token-0123456789",
    "PENJAGA_TOKEN_APP": "process-app-token-0123456789",
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _call(method: str, url: str, body: Any = None) -> Any:
    data = None if body is None else json.dumps(body).encode()
    headers = {"Authorization": f"Bearer {OPERATOR}", "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def _wait(what: str, check: Callable[[], bool], timeout_s: float = 60.0) -> None:
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
def services(tmp_path: Path) -> Iterator[tuple[str, str]]:
    stream_port, sim_port = _free_port(), _free_port()
    stream = f"http://127.0.0.1:{stream_port}"
    sim = f"http://127.0.0.1:{sim_port}"
    with ExitStack() as stack:
        stack.enter_context(
            _process(
                [sys.executable, "-m", "penjaga_stream.cli", "serve", "--port", str(stream_port),
                 "--data-dir", str(tmp_path / "stream"), "--mode", "accelerated",
                 "--start", at(START, 8.0).isoformat(), "--speed", "600"],
                tmp_path / "stream.log",
                f"{stream}/stream/v1/health",
            )
        )  # fmt: skip
        stack.enter_context(
            _process(
                [sys.executable, "-m", "penjaga_sim.cli", "serve", "--port", str(sim_port),
                 "--stream-url", stream, "--data-dir", str(tmp_path / "sim"), "--seed", "7"],
                tmp_path / "sim.log",
                f"{sim}/sim/v1/health",
            )
        )  # fmt: skip
        yield stream, sim


def test_injected_burst_reaches_the_stream_service(
    services: tuple[str, str], tmp_path: Path
) -> None:
    stream, sim = services

    def published() -> datetime | None:
        value = _call("GET", f"{sim}/sim/v1/status")["published_until"]
        return None if value is None else datetime.fromisoformat(value)

    _wait(
        "first readings",
        lambda: (published() or at(START)) >= at(START, 8.0) + timedelta(minutes=5),
    )
    catalogue = {e["fault_type"]: e for e in _call("GET", f"{sim}/sim/v1/faults/catalogue")}
    spec = {
        "fault_type": "W5",
        "location_id": catalogue["W5"]["locations"][0],
        "magnitude": {"value": 12.0, "unit": "L/min"},
        "progression": {"kind": "constant"},
        "start_at": None,
    }
    view = _call("POST", f"{sim}/sim/v1/faults", spec)
    start = datetime.fromisoformat(view["start"])
    _wait("burst published", lambda: (published() or start) >= start + timedelta(minutes=20))

    def flow(t0: datetime, t1: datetime) -> dict[str, Any]:
        query = urllib.parse.urlencode(
            {"stream": "water.main.flow_lpm", "from": t0.isoformat(), "to": t1.isoformat(),
             "bucket_s": (t1 - t0).total_seconds()}
        )  # fmt: skip
        points = _call("GET", f"{stream}/stream/v1/readings/summary?{query}")["points"]
        assert len(points) == 1
        return dict(points[0])

    during = flow(start + timedelta(minutes=1), start + timedelta(minutes=16))
    assert during["n"] >= 80  # 10 s samples over 15 min, minus any radio dropouts
    assert during["min"] >= 11.5  # the burst never stops; household use only adds to it
    health = json.loads(urllib.request.urlopen(f"{stream}/stream/v1/health", timeout=5).read())
    assert health["rejected"] == 0
    truth = _call("GET", f"{sim}/sim/v1/truth")
    assert [r["label"] for r in truth if r["kind"] == "fault"] == ["W5"]
    store = tmp_path / "sim" / "episode-0000" / "truth.jsonl"
    assert any('"W5"' in line for line in store.read_text(encoding="utf-8").splitlines())
