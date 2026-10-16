"""Run the whole data/ML backend locally for integration: M4, simulator, detection, evaluation.

    uv run python scripts/dev_stack.py                          # house time from today, 60x
    uv run python scripts/dev_stack.py --start 2026-10-01T00:00:00+08:00 --speed 600
    uv run python scripts/dev_stack.py --real                   # house time = wall time

Starts the four services in dependency order and waits for each one's health check. Prints the
URLs and the dev token file (``var/dev-tokens.json``: one bearer token per role; never commit
it), then keeps running until Ctrl+C, which stops them all. Logs go to ``var/dev-stack/``.

| Service | URL | Callers' role |
|---|---|---|
| M4 stream and house clock | http://127.0.0.1:8701/stream/v1 | app, operator |
| M2 simulator (Lab) | http://127.0.0.1:8702/sim/v1 | operator |
| M5 detection | http://127.0.0.1:8703/detect/v1 | app, operator |
| M11 evaluation (Lab) | http://127.0.0.1:8704/eval/v1 | operator |
"""

from __future__ import annotations

import argparse
import contextlib
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator
from datetime import datetime
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
VAR = ROOT / "var"
TOKENS = VAR / "dev-tokens.json"
ZONE = ZoneInfo("Asia/Kuala_Lumpur")
PORTS = {"stream": 8701, "sim": 8702, "detect": 8703, "eval": 8704}


def _healthy(url: str, proc: subprocess.Popen[bytes], log: Path, timeout_s: float = 90) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                f"{url} exited:\n{log.read_text(encoding='utf-8', errors='replace')}"
            )
        with contextlib.suppress(OSError), urllib.request.urlopen(url, timeout=1) as r:
            if r.status == 200:
                return
        time.sleep(0.3)
    raise TimeoutError(f"{url} not healthy after {timeout_s:g} s (log: {log})")


@contextlib.contextmanager
def _service(name: str, args: list[str], health: str) -> Iterator[None]:
    logs = VAR / "dev-stack"
    logs.mkdir(parents=True, exist_ok=True)
    log = logs / f"{name}.log"
    with log.open("wb") as fh:
        proc = subprocess.Popen(
            [sys.executable, "-m", *args], cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT
        )
        try:
            _healthy(health, proc, log)
            print(
                f"  {name:<7} {health.rsplit('/', 1)[0]}  (log: {log.relative_to(ROOT)})",
                flush=True,
            )
            yield
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--start", type=datetime.fromisoformat, help="house time to start at")
    parser.add_argument("--speed", type=float, default=60.0, help="house seconds per second")
    parser.add_argument("--real", action="store_true", help="house time follows wall time")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--archetype", default="working_family")
    args = parser.parse_args(argv)
    url = {k: f"http://127.0.0.1:{p}" for k, p in PORTS.items()}
    if args.real:
        clock = ["--mode", "real"]
    else:
        start = args.start or datetime.combine(datetime.now(ZONE).date(), dtime.min, tzinfo=ZONE)
        if start.tzinfo is None:
            parser.error("--start needs a UTC offset, e.g. 2026-10-01T00:00:00+08:00")
        clock = ["--mode", "accelerated", "--start", start.isoformat(), "--speed", str(args.speed)]
    tokens = ["--dev-tokens", str(TOKENS)]
    print("Penjaga data/ML stack:", flush=True)
    with contextlib.ExitStack() as stack:
        stack.enter_context(_service(
            "stream",
            ["penjaga_stream.cli", "serve", "--dev", "--port", str(PORTS["stream"]),
             "--data-dir", str(VAR / "stream"), *clock],
            f"{url['stream']}/stream/v1/health",
        ))  # fmt: skip
        stack.enter_context(_service(
            "sim",
            ["penjaga_sim.cli", "serve", "--port", str(PORTS["sim"]), "--stream-url",
             url["stream"], "--data-dir", str(VAR / "sim"), "--seed", str(args.seed),
             "--archetype", args.archetype, *tokens],
            f"{url['sim']}/sim/v1/health",
        ))  # fmt: skip
        stack.enter_context(_service(
            "detect",
            ["penjaga_detect.cli", "serve", "--port", str(PORTS["detect"]), "--stream-url",
             url["stream"], "--data-dir", str(VAR / "detect"), *tokens],
            f"{url['detect']}/detect/v1/health",
        ))  # fmt: skip
        stack.enter_context(_service(
            "eval",
            ["penjaga_eval.cli", "serve", "--port", str(PORTS["eval"]), *tokens],
            f"{url['eval']}/eval/v1/health",
        ))  # fmt: skip
        print(
            f"Tokens (one per role): {TOKENS.relative_to(ROOT)}. API docs: <url>/docs.", flush=True
        )
        print("Running; Ctrl+C stops everything.", flush=True)
        with contextlib.suppress(KeyboardInterrupt):
            while True:
                time.sleep(1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
