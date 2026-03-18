import httpx
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional, Any

from app.core.config import get_settings
from app.models.schemas import Game, Team, Player, PlayerStats


# ---------------------------------------------------------------------------
# Internal HTTP Layer
# ---------------------------------------------------------------------------

async def _fetch_data(endpoint: str, params: Optional[dict[str, Any]] = None) -> dict:
    """
    Execute a GET request against the BallDontLie API.

    This helper centralizes:
    - Authentication headers
    - Base URL usage
    - Timeout policy
    - Error propagation
    - JSON decoding

    Returns raw JSON payload as dict.
    """
    settings = get_settings()

    async with httpx.AsyncClient(
        base_url=settings.balldontlie_base_url,
        timeout=10.0,
    ) as client:
        response = await client.get(
            endpoint,
            headers={"Authorization": settings.balldontlie_api_key},
            params=params or {},
        )
        response.raise_for_status()
        return response.json()


# ---------------------------------------------------------------------------
# Parsing Utilities
# ---------------------------------------------------------------------------

def _parse_team(raw: dict) -> Team:
    """Convert raw API team payload into Team schema."""
    return Team(
        id=raw["id"],
        name=raw["name"],
        abbreviation=raw["abbreviation"],
        city=raw["city"],
        conference=raw["conference"],
        division=raw["division"],
    )


def _parse_game(raw: dict) -> Game:
    """Convert raw API game payload into Game schema."""
    return Game(
        id=raw["id"],
        date=raw["date"],
        status=raw["status"],
        period=raw.get("period"),
        time=raw.get("time"),
        home_team=_parse_team(raw["home_team"]),
        visitor_team=_parse_team(raw["visitor_team"]),
        home_team_score=raw.get("home_team_score") or 0,
        visitor_team_score=raw.get("visitor_team_score") or 0,
    )


def _parse_player(raw: dict) -> Player:
    """Convert raw API player payload into Player schema."""
    team_raw = raw.get("team")

    return Player(
        id=raw["id"],
        first_name=raw["first_name"],
        last_name=raw["last_name"],
        position=raw.get("position"),
        team=_parse_team(team_raw) if team_raw else None,
    )


# ---------------------------------------------------------------------------
# Public Service Layer
# ---------------------------------------------------------------------------

async def get_games_by_date(target_date: Optional[str] = None) -> list[Game]:
    """
    Fetch all NBA games scheduled for a specific date.

    If no date is provided, defaults to current US Central Time.
    """
    query_date = (
        target_date
        or datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d")
    )

    payload = await _fetch_data(
        "/games",
        params={"dates[]": query_date, "per_page": 100},
    )

    return [_parse_game(g) for g in payload.get("data", [])]


async def get_team_by_id(team_id: int) -> Team:
    """Retrieve a single team by its unique ID."""
    payload = await _fetch_data(f"/teams/{team_id}")
    return _parse_team(payload.get("data", {}))


async def get_all_teams() -> list[Team]:
    """Retrieve all NBA teams."""
    payload = await _fetch_data("/teams", params={"per_page": 100})
    return [_parse_team(t) for t in payload.get("data", [])]


async def search_players(name: str) -> list[Player]:
    """Search players by full or partial name."""
    payload = await _fetch_data(
        "/players",
        params={"search": name, "per_page": 25},
    )

    return [_parse_player(p) for p in payload.get("data", [])]


# ---------------------------------------------------------------------------
# Box Score Aggregation
# ---------------------------------------------------------------------------

async def get_game_boxscore(game_id: int) -> dict:
    """
    Retrieve full box score data for a game.

    Guarantees a stable response shape even if stats are missing.
    """

    # --- Game metadata (guaranteed) ---
    game_payload = await _fetch_data(f"/games/{game_id}")
    game_raw = game_payload.get("data", {})

    # --- Player statistics ---
    stats_payload = await _fetch_data(
        "/stats",
        params={"game_ids[]": game_id, "per_page": 100},
    )

    # -----------------------------------------------------------------------
    # Game Info
    # -----------------------------------------------------------------------

    game_info = {
        "id": game_raw.get("id"),
        "date": game_raw.get("date"),
        "status": game_raw.get("status"),
        "period": game_raw.get("period"),
        "time": game_raw.get("time"),
        "home_team_score": game_raw.get("home_team_score") or 0,
        "away_team_score": game_raw.get("visitor_team_score") or 0,
    }

    # -----------------------------------------------------------------------
    # Team Info
    # -----------------------------------------------------------------------

    home_raw = game_raw.get("home_team", {})
    away_raw = game_raw.get("visitor_team", {})

    home_team = {
        "id": home_raw.get("id"),
        "name": home_raw.get("full_name", ""),
        "abbreviation": home_raw.get("abbreviation", ""),
        "score": game_raw.get("home_team_score") or 0,
    }

    away_team = {
        "id": away_raw.get("id"),
        "name": away_raw.get("full_name", ""),
        "abbreviation": away_raw.get("abbreviation", ""),
        "score": game_raw.get("visitor_team_score") or 0,
    }

    home_players: list[dict] = []
    away_players: list[dict] = []

    # -----------------------------------------------------------------------
    # Player Parsing
    # -----------------------------------------------------------------------

    for s in stats_payload.get("data", []):
        player_raw = s.get("player", {})
        team_raw = s.get("team", {})

        stat_line = {
            "player": f"{player_raw.get('first_name', '')} {player_raw.get('last_name', '')}".strip(),
            "pos": player_raw.get("position") or "—",
            "min": s.get("min") or "0",
            "pts": s.get("pts") or 0,
            "reb": s.get("reb") or 0,
            "ast": s.get("ast") or 0,
            "stl": s.get("stl") or 0,
            "blk": s.get("blk") or 0,
            "fg": f"{s.get('fgm') or 0}-{s.get('fga') or 0}",
            "fg3": f"{s.get('fg3m') or 0}-{s.get('fg3a') or 0}",
            "ft": f"{s.get('ftm') or 0}-{s.get('fta') or 0}",
            "to": s.get("turnover") or 0,
            "pf": s.get("pf") or 0,
            "team_id": team_raw.get("id"),
            "team_abbr": team_raw.get("abbreviation", ""),
        }

        if team_raw.get("id") == home_team["id"]:
            home_players.append(stat_line)
        elif team_raw.get("id") == away_team["id"]:
            away_players.append(stat_line)

    # Sort by scoring output
    home_players.sort(key=lambda x: x["pts"], reverse=True)
    away_players.sort(key=lambda x: x["pts"], reverse=True)

    return {
        "game_id": game_id,
        "game_info": game_info,
        "home_team": home_team,
        "away_team": away_team,
        "home_players": home_players,
        "away_players": away_players,
        "total_players": len(home_players) + len(away_players),
    }


# ---------------------------------------------------------------------------
# Player Season Stats
# ---------------------------------------------------------------------------

async def get_player_stats(player_id: int, season: int = 2025) -> list[PlayerStats]:
    """Retrieve season game logs for a player."""
    payload = await _fetch_data(
        "/stats",
        params={
            "player_ids[]": player_id,
            "seasons[]": season,
            "per_page": 100,
        },
    )

    results: list[PlayerStats] = []

    for s in payload.get("data", []):
        player_raw = s.get("player", {})

        results.append(
            PlayerStats(
                player=_parse_player(player_raw),
                game_id=s.get("game", {}).get("id", 0),
                points=s.get("pts") or 0,
                rebounds=s.get("reb") or 0,
                assists=s.get("ast") or 0,
                steals=s.get("stl") or 0,
                blocks=s.get("blk") or 0,
                minutes=s.get("min"),
                fg_pct=s.get("fg_pct"),
                fg3_pct=s.get("fg3_pct"),
                ft_pct=s.get("ft_pct"),
            )
        )

    return results