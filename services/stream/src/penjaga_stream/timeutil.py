"""House-time conversions.

Inside M4, instants are integers of microseconds since the Unix epoch (UTC): exact, cheap to
compare, and what the store keeps. At API edges they become RFC 3339 strings in the house's
time zone (Asia/Kuala_Lumpur, +08:00).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

US_PER_S = 1_000_000
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_ONE_US = timedelta(microseconds=1)


def house_zone(name: str) -> ZoneInfo:
    return ZoneInfo(name)


def to_us(instant: datetime) -> int:
    """Aware datetime -> epoch microseconds (exact integer arithmetic, no float rounding)."""
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("house time must be timezone-aware")
    return (instant - _EPOCH) // _ONE_US


def from_us(us: int, zone: ZoneInfo) -> datetime:
    """Epoch microseconds -> aware datetime in ``zone``."""
    return (_EPOCH + timedelta(microseconds=us)).astimezone(zone)


def seconds_to_us(seconds: float) -> int:
    """Duration in seconds -> microseconds, rounded to the nearest microsecond."""
    return round(seconds * US_PER_S)
