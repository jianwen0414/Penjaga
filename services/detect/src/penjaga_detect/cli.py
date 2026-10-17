"""``penjaga-detect``: the live detection service (M5).

    penjaga-detect serve --dev-tokens var/dev-tokens.json   # local, with `penjaga-stream --dev`

Start M4 first (``penjaga-stream serve --dev``), then the simulator, then this service (port
8703). Tokens come from the environment (``PENJAGA_TOKEN_DETECTOR`` to reach M4;
``PENJAGA_TOKEN_APP`` and ``PENJAGA_TOKEN_OPERATOR`` accepted from callers), or from M4's dev
tokens file. The detection service holds no simulator token and never sees ground truth.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import uvicorn

from penjaga_contract.core import StreamKind
from penjaga_contract.validate import load_context
from penjaga_detect.api import create_app
from penjaga_detect.deployment import DEFAULT_PATH, Deployment
from penjaga_detect.detector import HouseContext
from penjaga_detect.localise_api import LocaliserSetup
from penjaga_detect.m4 import HttpM4
from penjaga_detect.service import DetectConfig, DetectService, StreamInfo
from penjaga_detect.store import FindingStore

REPO = Path(__file__).resolve().parents[4]


def _tokens(dev_tokens: Path | None) -> dict[str, str]:
    if dev_tokens is not None:
        data = json.loads(dev_tokens.read_text(encoding="utf-8"))
        return {role: data[role] for role in ("detector", "app", "operator") if role in data}
    found = {}
    for role in ("detector", "app", "operator"):
        value = os.environ.get(f"PENJAGA_TOKEN_{role.upper()}")
        if value:
            found[role] = value
    return found


def stream_infos() -> dict[str, StreamInfo]:
    ctx = load_context()
    return {
        sid: StreamInfo(float(s.nominal_period_s or 0.0), float(s.resolution or 0.0))
        for sid, s in ctx.streams.items()
        if s.kind is StreamKind.sampled
    }


def _cmd_serve(args: argparse.Namespace) -> int:
    tokens = _tokens(args.dev_tokens)
    callers = [tokens[r] for r in ("app", "operator") if r in tokens]
    if "detector" not in tokens or not callers:
        print(
            "need a detector token and an app or operator token: set PENJAGA_TOKEN_DETECTOR and "
            "PENJAGA_TOKEN_APP / PENJAGA_TOKEN_OPERATOR, or pass --dev-tokens",
            file=sys.stderr,
        )
        return 2
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    house = HouseContext.canonical()
    deployment = Deployment.load(args.deployment, args.params_dir)
    m4 = HttpM4(args.stream_url, token=tokens["detector"], zone=house.zone)
    store = FindingStore(args.data_dir / "findings.sqlite")
    service = DetectService(
        m4,
        house,
        specs=deployment.specs(house),
        streams=stream_infos(),
        store=store,
        config=DetectConfig(data_dir=args.data_dir),
        localiser=LocaliserSetup.load(args.params_dir),
    )
    try:
        uvicorn.run(create_app(service, tokens=callers), host=args.host, port=args.port)
    finally:
        store.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="penjaga-detect", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sv = sub.add_parser("serve", help="run the live detection service")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8703)
    sv.add_argument("--stream-url", default="http://127.0.0.1:8701")
    sv.add_argument("--data-dir", type=Path, default=REPO / "var" / "detect")
    sv.add_argument("--deployment", type=Path, default=DEFAULT_PATH)
    sv.add_argument("--params-dir", type=Path, default=REPO / "ml" / "params")
    sv.add_argument("--dev-tokens", type=Path, help="tokens file from `penjaga-stream serve --dev`")
    args = parser.parse_args(argv)
    if args.command == "serve":
        return _cmd_serve(args)
    return 2  # pragma: no cover (argparse enforces the subcommand)


if __name__ == "__main__":
    sys.exit(main())
