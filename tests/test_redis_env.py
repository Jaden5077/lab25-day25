"""redis_url environment helpers and fakeredis vs TCP classification."""

from __future__ import annotations

import pytest

from reliability_lab.config import CacheConfig, load_config
from reliability_lab.redis_env import (
    classify_redis_url,
    describe_redis_connection,
    effective_redis_url,
    get_redis_url_from_environment,
    redis_url_source,
    tcp_redis_ping_ok,
)


@pytest.fixture(autouse=True)
def _clear_redis_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("RELIABILITY_LAB_REDIS_URL", raising=False)


def test_classify_fakeredis_vs_tcp() -> None:
    assert classify_redis_url("fakeredis://lab/0") == "fakeredis"
    assert classify_redis_url("redis://localhost:6379/0") == "tcp"
    assert classify_redis_url("rediss://example:6380/0") == "tcp"


def test_reliability_lab_redis_url_wins_over_redis_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://a:6379/0")
    monkeypatch.setenv("RELIABILITY_LAB_REDIS_URL", "fakeredis://prio/0")
    assert get_redis_url_from_environment() == "fakeredis://prio/0"


def test_effective_redis_url_prefers_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://localhost:9999/0")
    cache = CacheConfig(
        ttl_seconds=60,
        similarity_threshold=0.85,
        redis_url="fakeredis://lab/0",
    )
    assert effective_redis_url(cache) == "redis://localhost:9999/0"


def test_load_config_merges_redis_url_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("REDIS_URL", "fakeredis://fromenv/0")
    p = tmp_path / "c.yaml"
    p.write_text(
        """
providers:
  - name: primary
    fail_rate: 0.0
    base_latency_ms: 1
    cost_per_1k_tokens: 0.001
circuit_breaker:
  failure_threshold: 1
  reset_timeout_seconds: 1
  success_threshold: 1
cache:
  enabled: false
  backend: memory
  ttl_seconds: 60
  similarity_threshold: 0.5
  redis_url: "redis://localhost:6379/0"
load_test:
  requests: 1
""",
        encoding="utf-8",
    )
    cfg = load_config(p)
    assert cfg.cache.redis_url == "fakeredis://fromenv/0"


def test_redis_url_source(monkeypatch: pytest.MonkeyPatch) -> None:
    assert redis_url_source() == "config"
    monkeypatch.setenv("REDIS_URL", "redis://x/0")
    assert redis_url_source() == "environment"


def test_describe_fakeredis() -> None:
    assert "fakeredis" in describe_redis_connection("fakeredis://lab/0").lower()


def test_tcp_redis_ping_fakeredis_is_true() -> None:
    assert tcp_redis_ping_ok("fakeredis://ping/0") is True
