"""
session.py
==========
Lightweight in-memory session store for PIVOT war-room continuity.

Each session accumulates a rolling log of analytical events (compares, trades,
scout notes, coach calls, predictions).  Before each Claude call the session
can inject a short context block (~200 tokens) that gives Claude continuity
without bloating the prompt.

Sessions are keyed by a client-supplied UUID (X-Pivot-Session header) and
expire after SESSION_TTL seconds of inactivity.  No persistence — this is
intentionally ephemeral; it spans a single war-room session, not days.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SESSION_TTL: int   = 4 * 3600   # sessions expire after 4 h of inactivity
_MAX_EVENTS: int   = 30          # keep the last 30 events per session
_CONTEXT_CHARS: int = 900        # ≈200 tokens; hard cap on context block size

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class SessionEvent:
    """One analytical action recorded during a session."""
    type: str                      # compare | trade | scout | coach | predict | roster
    summary: str                   # one-line human-readable description (≤120 chars)
    entities: list[str]            # player / team names involved (for frequency count)
    concern: Optional[str] = None  # key concern or limitation surfaced by the verdict
    ts: float = field(default_factory=time.time)


@dataclass
class SessionLog:
    events: list[SessionEvent]        = field(default_factory=list)
    entity_counts: dict[str, int]     = field(default_factory=dict)
    created: float                    = field(default_factory=time.time)
    last_updated: float               = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------

_SESSIONS: dict[str, SessionLog] = {}


def _prune() -> None:
    """Remove sessions that have been idle longer than SESSION_TTL."""
    cutoff = time.time() - SESSION_TTL
    stale = [k for k, v in _SESSIONS.items() if v.last_updated < cutoff]
    for k in stale:
        del _SESSIONS[k]
    if stale:
        logger.debug("session: pruned %d stale sessions", len(stale))


def get_or_create(session_id: str) -> SessionLog:
    _prune()
    if session_id not in _SESSIONS:
        _SESSIONS[session_id] = SessionLog()
        logger.debug("session: new session %s", session_id[:8])
    return _SESSIONS[session_id]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def record(session_id: Optional[str], event: SessionEvent) -> None:
    """Append an event to a session log.  Silently ignores empty session IDs."""
    if not session_id:
        return
    log = get_or_create(session_id)
    log.events.append(event)
    if len(log.events) > _MAX_EVENTS:
        log.events = log.events[-_MAX_EVENTS:]
    for entity in event.entities:
        key = entity.strip().lower()
        if key:
            log.entity_counts[key] = log.entity_counts.get(key, 0) + 1
    log.last_updated = time.time()
    logger.debug("session: recorded %s event for %s", event.type, session_id[:8])


def build_context_block(session_id: Optional[str]) -> str:
    """
    Return a compact context string to prepend to Claude prompts.

    Format:
        EARLIER THIS SESSION (context only — current data below is authoritative):
          Recurring focus: Tatum ×3, Brown ×2
          [COMPARE] Tatum vs Durant → contender starter — Tatum edge (TS% gap)
          [TRADE] Celtics ↔ Lakers → Celtics win — flagged: luxury tax crossing
          ...

    Returns empty string when there are no events, so callers can safely
    concatenate without adding a blank line.
    """
    if not session_id:
        return ""
    log = _SESSIONS.get(session_id)
    if not log or not log.events:
        return ""

    lines: list[str] = [
        "EARLIER THIS SESSION (for continuity — the current data below is authoritative):"
    ]

    # Recurring entities (seen more than once)
    repeated = {k: v for k, v in log.entity_counts.items() if v > 1}
    if repeated:
        top = sorted(repeated.items(), key=lambda x: -x[1])[:5]
        parts = [f"{k.title()} ×{v}" for k, v in top]
        lines.append(f"  Recurring focus: {', '.join(parts)}")

    # Most recent events, newest first (up to 8 in context)
    recent = log.events[-8:]
    for ev in recent:
        line = f"  [{ev.type.upper()}] {ev.summary}"
        if ev.concern:
            line += f" — flagged: {ev.concern}"
        lines.append(line)

    block = "\n".join(lines)

    # Hard cap: truncate oldest event lines until we fit
    if len(block) > _CONTEXT_CHARS:
        header_end = 2 if repeated else 1   # header line(s) to always keep
        kept_header = lines[:header_end]
        event_lines = lines[header_end:]
        while event_lines and len("\n".join(kept_header + event_lines)) > _CONTEXT_CHARS:
            event_lines.pop(0)              # drop the oldest event
        block = "\n".join(kept_header + event_lines)

    return block + "\n"
