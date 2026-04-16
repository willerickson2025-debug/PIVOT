from __future__ import annotations

import datetime
import logging
import time
from zoneinfo import ZoneInfo
from typing import Any

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
    """Return a short TTL during live NBA game windows, a longer one otherwise.

    ``active_ttl``  — seconds to cache during the game window (default 600 / 10 min).
    ``default_ttl`` — seconds to cache outside the game window (default 3600 / 60 min).

    Game window heuristic: 7:00 pm – 12:30 am Eastern, October through June.
    The window is intentionally generous so that tip-off variance and overtime
    games are covered.  No external API call is made — this is a time-of-day
    check only.
    """
    now = datetime.datetime.now(_ET)
    hour = now.hour + now.minute / 60.0   # fractional hour in ET

    in_season = now.month in _SEASON_MONTHS
    in_window = (hour >= _WINDOW_START_H) or (hour < _WINDOW_END_H)

    if in_season and in_window:
        logger.debug("get_cache_ttl: active window → ttl=%ds", active_ttl)
        return active_ttl

    return default_ttl

# ---------------------------------------------------------------------------
# TTL Cache
# ---------------------------------------------------------------------------

class TTLCache:
    """
    Simple in-memory key/value store with per-entry TTL expiry.

    Thread-safe for async use within a single process. Not persistent across
    restarts — entries are rebuilt by the agent on each deploy/boot.
    """

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
