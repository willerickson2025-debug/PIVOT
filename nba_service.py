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

    # First fetch the game itself so we know home vs away team IDs reliably
    game_meta = None
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            game_resp = await client.get(
                f"{settings.balldontlie_base_url}/games/{game_id}",
                headers=_build_headers(),
            )
            if game_resp.status_code == 200:
                gd = game_resp.json().get("data", {})
                if gd:
                    game_meta = {
                        "home_team_id": gd.get("home_team", {}).get("id"),
                        "away_team_id": gd.get("visitor_team", {}).get("id"),
                        "home_team_name": gd.get("home_team", {}).get("full_name") or gd.get("home_team", {}).get("name", ""),
                        "away_team_name": gd.get("visitor_team", {}).get("full_name") or gd.get("visitor_team", {}).get("name", ""),
                        "home_team_abbr": gd.get("home_team", {}).get("abbreviation", ""),
                        "away_team_abbr": gd.get("visitor_team", {}).get("abbreviation", ""),
                    }
        except Exception:
            pass

    # Fetch all stats for this game
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            f"{settings.balldontlie_base_url}/stats",
            headers=_build_headers(),
            params={"game_ids[]": game_id, "per_page": 100},
        )
        response.raise_for_status()
        data = response.json()

    raw_stats = data.get("data", [])

    # If we couldn't get game meta, infer home/away from the first two team IDs we see
    if not game_meta and raw_stats:
        seen_teams = []
        for s in raw_stats:
            tid = s.get("team", {}).get("id")
            if tid and tid not in seen_teams:
                seen_teams.append(tid)
            if len(seen_teams) == 2:
                break
        if len(seen_teams) == 2:
            t0 = next((s.get("team", {}) for s in raw_stats if s.get("team", {}).get("id") == seen_teams[0]), {})
            t1 = next((s.get("team", {}) for s in raw_stats if s.get("team", {}).get("id") == seen_teams[1]), {})
            game_meta = {
                "home_team_id": seen_teams[1],
                "away_team_id": seen_teams[0],
                "home_team_name": t1.get("full_name") or t1.get("name", ""),
                "away_team_name": t0.get("full_name") or t0.get("name", ""),
                "home_team_abbr": t1.get("abbreviation", ""),
                "away_team_abbr": t0.get("abbreviation", ""),
            }

    home_players = []
    away_players = []

    for s in raw_stats:
        team_raw = s.get("team", {})
        player_raw = s.get("player", {})
        team_id = team_raw.get("id")

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
            "team_id": team_id,
            "team_abbr": team_raw.get("abbreviation", ""),
        }

        if game_meta and team_id == game_meta.get("home_team_id"):
            home_players.append(stat)
        else:
            away_players.append(stat)

    home_players.sort(key=lambda x: x["pts"], reverse=True)
    away_players.sort(key=lambda x: x["pts"], reverse=True)

    home_team = {"id": game_meta.get("home_team_id"), "name": game_meta.get("home_team_name", ""), "abbreviation": game_meta.get("home_team_abbr", "")} if game_meta else None
    away_team = {"id": game_meta.get("away_team_id"), "name": game_meta.get("away_team_name", ""), "abbreviation": game_meta.get("away_team_abbr", "")} if game_meta else None

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
