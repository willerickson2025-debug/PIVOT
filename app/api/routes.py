import json
import xml.etree.ElementTree as ET

import httpx
from fastapi import APIRouter, HTTPException, Query, Body, Response, Request
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

router = APIRouter()


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
    return {"status": "ok", "environment": settings.environment, "version": "1.0.0"}


# ── NBA ───────────────────────────────────────────────────────────────────────

@router.get("/nba/games")
async def get_games(response: Response, date: Optional[str] = Query(None, description="Format: YYYY-MM-DD")):
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
        headlines = [
            item.findtext("title", "").strip()
            for item in root.findall("./channel/item")
            if item.findtext("title", "").strip()
        ][:25]
        return {"headlines": headlines}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ── Claude ────────────────────────────────────────────────────────────────────

@router.post("/claude/analyze", response_model=AnalysisResponse)
async def claude_analyze(body: AnalysisRequest):
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
async def game_analysis(request: Request):
    try:
        body = await request.json()
        return await analysis_service.analyze_game(body)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/analysis/today-games", response_model=GameAnalysisResponse)
async def today_games_analysis(date: Optional[str] = Query(None, description="Format: YYYY-MM-DD")):
    if date and not validate_date_string(date):
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")
    try:
        return await analysis_service.analyze_today_games(date)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/analysis/player")
async def player_analysis(
    player_id: int = Query(..., description="BallDontLie player ID"),
    season: int = Query(2025, description="NBA season year"),
):
    try:
        return await analysis_service.analyze_player(player_id, season)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/analysis/player/stream")
async def player_analysis_stream(
    player_id: int = Query(..., description="BallDontLie player ID"),
    season: int = Query(2025, description="NBA season year"),
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
async def player_section_analysis(
    player_id: int = Query(..., description="BallDontLie player ID"),
    season: int = Query(2025, description="NBA season year"),
    section: str = Query(..., description="Section: offense|defense|off_the_court|injuries|financials"),
):
    try:
        return await analysis_service.analyze_player_section(player_id, season, section)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ── Front Office ──────────────────────────────────────────────────────────────

@router.get("/frontoffice/roster")
async def get_roster_analysis(team_name: str = Query(..., description="Team name")):
    """Get roster breakdown and financial analysis for a team."""
    try:
        return await analysis_service.analyze_roster(team_name)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ── Coach Mode ────────────────────────────────────────────────────────────────

@router.post("/coach/adjust")
async def coach_adjustment(body: dict = Body(...)):
    """
    Real-time coaching adjustment. Reads live box score automatically.
    Expects: { "game_id": 12345, "my_team": "Lakers" }
    """
    try:
        return await analysis_service.coach_adjustment(body)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/coach/timeout")
async def timeout_play(body: dict = Body(...)):
    """
    Generate a timeout play. Derives all game context from live box score.
    Expects: { "game_id": 12345, "my_team": "Lakers" }
    """
    try:
        return await analysis_service.timeout_play(body)
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

