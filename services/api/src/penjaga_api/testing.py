"""Test support for the Core API: an in-memory detection + clock, and real findings captured from
the dev stack (``services/api/tests/fixtures/findings_*.json``)."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

from penjaga_api.agent.openrouter import Completion, LLMUnavailableError
from penjaga_api.upstream import UpstreamUnavailableError

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures"
_END = object()


def load_findings(name: str) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = json.loads(
        (FIXTURES / f"findings_{name}.json").read_text(encoding="utf-8")
    )
    return docs


def revision(doc: dict[str, Any], number: int, updated_at: str) -> dict[str, Any]:
    """``doc`` as an earlier or later revision, updated at ``updated_at``."""
    return {**doc, "revision": number, "updated_at": updated_at, "last_seen": updated_at}


class FakeUpstream:
    """Detection and the clock. ``emit`` publishes a revision to connected SSE readers and keeps it
    for replay; ``down`` makes every call fail; ``end_streams`` closes open SSE streams."""

    def __init__(
        self,
        *,
        episode_id: str = "epi_0000000000000001",
        episode_start: str = "2025-11-01T00:00:00+08:00",
    ) -> None:
        self.episode_id = episode_id
        self.episode_start = episode_start
        self.house_time = "2025-11-05T12:00:00+08:00"
        self.latest: dict[str, dict[str, Any]] = {}
        self.log: list[tuple[int, dict[str, Any]]] = []
        self.layer_state = {"L1": True, "L2": True, "L3": True, "L5": True}
        self.layer_calls: list[tuple[str, bool]] = []
        self.event_calls: list[int] = []
        self.down = False
        # stream → [(house time, mean)]: what readings_summary serves, bucketed on request
        self.readings: dict[str, list[tuple[str, float]]] = {}
        self.summary_calls: list[tuple[str, str, str, float]] = []
        # guided checks: requests received, and the verdict each one will complete with
        self.checks: dict[str, dict[str, Any]] = {}
        self.check_verdict = "unchanged"
        self.check_flow = (0.709, 0.944)  # (before, after) L/min
        self.check_residual = (9.0, 8.8)  # (before, after) mA: a healthy circuit's own share
        self._readers: list[asyncio.Queue[Any]] = []

    def _check(self) -> None:
        if self.down:
            raise UpstreamUnavailableError("fake upstream is down")

    def reset(self, episode_id: str, episode_start: str) -> None:
        self.episode_id, self.episode_start = episode_id, episode_start
        self.latest = {}

    def emit(self, doc: dict[str, Any]) -> int:
        seq = len(self.log) + 1
        self.log.append((seq, doc))
        self.latest[doc["id"]] = doc
        for q in self._readers:
            q.put_nowait((seq, doc))
        return seq

    def end_streams(self) -> None:
        for q in self._readers:
            q.put_nowait(_END)

    async def clock(self) -> dict[str, Any]:
        self._check()
        return {
            "house_time": self.house_time,
            "mode": "accelerated",
            "speed": 600.0,
            "effective_speed": 600.0,
            "paused": False,
            "episode": 1,
            "episode_start": self.episode_start,
            "episode_id": self.episode_id,
        }

    async def findings(self) -> list[dict[str, Any]]:
        self._check()
        return list(self.latest.values())

    async def layers(self) -> list[dict[str, Any]]:
        self._check()
        return [{"layer": k, "enabled": v} for k, v in self.layer_state.items()]

    async def set_layer(self, layer: str, enabled: bool) -> dict[str, Any]:
        self._check()
        self.layer_calls.append((layer, enabled))
        self.layer_state[layer] = enabled
        return {"layer": layer, "enabled": enabled}

    async def readings_summary(
        self, stream: str, start: str, end: str, bucket_s: float
    ) -> dict[str, Any]:
        """Bucket means of ``readings[stream]`` in [start, end), like the stream service."""
        self._check()
        self.summary_calls.append((stream, start, end, bucket_s))
        t0, t1 = datetime.fromisoformat(start), datetime.fromisoformat(end)
        buckets: dict[int, list[float]] = {}
        for t, value in self.readings.get(stream, []):
            at = datetime.fromisoformat(t)
            if t0 <= at < t1:
                buckets.setdefault(int((at - t0).total_seconds() // bucket_s), []).append(value)
        points = [
            {
                "t": (t0 + timedelta(seconds=b * bucket_s)).isoformat(),
                "n": len(vs),
                "min": min(vs),
                "max": max(vs),
                "mean": sum(vs) / len(vs),
                "last": vs[-1],
            }
            for b, vs in sorted(buckets.items())
        ]
        return {"stream": stream, "from": start, "to": end, "bucket_s": bucket_s, "points": points}

    async def start_check(self, request: dict[str, Any]) -> dict[str, Any]:
        self._check()
        check_id = f"chk_{len(self.checks) + 1:06d}"
        since = datetime.fromisoformat(request["since"])
        ready = since + timedelta(seconds=float(request.get("settle_s", 0)) + request["window_s"])
        self.checks[check_id] = {
            "check_id": check_id,
            "request": request,
            "status": "pending",
            "ready_at": ready.isoformat(),
            "measurements": [],
            "verdict": None,
        }
        return dict(self.checks[check_id])

    async def get_check(self, check_id: str) -> dict[str, Any]:
        """Completes once house time reaches ``ready_at``, with ``check_verdict``."""
        self._check()
        doc = self.checks[check_id]
        if doc["status"] == "pending" and self.house_time >= doc["ready_at"]:
            if doc["request"]["kind"] == "circuit_load":  # with the RCCB's residual current
                r0, r1 = self.check_residual
                doc.update(
                    status="complete",
                    verdict="dropped",
                    measurements=[
                        {"name": "current_before", "value": 1.2, "unit": "A"},
                        {"name": "current_after", "value": 0.0, "unit": "A"},
                        {"name": "rccb", "value": "rcd_sockets"},
                        {"name": "residual_before", "value": r0, "unit": "mA"},
                        {"name": "residual_after", "value": r1, "unit": "mA"},
                        {"name": "residual_change", "value": r1 - r0, "unit": "mA"},
                        {"name": "residual_change_se", "value": 0.05, "unit": "mA"},
                    ],
                )
                return dict(doc)
            before, after = self.check_flow
            doc.update(
                status="complete",
                verdict=self.check_verdict,
                measurements=[
                    {"name": "flow_before", "value": before, "unit": "L/min"},
                    {"name": "flow_after", "value": after, "unit": "L/min", "threshold": 0.02},
                ],
            )
        return dict(doc)

    async def finding_events(self, after: int) -> AsyncIterator[tuple[int, dict[str, Any]]]:
        self._check()
        self.event_calls.append(after)
        q: asyncio.Queue[Any] = asyncio.Queue()
        for seq, doc in self.log:
            if seq > after:
                q.put_nowait((seq, doc))
        self._readers.append(q)
        try:
            while True:
                item = await q.get()
                if item is _END:
                    return
                yield item
        finally:
            self._readers.remove(q)


# ---------------------------------------------------------------------- synthetic findings
# Shaped like each deployed detector's output (services/detect), for the finding types and
# revisions the captured fixtures don't cover. Every one passes the contract validator.

_LEAK_EVIDENCE: list[dict[str, Any]] = [
    {"name": "night_min_flow", "value": 0.42, "unit": "L/min"},
    {"name": "leak_probability", "value": 0.91},
    {"name": "most_likely_kind", "value": "W2"},
]
_FRIDGE_EVIDENCE: list[dict[str, Any]] = [
    {"name": "duty_cycle", "value": 0.55},
    {"name": "baseline_duty_cycle", "value": 0.38},
    {"name": "running_power", "value": 120.0, "unit": "W"},
    {"name": "baseline_running_power", "value": 118.0, "unit": "W"},
    {"name": "mean_run", "value": 24.0, "unit": "min"},
    {"name": "baseline_mean_run", "value": 18.0, "unit": "min"},
    {"name": "cusum", "value": 5.6},
]
_EVIDENCE: dict[str, list[dict[str, Any]]] = {
    "night_flow_unexplained": [
        {"name": "night_min_flow", "value": 0.42, "unit": "L/min"},
        {"name": "nights_above_threshold", "value": 3.0},
        {"name": "threshold", "value": 0.02, "unit": "L/min"},
    ],
    "dripping_fixture_suspected": _LEAK_EVIDENCE,
    "running_cistern_suspected": _LEAK_EVIDENCE,
    "tank_overflow_suspected": _LEAK_EVIDENCE,
    "concealed_leak_suspected": _LEAK_EVIDENCE,
    "burst_pipe_suspected": [
        {"name": "mean_flow", "value": 15.0, "unit": "L/min"},
        {"name": "window", "value": 60.0, "unit": "min"},
        {"name": "rule", "value": "continuous_high_flow"},
    ],
    "circuit_overload": [
        {"name": "mean_current_15min", "value": 24.0, "unit": "A"},
        {"name": "mean_current_1min", "value": 30.0, "unit": "A"},
        {"name": "rated_current", "value": 20.0, "unit": "A"},
        {"name": "multiple_of_rating", "value": 1.5},
        {"name": "rule", "value": "above_1_45_in"},
    ],
    "earth_leakage_elevated": [
        {"name": "mean_residual_10min", "value": 16.0, "unit": "mA"},
        {"name": "rated_residual", "value": 30.0, "unit": "mA"},
        {"name": "share_of_rated_residual", "value": 0.53},
        {"name": "rule", "value": "above_non_operating"},
    ],
    "earth_leakage_rising": [
        {"name": "daily_median_residual", "value": 9.0, "unit": "mA"},
        {"name": "trend", "value": 0.8, "unit": "mA/day"},
        {"name": "days_to_non_operating_limit", "value": 7.5, "unit": "day"},
        {"name": "rated_residual", "value": 30.0, "unit": "mA"},
    ],
    "protective_device_trip": [
        {"name": "device_kind", "value": "mcb"},
        {"name": "trips_in_24h", "value": 1.0},
    ],
    "repeated_trips": [
        {"name": "device_kind", "value": "mcb"},
        {"name": "trips_in_24h", "value": 3.0},
    ],
    "aircon_efficiency_decline": [
        {"name": "energy_per_running_hour", "value": 1.9, "unit": "kWh/h"},
        {"name": "expected_energy_per_running_hour", "value": 1.5, "unit": "kWh/h"},
        {"name": "outdoor_temperature_while_running", "value": 31.0, "unit": "degC"},
        {"name": "running_hours", "value": 8.0, "unit": "h"},
        {"name": "cusum", "value": 6.2},
    ],
    "fridge_duty_cycle_rise": _FRIDGE_EVIDENCE,
    "fridge_compressor_wear": _FRIDGE_EVIDENCE,
    "water_supply_interruption": [
        {"name": "mean_inlet_pressure_10min", "value": 0.1, "unit": "bar"},
        {"name": "threshold", "value": 0.5, "unit": "bar"},
    ],
    "power_supply_interruption": [{"name": "mains", "value": "lost"}],
}

# type → (layer, default severity, object_ids)
_SHAPE: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "night_flow_unexplained": ("L1", "heads_up", ()),
    "dripping_fixture_suspected": ("L1", "heads_up", ()),
    "running_cistern_suspected": ("L1", "heads_up", ()),
    "tank_overflow_suspected": ("L1", "heads_up", ()),
    "concealed_leak_suspected": ("L1", "heads_up", ()),
    "burst_pipe_suspected": ("L1", "urgent", ("meter_water_main",)),
    "circuit_overload": ("L2", "urgent", ("circ_sockets_kitchen", "mcb_sockets_kitchen")),
    "earth_leakage_elevated": ("L2", "act_soon", ("rcd_sockets",)),
    "earth_leakage_rising": ("L2", "heads_up", ("rcd_sockets",)),
    "protective_device_trip": ("L2", "act_soon", ("mcb_sockets_kitchen",)),
    "repeated_trips": ("L2", "urgent", ("mcb_sockets_kitchen",)),
    "aircon_efficiency_decline": ("L3", "heads_up", ("app_ac_master",)),
    "fridge_duty_cycle_rise": ("L3", "heads_up", ("app_fridge_kitchen",)),
    "fridge_compressor_wear": ("L3", "heads_up", ("app_fridge_kitchen",)),
    "water_supply_interruption": ("L5", "heads_up", ("meter_water_main",)),
    "power_supply_interruption": ("L5", "heads_up", ("db_main",)),
}
FINDING_TYPES = tuple(_SHAPE)
_MODEL_CANDIDATES = {
    "running_cistern_suspected": ("fix_bath_master_wc_cistern", "fix_bath2_wc_cistern"),
    "dripping_fixture_suspected": ("fix_bath2_basin_tap", "fix_bath2_shower"),
    "tank_overflow_suspected": ("valve_roof_tank_float",),
    "concealed_leak_suspected": ("pipe_rising_main", "pipe_kitchen_direct"),
}
_FLOOR_TYPES = frozenset(
    {"circuit_overload", "earth_leakage_elevated", "protective_device_trip", "repeated_trips"}
)
_SAFETY = {
    "burst_pipe_suspected": "sg_burst_pipe",
    "circuit_overload": "sg_circuit_overload",
    "repeated_trips": "sg_repeated_trips",
}
_QUANTITIES: dict[str, dict[str, dict[str, Any]]] = {
    "L1": {"water_l_per_day": {"value": 605.0, "unit": "L/day"}},
    "L3": {"extra_energy_kwh_per_day": {"value": 0.6, "unit": "kWh/day"}},
    "L5": {"tank_water_l": {"value": 900.0, "unit": "L"}},
}


def finding(
    finding_type: str,
    *,
    first_seen: str = "2025-11-03T04:00:00+08:00",
    updated_at: str | None = None,
    revision: int = 1,
    status: str = "active",
    target: str | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """A contract-valid finding of ``finding_type`` shaped like its detector's output.
    ``target`` replaces the element it is about (and the last segment of its key)."""
    layer, severity, shape_ids = _SHAPE[finding_type]
    ids = list(shape_ids)
    if target is not None and ids:
        ids[0] = target
    model = finding_type in _MODEL_CANDIDATES
    key_target = ids[0] if ids else "meter_water_main"
    key = f"{layer}:{'water_leak' if model else finding_type}:{key_target}"
    key = str(overrides.pop("key", key))
    severity = str(overrides.pop("severity", severity))
    updated = updated_at or first_seen
    digest = hashlib.sha256(f"{key}|{first_seen}".encode()).hexdigest()[:20]
    doc: dict[str, Any] = {
        "schema_version": "1.0",
        "id": f"fnd_{digest}",
        "key": key,
        "revision": revision,
        "status": status,
        "house_id": "elmina_g6_demo",
        "layer": layer,
        "type": finding_type,
        "severity": severity,
        "severity_floor": finding_type in _FLOOR_TYPES,
        "confidence": 0.91 if model else 1.0,
        "confidence_kind": "calibrated_probability" if model else "deterministic_rule",
        "object_ids": ids,
        "candidates": [],
        "candidates_other_p": 0.0,
        "quantities": dict(_QUANTITIES.get(layer, {})),
        "evidence": list(_EVIDENCE[finding_type]),
        "series_refs": [],
        "first_seen": first_seen,
        "last_seen": updated,
        "updated_at": updated,
        "source": {"detector": "synthetic", "kind": "rule", "version": "1.0.0"},
    }
    if model:
        cands = _MODEL_CANDIDATES[finding_type]
        p = round(1 / (len(cands) + 2), 6)
        doc["candidates"] = [
            {"object_id": c, "p": p, "basis": "leak_class_w2+contract_kind"} for c in cands
        ]
        doc["candidates_other_p"] = round(1 - p * len(cands), 6)
        doc["source"] = {
            "detector": "l1_leak_model",
            "kind": "model",
            "model": "leak_gbm",
            "version": "1.0.0",
            "model_card": "docs/models/l1-leak.md",
        }
    if severity == "urgent":
        doc["safety_guidance_key"] = _SAFETY.get(finding_type, "sg_burst_pipe")
    if status == "cleared":
        doc["cleared_at"] = updated
    doc.update(overrides)
    return doc


# ---------------------------------------------------------------------- a scripted LLM


class FakeLLM:
    """Replays scripted completions (or raises scripted errors) and records every request, so
    agent tests never call a real model."""

    models: ClassVar[list[str]] = ["fake/primary", "fake/fallback"]

    def __init__(self, replies: list[Any] | None = None) -> None:
        self.replies: list[Any] = list(replies or [])
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        messages: Any,
        *,
        tools: Any = None,
        response_format: Any = None,
        session_id: str | None = None,
        max_tokens: int | None = None,
    ) -> Any:
        self.calls.append(
            {
                "messages": [dict(m) for m in messages],
                "tools": tools,
                "response_format": response_format,
                "session_id": session_id,
            }
        )
        if not self.replies:
            raise LLMUnavailableError("script_exhausted")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def reply(content: str | None = None, *tool_calls: dict[str, Any], reasoning: Any = None) -> Any:
    """A scripted completion: text, and/or tool calls made with ``call``."""
    return Completion(
        content=content,
        tool_calls=list(tool_calls),
        reasoning_details=reasoning,
        finish_reason="tool_calls" if tool_calls else "stop",
        model="fake/primary",
    )


def call(call_id: str, name: str, **arguments: Any) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }
