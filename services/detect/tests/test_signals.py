"""Rolling means are exact functions of their window, however the history was carried."""

from __future__ import annotations

import numpy as np

from penjaga_detect.signals import rolling_mean

US = 1_000_000


def test_rolling_mean_does_not_depend_on_where_the_history_starts() -> None:
    r = np.random.default_rng(3)
    n = 5000
    ts = (np.arange(n, dtype=np.int64) + 1) * US
    values = np.round(r.uniform(0, 40, n), 2)  # quantised like a current stream
    values[r.random(n) < 0.02] = np.nan
    window = 900 * US
    full = rolling_mean(ts, values, window, 900, 0.5)
    for offset in (1, 17, 901, 2500):
        part = rolling_mean(ts[offset:], values[offset:], window, 900, 0.5)
        settled = np.searchsorted(ts[offset:], ts[offset] + window)  # full windows only
        assert np.array_equal(part[settled:], full[offset + settled :], equal_nan=True)


def test_rolling_mean_values() -> None:
    ts = np.array([1, 2, 3, 4], dtype=np.int64) * US
    values = np.array([1.0, np.nan, 3.0, 5.0])
    out = rolling_mean(ts, values, 2 * US, 2, 0.5)
    assert np.allclose(out, [1.0, 1.0, 3.0, 4.0])
