"""How much legitimate water flows at night in a suite's houses? (describes a suite; no detection)

    uv run python ml/tools/night_flow.py --suite l1_v0 --suite l1_lowp_v0 --split validation

For every night (02:00-04:00 house time, the minimum-night-flow window) before any water fault
starts, from the per-minute truth (``truth/water.parquet``): the minimum 15-minute mean of the
true meter flow, and whether the roof tank was refilling. A night whose minimum exceeds a leak
threshold would look like a leak to a night-minimum rule. Reads truth, so it describes the
population; it never runs on the sealed test split.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pyarrow.parquet as pq

from penjaga_contract.validate import load_context
from penjaga_eval import episodes
from penjaga_eval.scoring import load_truth
from penjaga_eval.suites import load_suite, manifests

US = 1_000_000
THRESHOLDS = (0.02, 0.05, 0.1)


def night_minima(episode: Path, zone: ZoneInfo) -> list[tuple[float, float]]:
    """(min 15-min mean true flow L/min, mean tank inflow L/min) per fault-free night."""
    table = pq.read_table(episode / "truth" / "water.parquet")
    ts = np.asarray(table.column("ts_us").to_numpy(), dtype=np.int64)
    flow = np.asarray(table.column("meter_true_lpm").to_numpy(), dtype=np.float64)
    inflow = np.asarray(table.column("tank_inflow_lpm").to_numpy(), dtype=np.float64)
    faults, _, _ = load_truth(episode)
    first_fault = min((f.start_us for f in faults), default=int(ts[-1]) + 1)
    out = []
    day = datetime.fromtimestamp(ts[0] / US, tz=zone).date()
    last = datetime.fromtimestamp(ts[-1] / US, tz=zone).date()
    while day <= last:
        start = int(datetime.combine(day, time(2), tzinfo=zone).timestamp()) * US
        end = start + 2 * 3600 * US
        sel = (ts > start) & (ts <= end)
        if sel.sum() == 120 and end <= first_fault:
            buckets = flow[sel].reshape(8, 15).mean(axis=1)
            out.append((float(buckets.min()), float(inflow[sel].mean())))
        day += timedelta(days=1)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", action="append", required=True)
    parser.add_argument("--split", choices=("train", "validation"), default="validation")
    parser.add_argument("--episodes-root", type=Path, default=episodes.DEFAULT_ROOT)
    args = parser.parse_args()
    zone = ZoneInfo(load_context().house.location.timezone)
    for name in args.suite:
        suite = load_suite(name)
        paths = episodes.ensure(
            manifests(suite, args.split), args.episodes_root, None, suite.streams
        )
        rows = [r for p in paths for r in night_minima(p, zone)]
        minima = np.array([r[0] for r in rows])
        refilling = np.array([r[1] for r in rows]) > 0.01
        summary = {
            "suite": name,
            "split": args.split,
            "nights": len(rows),
            "share_refilling_02_04": round(float(refilling.mean()), 4),
            **{
                f"share_min_flow_above_{t}": round(float((minima > t).mean()), 4)
                for t in THRESHOLDS
            },
            "p99_min_flow_lpm": round(float(np.percentile(minima, 99)), 4),
            "max_min_flow_lpm": round(float(minima.max()), 4),
        }
        print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
