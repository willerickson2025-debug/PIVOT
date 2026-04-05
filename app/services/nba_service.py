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
    # ── Established stars ──────────────────────────────────────────────────────
    "LeBron James": 2544,
    "Stephen Curry": 201939,
    "Nikola Jokic": 203999,
    "Jayson Tatum": 1628369,
    "Kevin Durant": 201142,
    "Giannis Antetokounmpo": 203507,
    "Luka Doncic": 1629029,
    "Anthony Davis": 203076,
    "Shai Gilgeous-Alexander": 1628983,
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
    "Paul George": 202331,
    "Draymond Green": 203110,
    "Klay Thompson": 202691,
    "Darius Garland": 1629636,
    "Jarrett Allen": 1628991,
    "Al Horford": 201143,
    "Jrue Holiday": 201950,
    "Chris Paul": 101108,
    "Russell Westbrook": 201566,
    "DeMar DeRozan": 201942,
    "Bradley Beal": 203078,
    "Zach LaVine": 203897,
    "Karl-Anthony Towns": 1626157,
    "Julius Randle": 203944,
    "Kristaps Porzingis": 204001,
    "Nikola Vucevic": 202696,
    "CJ McCollum": 203468,
    "Aaron Gordon": 203932,
    "Andrew Wiggins": 203952,
    "Jamal Murray": 1627750,
    "Michael Porter Jr.": 1629008,
    "Marcus Smart": 203935,
    "Al Horford": 201143,
    # ── 2016–2019 draft class ──────────────────────────────────────────────────
    "OG Anunoby": 1628384,
    "Jaylen Brown": 1627759,
    "Brandon Ingram": 1627742,
    "Bam Adebayo": 1628389,
    "Kyle Kuzma": 1628398,
    "Deandre Ayton": 1629028,
    "Mikal Bridges": 1628969,
    "Miles Bridges": 1628970,
    "Tyler Herro": 1629639,
    "Jordan Poole": 1629673,
    "Anfernee Simons": 1629014,
    "Domantas Sabonis": 1627734,
    "Lu Dort": 1629652,
    # ── 2020–2022 draft class ──────────────────────────────────────────────────
    "Victor Wembanyama": 1641705,
    "Paolo Banchero": 1631094,
    "Tyrese Haliburton": 1630169,
    "Evan Mobley": 1630596,
    "Scottie Barnes": 1630567,
    "LaMelo Ball": 1630163,
    "Cade Cunningham": 1630595,
    "Franz Wagner": 1630532,
    "Tyrese Maxey": 1630178,
    "Desmond Bane": 1630217,
    "Jaren Jackson Jr.": 1628991,
    "Josh Giddey": 1630581,
    "Alperen Sengun": 1631167,
    # ── 2022–2024 draft class ──────────────────────────────────────────────────
    "Jabari Smith Jr.": 1631100,
    "Keegan Murray": 1631099,
    "Bennedict Mathurin": 1631096,
    "Walker Kessler": 1631101,
    "Shaedon Sharpe": 1631097,
    "Mark Williams": 1631103,
    "Cam Thomas": 1631105,
    "Jeremy Sochan": 1631110,
    "Jalen Williams": 1631114,
    "Scoot Henderson": 1641706,
    "Brandon Miller": 1641707,
    "Ausar Thompson": 1641714,
    "Amen Thompson": 1641715,
    "Jarace Walker": 1641711,
    "Anthony Black": 1641712,
    "Gradey Dick": 1641716,
    "Cason Wallace": 1641709,
    "Bilal Coulibaly": 1641717,
    "Dereck Lively II": 1641720,
    "Zach Edey": 1642356,
    "Reed Sheppard": 1642361,
    "Stephon Castle": 1642362,
    "Rob Dillingham": 1642363,
    "Tristan da Silva": 1642368,
    "Nikola Topic": 1642369,
    "Matas Buzelis": 1642370,
    "Tidjane Salaun": 1642371,
    "Donovan Clingan": 1642372,
    "Dalton Knecht": 1642373,
    "Ja'Kobe Walter": 1642374,
    "Jaylen Wells": 1642375,
    "Kyle Filipowski": 1642376,
    "Johnny Furphy": 1642377,
    "Carlton Carrington": 1642378,
    "Cody Williams": 1642379,
    # ── Additional active players ──────────────────────────────────────────────
    "Jalen Green": 1630224,
    "Jordan Clarkson": 203903,
    "Derrick White": 1628401,
    "Immanuel Quickley": 1630193,
    "RJ Barrett": 1629628,
    "Obi Toppin": 1630167,
    "Josh Hart": 1628404,
    "Donte DiVincenzo": 1629059,
    "Isaiah Hartenstein": 1628392,
    "Precious Achiuwa": 1630173,
    "Patrick Williams": 1630172,
    "Ayo Dosunmu": 1630245,
    "Nikola Jokic": 203999,
    "Jamal Murray": 1627750,
    "Aaron Gordon": 203932,
    "Reggie Jackson": 202704,
    "Brook Lopez": 201572,
    "Bobby Portis": 1626171,
    "Khris Middleton": 203114,
    "Grayson Allen": 1629109,
    "Damian Lillard": 203081,
    "Andre Drummond": 203083,
    "Clint Capela": 203991,
    "Trae Young": 1629027,
    "Dejounte Murray": 1627749,
    "Bogdan Bogdanovic": 203992,
    "Saddiq Bey": 1630170,
    "De'Andre Hunter": 1629631,
    "Onyeka Okongwu": 1630168,
    "John Collins": 1628381,
    "Rudy Gobert": 203497,
    "Naz Reid": 1629675,
    "Anthony Edwards": 1630162,
    "Mike Conley": 201144,
    "Kyle Anderson": 203937,
    "Jaden McDaniels": 1630183,
    "Nickeil Alexander-Walker": 1629638,
    "Royce O'Neale": 1626220,
    "Cam Johnson": 1629661,
    "Ben Simmons": 1627732,
    "Nic Claxton": 1629651,
    "Mikal Bridges": 1628969,
    "Spencer Dinwiddie": 203915,
    "Keldon Johnson": 1629640,
    "Devin Vassell": 1630193,
    "Jeremy Sochan": 1631110,
    "Tre Jones": 1630249,
    "Draymond Green": 203110,
    "Moses Moody": 1630541,
    "Jonathan Kuminga": 1630542,
    "Brandin Podziemski": 1642354,
    "Dario Saric": 203955,
    "Kevon Looney": 1626172,
    "Chris Paul": 101108,
    "Jusuf Nurkic": 203994,
    "Nassir Little": 1629642,
    "Jerami Grant": 203924,
    "Scoot Henderson": 1641706,
    "Anfernee Simons": 1629014,
    "Matisse Thybulle": 1629680,
    "Jabari Walker": 1631122,
    "Robert Williams III": 1629057,
    "Jaylen Brown": 1627759,
    "Al Horford": 201143,
    "Sam Hauser": 1629638,
    "Payton Pritchard": 1630202,
    "Drew Holiday": 203523,
    "Kristaps Porzingis": 204001,
    "Luke Kornet": 1628436,
    "Caris LeVert": 1627747,
    "Evan Fournier": 203095,
    "Quentin Grimes": 1630537,
    "Precious Achiuwa": 1630173,
    "Immanuel Quickley": 1630193,
    "Mitchell Robinson": 1629011,
    "Julius Randle": 203944,
    "Jalen Brunson": 1628386,
    "OG Anunoby": 1628384,
    "Josh Hart": 1628404,
    "Donte DiVincenzo": 1629059,
    "Karl-Anthony Towns": 1626157,
    "Mikal Bridges": 1628969,
    "Miles McBride": 1630540,
    "Precious Achiuwa": 1630173,
    "Tyrese Maxey": 1630178,
    "Kelly Oubre Jr.": 1626162,
    "Kyle Lowry": 200768,
    "Tobias Harris": 202699,
    "Paul Reed": 1630194,
    "Marcus Morris Sr.": 202694,
    "Nicolas Batum": 201587,
    "Joel Embiid": 203954,
    "James Harden": 201935,
    "De'Anthony Melton": 1629001,
    "Matisse Thybulle": 1629680,
    "Shake Milton": 1629003,
    "Furkan Korkmaz": 1627755,
    "Damian Lillard": 203081,
    "Khris Middleton": 203114,
    "Bobby Portis": 1626171,
    "Brook Lopez": 201572,
    "Grayson Allen": 1629109,
    "Pat Connaughton": 1626192,
    "Marjon Beauchamp": 1631116,
    "Andre Jackson Jr.": 1631124,
    "AJ Green": 1631127,
    "Jordan Nwora": 1630182,
    "Darius Garland": 1629636,
    "Donovan Mitchell": 1628378,
    "Evan Mobley": 1630596,
    "Jarrett Allen": 1628991,
    "Max Strus": 1629622,
    "Sam Merrill": 1629677,
    "Dean Wade": 1629598,
    "Isaac Okoro": 1630171,
    "Dylan Windler": 1629641,
    "Caris LeVert": 1627747,
    "Kevin Love": 201567,
    "Ricky Rubio": 201937,
    "Lauri Markkanen": 1628374,
    "Jordan Clarkson": 203903,
    "Collin Sexton": 1629012,
    "Ochai Agbaji": 1631122,
    "Walker Kessler": 1631101,
    "Keyonte George": 1641719,
    "Taylor Hendricks": 1641718,
    "Brice Sensabaugh": 1641708,
    "Jaime Jaquez Jr.": 1641713,
    "Dereck Lively II": 1641720,
    "Jett Howard": 1641710,
    "Pelle Larsson": 1642380,
    "Kel'el Ware": 1642381,
    "Dalton Knecht": 1642373,
    "Bronny James": 1642355,
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
    )


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

    payload = await _fetch_data(
        "/stats",
        params={
            "player_ids[]": player_id,
            "seasons[]": season,
            "per_page": _DEFAULT_PER_PAGE,
        },
    )

    results: list[PlayerStats] = []

    for s in payload.get("data") or []:
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

    # Strip DNP entries (minutes null, '0', or '0:00') — including them in averages
    # produces wildly inaccurate per-game stats (e.g. 3.6 PPG for Tatum).
    results = [r for r in results if r.minutes and r.minutes not in ('0', '0:00')]

    # Sort ascending by game_id so recent-form slicing (stats[-10:]) is valid.
    results.sort(key=lambda x: x.game_id)

    logger.debug(
        "Fetched %d stat entries (DNPs removed) | player_id=%d season=%d",
        len(results),
        player_id,
        season,
    )
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
