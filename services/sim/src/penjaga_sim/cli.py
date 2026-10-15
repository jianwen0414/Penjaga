"""Command line for the M2 simulator.

  penjaga-sim serve --dev-tokens var/dev-tokens.json     # live; M4 from `penjaga-stream --dev`
  penjaga-sim catalogue                                  # injectable faults (JSON)
  penjaga-sim random-fault W2 --start 2026-10-16T09:00:00+08:00 --seed 3
  penjaga-sim episode manifest.json out/ep_001 [--overwrite]

`serve` takes its tokens from PENJAGA_TOKEN_OPERATOR (accepted on /sim/v1, and used for M4's
clock and reset) and PENJAGA_TOKEN_DEVICE (M4 ingest), or from the file `penjaga-stream serve
--dev` writes.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import uvicorn
from pydantic import ValidationError

from penjaga_contract.lab import EpisodeManifest, FaultType
from penjaga_contract.validate import load_index
from penjaga_sim import GENERATOR_VERSION
from penjaga_sim.api import create_app
from penjaga_sim.calibration import load_calibration
from penjaga_sim.export import run_episode
from penjaga_sim.faults import catalogue, random_spec, spec_document
from penjaga_sim.model import HouseModel
from penjaga_sim.rng import rng
from penjaga_sim.service import SimConfig, SimService
from penjaga_sim.stream_client import HttpStreamClient
from penjaga_sim.world import WorldError

MIN_TOKEN_CHARS = 16


def _model(seed: int) -> HouseModel:
    return HouseModel(load_index(), load_calibration(), seed)


def _house_time(text: str) -> datetime:
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        raise argparse.ArgumentTypeError("house times need a UTC offset, e.g. +08:00")
    return moment


def _cmd_catalogue(_: argparse.Namespace) -> int:
    print(json.dumps(catalogue(_model(0)), indent=2))
    return 0


def _cmd_random_fault(args: argparse.Namespace) -> int:
    spec = random_spec(
        _model(args.seed), args.fault_type, args.start, rng(args.seed, "cli", "random_fault")
    )
    print(json.dumps(spec_document(spec), indent=2))
    return 0


def _cmd_episode(args: argparse.Namespace) -> int:
    try:
        manifest = EpisodeManifest.model_validate_json(args.manifest.read_text(encoding="utf-8"))
    except ValidationError as exc:
        print(f"invalid manifest: {exc}", file=sys.stderr)
        return 2
    try:
        summary = run_episode(manifest, args.out, overwrite=args.overwrite)
    except (WorldError, FileExistsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary))
    return 0


def _tokens(args: argparse.Namespace) -> tuple[str, str] | None:
    if args.dev_tokens is not None:
        data = json.loads(args.dev_tokens.read_text(encoding="utf-8"))
        operator, device = data.get("operator", ""), data.get("device", "")
    else:
        operator = os.environ.get("PENJAGA_TOKEN_OPERATOR", "")
        device = os.environ.get("PENJAGA_TOKEN_DEVICE", "")
    if len(operator) < MIN_TOKEN_CHARS or len(device) < MIN_TOKEN_CHARS:
        return None
    return operator, device


def _cmd_serve(args: argparse.Namespace) -> int:
    tokens = _tokens(args)
    if tokens is None:
        print(
            "need operator and device tokens (16+ characters): set PENJAGA_TOKEN_OPERATOR and "
            "PENJAGA_TOKEN_DEVICE, or pass --dev-tokens",
            file=sys.stderr,
        )
        return 2
    operator, device = tokens
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    sensors = None if args.sensors is None else frozenset(args.sensors.split(","))
    config = SimConfig(
        data_dir=args.data_dir, seed=args.seed, archetype=args.archetype, enabled_sensors=sensors
    )
    client = HttpStreamClient(args.stream_url, device_token=device, operator_token=operator)
    app = create_app(SimService(config, client), operator_token=operator)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="penjaga-sim",
        description=f"Penjaga M2 house simulator ({GENERATOR_VERSION})",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sv = sub.add_parser("serve", help="run the live simulator and its /sim/v1 control plane")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8702)
    sv.add_argument("--stream-url", default="http://127.0.0.1:8701")
    sv.add_argument("--data-dir", type=Path, default=Path("var/sim"), help="truth store")
    sv.add_argument("--seed", type=int, default=0)
    sv.add_argument("--archetype", default="working_family")
    sv.add_argument("--sensors", help="comma-separated sensor ids (default: the full kit)")
    sv.add_argument("--dev-tokens", type=Path, help="tokens file from `penjaga-stream serve --dev`")
    sv.set_defaults(func=_cmd_serve)
    sub.add_parser("catalogue", help="list injectable faults").set_defaults(func=_cmd_catalogue)
    rf = sub.add_parser("random-fault", help="draw a plausible FaultSpec")
    rf.add_argument("fault_type", choices=[t.value for t in FaultType])
    rf.add_argument("--start", type=_house_time, required=True, help="house time")
    rf.add_argument("--seed", type=int, default=0)
    rf.set_defaults(func=_cmd_random_fault)
    ep = sub.add_parser("episode", help="generate an EpisodeManifest into a directory")
    ep.add_argument("manifest", type=Path)
    ep.add_argument("out", type=Path)
    ep.add_argument("--overwrite", action="store_true")
    ep.set_defaults(func=_cmd_episode)
    args = parser.parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
