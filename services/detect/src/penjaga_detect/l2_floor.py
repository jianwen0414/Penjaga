"""L2 electrical safety floor: deterministic, always on, never lowered by any model (PRD §15.3).

Every threshold comes from the house contract (circuit and device ratings) and a cited
standard, or is a stated assumption (FloorParams):

* Overload, per circuit, against its MCB rating In (IEC 60898-1, DS-021):
  - heads_up: the 15-minute mean current exceeds In (running beyond the circuit's rating);
  - act_soon: it exceeds 1.13 In, the conventional non-tripping current (beyond it the MCB
    may trip at any time and the cable runs hot);
  - urgent: the 1-minute mean exceeds 1.45 In, the conventional tripping current (the MCB
    must trip within an hour; if it has not, it may be faulty).
* Residual (earth leakage) current, per RCCB, against its rated IΔn (IEC 61008-1, DS-024;
  ratings per Electricity Regulations 1994 reg. 36, DS-001):
  - heads_up: the 10-minute mean exceeds 30% of IΔn (assumption: a margin below the
    non-operating limit, so it warns before nuisance trips start);
  - act_soon: it exceeds 50% of IΔn, the rated non-operating current (the RCCB may now trip
    at any time).
* Rising leakage: daily medians over the last 7 days rise by at least 5% of IΔn and the
  linear trend reaches 50% of IΔn within 30 days (heads_up) or 7 days (act_soon).
* Trips (auxiliary contacts): every trip is a finding (act_soon); a second trip of the same
  device within 24 hours escalates to repeated_trips (urgent, with safety guidance: do not
  keep resetting it).

While a finding is open its severity only goes up. It clears after an hour without the
condition (three quiet days for a trend; a day after the last trip once the device has been
reset). Decisions are taken at sample or event times, so they do not depend on chunking.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np

from penjaga_contract.core import Finding
from penjaga_detect.chunk import Chunk
from penjaga_detect.detector import DetectorInfo, HouseContext
from penjaga_detect.findings import FindingTracker, Observation
from penjaga_detect.signals import Carry, Floats, Ints, TierState, rolling_mean, track_tiers

US = 1_000_000
DAY_US = 86_400 * US
TRIP = "elec.protection.trip"
I_NO_TRIP = 1.13  # IEC 60898-1 conventional non-tripping current, multiple of In (DS-021)
I_TRIP = 1.45  # IEC 60898-1 conventional tripping current, multiple of In (DS-021)
NON_OPERATING = 0.5  # IEC 61008-1 rated residual non-operating current, share of IΔn (DS-024)
SEVERITIES = ("info", "heads_up", "act_soon", "urgent")
GUIDANCE_OVERLOAD = "sg_circuit_overload"
GUIDANCE_TRIPS = "sg_repeated_trips"


@dataclass(frozen=True)
class FloorParams:
    overload_window_s: float = 900.0  # "sustained" overload: 15-minute mean (assumption)
    urgent_window_s: float = 60.0  # 1-minute mean above the conventional tripping current
    residual_window_s: float = 600.0  # 10-minute mean residual current (assumption)
    residual_warn_fraction: float = 0.3  # of IΔn (assumption, see module docstring)
    clear_after_s: float = 3600.0  # an hour without the condition clears a finding
    min_coverage: float = 0.5  # share of 1 s samples present for a mean to count
    trend_days: int = 7
    trend_min_days: int = 4
    trend_horizon_days: float = 30.0
    trend_act_soon_days: float = 7.0
    trend_min_rise_fraction: float = 0.05  # of IΔn, so noise near zero is not a "trend"
    trend_clear_days: int = 3
    repeat_window_s: float = 86_400.0
    trip_clear_s: float = 86_400.0


def ev(name: str, value: Any, unit: str | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"name": name, "value": value}
    if unit is not None:
        item["unit"] = unit
    return item


@dataclass
class _Daily:
    """Per-day residual samples of one RCCB, and the medians of completed days."""

    pending: dict[int, list[Floats]] = field(default_factory=dict)  # local day ordinal
    medians: list[tuple[int, float]] = field(default_factory=list)
    quiet_days: int = 0


class L2SafetyFloor:
    def __init__(self, house: HouseContext, params: FloorParams | None = None) -> None:
        self.p = params or FloorParams()
        self.house = house
        ix = house.index
        self.circuits = {c.id: ix.rated_current_a(c.id) for c in ix.house.electrical.circuits}
        self.rccbs = {
            d.id: float(d.rated_residual_ma)
            for d in ix.house.electrical.protective_devices
            if d.rated_residual_ma is not None
        }
        streams = {self._current(c) for c in self.circuits} | {
            self._residual(d) for d in self.rccbs
        }
        self.info = DetectorInfo(
            "l2_safety_floor",
            "L2",
            "rule",
            "1.0.0",
            streams=frozenset(streams),
            event_streams=frozenset({TRIP}),
        )
        self.tracker = FindingTracker(house.house_id, self.info.source(), house.zone)
        keep = round(max(self.p.overload_window_s, self.p.residual_window_s) * US)
        self.carry = {s: Carry(keep) for s in streams}
        self.overload = {c: TierState() for c in self.circuits}
        self.leakage = {d: TierState() for d in self.rccbs}
        self.daily = {d: _Daily() for d in self.rccbs}
        self.trips: dict[str, list[int]] = {}
        self.reset_seen: dict[str, bool] = {}
        self.out: list[Finding] = []

    @staticmethod
    def _current(circuit: str) -> str:
        return f"elec.circuit.{circuit}.current_a"

    @staticmethod
    def _residual(device: str) -> str:
        return f"elec.rcd.{device}.residual_ma"

    def update(self, chunk: Chunk) -> list[Finding]:
        self.out = []
        for circuit in self.circuits:
            self._overload(chunk, circuit)
        for device in self.rccbs:
            self._leakage(chunk, device)
            self._collect_days(chunk, device)
            self._trend(chunk, device)
        self._trip_events(chunk)
        # in time order, so the sequence does not depend on how house time was chunked
        self.out.sort(key=lambda f: (f.updated_at, f.key, f.revision))
        return self.out

    # ------------------------------------------------------------------ shared

    def _emit(self, finding: Finding | None) -> None:
        if finding is not None:
            self.out.append(finding)

    def _observe(self, **kw: Any) -> None:
        self._emit(self.tracker.observe(Observation(severity_floor=True, confidence=1.0, **kw)))

    def _clear(self, key: str, at_us: int) -> None:
        self._emit(self.tracker.clear(key, at_us))

    def _series_ref(self, stream: str, at_us: int) -> dict[str, Any]:
        return {
            "stream": stream,
            "window": {
                "from": self.tracker.iso(at_us - 3600 * US),
                "to": self.tracker.iso(at_us + 1),
            },
            "bucket_s": 60.0,
        }

    def _means(
        self, chunk: Chunk, stream: str, *windows_s: float
    ) -> tuple[Ints, list[Floats]] | None:
        """Rolling means over each window at the chunk's new samples (None when it has none)."""
        ts, values, first_new = self.carry[stream].extend(chunk.get(stream))
        if ts.size == first_new:
            return None
        means = [
            rolling_mean(ts, values, round(w * US), w, self.p.min_coverage)[first_new:]
            for w in windows_s
        ]
        return ts[first_new:], means

    # ------------------------------------------------------------------ overload

    def _overload(self, chunk: Chunk, circuit: str) -> None:
        p, rating, stream = self.p, self.circuits[circuit], self._current(circuit)
        key = f"L2:circuit_overload:{circuit}"
        state = self.overload[circuit]
        means = self._means(chunk, stream, p.overload_window_s, p.urgent_window_s)
        if means is None:
            track_tiers(state, np.empty(0, np.int64), np.empty(0, np.int64),
                        chunk_end_us=chunk.t1_us, clear_after_us=round(p.clear_after_s * US),
                        on_raise=_never, on_clear=functools.partial(self._clear, key))  # fmt: skip
            return
        ts, (m15, m1) = means
        tiers = np.zeros(ts.size, np.int64)
        tiers[np.nan_to_num(m15) > rating] = 1
        tiers[np.nan_to_num(m15) > I_NO_TRIP * rating] = 2
        tiers[np.nan_to_num(m1) > I_TRIP * rating] = 3
        raise_ = functools.partial(self._raise_overload, circuit, (m15, m1))
        track_tiers(state, ts, tiers, chunk_end_us=chunk.t1_us,
                    clear_after_us=round(p.clear_after_s * US),
                    on_raise=raise_, on_clear=functools.partial(self._clear, key))  # fmt: skip

    def _raise_overload(
        self, circuit: str, means: tuple[Floats, Floats], j: int, at_us: int, tier: int
    ) -> None:
        m15, m1 = means
        rating = self.circuits[circuit]
        severity = SEVERITIES[tier]
        peak = float(np.nanmax([m15[j], m1[j]]))
        self._observe(
            key=f"L2:circuit_overload:{circuit}",
            type="circuit_overload",
            severity=severity,
            at_us=at_us,
            object_ids=[circuit, self.house.index.circuit(circuit).mcb_id],
            evidence=[
                ev("mean_current_15min", round(float(m15[j]), 2), "A"),
                ev("mean_current_1min", round(float(m1[j]), 2), "A"),
                ev("rated_current", rating, "A"),
                ev("multiple_of_rating", round(peak / rating, 3)),
                ev("rule", ("above_rating", "above_1_13_in", "above_1_45_in")[tier - 1]),
            ],
            series_refs=[self._series_ref(self._current(circuit), at_us)],
            safety_guidance_key=GUIDANCE_OVERLOAD if severity == "urgent" else None,
        )

    # ------------------------------------------------------------------ residual current

    def _leakage(self, chunk: Chunk, device: str) -> None:
        p, idn, stream = self.p, self.rccbs[device], self._residual(device)
        key = f"L2:earth_leakage_elevated:{device}"
        means = self._means(chunk, stream, p.residual_window_s)
        clear = functools.partial(self._clear, key)
        if means is None:
            track_tiers(self.leakage[device], np.empty(0, np.int64), np.empty(0, np.int64),
                        chunk_end_us=chunk.t1_us, clear_after_us=round(p.clear_after_s * US),
                        on_raise=_never, on_clear=clear)  # fmt: skip
            return
        ts, (mean,) = means
        tiers = np.zeros(ts.size, np.int64)
        tiers[np.nan_to_num(mean) > p.residual_warn_fraction * idn] = 1
        tiers[np.nan_to_num(mean) > NON_OPERATING * idn] = 2
        raise_ = functools.partial(self._raise_leakage, device, mean)
        track_tiers(self.leakage[device], ts, tiers, chunk_end_us=chunk.t1_us,
                    clear_after_us=round(p.clear_after_s * US),
                    on_raise=raise_, on_clear=clear)  # fmt: skip

    def _raise_leakage(self, device: str, mean: Floats, j: int, at_us: int, tier: int) -> None:
        idn = self.rccbs[device]
        self._observe(
            key=f"L2:earth_leakage_elevated:{device}",
            type="earth_leakage_elevated",
            severity=SEVERITIES[tier],
            at_us=at_us,
            object_ids=[device],
            evidence=[
                ev("mean_residual_10min", round(float(mean[j]), 2), "mA"),
                ev("rated_residual", idn, "mA"),
                ev("share_of_rated_residual", round(float(mean[j]) / idn, 3)),
                ev("rule", ("above_warning_share", "above_non_operating")[tier - 1]),
            ],
            series_refs=[self._series_ref(self._residual(device), at_us)],
        )

    # ------------------------------------------------------------------ rising leakage

    def _local_days(self, ts: Ints) -> Ints:
        """House-local day of each sample (a sample stamped at midnight ends the day before)."""
        if not ts.size:
            return np.empty(0, np.int64)
        offsets = {
            int(datetime.fromtimestamp(t / US, tz=self.house.zone).utcoffset().total_seconds())  # type: ignore[union-attr]
            for t in (int(ts[0]), int(ts[-1]))
        }  # fmt: skip
        if len(offsets) != 1:
            return np.array(
                [self._day_of(int(t)) for t in ts]
            )  # fmt: skip
        local = ts + offsets.pop() * US - 1
        epoch = date(1970, 1, 1).toordinal()
        return np.asarray(local // DAY_US + epoch, dtype=np.int64)

    def _day_of(self, ts_us: int) -> int:
        return datetime.fromtimestamp((ts_us - 1) / US, tz=self.house.zone).date().toordinal()

    def _collect_days(self, chunk: Chunk, device: str) -> None:
        s = chunk.get(self._residual(device))
        days = self._local_days(s.ts_us)
        pending = self.daily[device].pending
        for d in np.unique(days):
            pending.setdefault(int(d), []).append(s.values[days == d])

    def _trend(self, chunk: Chunk, device: str) -> None:
        """Decide at the end of each completed house day."""
        daily = self.daily[device]
        for day in sorted(daily.pending):
            next_day = date.fromordinal(day) + timedelta(days=1)
            end = datetime.combine(next_day, datetime.min.time(), tzinfo=self.house.zone)
            end_us = int(end.timestamp()) * US
            if end_us > chunk.t1_us:
                break
            values = np.concatenate(daily.pending.pop(day))
            present = values[~np.isnan(values)]
            if present.size >= self.p.min_coverage * 86_400:  # 1 s stream
                daily.medians = [*daily.medians, (day, float(np.median(present)))][
                    -self.p.trend_days :
                ]
                self._decide_trend(device, end_us)

    def _decide_trend(self, device: str, at_us: int) -> None:
        p, idn, daily = self.p, self.rccbs[device], self.daily[device]
        key = f"L2:earth_leakage_rising:{device}"
        if len(daily.medians) < p.trend_min_days:
            return
        x = np.array([d for d, _ in daily.medians], dtype=np.float64)
        y = np.array([v for _, v in daily.medians])
        slope, level = np.polyfit(x - x[-1], y, 1)
        limit = NON_OPERATING * idn
        days_to_limit = (limit - level) / slope if slope > 0 else np.inf
        rising = float(y[-1] - y[0]) >= p.trend_min_rise_fraction * idn and level < limit
        if rising and days_to_limit <= p.trend_horizon_days:
            daily.quiet_days = 0
            self._observe(
                key=key,
                type="earth_leakage_rising",
                severity="act_soon" if days_to_limit <= p.trend_act_soon_days else "heads_up",
                at_us=at_us,
                object_ids=[device],
                evidence=[
                    ev("daily_median_residual", round(float(y[-1]), 3), "mA"),
                    ev("trend", round(float(slope), 4), "mA/day"),
                    ev("days_to_non_operating_limit", round(float(days_to_limit), 1), "day"),
                    ev("rated_residual", idn, "mA"),
                ],
            )
        elif self.tracker.is_open(key):
            daily.quiet_days += 1
            if daily.quiet_days >= p.trend_clear_days:
                daily.quiet_days = 0
                self._clear(key, at_us)

    # ------------------------------------------------------------------ trips

    def _trip_events(self, chunk: Chunk) -> None:
        window = round(self.p.repeat_window_s * US)
        for e in sorted((e for e in chunk.events if e.stream == TRIP), key=lambda e: e.ts_us):
            self._clear_quiet_trips(e.ts_us)
            device = e.payload["device_id"]
            if e.payload["state"] == "reset":
                self.reset_seen[device] = True
                continue
            self.reset_seen[device] = False
            times = [t for t in self.trips.get(device, []) if t > e.ts_us - window] + [e.ts_us]
            self.trips[device] = times
            evidence = [
                ev("device_kind", self.house.index.device(device).kind.value),
                ev("trips_in_24h", len(times)),
            ]
            self._observe(
                key=f"L2:protective_device_trip:{device}",
                type="protective_device_trip",
                severity="act_soon",
                at_us=e.ts_us,
                object_ids=[device],
                evidence=evidence,
            )
            if len(times) >= 2:
                self._observe(
                    key=f"L2:repeated_trips:{device}",
                    type="repeated_trips",
                    severity="urgent",
                    at_us=e.ts_us,
                    object_ids=[device],
                    evidence=evidence,
                    safety_guidance_key=GUIDANCE_TRIPS,
                )
        self._clear_quiet_trips(chunk.t1_us)

    def _clear_quiet_trips(self, now_us: int) -> None:
        clear_after = round(self.p.trip_clear_s * US)
        for device, times in self.trips.items():
            if not times or not self.reset_seen.get(device, False):
                continue
            quiet_from = times[-1] + clear_after
            if quiet_from <= now_us:
                self._clear(f"L2:protective_device_trip:{device}", quiet_from)
                self._clear(f"L2:repeated_trips:{device}", quiet_from)


def _never(index: int, at_us: int, tier: int) -> None:
    raise AssertionError("no samples, so nothing can be raised")
