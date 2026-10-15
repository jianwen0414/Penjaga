"""The single house-time clock (PRD §14) and the speed governor.

House time advances from an anchor at ``effective_speed`` house seconds per wall second,
measured on a monotonic wall clock, so wall-clock adjustments never make house time jump.

* real mode: speed is fixed at 1 and starts at the current wall time; it cannot be paused,
  sped up or jumped (it *is* "now" for a real product).
* accelerated mode: the operator sets speed, pauses, or jumps forward. House time never moves
  backwards; starting over is a reset, which begins a new episode.

The governor lowers the *effective* speed when detection or the producer falls behind, so
data is delayed rather than dropped (PRD §14 never-drop rule), then recovers.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from penjaga_contract.core import ClockMode, ClockState
from penjaga_stream.timeutil import US_PER_S, from_us

MAX_SPEED = 86_400.0


class ClockError(ValueError):
    """A clock command that is not allowed in the current state."""


class HouseClock:
    """Monotonic house-time clock. Not thread-safe; owned by the service's event loop."""

    def __init__(
        self,
        *,
        start_us: int,
        mode: ClockMode,
        speed: float,
        zone: ZoneInfo,
        monotonic: Callable[[], float] = time.monotonic,
        episode: int = 0,
    ) -> None:
        self._mono = monotonic
        self._zone = zone
        self._episode = episode
        self._configure(start_us, mode, speed)

    def _configure(self, start_us: int, mode: ClockMode, speed: float) -> None:
        if mode is ClockMode.real and speed != 1.0:
            raise ClockError("a real-mode clock runs at speed 1")
        _check_speed(speed)
        self._mode = mode
        self._speed = speed
        self._limit: float | None = None
        self._paused = False
        self._anchor_us = start_us
        self._anchor_mono = self._mono()

    # ------------------------------------------------------------------ reading

    @property
    def mode(self) -> ClockMode:
        return self._mode

    @property
    def episode(self) -> int:
        return self._episode

    @property
    def speed(self) -> float:
        return self._speed

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def governor_limit(self) -> float | None:
        return self._limit

    def effective_speed(self) -> float:
        if self._paused:
            return 0.0
        if self._limit is None:
            return self._speed
        return min(self._speed, self._limit)

    def now_us(self) -> int:
        elapsed = self._mono() - self._anchor_mono
        return self._anchor_us + round(elapsed * self.effective_speed() * US_PER_S)

    def state(self) -> ClockState:
        return ClockState(
            house_time=from_us(self.now_us(), self._zone),
            mode=self._mode,
            speed=self._speed,
            effective_speed=self.effective_speed(),
            paused=self._paused,
            episode=self._episode,
        )

    def wall_seconds_until(self, house_us: int) -> float | None:
        """Wall seconds until house time reaches ``house_us`` (None while stopped)."""
        speed = self.effective_speed()
        if speed <= 0:
            return None
        return max(0.0, (house_us - self.now_us()) / US_PER_S / speed)

    # ------------------------------------------------------------------ changing

    def _reanchor(self) -> None:
        self._anchor_us = self.now_us()
        self._anchor_mono = self._mono()

    def _require_accelerated(self, what: str) -> None:
        if self._mode is ClockMode.real:
            raise ClockError(f"cannot {what} a real-mode clock; reset to accelerated mode first")

    def set_speed(self, speed: float) -> None:
        self._require_accelerated("change the speed of")
        _check_speed(speed)
        self._reanchor()
        self._speed = speed

    def set_paused(self, paused: bool) -> None:
        self._require_accelerated("pause")
        self._reanchor()
        self._paused = paused

    def jump_to(self, house_us: int) -> tuple[int, int]:
        """Move house time forward to ``house_us``; returns the skipped interval [from, to)."""
        self._require_accelerated("jump")
        now = self.now_us()
        if house_us < now:
            raise ClockError("house time is monotonic: jump_to must not be in the past")
        self._anchor_us = house_us
        self._anchor_mono = self._mono()
        return now, house_us

    def set_governor_limit(self, limit: float | None) -> None:
        """Cap the effective speed (None removes the cap). Ignored in real mode."""
        if self._mode is ClockMode.real:
            return
        if limit is not None and limit <= 0:
            raise ClockError("governor limit must be positive")
        self._reanchor()
        self._limit = limit

    def reset(self, *, start_us: int, mode: ClockMode, speed: float) -> None:
        """Start a new episode at ``start_us`` (the only way house time may go backwards)."""
        self._configure(start_us, mode, speed)
        self._episode += 1


def _check_speed(speed: float) -> None:
    if not 0 < speed <= MAX_SPEED:
        raise ClockError(f"speed must be in (0, {MAX_SPEED:g}]")


@dataclass
class Governor:
    """Additive-increase / multiplicative-decrease control of the effective clock speed.

    Inputs each step: how full the fullest lossless subscriber queue is (0..1), and the
    producer's data lag expressed in wall seconds *at the requested speed*
    (lag_house_s / requested). Normalising by the requested speed, not the current one, keeps
    the signal from feeding back on itself as the governor slows the clock. Output: the new
    speed cap, or None for "no cap" (run at the requested speed).
    """

    min_speed: float = 1.0
    decrease_factor: float = 0.5
    increase_factor: float = 1.25
    queue_high: float = 0.5
    queue_low: float = 0.1
    lag_high_wall_s: float = 2.0
    lag_low_wall_s: float = 0.5

    def step(
        self,
        *,
        requested: float,
        current_limit: float | None,
        queue_fill: float,
        data_lag_wall_s: float,
    ) -> float | None:
        current = requested if current_limit is None else min(current_limit, requested)
        if queue_fill >= self.queue_high or data_lag_wall_s >= self.lag_high_wall_s:
            return max(min(self.min_speed, requested), current * self.decrease_factor)
        if current_limit is None:
            return None
        if queue_fill <= self.queue_low and data_lag_wall_s <= self.lag_low_wall_s:
            raised = current * self.increase_factor
            return None if raised >= requested else raised
        return current_limit
