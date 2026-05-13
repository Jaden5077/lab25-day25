"""Chaos simulation: gateway construction, metrics aggregation, scenarios."""

from __future__ import annotations

import random
from typing import Any

import pytest

from reliability_lab.circuit_breaker import CircuitBreaker
from reliability_lab.chaos import (
    build_gateway,
    calculate_recovery_time_ms,
    collect_metrics_for_gateway,
    run_scenario,
    run_simulation,
    scenario_passes,
)
from reliability_lab.config import LabConfig, ScenarioConfig


def _tiny_lab_config(**overrides: Any) -> LabConfig:
    data: dict[str, object] = {
        "providers": [
            {"name": "primary", "fail_rate": 0.0, "base_latency_ms": 1, "cost_per_1k_tokens": 0.001},
            {"name": "backup", "fail_rate": 0.0, "base_latency_ms": 1, "cost_per_1k_tokens": 0.001},
        ],
        "circuit_breaker": {
            "failure_threshold": 2,
            "reset_timeout_seconds": 0.5,
            "success_threshold": 1,
        },
        "cache": {
            "enabled": True,
            "backend": "memory",
            "ttl_seconds": 300,
            "similarity_threshold": 0.85,
            "redis_url": "fakeredis://lab/0",
        },
        "load_test": {"requests": 5},
        "scenarios": [],
    }
    data.update(overrides)
    return LabConfig.model_validate(data)


def test_calculate_recovery_time_from_transition_log() -> None:
    cb = CircuitBreaker("p", failure_threshold=1, reset_timeout_seconds=1.0)
    cb.transition_log = [
        {"from": "closed", "to": "open", "reason": "t", "ts": 10.0},
        {"from": "open", "to": "closed", "reason": "t", "ts": 12.5},
    ]
    gw = build_gateway(_tiny_lab_config(), None)
    gw.breakers["primary"] = cb
    rt = calculate_recovery_time_ms(gw)
    assert rt is not None
    assert abs(rt - 2500.0) < 0.01


def test_build_gateway_applies_provider_overrides() -> None:
    cfg = _tiny_lab_config()
    sc = ScenarioConfig(name="x", provider_overrides={"primary": 1.0})
    gw = build_gateway(cfg, sc)
    assert gw.providers[0].fail_rate == 1.0
    assert gw.providers[1].fail_rate == 0.0


def test_build_gateway_cache_enabled_override() -> None:
    cfg = _tiny_lab_config()
    sc = ScenarioConfig(name="x")
    assert build_gateway(cfg, sc, cache_enabled_override=False).cache is None
    assert build_gateway(cfg, sc, cache_enabled_override=True).cache is not None


def test_collect_metrics_classifies_routes() -> None:
    random.seed(0)
    cfg = _tiny_lab_config(
        cache={"enabled": False, "backend": "memory", "ttl_seconds": 300, "similarity_threshold": 0.9}
    )
    gw = build_gateway(cfg, ScenarioConfig(name="t", provider_overrides={"primary": 1.0}))
    m = collect_metrics_for_gateway(gw, cfg, ["hello", "world"])
    assert m.total_requests == 5
    assert m.fallback_successes >= 1
    assert m.successful_requests == 5


def test_scenario_passes_primary_timeout() -> None:
    m = collect_metrics_for_gateway(
        build_gateway(
            _tiny_lab_config(),
            ScenarioConfig(name="primary_timeout_100", provider_overrides={"primary": 1.0}),
        ),
        _tiny_lab_config(),
        ["q"],
    )
    assert scenario_passes("primary_timeout_100", m) is True


def test_run_scenario_sets_scenario_outcome() -> None:
    random.seed(1)
    cfg = _tiny_lab_config()
    sc = ScenarioConfig(name="all_healthy", provider_overrides={})
    m = run_scenario(cfg, ["a", "b"], sc)
    assert m.scenarios.get("all_healthy") == "pass"


def test_run_cache_ab_comparison_populates_cache_comparison() -> None:
    random.seed(2)
    cfg = _tiny_lab_config()
    sc = ScenarioConfig(name="cache_latency_ab", run_cache_ab_comparison=True)
    m = run_scenario(cfg, ["hello world", "other"], sc)
    assert "cache_latency_ab" in m.cache_comparison
    assert "without_cache" in m.cache_comparison["cache_latency_ab"]
    assert "with_cache" in m.cache_comparison["cache_latency_ab"]


def test_run_simulation_merges_multiple_scenarios() -> None:
    random.seed(3)
    cfg = _tiny_lab_config(
        scenarios=[
            {"name": "s1", "provider_overrides": {"primary": 1.0}},
            {"name": "s2", "provider_overrides": {}},
        ]
    )
    m = run_simulation(cfg, ["x"])
    assert m.scenarios.get("s1") == "pass"
    assert m.scenarios.get("s2") == "pass"
    assert m.total_requests == 10
