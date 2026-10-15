"""End-to-end: the real service process (uvicorn) driven over the network."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidHandshake

from penjaga_stream.config import Role
from penjaga_stream.testing import at, batch_doc

TOKENS = {role: f"process-token-{role.value}-0123456789" for role in Role}
CURRENT = "elec.circuit.circ_sockets_kitchen.current_a"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def server(tmp_path: Path) -> Iterator[str]:
    port = _free_port()
    env = {
        **os.environ,
        "PYTHONUTF8": "1",
        **{f"PENJAGA_TOKEN_{r.value.upper()}": t for r, t in TOKENS.items()},
    }
    cmd = [
        sys.executable, "-m", "penjaga_stream.cli", "serve",
        "--port", str(port), "--data-dir", str(tmp_path / "data"),
        "--mode", "accelerated", "--start", at(0).isoformat(), "--speed", "600",
    ]  # fmt: skip
    log_path = tmp_path / "server.log"
    with log_path.open("wb") as log:
        proc = subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT)
        base = f"http://127.0.0.1:{port}"
        try:
            deadline = time.monotonic() + 30
            while True:
                try:
                    with urllib.request.urlopen(f"{base}/stream/v1/health", timeout=1) as r:
                        if r.status == 200:
                            break
                except OSError:
                    if proc.poll() is not None or time.monotonic() > deadline:
                        log.flush()
                        out = log_path.read_text(encoding="utf-8", errors="replace")
                        raise RuntimeError("server did not start:\n" + out) from None
                    time.sleep(0.2)
            yield base
        finally:
            proc.terminate()
            proc.wait(timeout=15)


def _get(base: str, path: str, role: Role, **params: Any) -> Any:
    url = f"{base}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKENS[role]}"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def test_ingest_subscribe_and_query_over_the_network(server: str):
    ws_base = server.replace("http", "ws", 1)

    async def scenario() -> list[dict[str, Any]]:
        headers = {"Authorization": f"Bearer {TOKENS[Role.detector]}"}
        async with connect(f"{ws_base}/stream/v1/subscribe", additional_headers=headers) as sub:
            first = json.loads(await sub.recv())
            producer_headers = {"Authorization": f"Bearer {TOKENS[Role.device]}"}
            async with connect(
                f"{ws_base}/stream/v1/ingest", additional_headers=producer_headers
            ) as prod:
                for seq in range(3):
                    doc = batch_doc(CURRENT, at(seq * 60), [float(seq)] * 60, seq=seq)
                    await prod.send(json.dumps({"type": "batch", "data": doc}))
            got = [json.loads(await asyncio.wait_for(sub.recv(), 10)) for _ in range(3)]
            return [first, *got]

    messages = asyncio.run(scenario())
    assert messages[0]["type"] == "clock"
    assert messages[0]["data"]["mode"] == "accelerated"
    assert [m["data"]["seq"] for m in messages[1:]] == [0, 1, 2]
    summary = _get(
        server, "/stream/v1/readings/summary", Role.app,
        stream=CURRENT, bucket_s=60, **{"from": at(0).isoformat(), "to": at(180).isoformat()},
    )  # fmt: skip
    assert [(p["n"], p["mean"]) for p in summary["points"]] == [(60, 0.0), (60, 1.0), (60, 2.0)]


def test_wrong_role_cannot_open_ingest(server: str):
    ws_base = server.replace("http", "ws", 1)

    async def attempt() -> None:
        headers = {"Authorization": f"Bearer {TOKENS[Role.app]}"}
        async with connect(f"{ws_base}/stream/v1/ingest", additional_headers=headers) as ws:
            await ws.recv()

    with pytest.raises((InvalidHandshake, ConnectionError)):
        asyncio.run(attempt())
