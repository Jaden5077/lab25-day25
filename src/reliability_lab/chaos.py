from __future__ import annotations

import json
import random
from pathlib import Path

from reliability_lab.cache import ResponseCache, SharedRedisCache
from reliability_lab.circuit_breaker import CircuitBreaker
from reliability_lab.config import LabConfig, ScenarioConfig
from reliability_lab.redis_env import effective_redis_url
from reliability_lab.gateway import GatewayResponse, ReliabilityGateway
from reliability_lab.metrics import RunMetrics
from reliability_lab.providers import FakeLLMProvider


def load_queries(path: str | Path = "data/sample_queries.jsonl") -> list[str]:
    queries: list[str] = []
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        queries.append(json.loads(line)["query"])
    return queries


def build_gateway(
    config: LabConfig,
    scenario: ScenarioConfig | None = None,
    *,
    cache_enabled_override: bool | None = None,
) -> ReliabilityGateway:
    """Construct a gateway; optional scenario overrides provider fail rates and cache."""
    provider_overrides = scenario.provider_overrides if scenario else None
    providers = []
    for p in config.providers:
        fail_rate = provider_overrides.get(p.name, p.fail_rate) if provider_overrides else p.fail_rate
        providers.append(FakeLLMProvider(p.name, fail_rate, p.base_latency_ms, p.cost_per_1k_tokens))
    breakers = {
        p.name: CircuitBreaker(
            name=p.name,
            failure_threshold=config.circuit_breaker.failure_threshold,
            reset_timeout_seconds=config.circuit_breaker.reset_timeout_seconds,
            success_threshold=config.circuit_breaker.success_threshold,
        )
        for p in config.providers
    }
    cache_enabled = config.cache.enabled
    sim_threshold = config.cache.similarity_threshold
    if scenario is not None:
        if scenario.cache_enabled is not None:
            cache_enabled = scenario.cache_enabled
        if scenario.similarity_threshold is not None:
            sim_threshold = scenario.similarity_threshold
    if cache_enabled_override is not None:
        cache_enabled = cache_enabled_override

    cache: ResponseCache | SharedRedisCache | None = None
    if cache_enabled:
        if config.cache.backend == "redis":
            cache = SharedRedisCache(
                effective_redis_url(config.cache),
                config.cache.ttl_seconds,
                sim_threshold,
            )
        else:
            cache = ResponseCache(config.cache.ttl_seconds, sim_threshold)
    return ReliabilityGateway(providers, breakers, cache, cost_budget=config.cost_budget)


def calculate_recovery_time_ms(gateway: ReliabilityGateway) -> float | None:
    """Derive recovery time from circuit breaker transition logs.

    Recovery time = wall-clock time between circuit opening and next successful close.
    Returns the average recovery time across all breakers, or None if no recovery occurred.
    """
    recovery_times: list[float] = []
    for breaker in gateway.breakers.values():
        open_ts: float | None = None
        for entry in breaker.transition_log:
            if entry["to"] == "open" and open_ts is None:
                open_ts = float(entry["ts"])
            elif entry["to"] == "closed" and open_ts is not None:
                recovery_times.append((float(entry["ts"]) - open_ts) * 1000)
                open_ts = None
    if not recovery_times:
        return None
    return sum(recovery_times) / len(recovery_times)


def _record_request_metrics(result: GatewayResponse, metrics: RunMetrics) -> None:
    metrics.total_requests += 1
    metrics.estimated_cost += result.estimated_cost
    if result.cache_hit or result.route.startswith("cache_hit"):
        metrics.cache_hits += 1
        metrics.estimated_cost_saved += 0.001
        metrics.successful_requests += 1
    elif result.route.startswith("fallback:"):
        metrics.fallback_successes += 1
        metrics.successful_requests += 1
    elif result.route in ("static_fallback", "budget_exceeded"):
        metrics.static_fallbacks += 1
        metrics.failed_requests += 1
    else:
        metrics.successful_requests += 1
    if result.latency_ms:
        metrics.latencies_ms.append(result.latency_ms)


def _finalize_breaker_metrics(gateway: ReliabilityGateway, metrics: RunMetrics) -> None:
    metrics.circuit_open_count = sum(
        1 for breaker in gateway.breakers.values() for t in breaker.transition_log if t["to"] == "open"
    )
    metrics.recovery_time_ms = calculate_recovery_time_ms(gateway)


def collect_metrics_for_gateway(
    gateway: ReliabilityGateway,
    config: LabConfig,
    queries: list[str],
) -> RunMetrics:
    """Drive a gateway with repeated random prompts and aggregate RunMetrics."""
    metrics = RunMetrics()
    request_count = config.load_test.requests
    for _ in range(request_count):
        prompt = random.choice(queries)
        result = gateway.complete(prompt)
        _record_request_metrics(result, metrics)
    _finalize_breaker_metrics(gateway, metrics)
    return metrics


def scenario_passes(name: str, metrics: RunMetrics) -> bool:
    """Heuristic pass/fail per named chaos scenario."""
    if name == "primary_timeout_100":
        return metrics.fallback_successes > 0 and metrics.fallback_success_rate >= 0.5
    if name == "primary_flaky_50":
        return metrics.successful_requests > 0 and (
            metrics.circuit_open_count > 0 or metrics.fallback_successes > 0
        )
    if name == "cache_stale_candidate":
        return metrics.total_requests > 0 and metrics.successful_requests > 0
    if name == "cache_latency_ab":
        return metrics.successful_requests > 0
    if name == "all_healthy":
        return metrics.successful_requests > 0
    return metrics.successful_requests > 0


def run_scenario(config: LabConfig, queries: list[str], scenario: ScenarioConfig) -> RunMetrics:
    """Run a single named chaos scenario."""
    if scenario.run_cache_ab_comparison:
        gw_off = build_gateway(config, scenario, cache_enabled_override=False)
        gw_on = build_gateway(config, scenario, cache_enabled_override=True)
        metrics_off = collect_metrics_for_gateway(gw_off, config, queries)
        metrics_on = collect_metrics_for_gateway(gw_on, config, queries)
        out = metrics_on.model_copy(deep=True)
        out.scenarios = {
            scenario.name: "pass" if scenario_passes(scenario.name, metrics_on) else "fail"
        }
        out.cache_comparison = {
            scenario.name: {
                "without_cache": metrics_off.to_report_dict(),
                "with_cache": metrics_on.to_report_dict(),
            }
        }
        return out

    gateway = build_gateway(config, scenario)
    metrics = collect_metrics_for_gateway(gateway, config, queries)
    metrics.scenarios[scenario.name] = "pass" if scenario_passes(scenario.name, metrics) else "fail"
    return metrics


def run_simulation(config: LabConfig, queries: list[str]) -> RunMetrics:
    """Run all named scenarios from config, or a default run if none defined."""
    if not config.scenarios:
        gateway = build_gateway(config, None)
        metrics = collect_metrics_for_gateway(gateway, config, queries)
        metrics.scenarios = {"default": "pass" if metrics.successful_requests > 0 else "fail"}
        return metrics

    combined = RunMetrics()
    for scenario in config.scenarios:
        result = run_scenario(config, queries, scenario)
        combined.scenarios[scenario.name] = result.scenarios.get(scenario.name, "fail")
        if result.cache_comparison:
            combined.cache_comparison.update(result.cache_comparison)

        combined.total_requests += result.total_requests
        combined.successful_requests += result.successful_requests
        combined.failed_requests += result.failed_requests
        combined.fallback_successes += result.fallback_successes
        combined.static_fallbacks += result.static_fallbacks
        combined.cache_hits += result.cache_hits
        combined.circuit_open_count += result.circuit_open_count
        combined.estimated_cost += result.estimated_cost
        combined.estimated_cost_saved += result.estimated_cost_saved
        combined.latencies_ms.extend(result.latencies_ms)
        if result.recovery_time_ms is not None:
            if combined.recovery_time_ms is None:
                combined.recovery_time_ms = result.recovery_time_ms
            else:
                combined.recovery_time_ms = (combined.recovery_time_ms + result.recovery_time_ms) / 2

    return combined
