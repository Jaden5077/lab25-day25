"""Environment overrides and classification for Redis / fakeredis URLs."""

from __future__ import annotations

import os
from typing import Literal
from urllib.parse import urlparse

from reliability_lab.config import CacheConfig

# Highest priority first — lab-specific override, then the usual REDIS_URL convention.
_REDIS_URL_ENV_KEYS: tuple[str, ...] = ("RELIABILITY_LAB_REDIS_URL", "REDIS_URL")


def get_redis_url_from_environment() -> str | None:
    """Return ``redis_url`` from environment if any supported variable is set and non-empty."""
    for key in _REDIS_URL_ENV_KEYS:
        raw = os.environ.get(key)
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    return None


def effective_redis_url(cache: CacheConfig) -> str:
    """Config file value, overridden by ``RELIABILITY_LAB_REDIS_URL`` or ``REDIS_URL`` when set."""
    return get_redis_url_from_environment() or cache.redis_url


def classify_redis_url(redis_url: str) -> Literal["fakeredis", "tcp"]:
    """``fakeredis://`` → in-process RAM; anything else (``redis://``, ``rediss://``, …) → network TCP/TLS."""
    scheme = urlparse(redis_url.strip()).scheme.lower()
    if scheme == "fakeredis":
        return "fakeredis"
    return "tcp"


def tcp_redis_ping_ok(redis_url: str, *, timeout_s: float = 0.75) -> bool:
    """Return whether a **network** Redis responds to PING. Always True for ``fakeredis://`` URLs."""
    if classify_redis_url(redis_url) == "fakeredis":
        return True
    try:
        import redis as redis_lib

        client = redis_lib.Redis.from_url(
            redis_url,
            socket_connect_timeout=timeout_s,
            decode_responses=True,
        )
        ok = bool(client.ping())
        client.close()
        return ok
    except Exception:
        return False


def describe_redis_connection(redis_url: str) -> str:
    """One-line description for logs: in-process fakeredis vs network Redis (e.g. Docker) reachability."""
    if classify_redis_url(redis_url) == "fakeredis":
        return "mode=fakeredis (in-process RAM, no Docker / no redis-server required)"
    reachable = tcp_redis_ping_ok(redis_url)
    if reachable:
        return "mode=tcp (network Redis reachable — Docker, compose, or native install)"
    return (
        "mode=tcp (network Redis configured but not reachable — "
        "start Docker Desktop / `docker compose up -d` or fix REDIS_URL)"
    )


def redis_url_source() -> Literal["environment", "config"]:
    """Whether ``effective_redis_url`` would take the value from env or from YAML."""
    return "environment" if get_redis_url_from_environment() is not None else "config"
