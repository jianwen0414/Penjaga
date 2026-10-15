"""Command line for the M2 simulator.

penjaga-sim catalogue                      # injectable faults (JSON)
penjaga-sim random-fault W2 --start 2026-10-16T09:00:00+08:00 --seed 3
penjaga-sim episode manifest.json out/ep_001 [--overwrite]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from penjaga_contract.lab import EpisodeManifest, FaultType
from penjaga_contract.validate import load_index
from penjaga_sim import GENERATOR_VERSION
from penjaga_sim.calibration import load_calibration
from penjaga_sim.export import run_episode
from penjaga_sim.faults import catalogue, random_spec, spec_document
from penjaga_sim.model import HouseModel
from penjaga_sim.rng import rng
from penjaga_sim.world import WorldError


def _model(seed: int) -> HouseModel:
    return HouseModel(load_index(), load_calibration(), seed)


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="penjaga-sim",
        description=f"Penjaga M2 house simulator ({GENERATOR_VERSION})",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("catalogue", help="list injectable faults").set_defaults(func=_cmd_catalogue)
    rf = sub.add_parser("random-fault", help="draw a plausible FaultSpec")
    rf.add_argument("fault_type", choices=[t.value for t in FaultType])
    rf.add_argument("--start", type=datetime.fromisoformat, required=True, help="house time")
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
