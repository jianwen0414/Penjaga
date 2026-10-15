"""Exact conversions between aware datetimes and integer microseconds since the epoch."""

from __future__ import annotations

from datetime import datetime

US = 1_000_000


def to_us(moment: datetime) -> int:
    """Microseconds since the epoch for an aware datetime, without float rounding."""
    if moment.tzinfo is None:
        raise ValueError("house times carry a UTC offset")
    return int(moment.replace(microsecond=0).timestamp()) * US + moment.microsecond
