"""
nba_service.py
==============
Data access layer for the BallDontLie NBA API.

Responsibilities
----------------
- All HTTP communication with the BallDontLie REST API
- Domain object hydration (raw dict → typed schema)
- Retry / timeout / error-propagation policy
- Box score aggregation and player-stat retrieval

This module is intentionally free of business logic. Analysis logic lives in
analysis_service.py; Claude integration lives in claude_service.py.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx

from app.core.config import get_settings
from app.core.http_client import GlobalHTTPClient
from app.models.schemas import Game, Player, PlayerStats, Team

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_PER_PAGE: int = 100
_REQUEST_TIMEOUT: float = 12.0          # seconds per attempt
_MAX_RETRIES: int = 3                   # total attempts before raising
_RETRY_BACKOFF_BASE: float = 0.5        # seconds; multiplied by attempt index
_DEFAULT_SEASON: int = 2025
_CENTRAL_TZ: str = "America/Chicago"

# NBA.com player ID lookup by full name for headshot CDN URLs.
# Only include verified IDs — never add unverified entries.
_NBA_ID_BY_NAME: dict[str, int] = {
    # Only NBA.com player IDs that have been individually verified.
    # Do NOT add entries from memory — a wrong ID shows the wrong player photo.
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
    - Base URL & authentication header injection
    - Per-request timeout enforcement
    - Exponential-ish back-off retry on transient network/server errors
    - Structured logging of every outbound request and its outcome
    - JSON decoding with a meaningful error on malformed payloads

    Parameters
    ----------
    endpoint:
        Path relative to the configured base URL, e.g. ``"/games"``.
    params:
        Optional query-string parameters forwarded verbatim to httpx.

    Returns
    -------
    dict
        Parsed JSON payload from the API response body.

    Raises
    ------
    httpx.HTTPStatusError
        Propagated after all retries are exhausted for 4xx/5xx responses.
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
            # Allow passing an absolute URL (useful for the /nba/v1 namespace)
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
            # 4xx errors are not retried — retrying a 404 or 422 won't help.
            # 5xx errors could be retried, but we surface them immediately so
            # callers can make that decision at a higher layer.
            logger.error(
                "BallDontLie HTTP error | endpoint=%s status=%d body=%s",
                endpoint,
                exc.response.status_code,
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


def _has_real_minutes(m: Any) -> bool:
    """Return True only when a player actually played (minutes > 0).

    BDL represents DNPs as None, '0', '0:00', or '00' — all map to False.
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

    The nested ``team`` object is optional — players without a current team
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
        "pos": player_raw.get("position") or "—",
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
# Public Service Layer — Queries
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

    logger.info("Fetching games for date=%s", query_date)

    payload = await _fetch_data(
        "/games",
        params={"dates[]": query_date, "per_page": _DEFAULT_PER_PAGE},
    )

    games = [_parse_game(g) for g in payload.get("data") or []]
    logger.info("Found %d game(s) for %s", len(games), query_date)
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
    logger.debug("Fetching all teams")
    payload = await _fetch_data("/teams", params={"per_page": _DEFAULT_PER_PAGE})
    teams = [_parse_team(t) for t in payload.get("data") or []]
    logger.debug("Received %d teams", len(teams))
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
) -> list[Player]:
    """
    Search NBA players by name.

    Parameters
    ----------
    name:
        Full or partial player name — used as the ``search=`` fallback and
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

    Search priority
    ---------------
    1. Both first_name + last_name → ``?first_name=…&last_name=…``
    2. last_name only              → ``?last_name=…``
    3. first_name only             → ``?first_name=…``
    4. Neither                     → ``?search=name``  (fuzzy fallback)

    Returns
    -------
    list[Player]
        Matching players, ordered by API relevance. Empty list on no match.
    """
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

    logger.debug("Searching players | %s", mode)
    payload = await _fetch_data("/players", params=params)
    players = [_parse_player(p) for p in payload.get("data") or []]
    logger.debug("Player search (%s) returned %d result(s)", mode, len(players))
    return players


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
    Return basic player info for a team's current roster, resolved from
    the team's abbreviation (e.g. 'LAL', 'BOS').
    """
    teams_payload = await _fetch_data("/teams", params={"per_page": 100})
    team_id: int | None = None
    for t in teams_payload.get("data") or []:
        if (t.get("abbreviation") or "").upper() == abbr.upper():
            team_id = int(t["id"])
            break
    if team_id is None:
        raise ValueError(f"No team found with abbreviation '{abbr}'")

    players_payload = await _fetch_data("/players", params={"team_ids[]": team_id, "per_page": 100})
    result: list[dict] = []
    for p in players_payload.get("data") or []:
        pid = p.get("id")
        first = (p.get("first_name") or "").strip()
        last = (p.get("last_name") or "").strip()
        if not pid or not (first or last):
            continue
        result.append({
            "id": pid,
            "first_name": first,
            "last_name": last,
            "position": (p.get("position") or "").strip(),
        })
    result.sort(key=lambda x: x.get("last_name", ""))
    logger.debug("Roster for abbr=%s: %d players", abbr, len(result))
    return result


# ---------------------------------------------------------------------------
# Contracts, Advanced Stats, Lineups
# ---------------------------------------------------------------------------


def _parse_contract(raw: dict[str, Any]) -> Contract:  # type: ignore[name-defined]
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


async def get_player_contracts(player_id: int, seasons: Optional[list[int]] = None, per_page: int = 25, cursor: int | None = None) -> list["Contract"]:  # type: ignore[name-defined]
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


def _parse_advanced_stat(raw: dict[str, Any]) -> AdvancedStat:  # type: ignore[name-defined]
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


async def get_advanced_stats(seasons: Optional[list[int]] = None, player_ids: Optional[list[int]] = None, per_page: int = 25, cursor: int | None = None) -> list["AdvancedStat"]:  # type: ignore[name-defined]
    """Fetch advanced stats from the NBA namespace.

    Note: advanced stats are served under the `/nba/v1` namespace on the
    BallDontLie host; we build the absolute endpoint accordingly.
    """
    params: dict[str, Any] = {"per_page": per_page}
    if seasons:
        params.update({"seasons[]": seasons})
    if player_ids:
        params.update({"player_ids[]": player_ids})
    if cursor:
        params["cursor"] = cursor

    settings = get_settings()
    endpoint = settings.balldontlie_base_url.replace("/v1", "/nba/v1") + "/stats/advanced"
    payload = await _fetch_data(endpoint, params=params)
    data = payload.get("data") or []
    stats = [_parse_advanced_stat(s) for s in data]
    logger.debug("Advanced stats query returned %d rows", len(stats))
    return stats


def _parse_lineup(raw: dict[str, Any]) -> LineupEntry:  # type: ignore[name-defined]
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

    Fires two concurrent API requests — one for game metadata and one for
    per-player statistics — and merges them into a single normalised payload.

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

    # Sort by points descending — highest scorers first in each list.
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

    return {
        "game_id": game_id,
        "game_info": game_info,
        "home_team": home_team,
        "away_team": away_team,
        "home_players": home_players,
        "away_players": away_players,
        "total_players": total,
    }


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
    home_team, away_team,                       — team objects
    home_q_scores, away_q_scores,               — list[int|None] per period
    home_timeouts, away_timeouts,               — int remaining
    home_in_bonus, away_in_bonus,               — bool
    score_diff,                                 — int (home − away)
    momentum,                                   — "home" | "away" | "even"
    current_run,                                — dict with team/points/periods
    quarter_summary,                            — list of period deltas
    top_performers,                             — top 3 players by impact
    foul_trouble,                               — players with ≥3 PF
    hot_shooters,                               — players on 50%+ FG with ≥6 FGA
    cold_shooters,                              — players on ≤25% FG with ≥6 FGA
    high_turnover_players,                      — players with ≥4 TO
    home_players, away_players,                 — full stat lines
    is_live,                                    — bool
    status,                                     — raw status string
    """
    logger.info("Fetching live game state | game_id=%d", game_id)

    game_payload, stats_payload = await asyncio.gather(
        _fetch_data(f"/games/{game_id}"),
        _fetch_data("/stats", params={"game_ids[]": game_id, "per_page": _DEFAULT_PER_PAGE}),
    )

    game_raw: dict[str, Any] = game_payload.get("data") or {}
    stats_raw: list[dict] = stats_payload.get("data") or []

    # ── Basic game fields ──────────────────────────────────────────────────
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

    # ── Period label ──────────────────────────────────────────────────────
    if period == 0:
        period_label = "PRE-GAME"
    elif period <= 4:
        period_label = f"Q{period}"
    elif period == 5:
        period_label = "OT"
    else:
        period_label = f"OT{period - 4}"

    # ── Quarter scores ────────────────────────────────────────────────────
    def _q_scores(prefix: str) -> list[int | None]:
        quarters = []
        for q in ["q1", "q2", "q3", "q4", "ot1", "ot2", "ot3"]:
            val = game_raw.get(f"{prefix}_{q}")
            quarters.append(int(val) if val is not None else None)
        return quarters

    home_q = _q_scores("home")
    away_q = _q_scores("visitor")

    # ── Quarter summary (deltas per period) ───────────────────────────────
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

    # ── Momentum: which team won the most recent two completed periods ─────
    momentum = "even"
    if len(quarter_summary) >= 2:
        recent = quarter_summary[-2:]
        home_won = sum(1 for q in recent if q["winner"] == "home")
        away_won = sum(1 for q in recent if q["winner"] == "away")
        if home_won > away_won:
            momentum = "home"
        elif away_won > home_won:
            momentum = "away"

    # ── Current run: score in the most recent completed period ─────────────
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

    # ── Player stat lines ─────────────────────────────────────────────────
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

    # ── Derived player flags ──────────────────────────────────────────────
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

async def get_player_stats(player_id: int, season: int = _DEFAULT_SEASON) -> list[PlayerStats]:
    """
    Retrieve per-game stat logs for a player for a given season.

    Results are sorted chronologically (ascending game ID) so callers can
    trivially slice ``[-10:]`` for a recent-form window.

    Parameters
    ----------
    player_id:
        BallDontLie internal player identifier.
    season:
        NBA season year (the year the season *starts* in; 2024 = 2024–25).

    Returns
    -------
    list[PlayerStats]
        One entry per game played. Empty list if no stats are found.
    """
    logger.debug("Fetching player stats | player_id=%d season=%d", player_id, season)

    results: list[PlayerStats] = []
    cursor: int | None = None

    for _page in range(10):  # safety cap — 82 games / 100 per page = 1 page normally
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
                )
            )

        next_cursor = (payload.get("meta") or {}).get("next_cursor")
        if not next_cursor or not page_data:
            break
        cursor = next_cursor

    # Strip DNP entries — BDL returns '0', '0:00', or '00' for non-playing rows.
    # Including them collapses averages (e.g. 22 DNP rows drops LeBron from ~23 PPG to 15).
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


async def get_recent_stats(player_id: int, season: int = _DEFAULT_SEASON, n: int = 10) -> list[PlayerStats]:
    """
    Fetch the N most-recent game logs for a player this season.

    Uses per_page=N sorted by most recent so we get true last-N games,
    not a slice of a larger sorted list that may include stale preseason entries.
    """
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
            )
        )
    results = [r for r in results if _has_real_minutes(r.minutes)]
    results.sort(key=lambda x: x.game_id, reverse=True)
    return results


async def get_season_averages(player_id: int, season: int = _DEFAULT_SEASON) -> dict[str, Any]:
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
    logger.debug(
        "Fetching season averages | player_id=%d season=%d", player_id, season
    )

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
        return averages

    except Exception as exc:
        # Season averages are supplementary data. A failure here degrades
        # gracefully — callers fall back to computing averages from game logs.
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

    # Batch-fetch full player records to get nba_player_id (not present in /stats payloads).
    missing_ids = [p["id"] for p in top if p["nba_id"] is None]
    if missing_ids:
        try:
            settings = get_settings()
            client = GlobalHTTPClient.get_client()
            resp = await client.get(
                settings.balldontlie_base_url + "/players",
                headers={"Authorization": settings.balldontlie_api_key},
                params=[("per_page", len(missing_ids) + 1)] + [("ids[]", pid) for pid in missing_ids],
                timeout=_REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            id_to_nba: dict[int, int] = {
                pr["id"]: pr["nba_player_id"]
                for pr in (resp.json().get("data") or [])
                if pr.get("nba_player_id")
            }
            for p in top:
                if p["nba_id"] is None:
                    p["nba_id"] = id_to_nba.get(p["id"])
        except Exception as exc:
            logger.warning("Batch nba_player_id lookup failed: %s", exc)

    logger.info("Trending players: found %d qualified | returning top %d", len(qualified), top_n)
    return top
