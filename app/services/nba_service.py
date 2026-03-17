import httpx
from datetime import date
from typing import Optional
from app.core.config import get_settings
from app.models.schemas import Game, Team, Player, PlayerStats


def _build_headers() -> dict:
    settings = get_settings()
    return {"Authorization": settings.balldontlie_api_key}


def _parse_team(raw: dict) -> Team:
    return Team(
        id=raw["id"],
        name=raw["name"],
        abbreviation=raw["abbreviation"],
        city=raw["city"],
        conference=raw["conference"],
        division=raw["division"],
    )


def _parse_game(raw: dict) -> Game:
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
    team_raw = raw.get("team")
    return Player(
        id=raw["id"],
        first_name=raw["first_name"],
        last_name=raw["last_name"],
        position=raw.get("position"),
        team=_parse_team(team_raw) if team_raw else None,
    )


async def get_games_by_date(target_date: Optional[str] = None) -> list[Game]:
    """Fetch all games for a given date (defaults to today)."""
    settings = get_settings()
    query_date = target_date or str(date.today())

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            f"{settings.balldontlie_base_url}/games",
            headers=_build_headers(),
            params={"dates[]": query_date, "per_page": 100},
        )
        response.raise_for_status()
        data = response.json()

    return [_parse_game(g) for g in data.get("data", [])]


async def get_team_by_id(team_id: int) -> Team:
    """Fetch a single team by ID."""
    settings = get_settings()

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            f"{settings.balldontlie_base_url}/teams/{team_id}",
            headers=_build_headers(),
        )
        response.raise_for_status()
        return _parse_team(response.json()["data"])


async def get_all_teams() -> list[Team]:
    """Fetch all NBA teams."""
    settings = get_settings()

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            f"{settings.balldontlie_base_url}/teams",
            headers=_build_headers(),
            params={"per_page": 100},
        )
        response.raise_for_status()
        data = response.json()

    return [_parse_team(t) for t in data.get("data", [])]


async def search_players(name: str) -> list[Player]:
    """Search players by name."""
    settings = get_settings()

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            f"{settings.balldontlie_base_url}/players",
            headers=_build_headers(),
            params={"search": name, "per_page": 25},
        )
        response.raise_for_status()
        data = response.json()

    return [_parse_player(p) for p in data.get("data", [])]


async def get_game_boxscore(game_id: int) -> dict:
    """Fetch all player stats for a specific game."""
    settings = get_settings()

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            f"{settings.balldontlie_base_url}/stats",
            headers=_build_headers(),
            params={"game_ids[]": game_id, "per_page": 100},
        )
        response.raise_for_status()
        data = response.json()

    home_team = None
    away_team = None
    home_players = []
    away_players = []

    for s in data.get("data", []):
        team_raw = s.get("team", {})
        player_raw = s.get("player", {})
        game_raw = s.get("game", {})

        if not home_team and game_raw:
            home_team_id = game_raw.get("home_team_id")
            visitor_team_id = game_raw.get("visitor_team_id")

        stat = {
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

        if game_raw:
            if team_raw.get("id") == game_raw.get("home_team_id"):
                if not home_team:
                    home_team = {"id": team_raw.get("id"), "name": team_raw.get("full_name", ""), "abbreviation": team_raw.get("abbreviation", "")}
                home_players.append(stat)
            else:
                if not away_team:
                    away_team = {"id": team_raw.get("id"), "name": team_raw.get("full_name", ""), "abbreviation": team_raw.get("abbreviation", "")}
                away_players.append(stat)

    # Sort by points descending
    home_players.sort(key=lambda x: x["pts"], reverse=True)
    away_players.sort(key=lambda x: x["pts"], reverse=True)

    return {
        "game_id": game_id,
        "home_team": home_team,
        "away_team": away_team,
        "home_players": home_players,
        "away_players": away_players,
        "total_players": len(home_players) + len(away_players),
    }


    """Fetch season stats for a player."""
    settings = get_settings()

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            f"{settings.balldontlie_base_url}/stats",
            headers=_build_headers(),
            params={
                "player_ids[]": player_id,
                "seasons[]": season,
                "per_page": 100,
            },
        )
        response.raise_for_status()
        data = response.json()

    results = []
    for s in data.get("data", []):
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
