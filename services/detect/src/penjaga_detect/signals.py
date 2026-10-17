"""Signal helpers shared by deterministic detectors: rolling means that span chunk
boundaries, and an escalate-only tier tracker whose decisions sit at sample times (so they do
not depend on how house time is chunked)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from penjaga_detect.chunk import Series

Floats = npt.NDArray[np.float64]
Ints = npt.NDArray[np.int64]


FIXED_POINT = float(2**20)  # sums use exact integers in units of 2^-20 (about 1e-6)


def rolling_mean(
    ts: Ints, values: Floats, window_us: int, expected_samples: float, min_coverage: float
) -> Floats:
    """Mean over (t - window, t] at each sample; NaN when too few samples are present.

    Window sums are differences of running sums. In floating point those differences depend on
    where the running sum started, which depends on how house time was chunked, so a mean on a
    rounding boundary could differ between live detection and replay. The sums are therefore
    taken over integers (each value rounded to 2^-20), which makes every window's mean a
    function of its own samples only.
    """
    valid = ~np.isnan(values)
    fixed = np.rint(np.where(valid, values, 0.0) * FIXED_POINT).astype(np.int64)
    cs_exact = np.concatenate(([0], np.cumsum(fixed)))
    cn = np.concatenate(([0], np.cumsum(valid)))
    start = np.searchsorted(ts, ts - window_us, side="right")
    idx = np.arange(1, ts.size + 1)
    total = (cs_exact[idx] - cs_exact[start]) / FIXED_POINT
    count = cn[idx] - cn[start]
    out = np.full(ts.size, np.nan)
    ok = count >= min_coverage * expected_samples
    out[ok] = total[ok] / count[ok]
    return out


class Carry:
    """The recent samples of a stream, so rolling means span chunk boundaries."""

    def __init__(self, keep_us: int) -> None:
        self.keep_us = keep_us
        self.ts: Ints = np.empty(0, np.int64)
        self.values: Floats = np.empty(0, np.float64)

    def extend(self, s: Series) -> tuple[Ints, Floats, int]:
        """All carried + new samples, and the index of the first new one."""
        ts = np.concatenate((self.ts, s.ts_us))
        values = np.concatenate((self.values, s.values))
        first_new = self.ts.size
        if ts.size:
            keep = ts > ts[-1] - self.keep_us
            self.ts, self.values = ts[keep], values[keep]
        return ts, values, first_new

    def extend_untrimmed(self, s: Series) -> tuple[Ints, Floats, int]:
        """Like ``extend`` but keep everything until ``trim``: for detectors that look back
        from decision times inside the chunk rather than from its last sample."""
        first_new = self.ts.size
        self.ts = np.concatenate((self.ts, s.ts_us))
        self.values = np.concatenate((self.values, s.values))
        return self.ts, self.values, first_new

    def trim(self, now_us: int) -> None:
        keep = self.ts > now_us - self.keep_us
        self.ts, self.values = self.ts[keep], self.values[keep]


@dataclass
class TierState:
    open: bool = False
    max_tier: int = 0
    last_active_us: int = 0


def track_tiers(
    state: TierState,
    ts: Ints,
    tiers: Ints,
    *,
    chunk_end_us: int,
    clear_after_us: int,
    on_raise: Callable[[int, int, int], None],
    on_clear: Callable[[int], None],
) -> None:
    """Open on the first active sample, raise on each new maximum tier, clear after a quiet
    ``clear_after_us``. Calls on_raise(index, time, tier) and on_clear(time) in time order."""
    i = 0
    while True:
        if not state.open:
            active = np.flatnonzero(tiers[i:] > 0)
            if not active.size:
                return
            j = i + int(active[0])
            state.open, state.max_tier, state.last_active_us = True, int(tiers[j]), int(ts[j])
            on_raise(j, int(ts[j]), int(tiers[j]))
            i = j + 1
            continue
        active = i + np.flatnonzero(tiers[i:] > 0)
        times = np.concatenate(([state.last_active_us], ts[active]))
        gaps = np.flatnonzero(np.diff(times) > clear_after_us)
        clear_at: int | None = None
        if gaps.size:
            clear_at = int(times[gaps[0]]) + clear_after_us
        elif chunk_end_us - int(times[-1]) >= clear_after_us:
            clear_at = int(times[-1]) + clear_after_us
        higher = active[tiers[active] > state.max_tier]
        if higher.size and (clear_at is None or ts[higher[0]] < clear_at):
            j = int(higher[0])
            state.max_tier, state.last_active_us = int(tiers[j]), int(ts[j])
            on_raise(j, int(ts[j]), int(tiers[j]))
            i = j + 1
            continue
        if clear_at is not None:
            on_clear(clear_at)
            state.open, state.max_tier = False, 0
            i = int(np.searchsorted(ts, clear_at, side="right"))
            continue
        state.last_active_us = int(times[-1])
        return
