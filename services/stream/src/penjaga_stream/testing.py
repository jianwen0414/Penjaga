"""Test helpers shared by the stream service tests and its clients' tests."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

START = datetime.fromisoformat("2026-10-16T00:00:00+08:00")


class FakeMonotonic:
    """Controllable monotonic clock in wall seconds (inject into HouseClock / StreamService)."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def at(seconds: float, start: datetime = START) -> datetime:
    return start + timedelta(seconds=seconds)


def batch_doc(
    stream: str,
    t0: datetime,
    values: list[float | None],
    *,
    seq: int = 0,
    period_s: float = 1.0,
    unit: str = "A",
    quality: Any = None,
    house_id: str = "elmina_g6_demo",
) -> dict[str, Any]:
    """A ReadingBatch document; quality defaults to ok/missing per value."""
    if quality is None:
        quality = ["missing" if v is None else "ok" for v in values]
    return {
        "house_id": house_id,
        "stream": stream,
        "t0": t0.isoformat(),
        "period_s": period_s,
        "unit": unit,
        "values": values,
        "quality": quality,
        "seq": seq,
    }


def trip_event_doc(
    device_id: str, ts: datetime, *, seq: int, state: str = "tripped"
) -> dict[str, Any]:
    return {
        "ts": ts.isoformat(),
        "house_id": "elmina_g6_demo",
        "stream": "elec.protection.trip",
        "seq": seq,
        "payload": {"type": "protection_device_state", "device_id": device_id, "state": state},
    }
