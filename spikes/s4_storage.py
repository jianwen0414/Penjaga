"""Spike S4: raw-reading storage throughput at accelerated house time.

Question: can an embedded store keep up with the full stream catalogue at 600x house time
(1 wall second = 10 house minutes) while answering the agent/app summary queries fast?

Compared:
  A. DuckDB native table (single file), long format, Arrow batch inserts.
  B. Parquet part files (zstd), one per chunk, queried through DuckDB read_parquet.

Workload: the real catalogue (packages/contract streams.json), synthetic quantised values,
inserted in chunks of 600 house-seconds (what one wall second carries at 600x).

Run:  uv run python spikes/s4_storage.py --days 7 --out var/spikes/s4
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from penjaga_contract.resources import load_streams_document

CHUNK_S = 600  # house seconds per insert (= 1 wall second at 600x)
EPOCH_US = 1_760_000_000 * 1_000_000  # arbitrary fixed start (UTC microseconds)


def sampled_streams() -> list[dict[str, Any]]:
    return [s for s in load_streams_document()["streams"] if s["kind"] == "sampled"]


def chunk_table(streams: list[dict[str, Any]], t0_s: int, rng: np.random.Generator) -> pa.Table:
    """Long-format rows for every sample whose timestamp falls in [t0_s, t0_s + CHUNK_S)."""
    ids, ts, vals = [], [], []
    for sid, s in enumerate(streams):
        period = int(s["nominal_period_s"])
        first = ((t0_s + period - 1) // period) * period
        times = np.arange(first, t0_s + CHUNK_S, period, dtype=np.int64)
        if times.size == 0:
            continue
        hi, res = float(s["value_max"]), float(s["resolution"])
        base = rng.uniform(0, hi * 0.2)
        walk = base + np.cumsum(rng.normal(0, hi * 0.001, times.size))
        values = np.clip(np.round(walk / res) * res, 0, hi).astype(np.float32)
        ids.append(np.full(times.size, sid, dtype=np.uint16))
        ts.append(EPOCH_US + times * 1_000_000)
        vals.append(values)
    return pa.table(
        {
            "stream": pa.array(np.concatenate(ids), pa.uint16()),
            "ts": pa.array(np.concatenate(ts), pa.timestamp("us")),  # UTC, naive by convention
            "value": pa.array(np.concatenate(vals), pa.float32()),
            "quality": pa.array(np.zeros(sum(len(v) for v in vals), dtype=np.uint8), pa.uint8()),
        }
    )


def bench_duckdb(out: Path, chunks: list[pa.Table]) -> dict[str, float]:
    db = out / "readings.duckdb"
    con = duckdb.connect(str(db))
    con.execute(
        "CREATE TABLE readings (stream USMALLINT, ts TIMESTAMP, value FLOAT, quality UTINYINT)"
    )
    times = []
    for tbl in chunks:
        start = time.perf_counter()
        con.from_arrow(tbl).insert_into("readings")
        times.append(time.perf_counter() - start)
    con.execute("CHECKPOINT")
    rows = con.execute("SELECT count(*) FROM readings").fetchone()[0]  # type: ignore[index]
    res = {"rows": rows, **_insert_stats(times, chunks)}
    res.update(_queries(con, "readings"))
    con.close()
    res["disk_mb"] = db.stat().st_size / 1e6
    return res


def bench_parquet(out: Path, chunks: list[pa.Table]) -> dict[str, float]:
    pdir = out / "parquet"
    pdir.mkdir(parents=True, exist_ok=True)
    times = []
    for i, tbl in enumerate(chunks):
        start = time.perf_counter()
        pq.write_table(tbl, pdir / f"part-{i:06d}.parquet", compression="zstd")
        times.append(time.perf_counter() - start)
    res: dict[str, float] = {"files": len(chunks), **_insert_stats(times, chunks)}
    con = duckdb.connect()
    con.execute(
        f"CREATE VIEW readings AS SELECT * FROM read_parquet('{pdir.as_posix()}/*.parquet')"
    )
    res.update(_queries(con, "readings"))
    con.close()
    res["disk_mb"] = sum(p.stat().st_size for p in pdir.glob("*.parquet")) / 1e6
    return res


def _insert_stats(times: list[float], chunks: list[pa.Table]) -> dict[str, float]:
    arr = np.array(times)
    rows = sum(t.num_rows for t in chunks)
    return {
        "insert_rows_per_s": rows / arr.sum(),
        "insert_chunk_p50_ms": float(np.percentile(arr, 50) * 1e3),
        "insert_chunk_p95_ms": float(np.percentile(arr, 95) * 1e3),
        "insert_chunk_max_ms": float(arr.max() * 1e3),
    }


def _timed(con: duckdb.DuckDBPyConnection, sql: str, repeat: int = 5) -> float:
    samples = []
    for _ in range(repeat):
        start = time.perf_counter()
        con.execute(sql).fetchall()
        samples.append(time.perf_counter() - start)
    return float(np.median(samples) * 1e3)


def _queries(con: duckdb.DuckDBPyConnection, table: str) -> dict[str, float]:
    day = "INTERVAL 1 DAY"
    t0 = f"make_timestamp({EPOCH_US})"
    summary_day = f"""
        SELECT epoch_us(time_bucket(INTERVAL 60 SECOND, ts)) AS b,
               min(value), max(value), avg(value), arg_max(value, ts), count(*)
        FROM {table} WHERE stream = 7 AND ts >= {t0} + {day} AND ts < {t0} + 2 * {day}
        GROUP BY b ORDER BY b"""
    summary_week = f"""
        SELECT epoch_us(time_bucket(INTERVAL 1210 SECOND, ts)) AS b,
               min(value), max(value), avg(value), arg_max(value, ts), count(*)
        FROM {table} WHERE stream = 7 GROUP BY b ORDER BY b"""
    raw_hour = f"""
        SELECT epoch_us(ts), value FROM {table}
        WHERE stream = 7 AND ts >= {t0} + {day} AND ts < {t0} + {day} + INTERVAL 1 HOUR
        ORDER BY ts"""
    return {
        "q_summary_1day_1440pts_ms": _timed(con, summary_day),
        "q_summary_7day_500pts_ms": _timed(con, summary_week),
        "q_raw_1hour_ms": _timed(con, raw_hour),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=7)
    ap.add_argument("--out", type=Path, default=Path("var/spikes/s4"))
    ap.add_argument("--seed", type=int, default=4)
    args = ap.parse_args()
    if args.out.exists():
        shutil.rmtree(args.out)
    args.out.mkdir(parents=True)
    streams = sampled_streams()
    rng = np.random.default_rng(args.seed)
    n_chunks = int(args.days * 86400 // CHUNK_S)
    gen_start = time.perf_counter()
    chunks = [chunk_table(streams, i * CHUNK_S, rng) for i in range(n_chunks)]
    gen_s = time.perf_counter() - gen_start
    rows = sum(t.num_rows for t in chunks)
    result = {
        "days": args.days,
        "streams_sampled": len(streams),
        "rows": rows,
        "rows_per_house_day": rows / args.days,
        "chunks": n_chunks,
        "rows_per_chunk_mean": rows / n_chunks,
        "required_rows_per_wall_s_at_600x": rows / n_chunks,
        "generation_s": gen_s,
        "duckdb_native": bench_duckdb(args.out, chunks),
        "parquet_parts": bench_parquet(args.out, chunks),
        "versions": {
            "duckdb": duckdb.__version__,
            "pyarrow": pa.__version__,
            "numpy": np.__version__,
        },
    }
    (args.out / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
