"""
nba_service.py
==============
Data access layer for the BallDontLie NBA API.

Responsibilities
----------------
- All HTTP communication with the BallDontLie REST API
- Domain object hydration (raw dict to typed schema)
- Retry / timeout / error-propagation policy
- Box score aggregation and player-stat retrieval
- V2 advanced stats ingestion and season aggregation

This module is intentionally free of business logic. Analysis logic lives in
analysis_service.py; Claude integration lives in claude_service.py.

V2 Advanced Stats
-----------------
get_v2_advanced_stats() fetches per-game rows from the V2 endpoint
(https://api.balldontlie.io/nba/v2/stats/advanced). It handles cursor
pagination and 429 retry automatically.

aggregate_season_advanced(player_id, season) pulls all rows for one
player-season and collapses them into a single flat dict. Rate stats
(percentages, ratings) are averaged across games; counting stats
(deflections, touches, distance, etc.) are summed to season totals.
The returned dict also includes per-game averages (_pg suffix) for each
counting stat and carries every field present in the V2 response.

get_advanced_stats() is kept as a thin deprecation shim so existing callers
do not break while the migration to get_v2_advanced_stats proceeds.
"""

from __future__ import annotations

import asyncio
import hashlib as _hashlib
import json as _json
import logging
import time
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx

from app.core.cache import cache_get, cache_set
from app.core.config import get_settings
from app.core.http_client import GlobalHTTPClient
from app.core.season import get_current_season
from app.models.schemas import Game, Player, PlayerStats, Team

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Player Resolution Errors
# ---------------------------------------------------------------------------

class PlayerResolutionError(Exception):
    """Base class for player name resolution failures."""


class PlayerNotFoundError(PlayerResolutionError):
    """No player matched the given name in the requested scope."""


class AmbiguousPlayerError(PlayerResolutionError):
    """Multiple active players share the given name."""

    def __init__(self, message: str, candidates: list[dict]) -> None:
        super().__init__(message)
        self.candidates: list[dict] = candidates


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_PER_PAGE: int = 100
_REQUEST_TIMEOUT: float = 30.0          # seconds per attempt
_MAX_RETRIES: int = 3                   # total attempts before raising
_RETRY_BACKOFF_BASE: float = 0.5        # seconds; multiplied by attempt index
# Season is computed at call time -- see get_current_season() in app/core/season.py
_CENTRAL_TZ: str = "America/Chicago"

# V2 advanced stats endpoint (absolute URL -- bypasses base URL construction).
_BDL_V2_ADV_URL: str = "https://api.balldontlie.io/nba/v2/stats/advanced"

# NBA.com player ID lookup by full name for headshot CDN URLs.
# Only include verified IDs -- never add unverified entries.
_NBA_ID_BY_NAME: dict[str, int] = {
    # Only NBA.com player IDs that have been individually verified.
    # Do NOT add entries from memory -- a wrong ID shows the wrong player photo.
    # Verified via NBA.com stats pages:
    "LeBron James": 2544,
    "Stephen Curry": 201939,
    "Nikola Jokic": 203999,
    "Jayson Tatum": 1628369,
    "Kevin Durant": 201142,
    "Giannis Antetokounmpo": 203507,
    "Luka Doncic": 1629029,
    "Anthony Davis": 203076,
    "Shai Gilgeous-Alexander": 1628983,
    "OG Anunoby": 1628384,
    "Joel Embiid": 203954,
    "Kawhi Leonard": 202695,
    "Kyrie Irving": 202681,
    "James Harden": 201935,
    "Trae Young": 1629027,
    "Damian Lillard": 203081,
    "Devin Booker": 1626164,
    "Ja Morant": 1629630,
    "Zion Williamson": 1629627,
    "Jimmy Butler": 202710,
    "De'Aaron Fox": 1628368,
    "Jalen Brunson": 1628386,
    "Donovan Mitchell": 1628378,
    "Victor Wembanyama": 1641705,
    "Paolo Banchero": 1631094,
    "Tyrese Haliburton": 1630169,
    "Evan Mobley": 1630596,
    "Scottie Barnes": 1630567,
    "Anthony Edwards": 1630162,
    "LaMelo Ball": 1630163,
    "Cade Cunningham": 1630595,
    "Jaylen Brown": 1627759,
    "Bam Adebayo": 1628389,
    "Tyler Herro": 1629639,
    "Tyrese Maxey": 1630178,
    "Karl-Anthony Towns": 1626157,
    "Jalen Green": 1630224,
    "Draymond Green": 203110,
    "Klay Thompson": 202691,
    "Jamal Murray": 1627750,
    "Paul George": 202331,
    "Rudy Gobert": 203497,
    "Andrew Wiggins": 203952,
    "Darius Garland": 1629636,
    "Jarrett Allen": 1628991,
    "Lauri Markkanen": 1628374,
    "Franz Wagner": 1630532,
    "Josh Giddey": 1630581,
    "Alperen Sengun": 1631167,
    "Jalen Williams": 1631114,
    "Deandre Ayton": 1629028,
    "Mikal Bridges": 1628969,
    "Brandon Ingram": 1627742,
    "Zach LaVine": 203897,
    "DeMar DeRozan": 201942,
    "Nikola Vucevic": 202696,
    "Domantas Sabonis": 1627734,
}

# In-memory cache for aggregate_season_advanced results.
# Keyed by "player_id:season:postseason". Cleared on process restart.
_ADV_SEASON_CACHE: dict[str, dict[str, Any]] = {}
_ADV_CACHE_EXPIRY: dict[str, float] = {}      # unix timestamps; 30-min TTL
_ADV_CACHE_TTL: float = 1800.0                # 30 minutes in seconds


# ---------------------------------------------------------------------------
# Internal HTTP Layer
# ---------------------------------------------------------------------------

async def _fetch_data(
    endpoint: str,
    params: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    Execute an authenticated GET request against the BallDontLie API.

    Centralises:
    - Base URL and authentication header injection
    - Per-request timeout enforcement
    - Exponential-ish back-off retry on transient network/server errors
    - 429 rate-limit retry with Retry-After header support
    - Structured logging of every outbound request and its outcome
    - JSON decoding with a meaningful error on malformed payloads

    Parameters
    ----------
    endpoint:
        Path relative to the configured base URL (e.g. "/games") or an
        absolute URL for namespaced endpoints like the V2 advanced stats path.
    params:
        Optional query-string parameters forwarded verbatim to httpx.

    Returns
    -------
    dict
        Parsed JSON payload from the API response body.

    Raises
    ------
    httpx.HTTPStatusError
        Propagated after all retries are exhausted for non-429 4xx/5xx responses.
    httpx.RequestError
        Propagated after all retries are exhausted for connection failures.
    ValueError
        If the response body cannot be decoded as JSON.
    """
    settings = get_settings()
    clean_params = params or {}

    last_exc: Exception | None = None

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            logger.debug(
                "BallDontLie request | attempt=%d endpoint=%s params=%s",
                attempt,
                endpoint,
                clean_params,
            )

            client = GlobalHTTPClient.get_client()
            # Allow passing an absolute URL (useful for the /nba/v2 namespace)
            url = endpoint if endpoint.startswith("http") else settings.balldontlie_base_url + endpoint
            response = await client.get(
                url,
                headers={"Authorization": settings.balldontlie_api_key},
                params=clean_params,
                timeout=_REQUEST_TIMEOUT,
            )

            response.raise_for_status()

            try:
                payload: dict[str, Any] = response.json()
            except Exception as exc:
                raise ValueError(
                    f"BallDontLie returned non-JSON body for {endpoint}: "
                    f"{response.text[:200]}"
                ) from exc

            logger.debug(
                "BallDontLie response | endpoint=%s status=%d",
                endpoint,
                response.status_code,
            )
            return payload

        except (httpx.TransportError, httpx.TimeoutException) as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                backoff = _RETRY_BACKOFF_BASE * attempt
                logger.warning(
                    "BallDontLie transient error | attempt=%d endpoint=%s error=%s | "
                    "retrying in %.1fs",
                    attempt,
                    endpoint,
                    exc,
                    backoff,
                )
                await asyncio.sleep(backoff)
            else:
                logger.error(
                    "BallDontLie request failed after %d attempts | endpoint=%s error=%s",
                    _MAX_RETRIES,
                    endpoint,
                    exc,
                )

        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 429 and attempt < _MAX_RETRIES:
                # Rate limited -- back off and retry, honoring Retry-After when present.
                try:
                    header_val = float(exc.response.headers.get("Retry-After", 0))
                except (TypeError, ValueError):
                    header_val = 0.0
                backoff = max(header_val, _RETRY_BACKOFF_BASE * attempt * 2)
                last_exc = exc
                logger.warning(
                    "BallDontLie rate limited (429) | attempt=%d endpoint=%s | "
                    "retrying in %.1fs",
                    attempt,
                    endpoint,
                    backoff,
                )
                await asyncio.sleep(backoff)
            else:
                # Non-429 4xx and all 5xx are not retried further.
                logger.error(
                    "BallDontLie HTTP error | endpoint=%s status=%d body=%s",
                    endpoint,
                    status,
                    exc.response.text[:500],
                )
                raise

    # All retries exhausted for transient errors
    assert last_exc is not None
    raise last_exc


# ---------------------------------------------------------------------------
# Parsing Utilities
# ---------------------------------------------------------------------------

def _require(mapping: dict[str, Any], key: str, context: str = "") -> Any:
    """
    Return ``mapping[key]``, raising ``KeyError`` with a descriptive message
    if the key is absent. Used to surface schema mismatches early rather than
    producing silent ``None`` values downstream.
    """
    if key not in mapping:
        location = f" (in {context})" if context else ""
        raise KeyError(
            f"Expected key '{key}' missing from BallDontLie payload{location}. "
            f"Available keys: {list(mapping.keys())}"
        )
    return mapping[key]


def _parse_team(raw: dict[str, Any]) -> Team:
    """
    Hydrate a ``Team`` domain object from a raw BallDontLie team payload.

    The API occasionally returns partial team objects inside game payloads
    (e.g. missing ``full_name``). We prefer ``full_name`` but fall back to
    composing it from ``city`` + ``name`` if needed.
    """
    team_id: int = _require(raw, "id", "team")
    city: str = raw.get("city") or ""
    name: str = raw.get("name") or ""

    return Team(
        id=team_id,
        name=raw.get("full_name") or f"{city} {name}".strip() or name,
        abbreviation=raw.get("abbreviation") or "",
        city=city,
        conference=raw.get("conference") or "",
        division=raw.get("division") or "",
    )


def _parse_game(raw: dict[str, Any]) -> Game:
    """
    Hydrate a ``Game`` domain object from a raw BallDontLie game payload.

    Scores default to 0 rather than ``None`` so callers can do arithmetic
    without null-checks everywhere.
    """
    return Game(
        id=_require(raw, "id", "game"),
        date=raw.get("date") or "",
        status=raw.get("status") or "Unknown",
        period=raw.get("period"),
        time=raw.get("time"),
        home_team=_parse_team(_require(raw, "home_team", "game")),
        visitor_team=_parse_team(_require(raw, "visitor_team", "game")),
        home_team_score=int(raw.get("home_team_score") or 0),
        visitor_team_score=int(raw.get("visitor_team_score") or 0),
        postseason=bool(raw.get("postseason", False)),
    )


def _int_or_none(v: Any) -> int | None:
    """Return int if value is a non-zero integer-like, else None."""
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def _has_real_minutes(m: Any) -> bool:
    """Return True only when a player actually played (minutes > 0).

    BDL represents DNPs as None, '0', '0:00', or '00' -- all map to False.
    """
    if not m:
        return False
    try:
        return float(str(m).strip().split(':')[0]) > 0
    except (ValueError, IndexError):
        return False


def _parse_player(raw: dict[str, Any]) -> Player:
    """
    Hydrate a ``Player`` domain object from a raw BallDontLie player payload.

    The nested ``team`` object is optional -- players without a current team
    assignment (free agents, two-way contracts in flux) are handled gracefully.
    """
    team_raw: dict[str, Any] | None = raw.get("team")
    first = raw.get("first_name") or ""
    last = raw.get("last_name") or ""
    full_name = f"{first} {last}".strip()

    return Player(
        id=_require(raw, "id", "player"),
        first_name=first,
        last_name=last,
        position=raw.get("position") or None,
        team=_parse_team(team_raw) if team_raw else None,
        nba_id=raw.get("nba_player_id") or _NBA_ID_BY_NAME.get(full_name),
    )


def _parse_stat_line(s: dict[str, Any]) -> dict[str, Any]:
    """
    Convert a single raw stat entry from the BallDontLie ``/stats`` endpoint
    into a normalised, display-ready dict.

    All numeric fields default to 0 and all string fields to sensible
    placeholders so renderers never encounter ``None``.
    """
    player_raw: dict[str, Any] = s.get("player") or {}
    team_raw: dict[str, Any] = s.get("team") or {}

    first = player_raw.get("first_name") or ""
    last = player_raw.get("last_name") or ""
    full_name = f"{first} {last}".strip() or "Unknown"

    fgm = int(s.get("fgm") or 0)
    fga = int(s.get("fga") or 0)
    fg3m = int(s.get("fg3m") or 0)
    fg3a = int(s.get("fg3a") or 0)
    ftm = int(s.get("ftm") or 0)
    fta = int(s.get("fta") or 0)

    return {
        "player": full_name,
        "pos": player_raw.get("position") or "--",
        "min": s.get("min") or "0",
        "pts": int(s.get("pts") or 0),
        "reb": int(s.get("reb") or 0),
        "ast": int(s.get("ast") or 0),
        "stl": int(s.get("stl") or 0),
        "blk": int(s.get("blk") or 0),
        "fg": f"{fgm}-{fga}",
        "fg3": f"{fg3m}-{fg3a}",
        "ft": f"{ftm}-{fta}",
        "fg_pct": round(fgm / fga, 3) if fga > 0 else 0.0,
        "fg3_pct": round(fg3m / fg3a, 3) if fg3a > 0 else 0.0,
        "ft_pct": round(ftm / fta, 3) if fta > 0 else 0.0,
        "to": int(s.get("turnover") or 0),
        "pf": int(s.get("pf") or 0),
        "plus_minus": int(s.get("plus_minus") or 0),
        "team_id": team_raw.get("id"),
        "team_abbr": team_raw.get("abbreviation") or "",
    }


# ---------------------------------------------------------------------------
# Public Service Layer -- Queries
# ---------------------------------------------------------------------------

async def get_games_by_date(target_date: Optional[str] = None) -> list[Game]:
    """
    Fetch all NBA games scheduled for a specific calendar date.

    Parameters
    ----------
    target_date:
        ISO-8601 date string (``"YYYY-MM-DD"``). Defaults to the current date
        in US Central Time when omitted, which aligns with the NBA schedule
        timezone used by BallDontLie.

    Returns
    -------
    list[Game]
        All games found for the given date, or an empty list when there are
        none scheduled.
    """
    query_date = target_date or datetime.now(ZoneInfo(_CENTRAL_TZ)).strftime("%Y-%m-%d")
    ck = f"games:{query_date}"

    cached_raw = await cache_get(ck)
    if cached_raw is not None:
        logger.debug("Redis hit | games:%s", query_date)
        return [_parse_game(g) for g in cached_raw]

    logger.info("Fetching games for date=%s", query_date)

    payload = await _fetch_data(
        "/games",
        params={"dates[]": query_date, "per_page": _DEFAULT_PER_PAGE},
    )

    raw = payload.get("data") or []
    games = [_parse_game(g) for g in raw]
    logger.info("Found %d game(s) for %s", len(games), query_date)
    await cache_set(ck, raw, 120)
    return games


async def get_team_by_id(team_id: int) -> Team:
    """
    Retrieve a single NBA team by its BallDontLie team ID.

    Parameters
    ----------
    team_id:
        BallDontLie internal team identifier.

    Returns
    -------
    Team
        Hydrated Team domain object.
    """
    logger.debug("Fetching team id=%d", team_id)
    payload = await _fetch_data(f"/teams/{team_id}")
    return _parse_team(payload.get("data") or {})


async def get_all_teams() -> list[Team]:
    """
    Retrieve every NBA team from BallDontLie.

    Returns
    -------
    list[Team]
        All 30 franchises (and any G-League entries if present).
    """
    ck = "teams:all"
    cached_raw = await cache_get(ck)
    if cached_raw is not None:
        logger.debug("Redis hit | teams:all")
        return [_parse_team(t) for t in cached_raw]

    logger.debug("Fetching all teams")
    payload = await _fetch_data("/teams", params={"per_page": _DEFAULT_PER_PAGE})
    raw = payload.get("data") or []
    teams = [_parse_team(t) for t in raw]
    logger.debug("Received %d teams", len(teams))
    await cache_set(ck, raw, 86400)
    return teams


async def get_player_by_id(player_id: int) -> Player:
    """
    Retrieve a single player by their BallDontLie player ID.

    Parameters
    ----------
    player_id:
        BallDontLie internal player identifier, as returned by the
        ``/players`` search endpoint.

    Returns
    -------
    Player
        Hydrated Player domain object.

    Raises
    ------
    KeyError
        If the API response is missing expected fields.
    httpx.HTTPStatusError
        If the player ID does not exist (404) or another HTTP error occurs.
    """
    logger.debug("Fetching player by id=%d", player_id)
    payload = await _fetch_data(f"/players/{player_id}")
    return _parse_player(payload.get("data") or {})


async def search_players(
    name: str,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    include_inactive: bool = False,
) -> list[Player]:
    """
    Search NBA players by name.

    Parameters
    ----------
    name:
        Full or partial player name -- used as the ``search=`` fallback and
        for logging context.
    first_name:
        When provided (together with ``last_name``), passed directly to
        BallDontLie's ``first_name=`` parameter, bypassing the fuzzy
        ``search=`` index entirely.  Combine with ``last_name`` for the most
        precise results (e.g. first_name="Stephen", last_name="Curry").
    last_name:
        When provided alone, passed to BallDontLie's ``last_name=``
        parameter.  This avoids the pagination issue where a common last name
        like "James" fills page 1 with wrong players before the intended
        result appears.
    include_inactive:
        When False (default), hits ``/players/active`` so retired players are
        never returned.  Set to True to search the full historical player
        pool via ``/players``.

    Search priority
    ---------------
    1. Both first_name + last_name: ``?first_name=...&last_name=...``
    2. last_name only             : ``?last_name=...``
    3. first_name only            : ``?first_name=...``
    4. Neither                    : ``?search=name``  (fuzzy fallback)

    Returns
    -------
    list[Player]
        Matching players, ordered by API relevance. Empty list on no match.
    """
    endpoint = "/players" if include_inactive else "/players/active"

    if first_name and last_name:
        params: dict[str, Any] = {"first_name": first_name.strip(), "last_name": last_name.strip(), "per_page": 50}
        mode = f"first_name={first_name!r} last_name={last_name!r}"
    elif last_name:
        params = {"last_name": last_name.strip(), "per_page": 50}
        mode = f"last_name={last_name!r}"
    elif first_name:
        params = {"first_name": first_name.strip(), "per_page": 50}
        mode = f"first_name={first_name!r}"
    else:
        params = {"search": name.strip(), "per_page": 50}
        mode = f"search={name!r}"

    logger.debug("Searching players | endpoint=%s %s", endpoint, mode)
    payload = await _fetch_data(endpoint, params=params)
    players = [_parse_player(p) for p in payload.get("data") or []]
    logger.debug("Player search (%s %s) returned %d result(s)", endpoint, mode, len(players))
    return players


async def resolve_player_exact(name: str, active_only: bool = True) -> Player:
    """
    Resolve a full player name to exactly one Player object.

    Hits ``/players/active`` (or ``/players`` when ``active_only=False``) by
    last name, then filters to case-insensitive full-name equality.

    Parameters
    ----------
    name:
        Full player name, e.g. "Nikola Jokic".  Single-token names are
        treated as last names.
    active_only:
        When True (default), only currently rostered players are searched.
        When False, the historical player pool is included.

    Returns
    -------
    Player
        The single matching player.

    Raises
    ------
    PlayerNotFoundError
        Zero players matched the name in the requested scope.
    AmbiguousPlayerError
        Two or more active players share the exact same full name.
    """
    clean = name.strip()
    tokens = clean.split(None, 1)
    last = tokens[1] if len(tokens) >= 2 else tokens[0]

    players = await search_players(clean, last_name=last, include_inactive=not active_only)

    target = clean.lower()
    matches = [p for p in players if f"{p.first_name} {p.last_name}".lower() == target]

    if len(matches) == 0:
        scope = "active" if active_only else "active or historical"
        raise PlayerNotFoundError(
            f"No {scope} player found with name '{clean}'. "
            "Check spelling or use active_only=False to include retired players."
        )

    if len(matches) > 1:
        candidates = [
            {
                "name": f"{p.first_name} {p.last_name}",
                "team": (p.team.abbreviation if p.team and hasattr(p.team, "abbreviation") else str(p.team or "?")),
                "player_id": p.id,
            }
            for p in matches
        ]
        raise AmbiguousPlayerError(
            f"Multiple players match '{clean}': "
            + ", ".join(f"{c['name']} ({c['team']})" for c in candidates),
            candidates=candidates,
        )

    return matches[0]


async def get_team_roster_last_names(team_id: int) -> set[str]:
    """
    Return the set of lowercased last names for all players on a team's
    current BDL roster.  Used to validate ESPN injury report entries so
    stale/traded players are not included in predictions.
    """
    try:
        payload = await _fetch_data("/players", params={"team_ids[]": team_id, "per_page": 100})
        names: set[str] = set()
        for p in payload.get("data") or []:
            ln = (p.get("last_name") or "").strip().lower()
            if ln:
                names.add(ln)
        logger.debug("Roster last names for team_id=%d: %d players", team_id, len(names))
        return names
    except Exception:
        return set()


async def get_roster_by_abbr(abbr: str) -> list[dict]:
    """
    Return current-roster players for a team by extracting unique players
    from the team's most recent game logs this season.

    Two-step approach required because BDL free tier ignores team_ids[] on
    /stats -- the filter only works on /games.  We therefore:
      1. Fetch this season's game schedule for the team via /games (filter works)
      2. Extract the 3 most-recent completed game IDs
      3. Fetch /stats for those specific game_ids (both teams returned)
      4. Client-side filter: keep only rows where stat.team.id == team_id
    """
    ck = f"roster:{abbr.upper()}"
    cached_result = await cache_get(ck)
    if cached_result is not None:
        logger.debug("Redis hit | %s", ck)
        return cached_result

    # 1. Resolve team ID from abbreviation
    teams_payload = await _fetch_data("/teams", params={"per_page": 100})
    team_id: int | None = None
    for t in teams_payload.get("data") or []:
        if (t.get("abbreviation") or "").upper() == abbr.upper():
            team_id = int(t["id"])
            break
    if team_id is None:
        raise ValueError(f"No team found with abbreviation '{abbr}'")

    # 2. Fetch this season's games for the team.
    #    /games?team_ids[] correctly filters by team (unlike /stats?team_ids[]).
    current_season = get_current_season()
    games_payload = await _fetch_data(
        "/games",
        params={"team_ids[]": team_id, "seasons[]": current_season, "per_page": 100},
    )
    all_games = games_payload.get("data") or []

    # 3. Pick the 3 most-recent completed games (reversed so newest is first).
    recent_game_ids: list[int] = [
        g["id"] for g in reversed(all_games) if g.get("status") == "Final"
    ][:3]

    if not recent_game_ids:
        logger.warning("No completed games found for abbr=%s season=%s", abbr, current_season)
        return []

    # 4. Fetch stats for those specific games.
    #    Both teams' rows are returned -- we filter to our team below.
    stats_payload = await _fetch_data(
        "/stats",
        params={"game_ids[]": recent_game_ids, "per_page": 100},
    )
    stats_data = stats_payload.get("data") or []

    # 5. Keep only rows belonging to this team, deduplicate by player ID.
    seen_ids: set[int] = set()
    result: list[dict] = []
    for s in stats_data:
        stat_team = s.get("team") or {}
        if stat_team.get("id") != team_id:
            continue
        player = s.get("player") or {}
        pid = player.get("id")
        if not pid or pid in seen_ids:
            continue
        seen_ids.add(pid)
        result.append({
            "id": pid,
            "first_name": (player.get("first_name") or "").strip(),
            "last_name":  (player.get("last_name") or "").strip(),
            "position":   (player.get("position") or "").strip(),
        })

    result.sort(key=lambda x: x.get("last_name", ""))
    logger.debug("Roster for abbr=%s: %d players (from %d recent games)", abbr, len(result), len(recent_game_ids))
    await cache_set(ck, result, 3600)
    return result


# ---------------------------------------------------------------------------
# Contracts, Advanced Stats, Lineups
# ---------------------------------------------------------------------------


def _parse_contract(raw: dict[str, Any]) -> "Contract":  # type: ignore[name-defined]
    from app.models.schemas import Contract as _Contract  # local import to avoid cycle

    player_raw = raw.get("player") or {}
    team_raw = raw.get("team") or {}

    return _Contract(
        id=int(raw.get("id") or 0),
        player_id=int(raw.get("player_id") or 0),
        season=int(raw.get("season") or 0),
        team_id=int(raw.get("team_id") or 0),
        cap_hit=int(raw.get("cap_hit") or 0) if raw.get("cap_hit") is not None else None,
        total_cash=int(raw.get("total_cash") or 0) if raw.get("total_cash") is not None else None,
        base_salary=int(raw.get("base_salary") or 0) if raw.get("base_salary") is not None else None,
        rank=int(raw.get("rank") or 0) if raw.get("rank") is not None else None,
        player=_parse_player(player_raw) if player_raw else None,
        team=_parse_team(team_raw) if team_raw else None,
    )


async def get_player_contracts(
    player_id: int,
    seasons: Optional[list[int]] = None,
    per_page: int = 25,
    cursor: int | None = None,
) -> list["Contract"]:  # type: ignore[name-defined]
    """Retrieve contract entries for a given player.

    Parameters
    ----------
    player_id:
        BallDontLie player id
    seasons:
        Optional list of seasons to filter (e.g. [2024,2025])
    """
    params: dict[str, Any] = {"player_id": player_id, "per_page": per_page}
    if seasons:
        params.update({"seasons[]": seasons})
    if cursor:
        params["cursor"] = cursor

    payload = await _fetch_data("/contracts/players", params=params)
    data = payload.get("data") or []
    contracts = [_parse_contract(c) for c in data]
    logger.debug("Contracts search for player_id=%s returned %d rows", player_id, len(contracts))
    return contracts


def _parse_advanced_stat(raw: dict[str, Any]) -> "AdvancedStat":  # type: ignore[name-defined]
    """
    Hydrate an AdvancedStat schema object from a raw V2 advanced stats row.
    The V2 schema is a superset of V1, so all V1 fields are still present.
    """
    from app.models.schemas import AdvancedStat as _AdvancedStat  # local import

    return _AdvancedStat(
        id=int(raw.get("id") or 0),
        pie=raw.get("pie"),
        pace=raw.get("pace"),
        assist_percentage=raw.get("assist_percentage"),
        assist_ratio=raw.get("assist_ratio"),
        defensive_rating=raw.get("defensive_rating"),
        defensive_rebound_percentage=raw.get("defensive_rebound_percentage"),
        effective_field_goal_percentage=raw.get("effective_field_goal_percentage"),
        net_rating=raw.get("net_rating"),
        offensive_rating=raw.get("offensive_rating"),
        offensive_rebound_percentage=raw.get("offensive_rebound_percentage"),
        rebound_percentage=raw.get("rebound_percentage"),
        true_shooting_percentage=raw.get("true_shooting_percentage"),
        turnover_ratio=raw.get("turnover_ratio"),
        usage_percentage=raw.get("usage_percentage"),
        player=_parse_player(raw.get("player") or {}),
        team=_parse_team(raw.get("team") or {}),
        game=raw.get("game") or None,
    )


async def get_v2_advanced_stats(
    player_ids: Optional[list[int]] = None,
    seasons: Optional[list[int]] = None,
    game_ids: Optional[list[int]] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    postseason: Optional[bool] = None,
    period: int = 0,
    per_page: int = 100,
) -> list[dict[str, Any]]:
    """
    Fetch per-game advanced stats rows from the BDL V2 endpoint.

    Handles cursor pagination automatically -- returns ALL matching rows
    across all pages in a single call. Rate-limit (429) retry is handled
    by the underlying _fetch_data layer.

    Parameters
    ----------
    player_ids:
        BallDontLie player IDs to filter. None means no filter (all players).
    seasons:
        Season start years (e.g. [2024] for the 2024-25 season).
    game_ids:
        Specific game IDs to fetch.
    start_date:
        ISO-8601 date string (inclusive lower bound on game date).
    end_date:
        ISO-8601 date string (inclusive upper bound on game date).
    postseason:
        True for playoff games only, False for regular-season only, None for both.
    period:
        0 = full game (default, required for hustle/tracking fields to be populated).
        1-4 = individual quarter.
    per_page:
        Page size for each BDL request. Max 100 (BDL hard limit).

    Returns
    -------
    list[dict]
        One raw dict per game row, preserving all V2 fields. Nested objects
        (player, team, game) are included as-is.
    """
    params: dict[str, Any] = {"per_page": min(per_page, 100), "period": period}

    if player_ids:
        params["player_ids[]"] = player_ids
    if seasons:
        params["seasons[]"] = seasons
    if game_ids:
        params["game_ids[]"] = game_ids
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    if postseason is not None:
        params["postseason"] = str(postseason).lower()

    _ck = "v2adv:" + _hashlib.md5(_json.dumps(params, sort_keys=True).encode()).hexdigest()
    _cached_rows = await cache_get(_ck)
    if _cached_rows is not None:
        return _cached_rows

    all_rows: list[dict[str, Any]] = []
    cursor: int | None = None
    page = 0
    _PAGE_SAFETY_CAP = 50  # 50 pages x 100 per page = 5000 rows max

    while page < _PAGE_SAFETY_CAP:
        page_params = dict(params)
        if cursor is not None:
            page_params["cursor"] = cursor

        payload = await _fetch_data(_BDL_V2_ADV_URL, params=page_params)
        data: list[dict[str, Any]] = payload.get("data") or []
        all_rows.extend(data)

        cursor = (payload.get("meta") or {}).get("next_cursor")
        logger.debug(
            "get_v2_advanced_stats | page=%d rows_this_page=%d total=%d next_cursor=%s",
            page, len(data), len(all_rows), cursor,
        )

        if not cursor or not data:
            break
        page += 1

    logger.info(
        "get_v2_advanced_stats | total_rows=%d player_ids=%s seasons=%s postseason=%s",
        len(all_rows), player_ids, seasons, postseason,
    )
    await cache_set(_ck, all_rows, 1800)
    return all_rows


async def get_advanced_stats(
    seasons: Optional[list[int]] = None,
    player_ids: Optional[list[int]] = None,
    per_page: int = 25,
    cursor: int | None = None,
) -> list["AdvancedStat"]:  # type: ignore[name-defined]
    """
    DEPRECATED shim -- calls get_v2_advanced_stats internally.

    Kept for backward compatibility during migration. New code should call
    get_v2_advanced_stats() directly and work with raw dicts.

    Returns at most per_page AdvancedStat objects (one-page contract preserved).
    """
    logger.warning(
        "get_advanced_stats: deprecated -- migrate callers to get_v2_advanced_stats"
    )
    rows = await get_v2_advanced_stats(
        player_ids=player_ids,
        seasons=seasons,
        per_page=per_page,
    )
    # Honor the original one-page contract: cap at per_page results.
    rows = rows[:per_page]
    stats = [_parse_advanced_stat(r) for r in rows]
    logger.debug("get_advanced_stats (shim) returned %d rows", len(stats))
    return stats


# ---------------------------------------------------------------------------
# V2 Season Aggregation
# ---------------------------------------------------------------------------

# V2 fields that represent per-game rates or ratios: averaged across games.
_V2_RATE_FIELDS: tuple[str, ...] = (
    "pie",
    "assist_percentage", "assist_ratio", "assist_to_turnover",
    "defensive_rating", "defensive_rebound_percentage",
    "effective_field_goal_percentage",
    "estimated_defensive_rating", "estimated_net_rating",
    "estimated_offensive_rating", "estimated_pace", "estimated_usage_percentage",
    "net_rating", "offensive_rating", "offensive_rebound_percentage",
    "pace", "pace_per_40",
    "rebound_percentage", "true_shooting_percentage",
    "turnover_ratio", "usage_percentage",
    "pct_assisted_2pt", "pct_assisted_3pt", "pct_assisted_fgm",
    "pct_fga_2pt", "pct_fga_3pt",
    "pct_pts_2pt", "pct_pts_3pt", "pct_pts_fast_break",
    "pct_pts_free_throw", "pct_pts_midrange_2pt",
    "pct_pts_off_turnovers", "pct_pts_paint",
    "pct_unassisted_2pt", "pct_unassisted_3pt", "pct_unassisted_fgm",
    "four_factors_efg_pct", "free_throw_attempt_rate",
    "four_factors_oreb_pct",
    "opp_efg_pct", "opp_free_throw_attempt_rate",
    "opp_oreb_pct", "opp_turnover_pct",
    "team_turnover_pct",
    "matchup_fg_pct", "matchup_3pt_pct",
    "contested_fg_pct", "uncontested_fg_pct", "defended_at_rim_fg_pct",
    "speed",
    "pct_blocks", "pct_blocks_allowed",
    "pct_fga", "pct_fgm", "pct_fta", "pct_ftm",
    "pct_personal_fouls", "pct_personal_fouls_drawn", "pct_points",
    "pct_rebounds_def", "pct_rebounds_off", "pct_rebounds_total",
    "pct_steals", "pct_3pa", "pct_3pm", "pct_turnovers",
)

# V2 fields that are per-game counting stats: summed to season totals.
# Per-game averages are also included in the output with a _pg suffix.
_V2_COUNT_FIELDS: tuple[str, ...] = (
    "blocks_against", "fouls_drawn",
    "points_fast_break", "points_off_turnovers", "points_paint", "points_second_chance",
    "opp_points_fast_break", "opp_points_off_turnovers",
    "opp_points_paint", "opp_points_second_chance",
    "box_outs", "box_out_player_rebounds", "box_out_player_team_rebounds",
    "defensive_box_outs", "offensive_box_outs",
    "charges_drawn",
    "contested_shots", "contested_shots_2pt", "contested_shots_3pt",
    "deflections",
    "loose_balls_recovered_def", "loose_balls_recovered_off", "loose_balls_recovered_total",
    "screen_assists", "screen_assist_points",
    "matchup_fga", "matchup_fgm", "matchup_3pa", "matchup_3pm",
    "matchup_assists", "matchup_turnovers", "matchup_player_points",
    "switches_on",
    "possessions", "partial_possessions",
    "passes", "secondary_assists", "free_throw_assists",
    "contested_fga", "contested_fgm",
    "uncontested_fga", "uncontested_fgm",
    "defended_at_rim_fga", "defended_at_rim_fgm",
    "rebound_chances_def", "rebound_chances_off", "rebound_chances_total",
    "touches", "distance",
)


def _mean(values: list[float]) -> Optional[float]:
    """Return the arithmetic mean of a non-empty list, else None."""
    clean = [v for v in values if v is not None]
    return round(sum(clean) / len(clean), 4) if clean else None


def _parse_matchup_minutes(val: Any) -> Optional[float]:
    """
    Parse a matchup_minutes string like '15:01' to fractional minutes.
    Returns None if the value is absent or unparseable.
    """
    if val is None:
        return None
    try:
        parts = str(val).strip().split(":")
        return float(parts[0]) + (float(parts[1]) / 60 if len(parts) == 2 else 0.0)
    except (ValueError, IndexError):
        return None


async def aggregate_season_advanced(
    player_id: int,
    season: int = 0,
    postseason: bool = False,
) -> dict[str, Any]:
    """
    Pull all V2 advanced stats game rows for one player-season and aggregate
    them into a single flat dict.

    Rate stats (percentages, ratings, ratios) are averaged across games.
    Counting stats (deflections, touches, distance, etc.) are summed to
    season totals; per-game averages for each count field are also included
    under the same key with a _pg suffix.

    All V2 fields present in the response are included in the output.
    Spec-required alias keys (ts_pct, usage_pct, off_rtg, etc.) are added
    alongside the original V2 field names for convenience.

    Results are cached in _ADV_SEASON_CACHE (memory, process-scoped).

    Parameters
    ----------
    player_id:
        BallDontLie player ID.
    season:
        Season start year (2024 = 2024-25 season). Defaults to current season.
    postseason:
        If True, aggregate playoff games only. If False, regular season only.

    Returns
    -------
    dict
        Flat dict with games_played, all rate-stat averages, all count-stat
        totals plus _pg averages, spec-required alias keys, and metadata.
        Returns {"games_played": 0} when no data is available for the season.
    """
    season = season or get_current_season()
    cache_key = f"{player_id}:{season}:{postseason}"
    _rk = f"agg_adv:{player_id}:{season}:{postseason}"
    _redis_hit = await cache_get(_rk)
    if _redis_hit is not None:
        _ADV_SEASON_CACHE[cache_key] = _redis_hit
        _ADV_CACHE_EXPIRY[cache_key] = time.time()
        return _redis_hit
    if cache_key in _ADV_SEASON_CACHE:
        age = time.time() - _ADV_CACHE_EXPIRY.get(cache_key, 0.0)
        if age < _ADV_CACHE_TTL:
            logger.debug("aggregate_season_advanced cache hit | %s (age=%.0fs)", cache_key, age)
            return _ADV_SEASON_CACHE[cache_key]
        logger.debug("aggregate_season_advanced cache expired | %s (age=%.0fs)", cache_key, age)

    logger.info(
        "aggregate_season_advanced | player_id=%d season=%d postseason=%s",
        player_id, season, postseason,
    )

    rows = await get_v2_advanced_stats(
        player_ids=[player_id],
        seasons=[season],
        postseason=postseason,
        period=0,  # full game only -- required for hustle/tracking fields
        per_page=100,
    )

    if not rows:
        logger.warning(
            "aggregate_season_advanced: no V2 rows | player_id=%d season=%d postseason=%s",
            player_id, season, postseason,
        )
        result: dict[str, Any] = {
            "player_id": player_id,
            "season": season,
            "postseason": postseason,
            "games_played": 0,
        }
        _ADV_SEASON_CACHE[cache_key] = result
        _ADV_CACHE_EXPIRY[cache_key] = time.time()
        await cache_set(_rk, result, 3600)
        return result

    games_played = len(rows)

    # ---- Rate stats: mean across games ----
    rate_avgs: dict[str, Any] = {}
    for field in _V2_RATE_FIELDS:
        vals = [r[field] for r in rows if r.get(field) is not None]
        rate_avgs[field] = _mean(vals)  # type: ignore[assignment]

    # ---- Count stats: season totals + per-game averages ----
    count_totals: dict[str, Any] = {}
    count_pg: dict[str, Any] = {}
    for field in _V2_COUNT_FIELDS:
        vals = [r[field] for r in rows if r.get(field) is not None]
        if vals:
            total = sum(vals)
            count_totals[field] = round(total, 2)
            count_pg[f"{field}_pg"] = round(total / games_played, 2)
        else:
            count_totals[field] = None
            count_pg[f"{field}_pg"] = None

    # ---- matchup_minutes: special string field, aggregate separately ----
    mm_vals = [_parse_matchup_minutes(r.get("matchup_minutes")) for r in rows]
    mm_vals_clean = [v for v in mm_vals if v is not None]
    matchup_minutes_total = round(sum(mm_vals_clean), 2) if mm_vals_clean else None
    matchup_minutes_pg = round(matchup_minutes_total / games_played, 2) if matchup_minutes_total is not None else None

    # ---- Assemble output dict ----
    result = {
        # Metadata
        "player_id": player_id,
        "season": season,
        "postseason": postseason,
        "games_played": games_played,

        # All rate fields (V2 original names)
        **rate_avgs,

        # All count fields as season totals (V2 original names)
        **count_totals,

        # Per-game averages for count fields (_pg suffix)
        **count_pg,

        # matchup_minutes as total and per-game
        "matchup_minutes_total": matchup_minutes_total,
        "matchup_minutes_pg": matchup_minutes_pg,

        # -------------------------------------------------------------------
        # Spec-required alias keys for Claude payload consumption.
        # These duplicate the V2 field names above for a cleaner interface.
        # -------------------------------------------------------------------
        "ts_pct":                 rate_avgs.get("true_shooting_percentage"),
        "efg_pct":                rate_avgs.get("effective_field_goal_percentage"),
        "usage_pct":              rate_avgs.get("usage_percentage"),
        "off_rtg":                rate_avgs.get("offensive_rating"),
        "def_rtg":                rate_avgs.get("defensive_rating"),
        "net_rtg":                rate_avgs.get("net_rating"),
        "pace":                   rate_avgs.get("pace"),
        "ast_pct":                rate_avgs.get("assist_percentage"),
        "ast_to_tov":             rate_avgs.get("assist_to_turnover"),
        "reb_pct":                rate_avgs.get("rebound_percentage"),
        "pie":                    rate_avgs.get("pie"),
        "pct_pts_paint":          rate_avgs.get("pct_pts_paint"),
        "pct_pts_3pt":            rate_avgs.get("pct_pts_3pt"),
        "pct_assisted_fgm":       rate_avgs.get("pct_assisted_fgm"),
        "contested_fg_pct":       rate_avgs.get("contested_fg_pct"),
        "uncontested_fg_pct":     rate_avgs.get("uncontested_fg_pct"),
        "defended_at_rim_fg_pct": rate_avgs.get("defended_at_rim_fg_pct"),
    }

    _ADV_SEASON_CACHE[cache_key] = result
    _ADV_CACHE_EXPIRY[cache_key] = time.time()
    await cache_set(_rk, result, 3600)
    logger.info(
        "aggregate_season_advanced complete | player_id=%d season=%d games=%d "
        "ts_pct=%.3f usage_pct=%.3f off_rtg=%.1f",
        player_id, season, games_played,
        result["ts_pct"] or 0,
        result["usage_pct"] or 0,
        result["off_rtg"] or 0,
    )
    return result


def _parse_lineup(raw: dict[str, Any]) -> "LineupEntry":  # type: ignore[name-defined]
    from app.models.schemas import LineupEntry as _LineupEntry

    player_raw = raw.get("player") or {}
    team_raw = raw.get("team") or {}

    return _LineupEntry(
        id=int(raw.get("id") or 0),
        game_id=int(raw.get("game_id") or 0),
        starter=bool(raw.get("starter")) if raw.get("starter") is not None else None,
        position=raw.get("position"),
        player=_parse_player(player_raw) if player_raw else None,
        team=_parse_team(team_raw) if team_raw else None,
    )


async def get_lineups(game_ids: list[int], per_page: int = 25, cursor: int | None = None) -> list["LineupEntry"]:  # type: ignore[name-defined]
    params: dict[str, Any] = {"per_page": per_page, "game_ids[]": game_ids}
    if cursor:
        params["cursor"] = cursor

    payload = await _fetch_data("/lineups", params=params)
    data = payload.get("data") or []
    lineups = [_parse_lineup(l) for l in data]
    logger.debug("Lineups fetch returned %d rows for games=%s", len(lineups), game_ids)
    return lineups


# ---------------------------------------------------------------------------
# Box Score Aggregation
# ---------------------------------------------------------------------------

async def get_game_boxscore(game_id: int) -> dict[str, Any]:
    """
    Retrieve and aggregate a full box score for a completed or in-progress game.

    Fires two concurrent API requests -- one for game metadata and one for
    per-player statistics -- and merges them into a single normalised payload.

    The returned dict is guaranteed to have all top-level keys present even
    when no player stats are available yet (e.g. a game that has not tipped off).

    Parameters
    ----------
    game_id:
        BallDontLie internal game identifier.

    Returns
    -------
    dict
        Keys: ``game_id``, ``game_info``, ``home_team``, ``away_team``,
        ``home_players``, ``away_players``, ``total_players``.
    """
    ck = f"boxscore:{game_id}"
    cached_raw = await cache_get(ck)
    if cached_raw is not None:
        return cached_raw

    logger.info("Fetching box score for game_id=%d", game_id)

    # Fire both API calls concurrently to halve wall-clock latency.
    game_payload, stats_payload = await asyncio.gather(
        _fetch_data(f"/games/{game_id}"),
        _fetch_data("/stats", params={"game_ids[]": game_id, "per_page": _DEFAULT_PER_PAGE}),
    )

    game_raw: dict[str, Any] = game_payload.get("data") or {}

    # -----------------------------------------------------------------------
    # Game metadata
    # -----------------------------------------------------------------------

    game_info: dict[str, Any] = {
        "id": game_raw.get("id"),
        "date": game_raw.get("date") or "",
        "status": game_raw.get("status") or "Unknown",
        "period": game_raw.get("period"),
        "time": game_raw.get("time"),
        "home_team_score": int(game_raw.get("home_team_score") or 0),
        "away_team_score": int(game_raw.get("visitor_team_score") or 0),
    }

    # -----------------------------------------------------------------------
    # Team metadata
    # -----------------------------------------------------------------------

    home_raw: dict[str, Any] = game_raw.get("home_team") or {}
    away_raw: dict[str, Any] = game_raw.get("visitor_team") or {}

    home_city = home_raw.get("city") or ""
    home_name = home_raw.get("name") or ""
    away_city = away_raw.get("city") or ""
    away_name = away_raw.get("name") or ""

    home_team: dict[str, Any] = {
        "id": home_raw.get("id"),
        "name": home_raw.get("full_name") or f"{home_city} {home_name}".strip(),
        "abbreviation": home_raw.get("abbreviation") or "",
        "score": int(game_raw.get("home_team_score") or 0),
    }

    away_team: dict[str, Any] = {
        "id": away_raw.get("id"),
        "name": away_raw.get("full_name") or f"{away_city} {away_name}".strip(),
        "abbreviation": away_raw.get("abbreviation") or "",
        "score": int(game_raw.get("visitor_team_score") or 0),
    }

    # -----------------------------------------------------------------------
    # Player stat lines
    # -----------------------------------------------------------------------

    home_players: list[dict[str, Any]] = []
    away_players: list[dict[str, Any]] = []

    for s in stats_payload.get("data") or []:
        stat_line = _parse_stat_line(s)
        team_id = stat_line.get("team_id")

        if team_id == home_team["id"]:
            home_players.append(stat_line)
        elif team_id == away_team["id"]:
            away_players.append(stat_line)
        else:
            logger.debug(
                "Stat line for %r has unknown team_id=%s; skipping",
                stat_line["player"],
                team_id,
            )

    # Sort by points descending -- highest scorers first in each list.
    home_players.sort(key=lambda x: x["pts"], reverse=True)
    away_players.sort(key=lambda x: x["pts"], reverse=True)

    total = len(home_players) + len(away_players)
    logger.info(
        "Box score assembled | game_id=%d players=%d home=%d away=%d",
        game_id,
        total,
        len(home_players),
        len(away_players),
    )

    result = {
        "game_id": game_id,
        "game_info": game_info,
        "home_team": home_team,
        "away_team": away_team,
        "home_players": home_players,
        "away_players": away_players,
        "total_players": total,
    }
    is_final = "final" in (game_info.get("status") or "").lower()
    await cache_set(ck, result, 86400 if is_final else 60)
    return result


# ---------------------------------------------------------------------------
# Live Game State
# ---------------------------------------------------------------------------

async def get_live_game_state(game_id: int) -> dict[str, Any]:
    """
    Build a rich live-game state object from BDL game metadata + player stats.

    BDL does not expose a play-by-play endpoint.  Instead we derive momentum
    and run context from quarter-score deltas and cumulative player stat lines.

    The returned dict is the canonical payload consumed by both the Games
    Section live dashboard and Coach Mode's tactical engine.

    Keys
    ----
    game_id, clock, period, period_label, home_score, away_score,
    home_team, away_team,                       -- team objects
    home_q_scores, away_q_scores,               -- list[int|None] per period
    home_timeouts, away_timeouts,               -- int remaining
    home_in_bonus, away_in_bonus,               -- bool
    score_diff,                                 -- int (home minus away)
    momentum,                                   -- "home" | "away" | "even"
    current_run,                                -- dict with team/points/periods
    quarter_summary,                            -- list of period deltas
    top_performers,                             -- top 3 players by impact
    foul_trouble,                               -- players with 3+ PF
    hot_shooters,                               -- players on 50%+ FG with 6+ FGA
    cold_shooters,                              -- players on 25% or below FG with 6+ FGA
    high_turnover_players,                      -- players with 4+ TO
    home_players, away_players,                 -- full stat lines
    is_live,                                    -- bool
    status,                                     -- raw status string
    """
    logger.info("Fetching live game state | game_id=%d", game_id)

    game_payload, stats_payload = await asyncio.gather(
        _fetch_data(f"/games/{game_id}"),
        _fetch_data("/stats", params={"game_ids[]": game_id, "per_page": _DEFAULT_PER_PAGE}),
    )

    game_raw: dict[str, Any] = game_payload.get("data") or {}
    stats_raw: list[dict] = stats_payload.get("data") or []

    # Basic game fields
    period   = int(game_raw.get("period") or 0)
    clock    = str(game_raw.get("time") or "")
    status   = str(game_raw.get("status") or "")
    is_live  = status.lower() not in ("final", "") and period > 0 and "final" not in status.lower()

    home_score = int(game_raw.get("home_team_score") or 0)
    away_score = int(game_raw.get("visitor_team_score") or 0)
    score_diff = home_score - away_score

    home_raw = game_raw.get("home_team") or {}
    away_raw = game_raw.get("visitor_team") or {}

    def _team(raw: dict) -> dict:
        return {
            "id": raw.get("id"),
            "name": raw.get("full_name") or f"{raw.get('city','')} {raw.get('name','')}".strip(),
            "abbreviation": raw.get("abbreviation") or "",
            "city": raw.get("city") or "",
        }

    home_team = _team(home_raw)
    away_team = _team(away_raw)

    # Period label
    if period == 0:
        period_label = "PRE-GAME"
    elif period <= 4:
        period_label = f"Q{period}"
    elif period == 5:
        period_label = "OT"
    else:
        period_label = f"OT{period - 4}"

    # Quarter scores
    def _q_scores(prefix: str) -> list[int | None]:
        quarters = []
        for q in ["q1", "q2", "q3", "q4", "ot1", "ot2", "ot3"]:
            val = game_raw.get(f"{prefix}_{q}")
            quarters.append(int(val) if val is not None else None)
        return quarters

    home_q = _q_scores("home")
    away_q = _q_scores("visitor")

    # Quarter summary (deltas per period)
    quarter_summary = []
    labels = ["Q1", "Q2", "Q3", "Q4", "OT1", "OT2", "OT3"]
    for i, lbl in enumerate(labels):
        h, a = home_q[i], away_q[i]
        if h is None or a is None:
            break
        quarter_summary.append({
            "period": lbl,
            "home": h,
            "away": a,
            "winner": "home" if h > a else ("away" if a > h else "even"),
            "margin": abs(h - a),
        })

    # Momentum: which team won the most recent two completed periods
    momentum = "even"
    if len(quarter_summary) >= 2:
        recent = quarter_summary[-2:]
        home_won = sum(1 for q in recent if q["winner"] == "home")
        away_won = sum(1 for q in recent if q["winner"] == "away")
        if home_won > away_won:
            momentum = "home"
        elif away_won > home_won:
            momentum = "away"

    # Current run: score in the most recent completed period
    current_run: dict[str, Any] = {}
    if quarter_summary:
        last = quarter_summary[-1]
        if last["margin"] >= 8:
            current_run = {
                "team": home_team["name"] if last["winner"] == "home" else away_team["name"],
                "points": last[last["winner"]] if last["winner"] != "even" else 0,
                "opponent_points": last["away" if last["winner"] == "home" else "home"],
                "period": last["period"],
            }

    # Player stat lines
    home_id = home_raw.get("id")
    away_id = away_raw.get("id")

    home_players: list[dict] = []
    away_players: list[dict] = []

    for s in stats_raw:
        p = s.get("player") or {}
        t = s.get("team") or {}
        mins_raw = str(s.get("min") or "0")
        try:
            mins = int(mins_raw.split(":")[0])
        except (ValueError, AttributeError):
            mins = 0

        fgm, fga = int(s.get("fgm") or 0), int(s.get("fga") or 0)
        fg3m, fg3a = int(s.get("fg3m") or 0), int(s.get("fg3a") or 0)
        ftm, fta = int(s.get("ftm") or 0), int(s.get("fta") or 0)

        line = {
            "player": f"{p.get('first_name','')} {p.get('last_name','')}".strip(),
            "player_id": p.get("id"),
            "pos": p.get("position") or "",
            "min": mins,
            "pts": int(s.get("pts") or 0),
            "reb": int(s.get("reb") or 0),
            "ast": int(s.get("ast") or 0),
            "stl": int(s.get("stl") or 0),
            "blk": int(s.get("blk") or 0),
            "to":  int(s.get("turnover") or 0),
            "pf":  int(s.get("pf") or 0),
            "fgm": fgm, "fga": fga,
            "fg3m": fg3m, "fg3a": fg3a,
            "ftm": ftm, "fta": fta,
            "fg_pct": round(fgm / fga, 3) if fga > 0 else None,
            "plus_minus": int(s.get("plus_minus") or 0),
        }

        tid = t.get("id")
        if tid == home_id:
            home_players.append(line)
        elif tid == away_id:
            away_players.append(line)

    home_players.sort(key=lambda x: x["pts"], reverse=True)
    away_players.sort(key=lambda x: x["pts"], reverse=True)
    all_players = home_players + away_players

    # Derived player flags
    def _impact(p: dict) -> float:
        return p["pts"] + p["reb"] * 0.7 + p["ast"] * 0.7 + p["stl"] * 1.5 + p["blk"] * 1.5 - p["to"] * 1.2

    top_performers = sorted(
        [p for p in all_players if p["min"] >= 8],
        key=_impact, reverse=True
    )[:4]

    foul_trouble = [
        {"player": p["player"], "pf": p["pf"],
         "team": home_team["name"] if p in home_players else away_team["name"]}
        for p in all_players if p["pf"] >= 3
    ]

    hot_shooters = [
        {"player": p["player"], "fgm": p["fgm"], "fga": p["fga"], "pts": p["pts"],
         "team": home_team["name"] if p in home_players else away_team["name"]}
        for p in all_players if p["fga"] >= 6 and (p["fg_pct"] or 0) >= 0.50
    ]

    cold_shooters = [
        {"player": p["player"], "fgm": p["fgm"], "fga": p["fga"], "pts": p["pts"],
         "team": home_team["name"] if p in home_players else away_team["name"]}
        for p in all_players if p["fga"] >= 6 and (p["fg_pct"] or 1) <= 0.25
    ]

    high_turnover_players = [
        {"player": p["player"], "to": p["to"],
         "team": home_team["name"] if p in home_players else away_team["name"]}
        for p in all_players if p["to"] >= 4
    ]

    return {
        "game_id": game_id,
        "clock": clock,
        "period": period,
        "period_label": period_label,
        "home_score": home_score,
        "away_score": away_score,
        "score_diff": score_diff,
        "home_team": home_team,
        "away_team": away_team,
        "home_q_scores": home_q,
        "away_q_scores": away_q,
        "home_timeouts": int(game_raw.get("home_timeouts_remaining") or 0),
        "away_timeouts": int(game_raw.get("visitor_timeouts_remaining") or 0),
        "home_in_bonus": bool(game_raw.get("home_in_bonus")),
        "away_in_bonus": bool(game_raw.get("visitor_in_bonus")),
        "momentum": momentum,
        "current_run": current_run,
        "quarter_summary": quarter_summary,
        "top_performers": top_performers,
        "foul_trouble": foul_trouble,
        "hot_shooters": hot_shooters,
        "cold_shooters": cold_shooters,
        "high_turnover_players": high_turnover_players,
        "home_players": home_players,
        "away_players": away_players,
        "is_live": is_live,
        "status": status,
    }


# ---------------------------------------------------------------------------
# Player Season Stats
# ---------------------------------------------------------------------------

async def get_player_stats(player_id: int, season: int = 0) -> list[PlayerStats]:
    """
    Retrieve per-game stat logs for a player for a given season.

    Results are sorted chronologically (ascending game ID) so callers can
    trivially slice ``[-10:]`` for a recent-form window.

    Parameters
    ----------
    player_id:
        BallDontLie internal player identifier.
    season:
        NBA season year (the year the season *starts* in; 2024 = 2024-25).

    Returns
    -------
    list[PlayerStats]
        One entry per game played. Empty list if no stats are found.
    """
    season = season or get_current_season()
    logger.debug("Fetching player stats | player_id=%d season=%d", player_id, season)

    results: list[PlayerStats] = []
    cursor: int | None = None

    for _page in range(10):  # safety cap -- 82 games / 100 per page = 1 page normally
        params: dict[str, Any] = {
            "player_ids[]": player_id,
            "seasons[]": season,
            "per_page": _DEFAULT_PER_PAGE,
        }
        if cursor is not None:
            params["cursor"] = cursor

        payload = await _fetch_data("/stats", params=params)
        page_data = payload.get("data") or []

        for s in page_data:
            player_raw: dict[str, Any] = s.get("player") or {}
            game_raw: dict[str, Any] = s.get("game") or {}
            results.append(
                PlayerStats(
                    player=_parse_player(player_raw),
                    game_id=int(game_raw.get("id") or 0),
                    points=int(s.get("pts") or 0),
                    rebounds=int(s.get("reb") or 0),
                    assists=int(s.get("ast") or 0),
                    steals=int(s.get("stl") or 0),
                    blocks=int(s.get("blk") or 0),
                    minutes=s.get("min"),
                    fg_pct=s.get("fg_pct"),
                    fg3_pct=s.get("fg3_pct"),
                    ft_pct=s.get("ft_pct"),
                    fgm=_int_or_none(s.get("fgm")),
                    fga=_int_or_none(s.get("fga")),
                    fg3m=_int_or_none(s.get("fg3m")),
                    fg3a=_int_or_none(s.get("fg3a")),
                    ftm=_int_or_none(s.get("ftm")),
                    fta=_int_or_none(s.get("fta")),
                    turnover=_int_or_none(s.get("turnover")),
                    game_date=game_raw.get("date"),
                    game_home_team_id=_int_or_none(game_raw.get("home_team_id")),
                    game_visitor_team_id=_int_or_none(game_raw.get("visitor_team_id")),
                    game_home_score=_int_or_none(game_raw.get("home_team_score")),
                    game_visitor_score=_int_or_none(game_raw.get("visitor_team_score")),
                )
            )

        next_cursor = (payload.get("meta") or {}).get("next_cursor")
        if not next_cursor or not page_data:
            break
        cursor = next_cursor

    # Strip DNP entries -- BDL returns '0', '0:00', or '00' for non-playing rows.
    results = [r for r in results if _has_real_minutes(r.minutes)]

    # Sort ascending by game_id so recent-form slicing (stats[-10:]) is valid.
    results.sort(key=lambda x: x.game_id)

    logger.debug(
        "Fetched %d stat entries (DNPs removed) | player_id=%d season=%d",
        len(results),
        player_id,
        season,
    )
    return results


async def get_recent_stats(player_id: int, season: int = 0, n: int = 10) -> list[PlayerStats]:
    """
    Fetch the N most-recent game logs for a player this season.

    Uses per_page=N sorted by most recent so we get true last-N games,
    not a slice of a larger sorted list that may include stale preseason entries.
    """
    season = season or get_current_season()
    logger.debug("Fetching recent stats | player_id=%d season=%d n=%d", player_id, season, n)
    try:
        payload = await _fetch_data(
            "/stats",
            params={
                "player_ids[]": player_id,
                "seasons[]": season,
                "per_page": n,
            },
        )
    except Exception as exc:
        logger.warning("Recent stats fetch failed | player_id=%d error=%s", player_id, exc)
        return []

    results: list[PlayerStats] = []
    for s in payload.get("data") or []:
        player_raw = s.get("player") or {}
        game_raw = s.get("game") or {}
        results.append(
            PlayerStats(
                player=_parse_player(player_raw),
                game_id=int(game_raw.get("id") or 0),
                points=int(s.get("pts") or 0),
                rebounds=int(s.get("reb") or 0),
                assists=int(s.get("ast") or 0),
                steals=int(s.get("stl") or 0),
                blocks=int(s.get("blk") or 0),
                minutes=s.get("min"),
                fg_pct=s.get("fg_pct"),
                fg3_pct=s.get("fg3_pct"),
                ft_pct=s.get("ft_pct"),
                fgm=_int_or_none(s.get("fgm")),
                fga=_int_or_none(s.get("fga")),
                fg3m=_int_or_none(s.get("fg3m")),
                fg3a=_int_or_none(s.get("fg3a")),
                ftm=_int_or_none(s.get("ftm")),
                fta=_int_or_none(s.get("fta")),
                turnover=_int_or_none(s.get("turnover")),
                game_date=game_raw.get("date"),
                game_home_team_id=_int_or_none(game_raw.get("home_team_id")),
                game_visitor_team_id=_int_or_none(game_raw.get("visitor_team_id")),
                game_home_score=_int_or_none(game_raw.get("home_team_score")),
                game_visitor_score=_int_or_none(game_raw.get("visitor_team_score")),
            )
        )
    results = [r for r in results if _has_real_minutes(r.minutes)]
    results.sort(key=lambda x: x.game_id, reverse=True)
    return results


async def get_recent_stats_full(
    player_id: int,
    season: int = 0,
    n: int = 10,
) -> list[dict[str, Any]]:
    """
    Fetch the N most-recent game logs as raw dicts, preserving every field
    the BDL /stats endpoint returns (fga, fgm, fg3a, fg3m, fta, ftm, oreb,
    dreb, turnover, pf, etc.).

    Used by enrich_player() in analysis_service so derived metrics (TS%, AST/TO,
    usage proxy) can be computed for the L10 window -- not just pts/reb/ast.
    DNPs are filtered out the same way get_recent_stats() does.
    """
    season = season or get_current_season()
    logger.debug("Fetching full recent stats | player_id=%d season=%d n=%d", player_id, season, n)
    try:
        payload = await _fetch_data(
            "/stats",
            params={
                "player_ids[]": player_id,
                "seasons[]": season,
                "per_page": n,
            },
        )
    except Exception as exc:
        logger.warning("Full recent stats fetch failed | player_id=%d error=%s", player_id, exc)
        return []

    def _to_float_inner(v: Any) -> Optional[float]:
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    results = []
    for s in payload.get("data") or []:
        raw_min = s.get("min")
        if not _has_real_minutes(raw_min):
            continue
        game_raw = s.get("game") or {}
        results.append({
            "game_id":  int(game_raw.get("id") or 0),
            "pts":      _to_float_inner(s.get("pts")),
            "reb":      _to_float_inner(s.get("reb")),
            "ast":      _to_float_inner(s.get("ast")),
            "stl":      _to_float_inner(s.get("stl")),
            "blk":      _to_float_inner(s.get("blk")),
            "tov":      _to_float_inner(s.get("turnover")),
            "pf":       _to_float_inner(s.get("pf")),
            "oreb":     _to_float_inner(s.get("oreb")),
            "dreb":     _to_float_inner(s.get("dreb")),
            "fga":      _to_float_inner(s.get("fga")),
            "fgm":      _to_float_inner(s.get("fgm")),
            "fg3a":     _to_float_inner(s.get("fg3a")),
            "fg3m":     _to_float_inner(s.get("fg3m")),
            "fta":      _to_float_inner(s.get("fta")),
            "ftm":      _to_float_inner(s.get("ftm")),
            "min":      raw_min,
        })

    results.sort(key=lambda x: x["game_id"], reverse=True)
    return results


def _to_float(v: Any) -> Optional[float]:
    """Safely coerce a BDL stat value to float, returning None on failure."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


async def get_season_averages(player_id: int, season: int = 0) -> dict[str, Any]:
    """
    Fetch official season averages for a player from BallDontLie.

    Uses the ``/season_averages`` endpoint which returns pre-computed averages
    that are more accurate than computing them from raw game logs (which can
    lag or omit double-headers).

    Parameters
    ----------
    player_id:
        BallDontLie internal player identifier.
    season:
        NBA season start year.

    Returns
    -------
    dict
        Season average dict from the API, or an empty dict if no data exists
        (e.g. the player did not play that season or the endpoint is unavailable).
    """
    season = season or get_current_season()
    logger.debug(
        "Fetching season averages | player_id=%d season=%d", player_id, season
    )

    _ck = f"season_avg:{player_id}:{season}"
    _cached = await cache_get(_ck)
    if _cached is not None:
        return _cached

    try:
        payload = await _fetch_data(
            "/season_averages",
            params={"player_id": player_id, "season": season},
        )
        data: list[dict[str, Any]] = payload.get("data") or []

        if not data:
            logger.debug(
                "No season averages found | player_id=%d season=%d", player_id, season
            )
            return {}

        averages = data[0]
        logger.debug(
            "Season averages retrieved | player_id=%d pts=%.1f reb=%.1f ast=%.1f",
            player_id,
            averages.get("pts") or 0,
            averages.get("reb") or 0,
            averages.get("ast") or 0,
        )
        await cache_set(_ck, averages, 3600)
        return averages

    except Exception as exc:
        # Season averages are supplementary data. A failure here degrades
        # gracefully -- callers fall back to computing averages from game logs.
        logger.warning(
            "Season averages fetch failed | player_id=%d season=%d error=%s",
            player_id,
            season,
            exc,
        )
        return {}


async def get_trending_players(days: int = 5, top_n: int = 8) -> list[dict[str, Any]]:
    """
    Return top NBA performers from the last N days based on points scored.

    Fetches raw per-game stats for the date range, strips DNPs, aggregates by
    player, and returns the top_n highest-scoring players. Uses BallDontLie IDs
    directly so results always have valid IDs for follow-up API calls.
    """
    from datetime import timedelta

    tz = ZoneInfo(_CENTRAL_TZ)
    today = datetime.now(tz).date()
    start = today - timedelta(days=days)

    logger.info("Fetching trending players | %s to %s", start, today)

    try:
        payload = await _fetch_data(
            "/stats",
            params={
                "start_date": str(start),
                "end_date": str(today),
                "per_page": _DEFAULT_PER_PAGE,
            },
        )
    except Exception as exc:
        logger.warning("Trending players fetch failed: %s", exc)
        return []

    player_agg: dict[int, dict[str, Any]] = {}

    for s in payload.get("data") or []:
        player_raw = s.get("player") or {}
        player_id = player_raw.get("id")
        if not player_id:
            continue

        # Skip DNPs
        min_raw = str(s.get("min") or "0")
        try:
            mins = int(min_raw.split(":")[0])
        except (ValueError, AttributeError):
            mins = 0
        if mins < 5:
            continue

        if player_id not in player_agg:
            team_raw = s.get("team") or {}
            full_name = f"{player_raw.get('first_name', '')} {player_raw.get('last_name', '')}".strip()
            player_agg[player_id] = {
                "id": player_id,
                "first_name": player_raw.get("first_name", ""),
                "last_name": player_raw.get("last_name", ""),
                "name": full_name,
                "position": player_raw.get("position", ""),
                "team": team_raw.get("abbreviation", ""),
                "nba_id": player_raw.get("nba_player_id") or _NBA_ID_BY_NAME.get(full_name),
                "games": 0,
                "pts_sum": 0,
                "reb_sum": 0,
                "ast_sum": 0,
            }

        player_agg[player_id]["games"] += 1
        player_agg[player_id]["pts_sum"] += int(s.get("pts") or 0)
        player_agg[player_id]["reb_sum"] += int(s.get("reb") or 0)
        player_agg[player_id]["ast_sum"] += int(s.get("ast") or 0)

    qualified = []
    for p in player_agg.values():
        g = p["games"]
        qualified.append({
            "id": p["id"],
            "first_name": p["first_name"],
            "last_name": p["last_name"],
            "name": p["name"],
            "position": p["position"],
            "team": p["team"],
            "nba_id": p["nba_id"],
            "games": g,
            "pts": round(p["pts_sum"] / g, 1),
            "reb": round(p["reb_sum"] / g, 1),
            "ast": round(p["ast_sum"] / g, 1),
        })

    qualified.sort(key=lambda x: x["pts"], reverse=True)
    top = qualified[:top_n]

    # Batch-fetch full player records to resolve nba_player_id for headshot URLs.
    # Routed through _fetch_data for consistent auth, retry, and rate-limit handling.
    missing_ids = [p["id"] for p in top if p["nba_id"] is None]
    if missing_ids:
        try:
            batch_payload = await _fetch_data(
                "/players",
                params={
                    "per_page": len(missing_ids) + 1,
                    "ids[]": missing_ids,
                },
            )
            id_to_nba: dict[int, int] = {
                pr["id"]: pr["nba_player_id"]
                for pr in (batch_payload.get("data") or [])
                if pr.get("nba_player_id")
            }
            for p in top:
                if p["nba_id"] is None:
                    p["nba_id"] = id_to_nba.get(p["id"])
        except Exception as exc:
            logger.warning("Batch nba_player_id lookup failed: %s", exc)

    logger.info("Trending players: found %d qualified | returning top %d", len(qualified), top_n)
    return top
