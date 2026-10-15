"""``penjaga-stream``: run the stream service (M4).

    penjaga-stream serve --dev        # local run; random tokens written to var/dev-tokens.json
    penjaga-stream serve --mode accelerated --start 2026-10-16T00:00:00+08:00 --speed 600

Without --dev, tokens come from PENJAGA_TOKEN_OPERATOR / _DEVICE / _DETECTOR / _APP.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import uvicorn

from penjaga_contract.core import ClockMode
from penjaga_contract.validate import load_context
from penjaga_stream.api import create_app
from penjaga_stream.clock import HouseClock
from penjaga_stream.config import StreamConfig, generate_dev_tokens, tokens_from_env
from penjaga_stream.service import StreamService
from penjaga_stream.timeutil import house_zone, to_us

DEV_TOKENS = Path("var/dev-tokens.json")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="penjaga-stream", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="run the HTTP/WebSocket service")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8701)
    serve.add_argument("--data-dir", type=Path, default=Path("var/stream"))
    serve.add_argument("--mode", choices=[m.value for m in ClockMode], default="real")
    serve.add_argument("--start", type=datetime.fromisoformat, help="house time to start at")
    serve.add_argument("--speed", type=float, default=1.0)
    serve.add_argument("--dev", action="store_true", help=f"generate tokens into {DEV_TOKENS}")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.dev:
        tokens = generate_dev_tokens(DEV_TOKENS)
        print(f"dev tokens written to {DEV_TOKENS}", file=sys.stderr)
    else:
        tokens = tokens_from_env()
        if not tokens:
            print("no tokens: set PENJAGA_TOKEN_<ROLE> or use --dev", file=sys.stderr)
            return 2
    ctx = load_context()
    zone = house_zone(ctx.house.location.timezone)
    mode = ClockMode(args.mode)
    clock = None
    if mode is ClockMode.real and (args.start is not None or args.speed != 1.0):
        print("real mode runs at wall time: --start and --speed are not allowed", file=sys.stderr)
        return 2
    if mode is ClockMode.accelerated:
        if args.start is None or args.start.tzinfo is None:
            print("accelerated mode needs --start with a UTC offset", file=sys.stderr)
            return 2
        clock = HouseClock(start_us=to_us(args.start), mode=mode, speed=args.speed, zone=zone)
    config = StreamConfig(data_dir=args.data_dir, tokens=tokens)
    service = StreamService(config, ctx=ctx, clock=clock)
    uvicorn.run(create_app(service), host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
