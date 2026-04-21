from __future__ import annotations

import datetime
import json
import logging
import os
import time
from typing import Any, Awaitable, Callable, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dynamic cache TTL — shrinks during NBA game windows so post-game stats
# are reflected quickly without hammering upstream APIs at all times.
# ---------------------------------------------------------------------------

_ET = ZoneInfo("America/New_York")

_ACTIVE_TTL:  int = 600    # 10 min — during game windows
_DEFAULT_TTL: int = 3600   # 60 min — outside game windows

# NBA season spans Oct (10) → Jun (6); off-season is Jul–Sep.
_SEASON_MONTHS: frozenset[int] = frozenset({10, 11, 12, 1, 2, 3, 4, 5, 6})

# Game window: 7:00 pm ET start → 12:30 am ET end (handles late West Coast tip-offs).
_WINDOW_START_H: float = 19.0   # 7:00 pm
_WINDOW_END_H:   float = 0.5    # 12:30 am (next calendar day)


def get_cache_ttl(
    active_ttl: int = _ACTIVE_TTL,
    default_ttl: int = _DEFAULT_TTL,
) -> int:
    """Return a short TTL during live NBA game windows, a longer one otherwise."""
    now = datetime.datetime.now(_ET)
    hour = now.hour + now.minute / 60.0

    in_season = now.month in _SEASON_MONTHS
    in_window = (hour >= _WINDOW_START_H) or (hour < _WINDOW_END_H)

    if in_season and in_window:
        logger.debug("get_cache_ttl: active window → ttl=%ds", active_ttl)
        return active_ttl

    return default_ttl


# ---------------------------------------------------------------------------
# In-memory TTL Cache (L1 — process-scoped, survives within a single deploy)
# ---------------------------------------------------------------------------

class TTLCache:
    """Simple in-memory key/value store with per-entry TTL expiry."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[Any, float]] = {}

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if time.monotonic() > expires_at:
            del self._store[key]
            logger.debug("Cache miss (expired) | key=%s", key)
            return None
        logger.debug("Cache hit | key=%s", key)
        return value

    def set(self, key: str, value: Any, ttl: float) -> None:
        self._store[key] = (value, time.monotonic() + ttl)
        logger.debug("Cache set | key=%s ttl=%.0fs", key, ttl)

    def delete(self, key: str) -> None:
        self._store.pop(key, None)

    def clear(self) -> None:
        self._store.clear()
        logger.info("Cache cleared")

    def stats(self) -> dict[str, int]:
        now = time.monotonic()
        live = sum(1 for _, exp in self._store.values() if exp > now)
        return {"total_keys": len(self._store), "live_keys": live}


# Module-level singleton — imported by analysis_service and agent_service.
analysis_cache = TTLCache()


# ---------------------------------------------------------------------------
# Redis async cache (L2 — shared across workers, survives redeploys)
# ---------------------------------------------------------------------------

try:
    from redis.asyncio import Redis as _AsyncRedis
    _REDIS_AVAILABLE = True
except ImportError:
    _REDIS_AVAILABLE = False
    _AsyncRedis = None  # type: ignore[assignment, misc]

_redis: Optional[Any] = None


async def get_redis() -> Optional[Any]:
    """Return a live Redis client, or None if REDIS_URL is unset or Redis unavailable."""
    global _redis
    if not _REDIS_AVAILABLE:
        return None
    if _redis is None:
        url = os.getenv("REDIS_URL")
        if not url:
            return None
        try:
            _redis = _AsyncRedis.from_url(
                url,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
        except Exception as exc:
            logger.warning("Redis init failed: %s", exc)
            return None
    return _redis


async def cache_get(key: str) -> Any:
    """Return the deserialized value for key, or None on miss / Redis unavailable."""
    r = await get_redis()
    if r is None:
        return None
    try:
        raw = await r.get(key)
        return json.loads(raw) if raw else None
    except Exception as exc:
        logger.debug("Redis cache_get error key=%s: %s", key, exc)
        return None


async def cache_set(key: str, value: Any, ttl_seconds: int) -> None:
    """Serialize value and store it in Redis with the given TTL. Silent on failure."""
    r = await get_redis()
    if r is None:
        return
    try:
        await r.set(key, json.dumps(value, default=str), ex=ttl_seconds)
    except Exception as exc:
        logger.debug("Redis cache_set error key=%s: %s", key, exc)


async def cached(key: str, ttl: int, loader: Callable[[], Awaitable[Any]]) -> Any:
    """
    Read-through cache helper.

    1. Check Redis for key.
    2. On hit: return deserialized value.
    3. On miss: call loader(), store result in Redis with ttl, return result.
    If Redis is unavailable, calls loader() every time (no-op degradation).
    """
    hit = await cache_get(key)
    if hit is not None:
        logger.debug("Redis hit | key=%s", key)
        return hit
    val = await loader()
    await cache_set(key, val, ttl)
    return val
