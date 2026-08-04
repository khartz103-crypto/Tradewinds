"""Shared async Redis client.

A single module-level client is reused across the app (scheduler state,
future caching). The client is created lazily so that importing modules
never requires Redis to be reachable.
"""

from __future__ import annotations

from redis.asyncio import Redis

from app.config import settings

_redis: Redis | None = None


def get_redis() -> Redis:
    """Return the shared async Redis client (created on first use)."""
    global _redis
    if _redis is None:
        _redis = Redis.from_url(settings.redis_url, decode_responses=True)
    return _redis


async def close_redis() -> None:
    """Close the shared client (used on app shutdown)."""
    global _redis
    if _redis is not None:
        try:
            await _redis.aclose()
        finally:
            _redis = None
