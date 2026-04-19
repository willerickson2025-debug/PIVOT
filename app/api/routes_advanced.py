"""
routes_advanced.py
==================
NBA.com advanced stats endpoints via nba_api.

All routes mount under /api/v1/advanced/* -- never overlap with /api/v1/*.

Player ID resolution
--------------------
The {player_id} path parameter is ambiguous at call time. Each route runs it
through _resolve_player_id(), which:

  1. Checks the nba_api static player registry (find_player_by_id). If found,
     the input is already a valid stats.nba.com ID -- use it directly.
  2. Falls back to id_bridge.bdl_to_nba(), treating the input as a BDL ID.
  3. Returns 404 if both lookups fail, naming which were tried.

This means both /advanced/203999/shot-chart (NBA ID for Jokic) and
/advanced/246/shot-chart (BDL ID for Jokic) resolve to the same player.

Season parameter
----------------
All data routes accept ?season= as either a 4-digit integer or "YYYY-YY" string.
  2025  -> "2025-26"  (treated as the start year of the season)
  "2025-26" -> "2025-26"  (pass through)
  "2024-25" -> "2024-25"  (pass through)
Normalisation happens via nba_client.norm_season() before the cache key is
computed and before any nba_api call is made.

Rate limit: 60/minute per IP (cached data-only endpoints).
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

from app.core.limiter import limiter
from app.services import id_bridge, nba_client

logger = logging.getLogger(__name__)

advanced_router = APIRouter()

_ADV_LIMIT = "60/minute"
_DEFAULT_SEASON = "2025"


# ── Player ID resolution ──────────────────────────────────────────────────────

def _is_nba_static_id(player_id: int) -> bool:
    """
    Return True if player_id is present in the nba_api bundled player registry.
    This is a sync dict lookup with no network call.
    """
    try:
        from nba_api.stats.static import players as nba_static  # type: ignore
        return nba_static.find_player_by_id(player_id) is not None
    except Exception:
        return False


async def _resolve_player_id(player_id: int) -> int:
    """
    Resolve player_id to a verified stats.nba.com player ID.

    Resolution order:
      1. nba_api static registry (input is already an NBA ID).
      2. id_bridge.bdl_to_nba() (input is a BDL ID).

    Raises HTTPException(404) if both fail, with a message naming both
    lookup strategies that were attempted.
    """
    if _is_nba_static_id(player_id):
        return player_id

    nba_id = await id_bridge.bdl_to_nba(player_id)
    if nba_id is not None:
        return nba_id

    raise HTTPException(
        status_code=404,
        detail=(
            f"Player ID {player_id} could not be resolved. "
            "Tried: nba_api static player registry (as NBA ID) "
            "and id_bridge (as BDL ID). "
            "Verify the player ID source."
        ),
    )


# ── Health ────────────────────────────────────────────────────────────────────

@advanced_router.get("/health")
async def advanced_health():
    """
    Probe stats.nba.com using CommonPlayerInfo(203999).

    Returns {ok, reachable, railway_ip_blocked, http_status, error}.

    ok=True means a valid response was received.
    railway_ip_blocked=True means HTTP 403 was returned -- all data endpoints
    will fail until the outbound IP changes or a proxy is added.
    """
    try:
        return await nba_client.probe_nba_api()
    except Exception as exc:
        return {
            "ok": False,
            "reachable": False,
            "railway_ip_blocked": False,
            "http_status": None,
            "error": str(exc),
        }


# ── Shot chart ────────────────────────────────────────────────────────────────

@advanced_router.get("/{player_id}/shot-chart")
@limiter.limit(_ADV_LIMIT)
async def shot_chart(
    request: Request,
    player_id: int,
    season: str = Query(
        _DEFAULT_SEASON,
        description="Season as start year (2025 -> 2025-26) or NBA format (2025-26).",
    ),
):
    """
    Shot chart for a player.

    Returns {shots, count, nba_id, season}.
    shots: [{x, y, made, zone, action_type, distance}]
    x in [-250, 250], y in [-47.5, 422.5] -- NBA coordinate system, tenths of a foot.
    """
    nba_id = await _resolve_player_id(player_id)
    season_str = nba_client.norm_season(season)
    try:
        shots = await nba_client.get_shot_chart(nba_id, season_str)
        if shots is None:
            raise HTTPException(
                status_code=502,
                detail="stats.nba.com shot chart unavailable after retries.",
            )
        return {"shots": shots, "count": len(shots), "nba_id": nba_id, "season": season_str}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("shot_chart player=%d nba_id=%d season=%s: %s", player_id, nba_id, season_str, exc)
        raise HTTPException(status_code=502, detail=f"Shot chart fetch failed: {exc}")


# ── Hustle stats ──────────────────────────────────────────────────────────────

@advanced_router.get("/{player_id}/hustle")
@limiter.limit(_ADV_LIMIT)
async def hustle_stats(
    request: Request,
    player_id: int,
    season: str = Query(
        _DEFAULT_SEASON,
        description="Season as start year (2025 -> 2025-26) or NBA format (2025-26).",
    ),
):
    """
    Hustle stats: contested shots, deflections, charges, screen assists, box outs.

    Returns {hustle, nba_id, season}.
    hustle: {contested_2pt, contested_3pt, deflections, charges_drawn,
             screen_assists, box_outs, loose_balls_recovered, games_played}
    """
    nba_id = await _resolve_player_id(player_id)
    season_str = nba_client.norm_season(season)
    try:
        hustle = await nba_client.get_hustle(nba_id, season_str)
        if hustle is None:
            raise HTTPException(
                status_code=502,
                detail="stats.nba.com hustle data unavailable after retries.",
            )
        return {"hustle": hustle, "nba_id": nba_id, "season": season_str}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("hustle player=%d nba_id=%d season=%s: %s", player_id, nba_id, season_str, exc)
        raise HTTPException(status_code=502, detail=f"Hustle stats fetch failed: {exc}")


# ── Shot types ────────────────────────────────────────────────────────────────

@advanced_router.get("/{player_id}/shot-types")
@limiter.limit(_ADV_LIMIT)
async def shot_types(
    request: Request,
    player_id: int,
    season: str = Query(
        _DEFAULT_SEASON,
        description="Season as start year (2025 -> 2025-26) or NBA format (2025-26).",
    ),
):
    """
    Shot breakdown by type (pull-up, catch-and-shoot, layup, etc.).

    Returns {shot_types, nba_id, season}.
    shot_types: [{shot_type, fga, fgm, fg_pct, freq}] ordered by fga desc.
    """
    nba_id = await _resolve_player_id(player_id)
    season_str = nba_client.norm_season(season)
    try:
        types = await nba_client.get_shot_types(nba_id, season_str)
        if types is None:
            raise HTTPException(
                status_code=502,
                detail="stats.nba.com shot type data unavailable after retries.",
            )
        return {"shot_types": types, "nba_id": nba_id, "season": season_str}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("shot_types player=%d nba_id=%d season=%s: %s", player_id, nba_id, season_str, exc)
        raise HTTPException(status_code=502, detail=f"Shot types fetch failed: {exc}")


# ── Tracking ──────────────────────────────────────────────────────────────────

@advanced_router.get("/{player_id}/tracking")
@limiter.limit(_ADV_LIMIT)
async def tracking(
    request: Request,
    player_id: int,
    season: str = Query(
        _DEFAULT_SEASON,
        description="Season as start year (2025 -> 2025-26) or NBA format (2025-26).",
    ),
):
    """
    Player movement tracking: speed, distance, touches, possession time.

    Returns {tracking, nba_id, season}.
    tracking: {avg_speed, dist_feet, touches, front_ct_touches, time_of_poss,
               avg_drib_per_touch, avg_sec_per_touch, elbow_touches, post_touches}
    Keys are absent when the underlying data source returned nothing for that field.
    """
    nba_id = await _resolve_player_id(player_id)
    season_str = nba_client.norm_season(season)
    try:
        data = await nba_client.get_tracking(nba_id, season_str)
        if data is None:
            raise HTTPException(
                status_code=502,
                detail="stats.nba.com tracking data unavailable after retries.",
            )
        return {"tracking": data, "nba_id": nba_id, "season": season_str}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("tracking player=%d nba_id=%d season=%s: %s", player_id, nba_id, season_str, exc)
        raise HTTPException(status_code=502, detail=f"Tracking fetch failed: {exc}")
