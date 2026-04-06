"""
standings_service.py
====================
Calculates real W-L standings for all 30 NBA teams by tallying completed
games from the BallDontLie /v1/games endpoint.

Cache policy: results are stored in-memory for 6 hours. The cache is a single
module-level dict so it survives across requests within the same process.

BallDontLie pagination: the API returns max 100 games per page. We walk every
page for the season until the cursor is exhausted, then tally results.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.services.nba_service import _fetch_data

logger = logging.getLogger(__name__)

_SEASON = 2025
_CACHE_TTL_SECONDS = 6 * 3600  # 6 hours

_cache: dict[str, Any] = {
    "data": None,
    "expires_at": 0.0,
}

# Conference membership — BallDontLie team abbreviations
_EAST = {"ATL", "BOS", "BKN", "CHA", "CHI", "CLE", "DET", "IND",
         "MIA", "MIL", "NYK", "ORL", "PHI", "TOR", "WAS"}
_WEST = {"DAL", "DEN", "GSW", "HOU", "LAC", "LAL", "MEM", "MIN",
         "NOP", "OKC", "PHX", "POR", "SAC", "SAS", "UTA"}

# Division membership
_DIVISIONS: dict[str, str] = {
    "ATL": "Southeast", "CHA": "Southeast", "MIA": "Southeast",
    "ORL": "Southeast", "WAS": "Southeast",
    "BOS": "Atlantic",  "BKN": "Atlantic",  "NYK": "Atlantic",
    "PHI": "Atlantic",  "TOR": "Atlantic",
    "CHI": "Central",   "CLE": "Central",   "DET": "Central",
    "IND": "Central",   "MIL": "Central",
    "DEN": "Northwest", "MIN": "Northwest", "OKC": "Northwest",
    "POR": "Northwest", "UTA": "Northwest",
    "GSW": "Pacific",   "LAC": "Pacific",   "LAL": "Pacific",
    "PHX": "Pacific",   "SAC": "Pacific",
    "DAL": "Southwest", "HOU": "Southwest", "MEM": "Southwest",
    "NOP": "Southwest", "SAS": "Southwest",
}

# Full team names by abbreviation
_TEAM_NAMES: dict[str, str] = {
    "ATL": "Hawks",       "BOS": "Celtics",      "BKN": "Nets",
    "CHA": "Hornets",     "CHI": "Bulls",        "CLE": "Cavaliers",
    "DAL": "Mavericks",   "DEN": "Nuggets",      "DET": "Pistons",
    "GSW": "Warriors",    "HOU": "Rockets",      "IND": "Pacers",
    "LAC": "Clippers",    "LAL": "Lakers",       "MEM": "Grizzlies",
    "MIA": "Heat",        "MIL": "Bucks",        "MIN": "Timberwolves",
    "NOP": "Pelicans",    "NYK": "Knicks",       "OKC": "Thunder",
    "ORL": "Magic",       "PHI": "76ers",        "PHX": "Suns",
    "POR": "Blazers",     "SAC": "Kings",        "SAS": "Spurs",
    "TOR": "Raptors",     "UTA": "Jazz",         "WAS": "Wizards",
}


def _empty_record(abbr: str) -> dict[str, Any]:
    conf = "East" if abbr in _EAST else "West"
    return {
        "abbr": abbr,
        "name": _TEAM_NAMES.get(abbr, abbr),
        "conference": conf,
        "division": _DIVISIONS.get(abbr, ""),
        "wins": 0,
        "losses": 0,
        "pct": 0.0,
        "gb": 0.0,
    }


async def _fetch_all_games() -> list[dict[str, Any]]:
    """Paginate through all completed season games. Returns raw game dicts."""
    all_games: list[dict[str, Any]] = []
    cursor: int | None = None
    page = 0

    while True:
        page += 1
        params: dict[str, Any] = {
            "seasons[]": _SEASON,
            "per_page": 100,
        }
        if cursor is not None:
            params["cursor"] = cursor

        try:
            payload = await _fetch_data("/games", params)
        except Exception as exc:
            logger.error("standings: failed to fetch games page %d: %s", page, exc)
            break

        games = payload.get("data", [])
        all_games.extend(games)

        meta = payload.get("meta", {})
        next_cursor = meta.get("next_cursor")

        logger.debug(
            "standings: fetched page %d (%d games, cursor=%s → %s)",
            page, len(games), cursor, next_cursor,
        )

        if not next_cursor or not games:
            break

        cursor = next_cursor
        # Brief pause to be polite to the API
        await asyncio.sleep(0.1)

    return all_games


def _tally(games: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Tally W-L from a list of raw game dicts. Only counts final games."""
    records: dict[str, dict[str, Any]] = {
        abbr: _empty_record(abbr) for abbr in _TEAM_NAMES
    }

    for g in games:
        status = (g.get("status") or "").lower()
        # BallDontLie marks finished games as "Final" (or "Final/OT" etc.)
        if "final" not in status:
            continue

        hs = g.get("home_team_score") or 0
        vs = g.get("visitor_team_score") or 0
        if not hs and not vs:
            continue

        home = (g.get("home_team") or {}).get("abbreviation", "")
        visitor = (g.get("visitor_team") or {}).get("abbreviation", "")

        # Normalise OKC which BallDontLie sometimes returns as "OKL"
        home = "OKC" if home == "OKL" else home
        visitor = "OKC" if visitor == "OKL" else visitor

        if home not in records or visitor not in records:
            continue

        if hs > vs:
            records[home]["wins"] += 1
            records[visitor]["losses"] += 1
        else:
            records[visitor]["wins"] += 1
            records[home]["losses"] += 1

    # Calculate win pct
    for r in records.values():
        gp = r["wins"] + r["losses"]
        r["pct"] = round(r["wins"] / gp, 3) if gp else 0.0

    return records


def _compute_gb(leader_wins: int, leader_losses: int, wins: int, losses: int) -> float:
    return round(((leader_wins - wins) + (losses - leader_losses)) / 2, 1)


def _build_conference(records: dict[str, dict[str, Any]], conf: str) -> list[dict[str, Any]]:
    teams = [r for r in records.values() if r["conference"] == conf]
    teams.sort(key=lambda x: (-x["wins"], x["losses"]))

    if teams:
        lw, ll = teams[0]["wins"], teams[0]["losses"]
        for i, t in enumerate(teams):
            t["seed"] = i + 1
            t["gb"] = 0.0 if i == 0 else _compute_gb(lw, ll, t["wins"], t["losses"])
            t["rec"] = f"{t['wins']}-{t['losses']}"

    return teams


def _build_league(records: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    teams = list(records.values())
    teams.sort(key=lambda x: (-x["wins"], x["losses"]))
    for i, t in enumerate(teams):
        t["rank"] = i + 1
    return teams


async def get_standings() -> dict[str, Any]:
    """
    Return standings dict. Serves from cache if still fresh.
    Structure:
      {
        "east": [ {abbr, name, seed, wins, losses, pct, gb, rec, conference, division}, ... ],
        "west": [ ... ],
        "league": [ {rank, ...}, ... ],
        "cached_at": <unix timestamp>,
        "games_counted": <int>,
      }
    """
    now = time.time()
    if _cache["data"] is not None and now < _cache["expires_at"]:
        logger.debug("standings: serving from cache (expires in %.0fs)", _cache["expires_at"] - now)
        return _cache["data"]

    logger.info("standings: cache miss — fetching all season games")
    games = await _fetch_all_games()

    completed = [g for g in games if "final" in (g.get("status") or "").lower()]
    logger.info("standings: %d total games fetched, %d completed", len(games), len(completed))

    records = _tally(completed)
    east = _build_conference(records, "East")
    west = _build_conference(records, "West")
    league = _build_league(records)

    result: dict[str, Any] = {
        "east": east,
        "west": west,
        "league": league,
        "cached_at": int(now),
        "games_counted": len(completed),
    }

    _cache["data"] = result
    _cache["expires_at"] = now + _CACHE_TTL_SECONDS
    logger.info("standings: cache populated (%d teams, expires in 6h)", len(league))

    return result
