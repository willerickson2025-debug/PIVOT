from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

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
