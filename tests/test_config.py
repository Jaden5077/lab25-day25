from reliability_lab.config import load_config


def test_default_config_loads() -> None:
    config = load_config("configs/default.yaml")
    assert len(config.providers) >= 2
    assert config.circuit_breaker.failure_threshold > 0
    assert 0 <= config.cache.similarity_threshold <= 1
    assert config.cache.redis_url.startswith("fakeredis://")


def test_scenarios_loaded() -> None:
    config = load_config("configs/default.yaml")
    assert len(config.scenarios) >= 5
    names = [s.name for s in config.scenarios]
    assert "primary_timeout_100" in names
    assert "cache_stale_candidate" in names
    assert "cache_latency_ab" in names
