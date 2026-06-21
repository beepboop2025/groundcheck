"""Tests for the vendored free-llm-router TokenBucket.

A fake monotonic clock is injected so refill behaviour is fully deterministic —
no sleeps, no flakiness."""
import asyncio

import pytest

from free_llm_router import TokenBucket


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, secs):
        self.t += secs


def run(coro):
    return asyncio.run(coro)


def test_starts_full_and_drains_one_per_acquire():
    clk = FakeClock()
    b = TokenBucket(rpm=3, monotonic=clk)
    assert run(b.try_acquire()) is True
    assert run(b.try_acquire()) is True
    assert run(b.try_acquire()) is True
    # capacity exhausted, no time has passed -> refused
    assert run(b.try_acquire()) is False


def test_refills_over_time():
    clk = FakeClock()
    b = TokenBucket(rpm=60, monotonic=clk)  # 1 token/sec
    for _ in range(60):
        assert run(b.try_acquire()) is True
    assert run(b.try_acquire()) is False
    clk.advance(1.0)  # one second -> one token back
    assert run(b.try_acquire()) is True
    assert run(b.try_acquire()) is False


def test_refill_is_capped_at_capacity():
    clk = FakeClock()
    b = TokenBucket(rpm=5, monotonic=clk)
    for _ in range(5):
        run(b.try_acquire())
    clk.advance(10_000)  # huge idle gap must NOT overflow the bucket
    granted = sum(1 for _ in range(100) if run(b.try_acquire()))
    assert granted == 5


def test_seconds_until_token_zero_when_available():
    clk = FakeClock()
    b = TokenBucket(rpm=10, monotonic=clk)
    assert run(b.seconds_until_token()) == 0.0


def test_seconds_until_token_estimates_wait():
    clk = FakeClock()
    b = TokenBucket(rpm=60, monotonic=clk)  # 1 token/sec
    for _ in range(60):
        run(b.try_acquire())
    # empty bucket, 1 token/sec refill -> ~1s until the next token
    assert run(b.seconds_until_token()) == pytest.approx(1.0, abs=1e-6)


def test_day_count_tracks_grants_only():
    clk = FakeClock()
    b = TokenBucket(rpm=2, monotonic=clk)
    run(b.try_acquire())
    run(b.try_acquire())
    run(b.try_acquire())  # refused — must not increment the daily counter
    assert b.day_count == 2
    b.reset_day()
    assert b.day_count == 0


def test_rpm_floor_of_one():
    # rpm<=0 must not divide-by-zero or permanently lock; floor is 1.
    clk = FakeClock()
    b = TokenBucket(rpm=0, monotonic=clk)
    assert run(b.try_acquire()) is True
    assert run(b.try_acquire()) is False
