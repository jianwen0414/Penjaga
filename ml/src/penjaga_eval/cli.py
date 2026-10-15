"""``penjaga-eval``: the M11 harness from one command.

  penjaga-eval episodes --suite l1_v0 --split validation      # generate (cached)
  penjaga-eval tune --suite l1_v0 --detector l1_mnf_baseline \\
      --grid threshold_lpm=0.02,0.05,0.1 --grid nights=1,2,3 --budget 0.5
  penjaga-eval run --suite l1_v0 --split validation --detector l1_mnf_baseline
  penjaga-eval seal --suite l1_v0                              # fix the test manifests
  penjaga-eval run --suite l1_v0 --split test --detector l1_mnf_baseline --markdown out.md

Test runs need a sealed suite and are appended to ml/runs/test-log.jsonl.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from penjaga_eval import episodes, sealing
from penjaga_eval.markdown import render
from penjaga_eval.run import evaluate, headline, write
from penjaga_eval.suites import SPLITS, load_suite, manifests
from penjaga_eval.tune import tune


def _value(text: str) -> Any:
    for cast in (int, float):
        try:
            return cast(text)
        except ValueError:
            pass
    return text


def _params(items: list[str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in items:
        target, _, value = item.partition("=")
        detector, _, name = target.rpartition(".")
        out.setdefault(detector, {})[name] = _value(value)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="penjaga-eval", description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--episodes-root", type=Path, default=episodes.DEFAULT_ROOT)
    parser.add_argument("--workers", type=int)
    sub = parser.add_subparsers(dest="command", required=True)
    ep = sub.add_parser("episodes", help="generate a split's episodes")
    ep.add_argument("--suite", required=True)
    ep.add_argument("--split", choices=SPLITS, required=True)
    sl = sub.add_parser("seal", help="record the digest of the suite's test manifests")
    sl.add_argument("--suite", required=True)
    rn = sub.add_parser("run", help="evaluate detectors on a split")
    rn.add_argument("--suite", required=True)
    rn.add_argument("--split", choices=SPLITS, required=True)
    rn.add_argument("--detector", action="append", required=True)
    rn.add_argument("--param", action="append", default=[], help="detector.name=value")
    rn.add_argument("--limit", type=int, help="first N episodes (not for test)")
    rn.add_argument("--markdown", type=Path, help="also write a readable summary here")
    tn = sub.add_parser("tune", help="choose parameters on the validation split")
    tn.add_argument("--suite", required=True)
    tn.add_argument("--detector", required=True)
    tn.add_argument("--grid", action="append", required=True, help="name=v1,v2,...")
    tn.add_argument("--budget", type=float, required=True, help="false alarms per house-month")
    tn.add_argument("--limit", type=int)
    args = parser.parse_args(argv)

    suite = load_suite(args.suite)
    if args.command == "episodes":
        paths = episodes.ensure(manifests(suite, args.split), args.episodes_root, args.workers)
        print(f"{len(paths)} episodes under {args.episodes_root}")
    elif args.command == "seal":
        print(json.dumps(sealing.seal(suite), indent=2))
    elif args.command == "tune":
        grid = {}
        for item in args.grid:
            name, _, values = item.partition("=")
            grid[name] = [_value(v) for v in values.split(",")]
        record = tune(
            suite,
            args.detector,
            grid,
            budget_fa_per_house_month=args.budget,
            episodes_root=args.episodes_root,
            workers=args.workers,
            limit=args.limit,
        )
        print(
            json.dumps({"params": record["params"], "objective": record["tuned_on"]["objective"]})
        )
    else:
        try:
            report = evaluate(
                suite,
                args.split,
                args.detector,
                overrides=_params(args.param),
                episodes_root=args.episodes_root,
                workers=args.workers,
                limit=args.limit,
            )
        except sealing.SealError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        path = write(report)
        if args.markdown is not None:
            args.markdown.write_text(render(report) + "\n", encoding="utf-8")
        summary = {n: headline(d["metrics"]) for n, d in report["detectors"].items()}
        print(json.dumps({"report": str(path), "summary": summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
