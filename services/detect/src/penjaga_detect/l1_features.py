"""Night features for L1 leak detection (D-008, rung 2).

One row per house night: what the meter, the roof-tank level and the inlet pressure did in the
quiet window (01:00-05:00 house time), compared with the household's own recent nights. The
same code computes features for training (penjaga_eval) and for live detection, so a model sees
identical inputs in both.

Physical intent of each feature:
- mnf, q50, frac_flowing: is water moving at night at all, and how much (leaks are continuous);
- n_events, event litres, longest run, interval regularity: refill signatures (a running
  cistern refills at a regular period; a float-valve fault never stops);
- tank slope and level: a tank-fed leak drains the tank between refills; a stuck float valve
  keeps it full while water still flows;
- pressure: a large leak or burst lowers the inlet pressure;
- day_min: the lowest 15-minute flow over the previous day (continuous leaks never reach zero);
- deltas and trend against the household's previous nights (personal baseline, worsening).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from penjaga_detect.chunk import Series

US = 1_000_000
Floats = npt.NDArray[np.float64]

FEATURES: tuple[str, ...] = (
    "mnf",
    "q50",
    "frac_flowing",
    "n_events",
    "event_litres_median",
    "event_litres_max",
    "longest_run_min",
    "interval_median_min",
    "interval_cv",
    "max_flow",
    "tank_slope_pct_per_h",
    "tank_level_mean",
    "pressure_mean",
    "day_min",
    "mnf_delta",
    "frac_flowing_delta",
    "mnf_trend",
)


@dataclass(frozen=True)
class NightWindow:
    start_h: float = 1.0
    end_h: float = 5.0
    bucket_min: float = 15.0
    flow_period_s: float = 10.0
    history_nights: int = 7


def _bucket_means(
    ts: npt.NDArray[np.int64], values: Floats, t0: int, t1: int, bucket_us: int
) -> Floats:
    n = max(1, (t1 - t0) // bucket_us)
    index = np.minimum((ts - t0 - 1) // bucket_us, n - 1)
    out = np.full(n, np.nan)
    for b in range(n):
        sel = (index == b) & ~np.isnan(values)
        if sel.sum() >= 0.5 * np.sum(index == b) and sel.any():
            out[b] = float(values[sel].mean())
    return out


def _runs(flowing: npt.NDArray[np.bool_]) -> list[tuple[int, int]]:
    edges = np.flatnonzero(np.diff(np.concatenate(([0], flowing.astype(np.int8), [0]))))
    return [(int(a), int(b)) for a, b in zip(edges[::2], edges[1::2], strict=True)]


def night_row(
    flow: Series, tank: Series, pressure: Series, *, t0: int, t1: int, window: NightWindow
) -> dict[str, float]:
    """Features of one night window (t0, t1] that need no history."""
    nan = float("nan")
    f = flow.between(t0, t1)
    present = ~np.isnan(f.values)
    row: dict[str, float] = dict.fromkeys(FEATURES, nan)
    if present.sum() < 0.5 * (t1 - t0) / US / window.flow_period_s:
        return row  # too little data to say anything about this night
    v = np.where(present, f.values, 0.0)
    bucket_us = round(window.bucket_min * 60 * US)
    means = _bucket_means(f.ts_us, f.values, t0, t1, bucket_us)
    row["mnf"] = float(np.nanmin(means)) if np.isfinite(means).any() else nan
    row["q50"] = float(np.median(f.values[present]))
    flowing = (v > 0) & present
    row["frac_flowing"] = float(flowing.sum() / present.sum())
    runs = _runs(flowing)
    row["n_events"] = float(len(runs))
    dt_min = window.flow_period_s / 60.0
    if runs:
        litres = [float(v[a:b].sum() * dt_min) for a, b in runs]
        row["event_litres_median"] = float(np.median(litres))
        row["event_litres_max"] = float(max(litres))
        row["longest_run_min"] = float(max(b - a for a, b in runs) * dt_min)
        if len(runs) >= 3:
            starts = np.array([a for a, _ in runs], dtype=np.float64) * dt_min
            intervals = np.diff(starts)
            row["interval_median_min"] = float(np.median(intervals))
            row["interval_cv"] = float(np.std(intervals) / max(np.mean(intervals), 1e-9))
    row["max_flow"] = float(f.values[present].max())
    k = tank.between(t0, t1)
    ok = ~np.isnan(k.values)
    if ok.sum() >= 10:
        hours = (k.ts_us[ok] - t0) / 3600 / US
        row["tank_slope_pct_per_h"] = float(np.polyfit(hours, k.values[ok], 1)[0])
        row["tank_level_mean"] = float(k.values[ok].mean())
    p = pressure.between(t0, t1)
    if np.isfinite(p.values).any():
        row["pressure_mean"] = float(np.nanmean(p.values))
    day = flow.between(t1 - 86_400 * US, t1)
    day_means = _bucket_means(day.ts_us, day.values, t1 - 86_400 * US, t1, bucket_us)
    if np.isfinite(day_means).any():
        row["day_min"] = float(np.nanmin(day_means))
    return row


def add_history(
    row: dict[str, float], previous: Sequence[dict[str, float]], window: NightWindow
) -> None:
    """Personal-baseline features from the household's previous nights (most recent last)."""
    recent = list(previous)[-window.history_nights :]
    for name in ("mnf", "frac_flowing"):
        past = np.array([r[name] for r in recent], dtype=np.float64)
        past = past[np.isfinite(past)]
        if past.size >= 2 and np.isfinite(row[name]):
            row[f"{name}_delta"] = float(row[name] - np.median(past))
    trend = np.array([r["mnf"] for r in recent[-3:]] + [row["mnf"]], dtype=np.float64)
    ok = np.isfinite(trend)
    if ok.sum() >= 3:
        row["mnf_trend"] = float(np.polyfit(np.flatnonzero(ok), trend[ok], 1)[0])


def as_vector(row: dict[str, float]) -> Floats:
    return np.array([row[name] for name in FEATURES], dtype=np.float64)
