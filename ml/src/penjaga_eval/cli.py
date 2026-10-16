"""``penjaga-eval``: the M11 harness from one command.

  penjaga-eval episodes --suite l1_v0 --split validation      # generate (cached)
  penjaga-eval tune --suite l1_v0 --detector l1_mnf_baseline \\
      --grid threshold_lpm=0.02,0.05,0.1 --grid nights=1,2,3 --budget 0.5
  penjaga-eval run --suite l1_v0 --split validation --detector l1_mnf_baseline
  penjaga-eval seal --suite l1_v0                              # fix the test manifests
  penjaga-eval run --suite l1_v0 --split test --detector l1_mnf_baseline --markdown out.md
  penjaga-eval serve --dev-tokens var/dev-tokens.json          # /eval/v1 for the Lab (8704)

Test runs need a sealed suite and are appended to ml/runs/test-log.jsonl.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import uvicorn

from penjaga_eval import episodes, registry, sealing, train_l1
from penjaga_eval.api import create_app
from penjaga_eval.markdown import render
from penjaga_eval.run import DEFAULT_RUNS, evaluate, headline, write
from penjaga_eval.service import EvalConfig, EvalService
from penjaga_eval.suites import SPLITS, SUITES_DIR, load_suite, manifests
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="penjaga-eval", description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--episodes-root", type=Path, default=episodes.DEFAULT_ROOT)
    parser.add_argument("--workers", type=int)
    parser.add_argument("--suites-dir", type=Path, default=SUITES_DIR)
    parser.add_argument("--sealed-dir", type=Path, default=sealing.SEALED_DIR)
    parser.add_argument("--test-log", type=Path, default=sealing.TEST_LOG)
    parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS)
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
    tr = sub.add_parser("train-l1", help="train the L1 leak model on the train split")
    tr.add_argument("--suite", required=True)
    tr.add_argument("--seed", type=int, default=0)
    tn = sub.add_parser("tune", help="choose parameters on the validation split")
    tn.add_argument("--suite", required=True)
    tn.add_argument("--detector", required=True)
    tn.add_argument("--grid", action="append", required=True, help="name=v1,v2,...")
    tn.add_argument("--budget", type=float, required=True, help="false alarms per house-month")
    tn.add_argument("--limit", type=int)
    sv = sub.add_parser("serve", help="run the /eval/v1 service for the Lab")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8704)
    sv.add_argument("--api-runs-dir", type=Path, default=Path("var/eval/api-runs"))
    sv.add_argument("--dev-tokens", type=Path, help="tokens file from `penjaga-stream serve --dev`")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    if args.command == "serve":
        return _serve(args)
    suite = load_suite(args.suite, args.suites_dir)
    if args.command == "episodes":
        paths = episodes.ensure(
            manifests(suite, args.split), args.episodes_root, args.workers, suite.streams
        )
        print(f"{len(paths)} episodes under {args.episodes_root}")
    elif args.command == "seal":
        print(json.dumps(sealing.seal(suite, args.sealed_dir), indent=2))
    elif args.command == "train-l1":
        record = train_l1.train(
            suite, root=args.episodes_root, seed=args.seed, workers=args.workers
        )
        # the artifact path joins any tuned parameters (the threshold is tuned afterwards)
        tuned = {**registry.tuned("l1_leak_model"), "artifact": record["artifact"]}
        params_path = registry.PARAMS_DIR / "l1_leak_model.json"
        params_path.parent.mkdir(parents=True, exist_ok=True)
        params_path.write_text(
            json.dumps({"detector": "l1_leak_model", "params": tuned}, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(json.dumps(record["trained_on"], indent=2))
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
                sealed_dir=args.sealed_dir,
                test_log=args.test_log,
                limit=args.limit,
            )
        except sealing.SealError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        path = write(report, args.runs_dir)
        if args.markdown is not None:
            args.markdown.write_text(render(report) + "\n", encoding="utf-8", newline="\n")
        summary = {n: headline(d["metrics"]) for n, d in report["detectors"].items()}
        print(json.dumps({"report": str(path), "summary": summary}, indent=2))
    return 0


def _serve(args: argparse.Namespace) -> int:
    """The /eval/v1 service: operator token from PENJAGA_TOKEN_OPERATOR or --dev-tokens."""
    if args.dev_tokens is not None:
        token = json.loads(args.dev_tokens.read_text(encoding="utf-8")).get("operator")
    else:
        token = os.environ.get("PENJAGA_TOKEN_OPERATOR")
    if not token or len(token) < 16:
        print("need an operator token (16+ characters): set PENJAGA_TOKEN_OPERATOR or pass "
              "--dev-tokens", file=sys.stderr)  # fmt: skip
        return 2
    service = EvalService(
        EvalConfig(
            runs_dir=args.api_runs_dir,
            suites_dir=args.suites_dir,
            episodes_root=args.episodes_root,
            sealed_dir=args.sealed_dir,
            test_log=args.test_log,
            workers=args.workers,
        )
    )
    uvicorn.run(create_app(service, operator_token=token), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
