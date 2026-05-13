from __future__ import annotations

import time
from dataclasses import dataclass

from reliability_lab.cache import ResponseCache, SharedRedisCache
from reliability_lab.circuit_breaker import CircuitBreaker, CircuitOpenError
from reliability_lab.providers import FakeLLMProvider, ProviderError, ProviderResponse


@dataclass(slots=True)
class GatewayResponse:
    text: str
    route: str
    provider: str | None
    cache_hit: bool
    latency_ms: float
    estimated_cost: float
    error: str | None = None


class ReliabilityGateway:
    """Routes requests through cache, circuit breakers, and fallback providers."""

    def __init__(
        self,
        providers: list[FakeLLMProvider],
        breakers: dict[str, CircuitBreaker],
        cache: ResponseCache | SharedRedisCache | None = None,
        cost_budget: float | None = None,
    ):
        self.providers = providers
        self.breakers = breakers
        self.cache = cache
        self.cost_budget = cost_budget
        self._cumulative_cost = 0.0

    def complete(self, prompt: str) -> GatewayResponse:
        """Return a reliable response or a static fallback."""
        t0 = time.perf_counter()

        def wall_latency_ms() -> float:
            return (time.perf_counter() - t0) * 1000

        if self.cache is not None:
            cached, score = self.cache.get(prompt)
            if cached is not None:
                return GatewayResponse(
                    text=cached,
                    route=f"cache_hit:{score:.2f}",
                    provider=None,
                    cache_hit=True,
                    latency_ms=wall_latency_ms(),
                    estimated_cost=0.0,
                )

        if (
            self.cost_budget is not None
            and self.cost_budget > 0
            and self._cumulative_cost >= self.cost_budget
        ):
            return GatewayResponse(
                text="The service is temporarily degraded. Please try again soon.",
                route="budget_exceeded",
                provider=None,
                cache_hit=False,
                latency_ms=wall_latency_ms(),
                estimated_cost=0.0,
                error="cost budget exceeded",
            )

        last_error: str | None = None
        for idx, provider in enumerate(self.providers):
            breaker = self.breakers[provider.name]
            try:
                response: ProviderResponse = breaker.call(provider.complete, prompt)
                self._cumulative_cost += response.estimated_cost
                if self.cache is not None:
                    self.cache.set(prompt, response.text, {"provider": provider.name})
                if idx == 0:
                    route = f"primary:{provider.name}"
                else:
                    route = f"fallback:{provider.name}"
                return GatewayResponse(
                    text=response.text,
                    route=route,
                    provider=provider.name,
                    cache_hit=False,
                    latency_ms=wall_latency_ms(),
                    estimated_cost=response.estimated_cost,
                )
            except (ProviderError, CircuitOpenError) as exc:
                last_error = str(exc)
                continue

        return GatewayResponse(
            text="The service is temporarily degraded. Please try again soon.",
            route="static_fallback",
            provider=None,
            cache_hit=False,
            latency_ms=wall_latency_ms(),
            estimated_cost=0.0,
            error=last_error,
        )
