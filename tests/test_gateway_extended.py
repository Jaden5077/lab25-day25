"""Gateway routing, latency, cost budget, and fallback chain."""

from __future__ import annotations

import re

from reliability_lab.cache import ResponseCache
from reliability_lab.circuit_breaker import CircuitBreaker, CircuitOpenError
from reliability_lab.gateway import ReliabilityGateway
from reliability_lab.providers import FakeLLMProvider, ProviderError, ProviderResponse


def test_primary_route_includes_provider_name() -> None:
    p = FakeLLMProvider("gpt4", fail_rate=0.0, base_latency_ms=1, cost_per_1k_tokens=0.001)
    b = CircuitBreaker("gpt4", failure_threshold=2, reset_timeout_seconds=1.0)
    gw = ReliabilityGateway([p], {"gpt4": b}, None)
    r = gw.complete("hello")
    assert r.route == "primary:gpt4"
    assert r.provider == "gpt4"
    assert r.cache_hit is False


def test_fallback_route_includes_provider_name() -> None:
    primary = FakeLLMProvider("a", fail_rate=1.0, base_latency_ms=1, cost_per_1k_tokens=0.001)
    backup = FakeLLMProvider("b", fail_rate=0.0, base_latency_ms=1, cost_per_1k_tokens=0.001)
    gw = ReliabilityGateway(
        [primary, backup],
        {
            "a": CircuitBreaker("a", failure_threshold=5, reset_timeout_seconds=1.0),
            "b": CircuitBreaker("b", failure_threshold=5, reset_timeout_seconds=1.0),
        },
        None,
    )
    r = gw.complete("hello")
    assert r.route == "fallback:b"
    assert r.text


def test_cache_hit_route_includes_score() -> None:
    cache = ResponseCache(60, 0.2)
    cache.set("hello world", "cached", {})
    p = FakeLLMProvider("p", fail_rate=0.0, base_latency_ms=1, cost_per_1k_tokens=0.001)
    gw = ReliabilityGateway(
        [p], {"p": CircuitBreaker("p", failure_threshold=2, reset_timeout_seconds=1.0)}, cache
    )
    r = gw.complete("hello world")
    assert r.cache_hit is True
    assert re.match(r"cache_hit:1\.00", r.route)


def test_latency_ms_covers_full_complete() -> None:
    p = FakeLLMProvider("p", fail_rate=0.0, base_latency_ms=5, cost_per_1k_tokens=0.001)
    gw = ReliabilityGateway(
        [p], {"p": CircuitBreaker("p", failure_threshold=2, reset_timeout_seconds=1.0)}, None
    )
    r = gw.complete("hello")
    assert r.latency_ms >= 1.0


def test_cost_budget_blocks_after_cumulative_spend() -> None:
    class CheapProvider(FakeLLMProvider):
        def complete(self, prompt: str) -> ProviderResponse:  # type: ignore[override]
            return ProviderResponse(
                provider=self.name,
                text="ok",
                latency_ms=0.0,
                input_tokens=1,
                output_tokens=1,
                estimated_cost=0.01,
            )

    p = CheapProvider("p", fail_rate=0.0, base_latency_ms=1, cost_per_1k_tokens=0.001)
    gw = ReliabilityGateway(
        [p],
        {"p": CircuitBreaker("p", failure_threshold=5, reset_timeout_seconds=1.0)},
        None,
        cost_budget=0.015,
    )
    r1 = gw.complete("a")
    assert r1.route.startswith("primary")
    r2 = gw.complete("b")
    assert r2.route == "budget_exceeded"
    assert r2.error == "cost budget exceeded"


def test_static_fallback_when_all_providers_fail_open() -> None:
    class FailingProvider(FakeLLMProvider):
        def complete(self, prompt: str) -> ProviderResponse:  # type: ignore[override]
            raise ProviderError("down")

    a = FailingProvider("a", fail_rate=0.0, base_latency_ms=1, cost_per_1k_tokens=0.001)
    b = FailingProvider("b", fail_rate=0.0, base_latency_ms=1, cost_per_1k_tokens=0.001)
    ba = CircuitBreaker("a", failure_threshold=1, reset_timeout_seconds=100.0)
    bb = CircuitBreaker("b", failure_threshold=1, reset_timeout_seconds=100.0)
    gw = ReliabilityGateway([a, b], {"a": ba, "b": bb}, None)
    gw.complete("x")
    gw.complete("x")
    r = gw.complete("x")
    assert r.route == "static_fallback"
    assert "degraded" in r.text.lower()


def test_circuit_open_skips_provider_call() -> None:
    calls = {"n": 0}

    class CountingProvider(FakeLLMProvider):
        def complete(self, prompt: str) -> ProviderResponse:  # type: ignore[override]
            calls["n"] += 1
            return ProviderResponse(
                provider=self.name,
                text="ok",
                latency_ms=0.0,
                input_tokens=1,
                output_tokens=1,
                estimated_cost=0.0,
            )

    p = CountingProvider("p", fail_rate=0.0, base_latency_ms=1, cost_per_1k_tokens=0.0)
    br = CircuitBreaker("p", failure_threshold=1, reset_timeout_seconds=100.0)

    def fail() -> ProviderResponse:
        raise ProviderError("fail")

    with pytest.raises(ProviderError):
        br.call(fail)
    with pytest.raises(CircuitOpenError):
        br.call(p.complete, "q")
    assert calls["n"] == 0
