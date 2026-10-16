"""Live-path latency (PRD §14): wall seconds from house time reaching an event to its finding
arriving at an SSE client.

    uv run python ml/tools/latency.py --speed 60 --events 20

Starts M4, the simulator and detection as real processes on free ports. It then injects
``--events`` short power cuts (U3), which the L5 rule stamps at the exact moment mains is lost,
so no evidence window is involved. The tool:
- records M4's clock (house time against wall time) throughout;
- reads ``/detect/v1/findings/stream`` as a client would;
- for each finding, computes when house time reached its ``first_seen`` and how long after that
  the event arrived.

The path measured is: simulator publishing step (``poll_interval_s``), M4 ingest and fan-out,
detection's completeness rule and detectors, SQLite, and SSE. It is end to end and so
conservative. PRD §14 asks for the part after a reading is processed.
"""

from __future__ import annotations

import argparse
import contextlib
import itertools
import json
import os
import shutil
import socket
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2

ROOT = Path(__file__).resolve().parents[2]
TOKENS = {
    "PENJAGA_TOKEN_OPERATOR": "latency-operator-token-0123456789",
    "PENJAGA_TOKEN_DEVICE": "latency-device-token-0123456789",
    "PENJAGA_TOKEN_DETECTOR": "latency-detector-token-0123456789",
    "PENJAGA_TOKEN_APP": "latency-app-token-0123456789",
}


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _call(method: str, url: str, body: Any = None, role: str = "OPERATOR") -> Any:
    data = None if body is None else json.dumps(body).encode()
    headers = {
        "Authorization": f"Bearer {TOKENS[f'PENJAGA_TOKEN_{role}']}",
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


@contextmanager
def _process(args: list[str], health: str, log: Path) -> Iterator[None]:
    env = {**os.environ, "PYTHONUTF8": "1", **TOKENS}
    with log.open("wb") as fh:
        proc = subprocess.Popen([sys.executable, "-m", *args], env=env, stdout=fh,
                                stderr=subprocess.STDOUT, cwd=ROOT)  # fmt: skip
        try:
            deadline = time.monotonic() + 90
            while True:
                if proc.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError(log.read_text(encoding="utf-8", errors="replace"))
                try:
                    with urllib.request.urlopen(health, timeout=1):
                        break
                except OSError:
                    time.sleep(0.3)
            yield
        finally:
            proc.terminate()
            proc.wait(timeout=15)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--speed", type=float, default=60.0)
    parser.add_argument("--events", type=int, default=20)
    parser.add_argument("--gap-min", type=float, default=20.0, help="house minutes between cuts")
    parser.add_argument("--out", type=Path, default=ROOT / "var" / "eval" / "latency")
    args = parser.parse_args(argv)
    for part in ("stream", "sim", "detect"):  # every run starts from empty stores
        shutil.rmtree(args.out / part, ignore_errors=True)
    args.out.mkdir(parents=True, exist_ok=True)
    ports = [_port() for _ in range(3)]
    stream, sim, detect = (f"http://127.0.0.1:{p}" for p in ports)
    start = "2026-10-01T06:00:00+08:00"
    clock_samples: list[tuple[float, datetime]] = []
    arrivals: list[tuple[float, dict[str, Any]]] = []
    stop = threading.Event()

    def watch_clock() -> None:
        while not stop.is_set():
            t = time.monotonic()
            house = datetime.fromisoformat(_call("GET", f"{stream}/stream/v1/clock")["house_time"])
            clock_samples.append(((t + time.monotonic()) / 2, house))
            time.sleep(0.05)

    def watch_sse() -> None:
        with contextlib.suppress(httpx2.HTTPError):  # the services stop under it at the end
            read_sse()

    def read_sse() -> None:
        headers = {"Authorization": f"Bearer {TOKENS['PENJAGA_TOKEN_APP']}"}
        with httpx2.stream("GET", f"{detect}/detect/v1/findings/stream", headers=headers,
                           timeout=None) as r:  # fmt: skip
            for line in r.iter_lines():
                if stop.is_set():
                    return
                if line.startswith("data: "):
                    arrivals.append((time.monotonic(), json.loads(line[6:])))

    with ExitStack() as stack:
        stack.enter_context(_process(
            ["penjaga_stream.cli", "serve", "--port", str(ports[0]), "--data-dir",
             str(args.out / "stream"), "--mode", "accelerated", "--start", start, "--speed",
             str(args.speed)],
            f"{stream}/stream/v1/health", args.out / "stream.log"))  # fmt: skip
        stack.enter_context(_process(
            ["penjaga_sim.cli", "serve", "--port", str(ports[1]), "--stream-url", stream,
             "--data-dir", str(args.out / "sim"), "--seed", "3"],
            f"{sim}/sim/v1/health", args.out / "sim.log"))  # fmt: skip
        stack.enter_context(_process(
            ["penjaga_detect.cli", "serve", "--port", str(ports[2]), "--stream-url", stream,
             "--data-dir", str(args.out / "detect")],
            f"{detect}/detect/v1/health", args.out / "detect.log"))  # fmt: skip
        threads = [threading.Thread(target=f, daemon=True) for f in (watch_clock, watch_sse)]
        for t in threads:
            t.start()
        time.sleep(2)
        now = datetime.fromisoformat(_call("GET", f"{stream}/stream/v1/clock")["house_time"])
        first = now + timedelta(minutes=10)
        for i in range(args.events):
            at = first + timedelta(minutes=args.gap_min * i)
            _call("POST", f"{sim}/sim/v1/faults", {
                "fault_type": "U3", "location_id": None, "magnitude": {"value": 1, "unit": "1"},
                "progression": {"kind": "constant"}, "start_at": at.isoformat(),
                "duration_s": 120})  # fmt: skip
        last = first + timedelta(minutes=args.gap_min * (args.events - 1) + 30)
        while not clock_samples or clock_samples[-1][1] < last:
            time.sleep(0.5)
        time.sleep(3)
        state = {
            "health": _call("GET", f"{detect}/detect/v1/health", role="APP"),
            "diagnostics": _call("GET", f"{detect}/detect/v1/diagnostics", role="APP"),
        }
        stop.set()

    def wall_at(house: datetime) -> float:
        for (w0, h0), (w1, h1) in itertools.pairwise(clock_samples):
            if h0 <= house <= h1 and h1 > h0:
                return w0 + (w1 - w0) * (house - h0) / (h1 - h0)
        raise ValueError(f"{house} outside the clock record")

    latencies = [
        w - wall_at(datetime.fromisoformat(doc["first_seen"]))
        for w, doc in arrivals
        if doc["type"] == "power_supply_interruption" and doc["revision"] == 1
    ]
    latencies.sort()
    print("latencies (s):", [round(x, 3) for x in latencies])
    result = {
        "speed": args.speed,
        "events": args.events,
        "findings": len(latencies),
        "median_s": round(statistics.median(latencies), 3),
        "p95_s": round(latencies[min(len(latencies) - 1, int(0.95 * len(latencies)))], 3),
        "max_s": round(latencies[-1], 3),
        "detection": state,
    }
    print(json.dumps(result, indent=1))
    (args.out / f"latency_{args.speed:g}x.json").write_text(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
