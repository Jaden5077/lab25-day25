"""ResponseCache similarity, TTL, privacy guardrails, and false-hit handling."""

from __future__ import annotations

import time

from reliability_lab.cache import ResponseCache, _is_uncacheable


def test_uncacheable_balance_query() -> None:
    assert _is_uncacheable("What is my account balance for user 123?") is True


def test_similarity_exact_match_one() -> None:
    assert ResponseCache.similarity("Hello", "hello") == 1.0


def test_false_hit_different_years_not_returned() -> None:
    cache = ResponseCache(ttl_seconds=60, similarity_threshold=0.3)
    cache.set("Summarize refund policy for 2024 deadline", "Old refund policy")
    cached, score = cache.get("Summarize refund policy for 2026 deadline")
    assert cached is None
    assert len(cache.false_hit_log) >= 1


def test_privacy_query_not_stored() -> None:
    cache = ResponseCache(60, 0.5)
    cache.set("account balance for user 123", "secret")
    cached, _ = cache.get("account balance for user 123")
    assert cached is None


def test_expected_risk_privacy_metadata_skips_set() -> None:
    cache = ResponseCache(60, 0.9)
    cache.set("generic faq text", "v", {"expected_risk": "privacy"})
    cached, _ = cache.get("generic faq text")
    assert cached is None


def test_ttl_evicts_entries() -> None:
    cache = ResponseCache(ttl_seconds=1, similarity_threshold=0.1)
    cache.set("q", "a")
    time.sleep(1.1)
    cached, _ = cache.get("q")
    assert cached is None
