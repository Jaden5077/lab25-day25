from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Shared utilities — use these in both ResponseCache and SharedRedisCache
# ---------------------------------------------------------------------------

PRIVACY_PATTERNS = re.compile(
    r"\b(balance|password|credit.card|ssn|social.security|user.\d+|account.\d+)\b",
    re.IGNORECASE,
)


def _is_uncacheable(query: str) -> bool:
    """Return True if query contains privacy-sensitive keywords."""
    return bool(PRIVACY_PATTERNS.search(query))


def _looks_like_false_hit(query: str, cached_key: str) -> bool:
    """Return True if query and cached key contain different 4-digit numbers (years, IDs)."""
    nums_q = set(re.findall(r"\b\d{4}\b", query))
    nums_c = set(re.findall(r"\b\d{4}\b", cached_key))
    return bool(nums_q and nums_c and nums_q != nums_c)


# One FakeServer per hostname so multiple clients can share the same in-memory keyspace.
_fakeredis_servers: dict[str, Any] = {}


def _redis_client_from_url(redis_url: str) -> Any:
    """Return a redis-py client. ``fakeredis://`` uses in-process RAM (no Redis daemon)."""
    from urllib.parse import urlparse

    parsed = urlparse(redis_url)
    if parsed.scheme == "fakeredis":
        import fakeredis

        name = parsed.hostname or "default"
        if name not in _fakeredis_servers:
            _fakeredis_servers[name] = fakeredis.FakeServer()
        return fakeredis.FakeStrictRedis(
            server=_fakeredis_servers[name],
            decode_responses=True,
        )
    import redis as redis_lib

    return redis_lib.Redis.from_url(redis_url, decode_responses=True)


# ---------------------------------------------------------------------------
# In-memory cache
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CacheEntry:
    key: str
    value: str
    created_at: float
    metadata: dict[str, str]


class ResponseCache:
    """In-memory semantic cache with TTL and false-hit guardrails."""

    def __init__(self, ttl_seconds: int, similarity_threshold: float) -> None:
        self.ttl_seconds = ttl_seconds
        self.similarity_threshold = similarity_threshold
        self._entries: list[CacheEntry] = []
        self.false_hit_log: list[dict[str, object]] = []

    def get(self, query: str) -> tuple[str | None, float]:
        if _is_uncacheable(query):
            return None, 0.0
        best_value: str | None = None
        best_key: str | None = None
        best_score = 0.0
        now = time.time()
        self._entries = [e for e in self._entries if now - e.created_at <= self.ttl_seconds]
        for entry in self._entries:
            score = self.similarity(query, entry.key)
            if score > best_score:
                best_score = score
                best_value = entry.value
                best_key = entry.key
        if best_score >= self.similarity_threshold and best_value is not None and best_key is not None:
            if _looks_like_false_hit(query, best_key):
                self.false_hit_log.append(
                    {"query": query, "cached_key": best_key, "score": best_score}
                )
                return None, best_score
            return best_value, best_score
        return None, best_score

    def set(self, query: str, value: str, metadata: dict[str, str] | None = None) -> None:
        if _is_uncacheable(query):
            return
        meta = metadata or {}
        if meta.get("expected_risk") == "privacy":
            return
        self._entries.append(CacheEntry(query, value, time.time(), meta))

    @staticmethod
    def similarity(a: str, b: str) -> float:
        """Token Jaccard plus character bigram overlap (no year penalty — handled in get())."""
        a_norm = a.lower().strip()
        b_norm = b.lower().strip()
        if not a_norm or not b_norm:
            return 0.0
        if a_norm == b_norm:
            return 1.0
        words_a = set(re.findall(r"[a-z0-9]+", a_norm))
        words_b = set(re.findall(r"[a-z0-9]+", b_norm))
        if not words_a or not words_b:
            return 0.0
        jaccard = len(words_a & words_b) / len(words_a | words_b)

        def bigrams(s: str) -> set[str]:
            s = re.sub(r"\s+", " ", s)
            if len(s) < 2:
                return set()
            return {s[i : i + 2] for i in range(len(s) - 1)}

        ba, bb = bigrams(a_norm), bigrams(b_norm)
        if ba and bb:
            bigram_sim = len(ba & bb) / len(ba | bb)
        else:
            bigram_sim = 0.0
        return float(0.55 * jaccard + 0.45 * bigram_sim)


# ---------------------------------------------------------------------------
# Redis shared cache
# ---------------------------------------------------------------------------


class SharedRedisCache:
    """Redis-backed shared cache for multi-instance deployments."""

    def __init__(
        self,
        redis_url: str,
        ttl_seconds: int,
        similarity_threshold: float,
        prefix: str = "rl:cache:",
    ):
        self.ttl_seconds = ttl_seconds
        self.similarity_threshold = similarity_threshold
        self.prefix = prefix
        self.false_hit_log: list[dict[str, object]] = []
        self._redis: Any = _redis_client_from_url(redis_url)

    def ping(self) -> bool:
        """Check Redis connectivity."""
        try:
            return bool(self._redis.ping())
        except Exception:
            return False

    def get(self, query: str) -> tuple[str | None, float]:
        """Look up a cached response from Redis."""
        try:
            if _is_uncacheable(query):
                return None, 0.0
            exact_key = f"{self.prefix}{self._query_hash(query)}"
            direct = self._redis.hget(exact_key, "response")
            if direct is not None:
                return direct, 1.0

            best_score = 0.0
            best_response: str | None = None
            best_cached_query: str | None = None
            for key in self._redis.scan_iter(f"{self.prefix}*"):
                cached_q = self._redis.hget(key, "query")
                if cached_q is None:
                    continue
                score = ResponseCache.similarity(query, cached_q)
                if score > best_score:
                    best_score = score
                    best_response = self._redis.hget(key, "response")
                    best_cached_query = cached_q

            if (
                best_score >= self.similarity_threshold
                and best_response is not None
                and best_cached_query is not None
            ):
                if _looks_like_false_hit(query, best_cached_query):
                    self.false_hit_log.append(
                        {"query": query, "cached_key": best_cached_query, "score": best_score}
                    )
                    return None, best_score
                return best_response, best_score
            return None, best_score
        except Exception:
            return None, 0.0

    def set(self, query: str, value: str, metadata: dict[str, str] | None = None) -> None:
        """Store a response in Redis with TTL."""
        try:
            if _is_uncacheable(query):
                return
            meta = metadata or {}
            if meta.get("expected_risk") == "privacy":
                return
            key = f"{self.prefix}{self._query_hash(query)}"
            self._redis.hset(key, mapping={"query": query, "response": value})
            self._redis.expire(key, self.ttl_seconds)
        except Exception:
            return

    def flush(self) -> None:
        """Remove all entries with this cache prefix (for testing)."""
        try:
            for key in self._redis.scan_iter(f"{self.prefix}*"):
                self._redis.delete(key)
        except Exception:
            return

    def close(self) -> None:
        """Close Redis connection."""
        if self._redis is not None:
            self._redis.close()

    @staticmethod
    def _query_hash(query: str) -> str:
        """Deterministic short hash for a query string."""
        return hashlib.md5(query.lower().strip().encode()).hexdigest()[:12]
