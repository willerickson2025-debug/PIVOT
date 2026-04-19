"""
nba_client.py
=============
Data access layer for stats.nba.com via the nba_api library.

Rules
-----
- Never touches BallDontLie. Purely additive.
- All network calls run in a thread executor (nba_api is sync).
- 24-hour disk cache at /tmp/nba_cache keyed by (endpoint, normalized params).
- 3 retries with exponential backoff (0.5s, 1s, 2s) on any exception.
- Returns plain dicts/lists -- no Pydantic models, no ORM.
- Callers must always handle None returns (cache miss + all retries failed).

Season format
-------------
All public functions accept season as either int (2025) or str ("2024-25" or
"2025-26"). Call norm_season() at every entry point before use so the cache
key and nba_api call always receive a normalized "YYYY-YY" string.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional, Union

import diskcache

logger = logging.getLogger(__name__)

# ── Cache ─────────────────────────────────────────────────────────────────────

_CACHE_DIR = "/tmp/nba_cache"
_CACHE_TTL = 86_400  # 24 h in seconds
_cache = diskcache.Cache(_CACHE_DIR)

# ── Thread executor for sync nba_api calls ────────────────────────────────────

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="nba_client")

# ── Header spoofing (stats.nba.com requires these) ───────────────────────────

_NBA_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Host": "stats.nba.com",
    "Origin": "https://www.nba.com",
    "Referer": "https://www.nba.com/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
}

_TIMEOUT = 15  # seconds per nba_api call


def _apply_headers() -> None:
    """Patch nba_api default headers so all endpoints use them."""
    try:
        from nba_api.library.http import NBAStatsHTTP  # type: ignore
        NBAStatsHTTP.HEADERS.update(_NBA_HEADERS)
        NBAStatsHTTP.TIMEOUT = _TIMEOUT
    except Exception:
        pass  # nba_api version differences -- best effort


_apply_headers()


# ── Season normalisation ──────────────────────────────────────────────────────

def norm_season(s: Union[int, str]) -> str:
    """
    Convert any season input to the nba_api "YYYY-YY" string format.

    Accepted inputs:
      2025  (int)   -> "2025-26"
      "2025"        -> "2025-26"
      "2025-26"     -> "2025-26"  (pass through)
      "2024-25"     -> "2024-25"  (pass through)
    """
    t = str(s).strip()
    if "-" in t:
        return t
    year = int(float(t))
    return f"{year}-{str(year + 1)[-2:]}"


# ── Low-level helpers ─────────────────────────────────────────────────────────

def _cache_key(tag: str, **kwargs: Any) -> str:
    payload = json.dumps({"tag": tag, **kwargs}, sort_keys=True)
    return hashlib.md5(payload.encode()).hexdigest()


async def _run_sync(fn, *args, **kwargs):
    """Run a synchronous callable in the thread executor."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, lambda: fn(*args, **kwargs))


def _retry_sync(fn, *args, retries: int = 3, backoff: float = 0.5, **kwargs):
    """Call fn(*args, **kwargs) with simple retry/backoff. Returns None on full failure."""
    last_exc = None
    for attempt in range(retries):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            wait = backoff * (2 ** attempt)
            logger.warning(
                "nba_client retry %d/%d for %s: %s",
                attempt + 1, retries, getattr(fn, "__name__", repr(fn)), exc,
            )
            time.sleep(wait)
    logger.error(
        "nba_client: all retries exhausted for %s: %s",
        getattr(fn, "__name__", repr(fn)), last_exc,
    )
    return None


# ── Public async API ──────────────────────────────────────────────────────────

async def probe_nba_api() -> dict:
    """
    Health probe for stats.nba.com using CommonPlayerInfo (Nikola Jokic, 203999).

    Returns:
      {ok, reachable, railway_ip_blocked, http_status, error}

    ok=True means stats.nba.com returned valid data.
    railway_ip_blocked=True means HTTP 403 was returned, indicating the
    outbound IP is blocked by stats.nba.com (common on cloud providers).
    """
    import re

    def _probe():
        try:
            from nba_api.stats.endpoints.commonplayerinfo import CommonPlayerInfo  # type: ignore
            cpi = CommonPlayerInfo(player_id=203999, timeout=10)
            frames = cpi.get_data_frames()
            if frames and not frames[0].empty:
                return 200, None
            return 200, "empty response"
        except Exception as exc:
            msg = str(exc)
            # nba_api InvalidResponse includes "Status Code: NNN" in the message
            m = re.search(r"[Ss]tatus[_ ]?[Cc]ode:?\s*(\d+)", msg)
            if m:
                return int(m.group(1)), msg
            # requests HTTPError carries a response attribute
            resp = getattr(exc, "response", None)
            if resp is not None:
                status = getattr(resp, "status_code", None)
                if status:
                    return int(status), msg
            return None, msg

    status, err = await _run_sync(_probe)
    ok = status == 200 and err is None
    blocked = status == 403
    reachable = status is not None and status < 400
    return {
        "ok": ok,
        "reachable": reachable,
        "railway_ip_blocked": blocked,
        "http_status": status,
        "error": err,
    }


async def get_shot_chart(
    nba_player_id: int,
    season: Union[int, str] = "2025-26",
) -> Optional[list[dict]]:
    """
    Shot chart for a player.

    Returns list of dicts:
      {x, y, made, zone, action_type, distance}

    NBA coordinate system: origin at basket,
      x in [-250, 250], y in [-47.5, 422.5]
      (units are tenths of a foot).
    """
    season_str = norm_season(season)
    key = _cache_key("shot_chart", player_id=nba_player_id, season=season_str)
    if key in _cache:
        return _cache[key]

    def _fetch():
        from nba_api.stats.endpoints import ShotChartDetail  # type: ignore
        sc = ShotChartDetail(
            player_id=nba_player_id,
            team_id=0,
            season_nullable=season_str,
            context_measure_simple="FGA",
            timeout=_TIMEOUT,
        )
        df = sc.get_data_frames()[0]
        return [
            {
                "x": int(row["LOC_X"]),
                "y": int(row["LOC_Y"]),
                "made": bool(row["SHOT_MADE_FLAG"]),
                "zone": str(row.get("SHOT_ZONE_BASIC", "")),
                "action_type": str(row.get("ACTION_TYPE", "")),
                "distance": int(row.get("SHOT_DISTANCE", 0)),
            }
            for _, row in df.iterrows()
        ]

    result = await _run_sync(_retry_sync, _fetch)
    if result is not None:
        _cache.set(key, result, expire=_CACHE_TTL)
    return result


async def get_hustle(
    nba_player_id: int,
    season: Union[int, str] = "2025-26",
) -> Optional[dict]:
    """
    Hustle stats for a player.

    Returns dict:
      {contested_2pt, contested_3pt, deflections, charges_drawn,
       screen_assists, box_outs, loose_balls_recovered, games_played}
    """
    season_str = norm_season(season)
    key = _cache_key("hustle", player_id=nba_player_id, season=season_str)
    if key in _cache:
        return _cache[key]

    def _fetch():
        from nba_api.stats.endpoints import LeagueHustleStatsPlayer  # type: ignore
        hustle = LeagueHustleStatsPlayer(season=season_str, timeout=_TIMEOUT)
        df = hustle.get_data_frames()[0]
        row = df[df["PLAYER_ID"] == nba_player_id]
        if row.empty:
            return None
        r = row.iloc[0]
        return {
            "contested_2pt": _safe_int(r, "CONTESTED_SHOTS_2PT"),
            "contested_3pt": _safe_int(r, "CONTESTED_SHOTS_3PT"),
            "deflections": _safe_int(r, "DEFLECTIONS"),
            "charges_drawn": _safe_int(r, "CHARGES_DRAWN"),
            "screen_assists": _safe_int(r, "SCREEN_ASSISTS"),
            "box_outs": _safe_int(r, "BOX_OUTS"),
            "loose_balls_recovered": _safe_int(r, "LOOSE_BALLS_RECOVERED"),
            "games_played": _safe_int(r, "GP"),
        }

    result = await _run_sync(_retry_sync, _fetch)
    if result is not None:
        _cache.set(key, result, expire=_CACHE_TTL)
    return result


async def get_shot_types(
    nba_player_id: int,
    season: Union[int, str] = "2025-26",
) -> Optional[list[dict]]:
    """
    Shot breakdown by type (pull-up, catch-and-shoot, layup, etc.).

    Returns list (ordered by fga desc):
      [{shot_type, fga, fgm, fg_pct, freq}]
    """
    season_str = norm_season(season)
    key = _cache_key("shot_types", player_id=nba_player_id, season=season_str)
    if key in _cache:
        return _cache[key]

    def _fetch():
        from nba_api.stats.endpoints import PlayerDashPtShots  # type: ignore
        pt = PlayerDashPtShots(
            player_id=nba_player_id,
            team_id=0,
            season=season_str,
            per_mode_simple="PerGame",
            timeout=_TIMEOUT,
        )
        df = pt.get_data_frames()[0]
        rows = []
        for _, row in df.iterrows():
            shot_type = str(row.get("SHOT_TYPE", "")).strip()
            if not shot_type:
                continue
            rows.append({
                "shot_type": shot_type,
                "fga": _safe_float(row, "FGA"),
                "fgm": _safe_float(row, "FGM"),
                "fg_pct": _safe_float(row, "FG_PCT"),
                "freq": _safe_float(row, "FGA_FREQUENCY"),
            })
        rows.sort(key=lambda r: r["fga"] or 0, reverse=True)
        return rows[:8]

    result = await _run_sync(_retry_sync, _fetch)
    if result is not None:
        _cache.set(key, result, expire=_CACHE_TTL)
    return result


async def get_tracking(
    nba_player_id: int,
    season: Union[int, str] = "2025-26",
) -> Optional[dict]:
    """
    Player movement tracking: speed, distance, touches, possession time.

    Returns dict (keys present only when data available):
      {avg_speed, dist_feet, touches, front_ct_touches, time_of_poss,
       avg_drib_per_touch, avg_sec_per_touch, elbow_touches, post_touches}
    """
    season_str = norm_season(season)
    key = _cache_key("tracking", player_id=nba_player_id, season=season_str)
    if key in _cache:
        return _cache[key]

    def _fetch():
        result: dict = {}

        # Speed / Distance
        try:
            from nba_api.stats.endpoints import SpeedDistance  # type: ignore
            sd = SpeedDistance(
                player_id=nba_player_id,
                per_mode_simple="PerGame",
                season=season_str,
                timeout=_TIMEOUT,
            )
            df = sd.get_data_frames()[0]
            if not df.empty:
                r = df.iloc[0]
                result["avg_speed"] = _safe_float(r, "AVG_SPEED")
                result["dist_feet"] = _safe_float(r, "DIST_FEET")
        except Exception as exc:
            logger.debug("tracking SpeedDistance failed: %s", exc)

        # Touch / Possession
        try:
            from nba_api.stats.endpoints import PlayerDashPtPass  # type: ignore
            pp = PlayerDashPtPass(
                player_id=nba_player_id,
                per_mode_simple="PerGame",
                season=season_str,
                timeout=_TIMEOUT,
            )
            dfs = pp.get_data_frames()
            if dfs and not dfs[0].empty:
                r = dfs[0].iloc[0]
                result["touches"] = _safe_float(r, "TOUCHES")
                result["front_ct_touches"] = _safe_float(r, "FRONT_CT_TOUCHES")
                result["time_of_poss"] = _safe_float(r, "TIME_OF_POSS")
                result["avg_drib_per_touch"] = _safe_float(r, "AVG_DRIB_PER_TOUCH")
                result["avg_sec_per_touch"] = _safe_float(r, "AVG_SEC_PER_TOUCH")
                result["elbow_touches"] = _safe_float(r, "ELBOW_TOUCHES")
                result["post_touches"] = _safe_float(r, "POST_TOUCHES")
        except Exception as exc:
            logger.debug("tracking PlayerDashPtPass failed: %s", exc)

        return result if result else None

    result = await _run_sync(_retry_sync, _fetch)
    if result is not None:
        _cache.set(key, result, expire=_CACHE_TTL)
    return result


# ── Private helpers ───────────────────────────────────────────────────────────

def _safe_int(row, col: str, default: int = 0) -> int:
    try:
        v = row[col]
        return int(v) if v is not None else default
    except (KeyError, TypeError, ValueError):
        return default


def _safe_float(row, col: str, default: Optional[float] = None) -> Optional[float]:
    try:
        v = row[col]
        return float(v) if v is not None else default
    except (KeyError, TypeError, ValueError):
        return default
