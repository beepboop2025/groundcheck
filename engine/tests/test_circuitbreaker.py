"""Tests for the vendored free-llm-router CircuitBreaker state machine.

Deterministic via an injected fake clock. Pins the documented invariants:
closed -> open after N failures, open -> half_open after cooldown, and the
single-probe rule in half_open."""
import asyncio

import pytest

from free_llm_router import CircuitBreaker, State


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, secs):
        self.t += secs


def run(coro):
    return asyncio.run(coro)


def test_starts_closed_and_allows():
    b = CircuitBreaker(monotonic=FakeClock())
    assert b.state is State.CLOSED
    assert run(b.allow()) is True


def test_opens_after_threshold_failures():
    b = CircuitBreaker(monotonic=FakeClock(), failure_threshold=3)
    run(b.record_failure())
    run(b.record_failure())
    assert b.state is State.CLOSED          # 2 < 3, still closed
    run(b.record_failure())
    assert b.state is State.OPEN            # 3rd trips it
    assert run(b.allow()) is False          # rejects fast while open


def test_success_resets_failure_count():
    b = CircuitBreaker(monotonic=FakeClock(), failure_threshold=3)
    run(b.record_failure())
    run(b.record_failure())
    run(b.record_success())                 # counter back to 0
    run(b.record_failure())
    run(b.record_failure())
    assert b.state is State.CLOSED          # only 2 since reset


def test_open_transitions_to_half_open_after_cooldown():
    clk = FakeClock()
    b = CircuitBreaker(monotonic=clk, failure_threshold=1, cooldown_sec=30.0)
    run(b.record_failure())
    assert b.state is State.OPEN
    assert run(b.allow()) is False          # still cooling down
    clk.advance(30.0)
    assert run(b.allow()) is True           # cooldown elapsed -> one probe
    assert b.state is State.HALF_OPEN


def test_half_open_allows_only_one_probe():
    clk = FakeClock()
    b = CircuitBreaker(monotonic=clk, failure_threshold=1, cooldown_sec=10.0)
    run(b.record_failure())
    clk.advance(10.0)
    assert run(b.allow()) is True           # the single probe
    # Concurrent / subsequent callers must be refused until the probe resolves.
    assert run(b.allow()) is False
    assert run(b.allow()) is False


def test_half_open_success_closes_circuit():
    clk = FakeClock()
    b = CircuitBreaker(monotonic=clk, failure_threshold=1, cooldown_sec=10.0)
    run(b.record_failure())
    clk.advance(10.0)
    run(b.allow())                          # take the probe
    run(b.record_success())
    assert b.state is State.CLOSED
    assert run(b.allow()) is True


def test_half_open_failure_reopens_and_restarts_cooldown():
    clk = FakeClock()
    b = CircuitBreaker(monotonic=clk, failure_threshold=1, cooldown_sec=10.0)
    run(b.record_failure())
    clk.advance(10.0)
    run(b.allow())                          # probe
    run(b.record_failure())                 # probe fails
    assert b.state is State.OPEN
    assert run(b.allow()) is False          # cooldown restarted, no immediate probe
    clk.advance(10.0)
    assert run(b.allow()) is True           # cooldown elapsed again
