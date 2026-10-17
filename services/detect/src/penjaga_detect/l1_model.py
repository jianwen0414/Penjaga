"""L1 leak detector: a calibrated classifier over night features, plus a burst rule (D-008).

Each night, when the quiet window ends, the detector builds the night's feature row
(``l1_features``) and asks the trained model for class probabilities over {none, W1..W5}. If the
probability of a leak (1 - P(none)) reaches ``threshold`` it reports (or updates) one leak
finding for the meter, typed by the most likely leak class, with candidates from the house
contract. ``clear_nights`` quiet nights clear it.

Burst rule (W5, deterministic, any hour): ``l1_burst.BurstWatch``, also run on its own as
``l1_burst_rule``.

The model is a trained artifact (``penjaga-eval train-l1``); without one the detector only
collects feature rows (training mode).
"""

from __future__ import annotations

import pickle
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from penjaga_contract.core import (
    Finding,
    FixtureKind,
    PipeKind,
    PipeRouting,
    ValveKind,
)
from penjaga_detect.chunk import Chunk, Series
from penjaga_detect.detector import DetectorInfo, HouseContext
from penjaga_detect.findings import FindingTracker, Observation
from penjaga_detect.l1_burst import BurstParams, BurstWatch
from penjaga_detect.l1_features import NightWindow, add_history, as_vector, night_row
from penjaga_detect.signals import Carry

US = 1_000_000
FLOW = "water.main.flow_lpm"
TANK = "water.tank.level_pct"
PRESSURE = "water.main.pressure_bar"
LEAK_TYPES = {
    "W1": "dripping_fixture_suspected",
    "W2": "running_cistern_suspected",
    "W3": "tank_overflow_suspected",
    "W4": "concealed_leak_suspected",
    "W5": "burst_pipe_suspected",
}
TAPS = {
    FixtureKind.basin_tap, FixtureKind.sink_tap, FixtureKind.laundry_tap, FixtureKind.garden_tap,
    FixtureKind.shower, FixtureKind.washing_machine_tap, FixtureKind.bidet_spray,
}  # fmt: skip


_PREDICTIONS: dict[tuple[str, bytes], np.ndarray] = {}
_PREDICTIONS_MAX = 100_000


@dataclass(frozen=True)
class L1Params:
    artifact: str = ""  # path to the trained model; empty = feature collection only
    threshold: float = 0.5  # P(leak) to report; chosen on validation
    type_confidence: float = 0.6  # share of P(leak) the top kind needs to be named; validation
    clear_nights: int = 2
    burst_lpm: float = 6.0
    burst_window_s: float = 3600.0
    max_candidates: int = 5


@dataclass
class _State:
    history: list[dict[str, float]] = field(default_factory=list)
    quiet: int = 0


def load_model(path: str) -> dict[str, Any]:
    """The trained artifact (written by penjaga_eval.train_l1; only load your own files).

    Loaded once per process and file version and shared (prediction never changes it), so
    many detectors built from one artifact, as while tuning, hold one copy."""
    stat = Path(path).stat()
    key = (str(Path(path).resolve()), stat.st_size, stat.st_mtime_ns)
    if key not in _MODELS:
        with Path(path).open("rb") as fh:
            _MODELS[key] = pickle.load(fh)  # project artifact, never user input
    return _MODELS[key]


_MODELS: dict[tuple[str, int, int], dict[str, Any]] = {}


class L1LeakModel:
    def __init__(self, house: HouseContext, params: L1Params | None = None) -> None:
        self.p = params or L1Params()
        self.house = house
        self.window = NightWindow()
        self.model = load_model(self.p.artifact) if self.p.artifact else None
        version = self.model["version"] if self.model else "0.0.0"
        self.info = DetectorInfo(
            "l1_leak_model",
            "L1",
            "model",
            version,
            model="hist_gradient_boosting",
            model_card="docs/models/l1_leak_model.md",
            streams=frozenset({FLOW, TANK, PRESSURE}),
        )
        self.tracker = FindingTracker(house.house_id, self.info.source(), house.zone)
        self.meter = house.index.meter_id
        self.flow = Carry(26 * 3600 * US)
        self.tank = Carry(5 * 3600 * US)
        self.pressure = Carry(5 * 3600 * US)
        self.state = _State()
        self.rows: list[tuple[int, dict[str, float]]] = []  # (night end, features): training mode
        self.next_night: date | None = None
        self.burst = BurstWatch(
            house, self.tracker, BurstParams(self.p.burst_lpm, self.p.burst_window_s)
        )
        self.leak_key = f"L1:water_leak:{self.meter}"
        self.out: list[Finding] = []

    # ------------------------------------------------------------------ chunks

    def update(self, chunk: Chunk) -> list[Finding]:
        self.out = []
        flow, tank, pressure = chunk.get(FLOW), chunk.get(TANK), chunk.get(PRESSURE)
        f_ts, f_v, f_new = self.flow.extend_untrimmed(flow)
        t_ts, t_v, _ = self.tank.extend_untrimmed(tank)
        p_ts, p_v, _ = self.pressure.extend_untrimmed(pressure)
        has_tank = bool(self.house.index.house.water.tanks) and t_ts.size > 0
        self.out += self.burst.update(chunk, f_ts, f_v, f_new, (t_ts, t_v) if has_tank else None)
        all_flow = Series(f_ts, f_v, np.zeros(f_ts.size, np.uint8))
        all_tank = Series(t_ts, t_v, np.zeros(t_ts.size, np.uint8))
        all_pressure = Series(p_ts, p_v, np.zeros(p_ts.size, np.uint8))
        self._nights(chunk, all_flow, all_tank, all_pressure)
        for carry in (self.flow, self.tank, self.pressure):
            carry.trim(chunk.t1_us)
        self.out.sort(key=lambda f: (f.updated_at, f.key, f.revision))
        return self.out

    def _night_bounds(self, night: date) -> tuple[int, int]:
        start = datetime.combine(night, time(0), tzinfo=self.house.zone)
        t0 = start + timedelta(hours=self.window.start_h)
        t1 = start + timedelta(hours=self.window.end_h)
        return int(t0.timestamp()) * US, int(t1.timestamp()) * US

    def _nights(self, chunk: Chunk, flow: Series, tank: Series, pressure: Series) -> None:
        if self.next_night is None:
            self.next_night = datetime.fromtimestamp(chunk.t0_us / US, tz=self.house.zone).date()
            if self._night_bounds(self.next_night)[0] < chunk.t0_us:
                self.next_night += timedelta(days=1)  # partial first night: skip it
        while True:
            t0, t1 = self._night_bounds(self.next_night)
            if t1 > chunk.t1_us:
                return
            row = night_row(flow, tank, pressure, t0=t0, t1=t1, window=self.window)
            add_history(row, self.state.history, self.window)
            self.state.history = [*self.state.history, row][-self.window.history_nights :]
            self.rows.append((t1, row))
            if self.model is not None:
                self._decide(row, t0, t1)
            self.next_night += timedelta(days=1)

    # ------------------------------------------------------------------ decisions

    def _decide(self, row: dict[str, float], t0: int, t1: int) -> None:
        assert self.model is not None
        if not np.isfinite(row["mnf"]):
            return  # no data tonight: no evidence either way
        proba = self._predict(as_vector(row))
        classes = list(self.model["classes"])
        p_leak = 1.0 - float(proba[classes.index("none")])
        if p_leak >= self.p.threshold:
            self.state.quiet = 0
            leak_classes = [c for c in classes if c != "none"]
            best = max(leak_classes, key=lambda c: proba[classes.index(c)])
            # unsure which kind of leak: say "unexplained night flow" rather than guess a kind
            share = float(proba[classes.index(best)]) / p_leak
            self._observe_leak(row, best, p_leak, t0, t1, specific=share >= self.p.type_confidence)
        elif self.tracker.is_open(self.leak_key):
            self.state.quiet += 1
            if self.state.quiet >= self.p.clear_nights:
                self._emit(self.tracker.clear(self.leak_key, t1))
                self.state.quiet = 0

    def _predict(self, x: np.ndarray) -> np.ndarray:
        """Class probabilities for one night. Memoised per process by artifact and features:
        tuning replays the same nights under many settings, and scikit-learn's per-row
        prediction dominates replay time (about 1.4 s a night for this model)."""
        assert self.model is not None
        key = (self.p.artifact, x.tobytes())
        cached = _PREDICTIONS.get(key)
        if cached is None:
            cached = np.asarray(self.model["estimator"].predict_proba(x[None, :])[0])
            if len(_PREDICTIONS) >= _PREDICTIONS_MAX:
                _PREDICTIONS.clear()
            _PREDICTIONS[key] = cached
        return cached

    def _observe_leak(
        self, row: dict[str, float], leak: str, p_leak: float, t0: int, t1: int, *, specific: bool
    ) -> None:
        candidates = self._candidates(leak)
        evidence: list[dict[str, Any]] = [
            {"name": "night_min_flow", "value": round(row["mnf"], 4), "unit": "L/min"},
            {"name": "leak_probability", "value": round(p_leak, 3)},
            {"name": "most_likely_kind", "value": leak},
        ]
        for name in ("frac_flowing", "interval_median_min", "tank_slope_pct_per_h"):
            if np.isfinite(row[name]):
                evidence.append({"name": name, "value": round(row[name], 4)})
        self._emit(
            self.tracker.observe(
                Observation(
                    key=self.leak_key,
                    type=LEAK_TYPES[leak] if specific else "night_flow_unexplained",
                    severity="act_soon" if leak == "W5" else "heads_up",
                    confidence=p_leak,
                    confidence_kind="calibrated_probability",
                    at_us=t1,
                    candidates=candidates,
                    candidates_other_p=round(max(0.0, 1.0 - sum(c["p"] for c in candidates)), 4),
                    evidence=evidence,
                    quantities={
                        "water_l_per_day": {
                            "value": round(max(row["mnf"], 0.0) * 1440, 1),
                            "unit": "L/day",
                        }
                    },
                    series_refs=[
                        {
                            "stream": FLOW,
                            "window": {"from": self.tracker.iso(t0), "to": self.tracker.iso(t1)},
                            "bucket_s": 60.0,
                        }
                    ],
                )
            )
        )

    def _candidates(self, leak: str) -> list[dict[str, Any]]:
        """Possible locations from the contract, equally likely within the leak kind."""
        house = self.house.index.house
        water = house.water
        if leak == "W1":
            ids = [f.id for f in water.fixtures if f.kind in TAPS]
        elif leak == "W2":
            ids = [f.id for f in water.fixtures if f.kind is FixtureKind.wc_cistern]
        elif leak == "W3":
            ids = [v.id for v in water.valves if v.kind is ValveKind.float]
        elif leak == "W4":
            ids = [p.id for p in water.pipes if p.routing is PipeRouting.concealed]
        else:
            ids = [p.id for p in water.pipes if p.kind is not PipeKind.overflow]
        if not ids:
            return []
        p = round(1.0 / len(ids), 4)
        basis = f"leak_class_{leak.lower()}+contract_kind"
        return [{"object_id": i, "p": p, "basis": basis} for i in ids[: self.p.max_candidates]]

    def _emit(self, finding: Finding | None) -> None:
        if finding is not None:
            self.out.append(finding)


def _never(index: int, at_us: int, tier: int) -> None:
    raise AssertionError("no samples, so nothing can be raised")
