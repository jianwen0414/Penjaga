"""L1 night features and the leak detector, on synthetic meter signals."""

from __future__ import annotations

import pickle
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from penjaga_contract.core import Finding
from penjaga_contract.validate import load_context, validate_finding_document
from penjaga_detect.chunk import Chunk, Series
from penjaga_detect.detector import HouseContext
from penjaga_detect.l1_features import FEATURES, NightWindow, night_row
from penjaga_detect.l1_model import FLOW, L1LeakModel, L1Params

US = 1_000_000
T0 = 1_790_784_000 * US  # 2026-10-01T00:00:00+08:00
DAY = 86_400 * US


def flow_series(days: float, value: Any) -> Series:
    """10 s flow samples from T0; ``value(ts_us)`` gives L/min."""
    ts = T0 + (np.arange(int(days * 8640), dtype=np.int64) + 1) * 10 * US
    values = np.asarray(value(ts), dtype=np.float64)
    return Series(ts, values, np.zeros(ts.size, np.uint8))


def night(ts: np.ndarray) -> np.ndarray:
    hour = ((ts - T0) % DAY) / 3600 / US
    return (hour > 1) & (hour <= 5)


def feed(detector: L1LeakModel, flow: Series, step_h: float) -> list[Finding]:
    out: list[Finding] = []
    t, end = T0, int(flow.ts_us[-1])
    while t < end:
        t1 = min(end, t + round(step_h * 3600 * US))
        out += detector.update(Chunk("elmina_g6_demo", t, t1, {FLOW: flow.between(t, t1)}))
        t = t1
    return out


def test_night_row_reads_a_continuous_leak() -> None:
    flow = flow_series(1, lambda ts: np.full(ts.size, 0.3))
    empty = Series.empty()
    row = night_row(
        flow, empty, empty, t0=T0 + 3600 * US, t1=T0 + 5 * 3600 * US, window=NightWindow()
    )
    assert row["mnf"] == pytest.approx(0.3)
    assert row["frac_flowing"] == 1.0
    assert row["n_events"] == 1
    assert np.isnan(row["tank_slope_pct_per_h"])  # no tank sensor: missing, not zero


def test_night_row_reads_periodic_refills() -> None:
    def pulses(ts: np.ndarray) -> np.ndarray:  # 1 minute at 9 L/min every 4 minutes
        return np.where(((ts - T0) // US) % 240 < 60, 9.0, 0.0)

    flow = flow_series(1, pulses)
    empty = Series.empty()
    row = night_row(
        flow, empty, empty, t0=T0 + 3600 * US, t1=T0 + 5 * 3600 * US, window=NightWindow()
    )
    assert row["interval_median_min"] == pytest.approx(4.0)
    assert row["interval_cv"] < 0.05
    assert row["mnf"] > 0  # every 15-minute bucket holds refills


def test_feature_rows_do_not_depend_on_chunking() -> None:
    flow = flow_series(4, lambda ts: np.where(night(ts), 0.05, 2.0))
    house = HouseContext.canonical()
    rows = []
    for step in (24.0, 5.0, 1.5):
        d = L1LeakModel(house, L1Params())
        feed(d, flow, step)
        rows.append([(t, [r[k] for k in FEATURES]) for t, r in d.rows])
    assert len(rows[0]) == 4  # nights 01:00-05:00 of each of the four days
    for other in rows[1:]:
        assert len(other) == len(rows[0])
        for (ta, a), (tb, b) in zip(rows[0], other, strict=True):
            assert ta == tb
            np.testing.assert_allclose(a, b, equal_nan=True)


class _FixedModel:
    """Stand-in estimator: leak probability from the night's minimum flow."""

    classes_ = np.array(["W1", "W2", "none"])

    def predict_proba(self, x: np.ndarray) -> np.ndarray:
        mnf = x[0, FEATURES.index("mnf")]
        p_leak = 0.9 if mnf > 0.1 else 0.05
        return np.array([[0.1 * p_leak, 0.9 * p_leak, 1 - p_leak]])


@pytest.fixture
def artifact(tmp_path: Path) -> str:
    path = tmp_path / "model.pkl"
    payload = {
        "estimator": _FixedModel(),
        "classes": ["W1", "W2", "none"],
        "features": list(FEATURES),
        "version": "9.9.9",
    }
    path.write_bytes(pickle.dumps(payload))
    return str(path)


def test_decisions_open_type_and_clear(artifact: str) -> None:
    def leak_then_fixed(ts: np.ndarray) -> np.ndarray:
        days = (ts - T0) // DAY
        return np.where(night(ts), np.where(days < 3, 0.3, 0.0), 1.0)

    flow = flow_series(6, leak_then_fixed)
    findings = feed(L1LeakModel(HouseContext.canonical(), L1Params(artifact=artifact)), flow, 6)
    states = [(f.type.value, f.status.value) for f in findings]
    assert states[0] == ("running_cistern_suspected", "active")
    assert states[-1] == ("running_cistern_suspected", "cleared")
    first = findings[0]
    assert first.confidence == pytest.approx(0.9)
    assert first.confidence_kind.value == "calibrated_probability"
    assert first.source.version == "9.9.9"
    assert first.candidates
    assert all(c.object_id.endswith("_wc_cistern") for c in first.candidates)
    assert first.source.model_card == "docs/models/l1_leak_model.md"
    ctx = load_context()
    for f in findings:
        assert validate_finding_document(
            f.model_dump(mode="json", by_alias=True, exclude_none=True), ctx
        ).ok


def test_burst_rule_needs_continuous_high_flow() -> None:
    def shower_then_burst(ts: np.ndarray) -> np.ndarray:
        hour = ((ts - T0) % DAY) / 3600 / US
        shower = (hour > 7) & (hour < 7.2)  # 12 minutes at 8 L/min: normal
        burst = (hour > 14) & (hour < 16)  # two hours at 12 L/min: a burst
        return np.where(burst, 12.0, np.where(shower, 8.0, 0.0))

    flow = flow_series(1, shower_then_burst)
    findings = feed(L1LeakModel(HouseContext.canonical(), L1Params()), flow, 3)
    bursts = [f for f in findings if f.type.value == "burst_pipe_suspected"]
    assert [f.status.value for f in bursts] == ["active", "cleared"]
    assert bursts[0].severity.value == "urgent"
    assert bursts[0].safety_guidance_key == "sg_burst_pipe"
    hours = (bursts[0].first_seen.timestamp() - T0 / US) / 3600
    assert 14.4 <= hours <= 14.6  # once a 30-minute window is all high flow
