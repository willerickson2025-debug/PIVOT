import asyncio
import json
import xml.etree.ElementTree as ET

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Body, Response, Request
from fastapi.responses import StreamingResponse
from typing import Optional, List

from app.services import nba_service, claude_service, analysis_service, agent_service
from app.services import standings_service
from app.models.schemas import (
    AnalysisRequest,
    AnalysisResponse,
    GameAnalysisResponse,
)
from app.utils.helpers import validate_date_string
from app.core.limiter import limiter
from app.core.security import verify_api_key

router = APIRouter()

# ── Rate limit tiers ──────────────────────────────────────────────────────────
# Claude-backed endpoints: 30 req/min per IP — prevents bill-burning abuse
_CLAUDE_LIMIT = "30/minute"
# Data-only endpoints: 120 req/min per IP — generous for polling frontends
_DATA_LIMIT = "120/minute"


# ── Headshot proxy ───────────────────────────────────────────────────────────

@router.get("/headshot/{nba_id}")
async def headshot_proxy(nba_id: int, response: Response):
    """Proxy NBA.com headshot images to avoid browser cross-origin blocks."""
    url = f"https://cdn.nba.com/headshots/nba/latest/1040x760/{nba_id}.png"
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            raise HTTPException(status_code=404, detail="Headshot not found")
        response.headers["Cache-Control"] = "public, max-age=86400"
        return Response(content=r.content, media_type="image/png")
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail="Headshot fetch failed")


# ── Health ────────────────────────────────────────────────────────────────────

@router.get("/health")
async def health():
    from app.core.config import get_settings
    settings = get_settings()
    return {"status": "ok", "environment": settings.environment, "version": "1.1.0"}


# ── NBA ───────────────────────────────────────────────────────────────────────

@router.get("/nba/games")
@limiter.limit(_DATA_LIMIT)
async def get_games(request: Request, response: Response, date: Optional[str] = Query(None, description="Format: YYYY-MM-DD")):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    if date and not validate_date_string(date):
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")
    try:
        games = await nba_service.get_games_by_date(date)
        return {"games": [g.model_dump() for g in games], "count": len(games)}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"BallDontLie API error: {str(e)}")


@router.get("/nba/teams")
async def get_teams():
    try:
        teams = await nba_service.get_all_teams()
        return {"teams": [t.model_dump() for t in teams], "count": len(teams)}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"BallDontLie API error: {str(e)}")


@router.get("/nba/teams/{team_id}")
async def get_team(team_id: int):
    try:
        team = await nba_service.get_team_by_id(team_id)
        return team.model_dump()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"BallDontLie API error: {str(e)}")


@router.get("/nba/players")
async def search_players(name: str = Query(..., description="Player name to search")):
    try:
        query = name.strip()
        tokens = query.split()

        if len(tokens) >= 2:
            first_tok = tokens[0]
            last_tok = " ".join(tokens[1:])
            players = await nba_service.search_players(query, first_name=first_tok, last_name=last_tok)
            if not players:
                players = await nba_service.search_players(query, last_name=last_tok)
        else:
            players = await nba_service.search_players(query, first_name=query)
            if not players:
                players = await nba_service.search_players(query, last_name=query)

        if not players:
            players = await nba_service.search_players(query)

        return {"players": [p.model_dump() for p in players], "count": len(players)}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"BallDontLie API error: {str(e)}")


@router.get("/nba/contracts")
async def get_player_contracts(
    player_id: int = Query(..., description="BallDontLie player id"),
    seasons: Optional[List[int]] = Query(None, description="Filter by seasons: ?seasons=2024&seasons=2025"),
    per_page: int = Query(25, description="Results per page, max 100"),
):
    try:
        contracts = await nba_service.get_player_contracts(player_id, seasons, per_page)
        return {"contracts": [c.model_dump() for c in contracts], "count": len(contracts)}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"BallDontLie API error: {str(e)}")


@router.get("/nba/stats/advanced")
async def nba_advanced_stats(
    seasons: Optional[List[int]] = Query(None, description="Seasons filter"),
    player_ids: Optional[List[int]] = Query(None, description="Player id(s) filter"),
    per_page: int = Query(25, description="Results per page"),
):
    try:
        stats = await nba_service.get_advanced_stats(seasons=seasons, player_ids=player_ids, per_page=per_page)
        return {"stats": [s.model_dump() for s in stats], "count": len(stats)}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"BallDontLie API error: {str(e)}")


@router.get("/nba/lineups")
async def get_lineups(game_ids: List[int] = Query(..., description="game_ids array"), per_page: int = Query(25)):
    try:
        lineups = await nba_service.get_lineups(game_ids, per_page)
        return {"lineups": [l.model_dump() for l in lineups], "count": len(lineups)}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"BallDontLie API error: {str(e)}")


@router.get("/nba/games/{game_id}/boxscore")
async def get_game_boxscore(game_id: int):
    try:
        return await nba_service.get_game_boxscore(game_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"BallDontLie API error: {str(e)}")


@router.get("/nba/games/{game_id}/live-state")
async def get_live_game_state(game_id: int):
    """Rich live game state: quarter scores, momentum, run detection, player flags."""
    try:
        return await nba_service.get_live_game_state(game_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"BallDontLie API error: {str(e)}")


@router.get("/nba/players/{player_id}/stats")
async def get_player_stats(player_id: int, season: int = Query(2025, description="NBA season year")):
    try:
        stats = await nba_service.get_player_stats(player_id, season)
        return {"stats": [s.model_dump() for s in stats], "count": len(stats)}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"BallDontLie API error: {str(e)}")


@router.get("/nba/trending")
async def get_trending_players():
    """Top NBA performers from the last 5 days by points scored."""
    try:
        players = await nba_service.get_trending_players()
        return {"players": players, "count": len(players)}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/nba/news")
async def nba_news():
    """Fetch latest NBA headlines from ESPN RSS feed."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://www.espn.com/espn/rss/nba/news",
                headers={"User-Agent": "Mozilla/5.0"},
            )
        root = ET.fromstring(r.text)
        headlines = []
        for item in root.findall("./channel/item")[:25]:
            title = item.findtext("title", "").strip()
            link  = item.findtext("link", "").strip()
            if title:
                headlines.append({"title": title, "url": link})
        return {"headlines": headlines}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ── Claude ────────────────────────────────────────────────────────────────────

@router.post("/claude/analyze", response_model=AnalysisResponse)
@limiter.limit(_CLAUDE_LIMIT)
async def claude_analyze(request: Request, body: AnalysisRequest, _key: str = Depends(verify_api_key)):
    try:
        result = await claude_service.analyze(
            prompt=body.prompt,
            system_prompt=body.context or "",
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Claude API error: {str(e)}")


# ── Analysis (Combined) ───────────────────────────────────────────────────────

@router.post("/analysis/game")
@limiter.limit(_CLAUDE_LIMIT)
async def game_analysis(request: Request, _key: str = Depends(verify_api_key)):
    try:
        body = await request.json()
        return await analysis_service.analyze_game(body)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/analysis/predict-game")
@limiter.limit(_CLAUDE_LIMIT)
async def predict_game(request: Request, _key: str = Depends(verify_api_key)):
    try:
        body = await request.json()
        return await analysis_service.predict_game(body)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/analysis/coach-live")
@limiter.limit(_CLAUDE_LIMIT)
async def coach_live(request: Request, _key: str = Depends(verify_api_key)):
    """Live tactical engine: run detection, clock management, structured adjustments."""
    try:
        body = await request.json()
        return await analysis_service.coach_live_adjustment(body)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/nba/bulk-averages")
async def bulk_player_averages(
    player_ids: List[int] = Query(..., description="BallDontLie player IDs"),
    season: int = Query(2025, description="NBA season year"),
):
    """Fetch season averages for multiple players in parallel. Returns a dict keyed by player_id."""
    async def _fetch_one(pid: int):
        try:
            avgs = await nba_service.get_season_averages(pid, season)
            if avgs:
                return pid, avgs
            # Fall back to computing from game logs when season_averages endpoint is empty
            stats = await nba_service.get_player_stats(pid, season)
            if not stats:
                return pid, {}
            def _avg(vals): return round(sum(v for v in vals if v is not None) / len([v for v in vals if v is not None]), 1) if any(v is not None for v in vals) else None
            return pid, {
                "pts": _avg([s.points for s in stats]),
                "reb": _avg([s.rebounds for s in stats]),
                "ast": _avg([s.assists for s in stats]),
                "stl": _avg([s.steals for s in stats]),
                "blk": _avg([s.blocks for s in stats]),
                "fg_pct": _avg([s.fg_pct for s in stats]),
                "fg3_pct": _avg([s.fg3_pct for s in stats]),
                "ft_pct": _avg([s.ft_pct for s in stats]),
                "games_played": len(stats),
            }
        except Exception:
            return pid, {}

    results = await asyncio.gather(*[_fetch_one(pid) for pid in player_ids])
    return {"averages": {str(pid): avgs for pid, avgs in results}, "season": season}


@router.get("/analysis/team-dna")
@limiter.limit(_CLAUDE_LIMIT)
async def team_dna(request: Request, team_name: str = Query(..., description="Team name"), _key: str = Depends(verify_api_key)):
    """Deep tactical identity breakdown: offense, defense, pace, shot diet, vulnerabilities."""
    try:
        return await analysis_service.analyze_team_dna(team_name)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/analysis/scout-note")
@limiter.limit(_CLAUDE_LIMIT)
async def scout_note(request: Request, _key: str = Depends(verify_api_key)):
    """Generate a live 1-2 sentence scout note for a single player via Claude."""
    try:
        body = await request.json()
        return await analysis_service.scout_note(
            name=body["name"],
            team=body.get("team", ""),
            pts=float(body.get("pts", 0)),
            reb=float(body.get("reb", 0)),
            ast=float(body.get("ast", 0)),
            context=body.get("context", "general"),
            age=body.get("age"),
            pos=body.get("pos"),
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/nba/mvp-odds")
async def mvp_odds():
    """
    Fetch current MVP odds from ESPN's award predictor.
    Returns a dict of player name → odds string (e.g. '-350').
    Falls back to empty dict if ESPN is unavailable.
    """
    # ESPN award tracker pages embed JSON odds in the page — scraping is brittle.
    # Return empty odds so the UI shows '—' and falls back gracefully.
    # A future integration with an odds API (e.g. The Odds API) can populate this.
    return {"odds": {}, "source": "placeholder"}


@router.get("/analysis/compare")
@limiter.limit(_CLAUDE_LIMIT)
async def compare_players(
    request: Request,
    player_a: int = Query(..., description="BallDontLie player ID for player A"),
    player_b: int = Query(..., description="BallDontLie player ID for player B"),
    season: int = Query(2025, description="NBA season year"),
    _key: str = Depends(verify_api_key),
):
    try:
        return await analysis_service.compare_players(player_a, player_b, season)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/analysis/today-games", response_model=GameAnalysisResponse)
@limiter.limit(_CLAUDE_LIMIT)
async def today_games_analysis(request: Request, date: Optional[str] = Query(None, description="Format: YYYY-MM-DD"), _key: str = Depends(verify_api_key)):
    if date and not validate_date_string(date):
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")
    try:
        return await analysis_service.analyze_today_games(date)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/analysis/player")
@limiter.limit(_CLAUDE_LIMIT)
async def player_analysis(
    request: Request,
    player_id: int = Query(..., description="BallDontLie player ID"),
    season: int = Query(2025, description="NBA season year"),
    _key: str = Depends(verify_api_key),
):
    try:
        return await analysis_service.analyze_player(player_id, season)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/analysis/player/stream")
@limiter.limit(_CLAUDE_LIMIT)
async def player_analysis_stream(
    request: Request,
    player_id: int = Query(..., description="BallDontLie player ID"),
    season: int = Query(2025, description="NBA season year"),
    _key: str = Depends(verify_api_key),
):
    """Stream player analysis as Server-Sent Events."""
    async def generate():
        try:
            async for event in analysis_service.analyze_player_stream(player_id, season):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/analysis/player/section")
@limiter.limit(_CLAUDE_LIMIT)
async def player_section_analysis(
    request: Request,
    player_id: int = Query(..., description="BallDontLie player ID"),
    season: int = Query(2025, description="NBA season year"),
    section: str = Query(..., description="Section: offense|defense|off_the_court|injuries|financials"),
    _key: str = Depends(verify_api_key),
):
    try:
        return await analysis_service.analyze_player_section(player_id, season, section)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ── Front Office ──────────────────────────────────────────────────────────────

@router.get("/frontoffice/roster")
@limiter.limit(_CLAUDE_LIMIT)
async def get_roster_analysis(request: Request, team_name: str = Query(..., description="Team name"), _key: str = Depends(verify_api_key)):
    """Get roster breakdown and financial analysis for a team."""
    try:
        return await analysis_service.analyze_roster(team_name)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ── Coach Mode ────────────────────────────────────────────────────────────────

@router.post("/coach/adjust")
@limiter.limit(_CLAUDE_LIMIT)
async def coach_adjustment(request: Request, body: dict = Body(...), _key: str = Depends(verify_api_key)):
    """
    Real-time coaching adjustment. Reads live box score automatically.
    Expects: { "game_id": 12345, "my_team": "Lakers" }
    """
    try:
        return await analysis_service.coach_adjustment(body)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/coach/timeout")
@limiter.limit(_CLAUDE_LIMIT)
async def timeout_play(request: Request, body: dict = Body(...), _key: str = Depends(verify_api_key)):
    """
    Generate a timeout play. Derives all game context from live box score.
    Expects: { "game_id": 12345, "my_team": "Lakers" }
    """
    try:
        return await analysis_service.timeout_play(body)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/coach/defense")
@limiter.limit(_CLAUDE_LIMIT)
async def defensive_play(request: Request, body: dict = Body(...), _key: str = Depends(verify_api_key)):
    """
    Design a defensive scheme. Reads live box score, identifies opponent threats,
    and prescribes a named scheme with exact player-by-player assignments.
    Expects: { "game_id": 12345, "my_team": "Lakers", "situation": "optional context" }
    """
    try:
        return await analysis_service.defensive_play(body)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ── Standings ────────────────────────────────────────────────────────────────

@router.get("/standings")
async def get_standings():
    """Live W-L standings for all 30 teams, cached 6 hours."""
    try:
        return await standings_service.get_standings()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/nba/injuries")
async def nba_injuries():
    """Live NBA injury report from ESPN public API, cached 30 minutes."""
    import time

    _cache = nba_injuries.__dict__
    now = time.time()
    if _cache.get("data") and now < _cache.get("expires_at", 0):
        return _cache["data"]

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries",
                headers={"User-Agent": "Mozilla/5.0"},
            )
        raw = r.json()

        teams = []
        for team_block in raw.get("injuries", []):
            team_name = team_block.get("displayName", "")
            players = []
            for inj in team_block.get("injuries", []):
                athlete = inj.get("athlete", {})
                status = inj.get("status", "")
                players.append({
                    "name": athlete.get("displayName", ""),
                    "short_name": athlete.get("shortName", ""),
                    "status": status,
                    "comment": inj.get("shortComment", ""),
                    "date": inj.get("date", ""),
                })
            if players:
                teams.append({"team": team_name, "players": players})

        result = {"teams": teams, "count": sum(len(t["players"]) for t in teams)}
        _cache["data"] = result
        _cache["expires_at"] = now + 1800  # 30 min
        return result
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ── Agents (background, no UI) ────────────────────────────────────────────────

@router.post("/agents/nightly")
async def agent_nightly():
    """Nightly run: pre-analyze tomorrow's slate + quality-pass today's."""
    try:
        return await agent_service.run_nightly()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/agents/pregame")
async def agent_pregame(date: Optional[str] = Query(None)):
    """Pre-warm a specific date's slate and player caches."""
    try:
        return await agent_service.run_pregame_agent(date)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/agents/quality-pass")
async def agent_quality_pass(date: Optional[str] = Query(None)):
    """Sharpen a cached slate analysis for the given date."""
    try:
        return await agent_service.run_quality_pass(date)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

