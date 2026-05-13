"""Circuit breaker state machine and fail-fast behavior."""

from __future__ import annotations

import time

import pytest

from reliability_lab.circuit_breaker import CircuitBreaker, CircuitOpenError, CircuitState


def test_closed_allows_requests() -> None:
    cb = CircuitBreaker("p", failure_threshold=3, reset_timeout_seconds=0.01)
    assert cb.allow_request() is True
    assert cb.state == CircuitState.CLOSED


def test_open_blocks_until_reset_then_half_open() -> None:
    cb = CircuitBreaker("p", failure_threshold=1, reset_timeout_seconds=0.05)
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    assert cb.allow_request() is False
    time.sleep(0.06)
    assert cb.allow_request() is True
    assert cb.state == CircuitState.HALF_OPEN


def test_closed_opens_after_failure_threshold() -> None:
    cb = CircuitBreaker("p", failure_threshold=2, reset_timeout_seconds=1.0)
    cb.record_failure()
    assert cb.state == CircuitState.CLOSED
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    assert cb.opened_at is not None


def test_half_open_failure_reopens() -> None:
    cb = CircuitBreaker("p", failure_threshold=1, reset_timeout_seconds=0.05)
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    time.sleep(0.06)
    cb.allow_request()
    assert cb.state == CircuitState.HALF_OPEN
    cb.record_failure()
    assert cb.state == CircuitState.OPEN


def test_half_open_success_threshold_closes() -> None:
    cb = CircuitBreaker("p", failure_threshold=1, reset_timeout_seconds=0.05, success_threshold=2)
    cb.record_failure()
    time.sleep(0.06)
    cb.allow_request()
    assert cb.state == CircuitState.HALF_OPEN
    cb.record_success()
    assert cb.state == CircuitState.HALF_OPEN
    cb.record_success()
    assert cb.state == CircuitState.CLOSED


def test_call_raises_circuit_open_error_when_open() -> None:
    cb = CircuitBreaker("p", failure_threshold=1, reset_timeout_seconds=100.0)
    cb.record_failure()
    assert cb.state == CircuitState.OPEN

    def boom() -> str:
        raise AssertionError("should not run")

    with pytest.raises(CircuitOpenError):
        cb.call(boom)


def test_call_invokes_fn_when_closed() -> None:
    cb = CircuitBreaker("p", failure_threshold=5, reset_timeout_seconds=1.0)

    def ok() -> str:
        return "x"

    assert cb.call(ok) == "x"


def test_transition_log_records_state_changes() -> None:
    cb = CircuitBreaker("p", failure_threshold=1, reset_timeout_seconds=0.02)
    cb.record_failure()
    assert any(t["to"] == "open" for t in cb.transition_log)
    time.sleep(0.03)
    cb.allow_request()
    assert any(t["to"] == "half_open" for t in cb.transition_log)
