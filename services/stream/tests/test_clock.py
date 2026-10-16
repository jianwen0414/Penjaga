from __future__ import annotations

import pytest

from penjaga_contract.core import ClockMode
from penjaga_stream.clock import ClockError, Governor, HouseClock
from penjaga_stream.testing import FakeMonotonic
from penjaga_stream.timeutil import US_PER_S, house_zone

ZONE = house_zone("Asia/Kuala_Lumpur")
T0 = 1_760_000_000 * US_PER_S


def accelerated(mono: FakeMonotonic, speed: float = 600.0) -> HouseClock:
    return HouseClock(
        start_us=T0, mode=ClockMode.accelerated, speed=speed, zone=ZONE, monotonic=mono
    )


def test_accelerated_clock_advances_at_speed():
    mono = FakeMonotonic()
    clock = accelerated(mono)
    mono.advance(2.0)
    assert clock.now_us() == T0 + 1200 * US_PER_S


def test_speed_change_is_continuous():
    mono = FakeMonotonic()
    clock = accelerated(mono)
    mono.advance(1.0)
    clock.set_speed(60.0)
    assert clock.now_us() == T0 + 600 * US_PER_S  # no jump at the change
    mono.advance(1.0)
    assert clock.now_us() == T0 + 660 * US_PER_S


def test_pause_and_resume():
    mono = FakeMonotonic()
    clock = accelerated(mono)
    mono.advance(1.0)
    clock.set_paused(True)
    mono.advance(100.0)
    assert clock.now_us() == T0 + 600 * US_PER_S
    assert clock.effective_speed() == 0.0
    assert clock.wall_seconds_until(T0 + 10**12) is None
    clock.set_paused(False)
    mono.advance(1.0)
    assert clock.now_us() == T0 + 1200 * US_PER_S


def test_jump_forward_only():
    mono = FakeMonotonic()
    clock = accelerated(mono)
    skipped = clock.jump_to(T0 + 86_400 * US_PER_S)
    assert skipped == (T0, T0 + 86_400 * US_PER_S)
    assert clock.now_us() == T0 + 86_400 * US_PER_S
    with pytest.raises(ClockError):
        clock.jump_to(T0)


def test_real_mode_cannot_be_controlled():
    mono = FakeMonotonic()
    clock = HouseClock(start_us=T0, mode=ClockMode.real, speed=1.0, zone=ZONE, monotonic=mono)
    mono.advance(5.0)
    assert clock.now_us() == T0 + 5 * US_PER_S
    for command in (
        lambda: clock.set_speed(2),
        lambda: clock.set_paused(True),
        lambda: clock.jump_to(T0 + 10**9),
    ):
        with pytest.raises(ClockError):
            command()
    clock.set_governor_limit(0.5)  # ignored in real mode
    assert clock.effective_speed() == 1.0
    with pytest.raises(ClockError):
        HouseClock(start_us=T0, mode=ClockMode.real, speed=2.0, zone=ZONE, monotonic=mono)


def test_reset_starts_new_episode_and_may_go_back():
    mono = FakeMonotonic()
    clock = accelerated(mono)
    mono.advance(10.0)
    clock.reset(start_us=T0 - 10**9, mode=ClockMode.accelerated, speed=60.0)
    assert clock.episode == 1
    assert clock.now_us() == T0 - 10**9
    assert clock.speed == 60.0
    assert not clock.paused
    assert clock.governor_limit is None


def test_governor_limit_caps_effective_speed_continuously():
    mono = FakeMonotonic()
    clock = accelerated(mono)
    mono.advance(1.0)
    clock.set_governor_limit(100.0)
    assert clock.effective_speed() == 100.0
    assert clock.now_us() == T0 + 600 * US_PER_S
    mono.advance(1.0)
    assert clock.now_us() == T0 + 700 * US_PER_S
    clock.set_governor_limit(None)
    assert clock.effective_speed() == 600.0


@pytest.mark.parametrize("speed", [0.0, -1.0, 1e9])
def test_invalid_speed(speed):
    with pytest.raises(ClockError):
        accelerated(FakeMonotonic(), speed=speed)


def test_state_reports_house_time_in_house_zone():
    clock = accelerated(FakeMonotonic())
    state = clock.state()
    assert state.house_time.utcoffset() is not None
    assert state.house_time.utcoffset().total_seconds() == 8 * 3600  # type: ignore[union-attr]
    assert state.mode is ClockMode.accelerated
    assert state.episode == 0


def test_governor_decreases_on_full_queue_and_recovers():
    gov = Governor()
    limit = gov.step(requested=600, current_limit=None, queue_fill=0.9, data_lag_wall_s=0)
    assert limit == 300
    limit = gov.step(requested=600, current_limit=limit, queue_fill=0.9, data_lag_wall_s=0)
    assert limit == 150
    # neutral zone: hold
    assert gov.step(requested=600, current_limit=150, queue_fill=0.3, data_lag_wall_s=1.0) == 150
    # recovery until the cap is lifted
    steps = 0
    while limit is not None:
        limit = gov.step(requested=600, current_limit=limit, queue_fill=0.0, data_lag_wall_s=0)
        steps += 1
    assert steps == 7  # 150 * 1.25^7 > 600


def test_governor_reacts_to_producer_lag_and_has_a_floor():
    gov = Governor()
    assert gov.step(requested=600, current_limit=None, queue_fill=0, data_lag_wall_s=5) == 300
    assert gov.step(requested=600, current_limit=1.5, queue_fill=0, data_lag_wall_s=5) == 1.0
    assert gov.step(requested=0.5, current_limit=None, queue_fill=1, data_lag_wall_s=0) == 0.5
    assert gov.step(requested=600, current_limit=None, queue_fill=0.0, data_lag_wall_s=0) is None


def test_episode_start_is_fixed_until_a_reset():
    mono = FakeMonotonic()
    clock = accelerated(mono)
    start = clock.state().episode_start
    mono.advance(5.0)
    clock.set_speed(60.0)
    clock.jump_to(clock.now_us() + 3600 * US_PER_S)
    assert clock.state().episode_start == start
    assert int(start.timestamp()) * US_PER_S == T0
    clock.reset(start_us=T0 + 10**9, mode=ClockMode.accelerated, speed=60.0)
    assert int(clock.state().episode_start.timestamp()) * US_PER_S == T0 + 10**9


def test_episode_id_is_unique_per_episode_and_per_start():
    mono = FakeMonotonic()
    clock = accelerated(mono)
    first = clock.state().episode_id
    clock.set_speed(60.0)
    assert clock.state().episode_id == first
    clock.reset(start_us=T0, mode=ClockMode.accelerated, speed=60.0)
    assert clock.state().episode_id != first
    assert accelerated(FakeMonotonic()).state().episode_id != first  # a restarted M4
