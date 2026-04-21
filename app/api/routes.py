import asyncio
import json
import xml.etree.ElementTree as ET

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Body, Response, Request
from fastapi.responses import StreamingResponse
from typing import Optional, List

from app.services import nba_service, claude_service, analysis_service, agent_service
from app.services import standings_service
from app.services.nba_service import PlayerResolutionError
from app.models.schemas import (
    AnalysisRequest,
    AnalysisResponse,
    GameAnalysisResponse,
)
from app.utils.helpers import validate_date_string
from app.core.limiter import limiter
from app.core.security import verify_api_key
from app.core.season import get_current_season

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
        async with httpx.AsyncClient(timeout=15) as client:
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


@router.get("/nba/teams/roster")
async def get_team_roster(
    abbr: Optional[str] = Query(None, description="Team abbreviation, e.g. LAL"),
    team_id: Optional[int] = Query(None, description="Team ID (alternate to abbr)"),
):
    if abbr is None and team_id is not None:
        try:
            team = await nba_service.get_team_by_id(team_id)
            abbr = team.abbreviation
        except Exception:
            raise HTTPException(status_code=404, detail=f"Team {team_id} not found")
    if abbr is None:
        raise HTTPException(status_code=400, detail="Provide abbr or team_id")
    try:
        players = await nba_service.get_roster_by_abbr(abbr)
        return {"players": players, "count": len(players)}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
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
async def search_players(
    name: str = Query(..., description="Player name to search"),
    include_inactive: bool = Query(False, description="Include retired/inactive players"),
):
    try:
        query = name.strip()
        tokens = query.split()

        if len(tokens) >= 2:
            first_tok = tokens[0]
            last_tok = " ".join(tokens[1:])
            players = await nba_service.search_players(query, first_name=first_tok, last_name=last_tok, include_inactive=include_inactive)
            if not players:
                players = await nba_service.search_players(query, last_name=last_tok, include_inactive=include_inactive)
        else:
            players = await nba_service.search_players(query, first_name=query, include_inactive=include_inactive)
            if not players:
                players = await nba_service.search_players(query, last_name=query, include_inactive=include_inactive)

        if not players:
            players = await nba_service.search_players(query, include_inactive=include_inactive)

        return {"players": [p.model_dump() for p in players], "count": len(players)}
    except PlayerResolutionError as e:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "player_resolution",
                "message": str(e),
                "candidates": getattr(e, "candidates", None),
            },
        )
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
        effective_seasons = seasons if seasons else [get_current_season()]
        stats = await nba_service.get_advanced_stats(seasons=effective_seasons, player_ids=player_ids, per_page=per_page)
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


_NEWS_SOURCES = [
    {"label": "ESPN",          "url": "https://www.espn.com/espn/rss/nba/news"},
    {"label": "CBS Sports",    "url": "https://www.cbssports.com/rss/headlines/nba/"},
    {"label": "Yahoo Sports",  "url": "https://sports.yahoo.com/nba/rss.xml"},
    {"label": "Bleacher Report","url": "https://bleacherreport.com/nba.rss"},
    {"label": "HoopsHype",     "url": "https://hoopshype.com/feed/"},
    {"label": "SI",            "url": "https://www.si.com/rss/si_nba.rss"},
    {"label": "NBA.com",       "url": "https://www.nba.com/news/rss.xml"},
    {"label": "ClutchPoints",  "url": "https://clutchpoints.com/rss.xml"},
]

async def _fetch_rss(client: httpx.AsyncClient, source: dict) -> list[dict]:
    """Fetch and parse a single RSS feed; returns [] on any failure."""
    try:
        r = await client.get(source["url"], headers={"User-Agent": "Mozilla/5.0 (compatible)"})
        r.raise_for_status()
        root = ET.fromstring(r.text)
        items = []
        for item in root.findall("./channel/item")[:20]:
            title = (item.findtext("title") or "").strip()
            link  = (item.findtext("link") or "").strip()
            pub   = (item.findtext("pubDate") or "").strip()
            if title:
                items.append({"title": title, "url": link, "pub": pub, "source": source["label"]})
        return items
    except Exception:
        return []

@router.get("/nba/news")
async def nba_news():
    """Fetch latest NBA headlines from multiple RSS feeds, merged and deduplicated."""
    async with httpx.AsyncClient(timeout=15) as client:
        results = await asyncio.gather(*[_fetch_rss(client, s) for s in _NEWS_SOURCES])

    # Flatten all items
    all_items: list[dict] = [item for feed in results for item in feed]

    if not all_items:
        raise HTTPException(status_code=502, detail="All news sources unavailable")

    # Deduplicate: normalize title to first 60 chars, lowercase, alphanumeric only
    import re as _re
    def _norm(t: str) -> str:
        return _re.sub(r"[^a-z0-9]", "", t.lower())[:60]

    seen: set[str] = set()
    headlines: list[dict] = []
    for item in all_items:
        key = _norm(item["title"])
        if key and key not in seen:
            seen.add(key)
            headlines.append({"title": item["title"], "url": item["url"], "source": item["source"]})

    return {"headlines": headlines[:50]}


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
        session_id = request.headers.get("X-Pivot-Session")
        return await analysis_service.predict_game(body, session_id=session_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/analysis/coach-live")
@limiter.limit(_CLAUDE_LIMIT)
async def coach_live(request: Request, _key: str = Depends(verify_api_key)):
    """Live tactical engine: run detection, clock management, structured adjustments."""
    try:
        body = await request.json()
        session_id = request.headers.get("X-Pivot-Session")
        return await analysis_service.coach_live_adjustment(body, session_id=session_id)
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
    """Stream deep tactical identity breakdown: offense, defense, pace, shot diet, vulnerabilities."""
    async def generate():
        try:
            async for event in analysis_service.analyze_team_dna(team_name):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/analysis/scout-note")
@limiter.limit(_CLAUDE_LIMIT)
async def scout_note(request: Request, _key: str = Depends(verify_api_key)):
    """Generate a live 1-2 sentence scout note for a single player via Claude."""
    try:
        body = await request.json()
        session_id = request.headers.get("X-Pivot-Session")
        return await analysis_service.scout_note(
            name=body["name"],
            team=body.get("team", ""),
            pts=float(body.get("pts", 0)),
            reb=float(body.get("reb", 0)),
            ast=float(body.get("ast", 0)),
            context=body.get("context", "general"),
            age=body.get("age"),
            pos=body.get("pos"),
            player_id=body.get("player_id"),
            session_id=session_id,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/nba/mvp-odds")
async def mvp_odds():
    """MVP odds — not yet integrated. Returns 503 so callers can degrade gracefully."""
    raise HTTPException(
        status_code=503,
        detail="MVP odds not available. Integrate an odds API (e.g. The Odds API) to populate this endpoint.",
    )


@router.get("/analysis/compare")
@limiter.limit(_CLAUDE_LIMIT)
async def compare_players(
    request: Request,
    player_a: str = Query(..., description="Player A full name, e.g. 'Nikola Jokic'"),
    player_b: str = Query(..., description="Player B full name, e.g. 'Shai Gilgeous-Alexander'"),
    season: int = Query(2024, description="NBA season start year (2024 = 2024-25)"),
    archetype: Optional[str] = Query(None, description="Coaching archetype lens"),
    compare_context: Optional[str] = Query(None, description="Situational context for the comparison"),
    _key: str = Depends(verify_api_key),
):
    try:
        session_id = request.headers.get("X-Pivot-Session")
        return await analysis_service.compare_players(
            player_a, player_b, season,
            archetype=archetype,
            compare_context=compare_context,
            session_id=session_id,
        )
    except PlayerResolutionError as e:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "player_resolution",
                "message": str(e),
                "candidates": getattr(e, "candidates", None),
            },
        )
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
    player_id: Optional[int] = Query(None, description="BallDontLie player ID"),
    player_name: Optional[str] = Query(None, description="Player full name (resolved via exact match)"),
    season: int = Query(2025, description="NBA season year"),
    _key: str = Depends(verify_api_key),
):
    """Accepts player_id (preferred) or player_name (resolved via resolve_player_exact)."""
    try:
        if player_id is None and player_name:
            from app.services.nba_service import PlayerResolutionError
            try:
                player = await nba_service.resolve_player_exact(player_name)
                player_id = player.id
            except PlayerResolutionError as e:
                raise HTTPException(status_code=400, detail={"error": "player_resolution", "message": str(e), "candidates": getattr(e, "candidates", None)})
        if player_id is None:
            raise HTTPException(status_code=422, detail="Provide player_id or player_name")
        result = await analysis_service.analyze_player(player_id, season)
        # Add season_status flag for injured/inactive players (H2)
        if isinstance(result, dict):
            payload_player = result.get("payload", {}).get("player", {})
            if isinstance(payload_player, dict):
                basic = payload_player.get("basic", {})
                advanced = payload_player.get("advanced", {})
                if basic.get("pts") is None and (advanced.get("games_played") or 0) > 0:
                    result["season_status"] = "injured_or_inactive"
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/analysis/player/stream")
@limiter.limit(_CLAUDE_LIMIT)
async def player_analysis_stream(
    request: Request,
    player_id: Optional[int] = Query(None, description="BallDontLie player ID"),
    player_name: Optional[str] = Query(None, description="Player full name (resolved via exact match)"),
    season: int = Query(2025, description="NBA season year"),
    _key: str = Depends(verify_api_key),
):
    """Stream player analysis as Server-Sent Events. Accepts player_id or player_name."""
    # Resolve name to id before entering the streaming context where exceptions are harder to surface
    if player_id is None and player_name:
        from app.services.nba_service import PlayerResolutionError
        try:
            player = await nba_service.resolve_player_exact(player_name)
            player_id = player.id
        except PlayerResolutionError as e:
            raise HTTPException(status_code=400, detail={"error": "player_resolution", "message": str(e), "candidates": getattr(e, "candidates", None)})
    if player_id is None:
        raise HTTPException(status_code=422, detail="Provide player_id or player_name")

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


@router.get("/analysis/player/section/stream")
@limiter.limit(_CLAUDE_LIMIT)
async def player_section_analysis_stream(
    request: Request,
    player_id: int = Query(..., description="BallDontLie player ID"),
    season: int = Query(2025, description="NBA season year"),
    section: str = Query(..., description="Section: offense|defense|off_the_court|injuries|financials|on_off"),
    _key: str = Depends(verify_api_key),
):
    """Stream player section analysis as Server-Sent Events."""
    async def generate():
        try:
            async for event in analysis_service.analyze_player_section_stream(player_id, season, section):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Basketball Chat ───────────────────────────────────────────────────────────

_CHAT_SYSTEM_BASE = (
    "You are a sharp basketball analyst. Real-time NBA data has been injected at the top of this "
    "prompt under LIVE DATA. That data was fetched seconds ago and is current. "
    "You HAVE the standings. You HAVE recent scores. Use them. "
    "Never say 'I don't have real-time data' or 'my knowledge has a cutoff' — that is false here. "
    "Never tell the user to check ESPN or NBA.com — you already have the data they would find there. "
    "If a question is about current standings, playoff seeding, or recent results, answer it "
    "directly using the LIVE DATA provided. "
    "Write like a beat reporter: confident, direct, grounded in numbers, no hype. "
    "Every claim needs a stat. Say '31.2 PPG on 54.1% TS' not 'he is an elite scorer'. "
    "Use PPG, RPG, APG, TS%, PER, net rating, usage rate, win shares, cap figures. "
    "Keep it tight. Two to three paragraphs unless the question genuinely needs more. "
    "No filler. No 'great question'. No 'certainly'. No AI disclaimers. "
    "Plain prose only. No headers, no bullets, no numbered lists, no asterisks, "
    "no pound signs, no bold, no italics. Sentences and paragraphs only."
)

_ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
_ESPN_NEWS = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/news?limit=10"

async def _fetch_live_context() -> str:
    """Build a live data block from our standings + ESPN scoreboard to ground the chat model."""
    import logging
    _log = logging.getLogger(__name__)
    lines: list[str] = ["LIVE DATA (fetched right now — treat as ground truth):"]

    # 1. Our standings
    try:
        standings = await standings_service.get_standings()
        east = standings.get("east", [])[:8]
        west = standings.get("west", [])[:8]
        east_str = ", ".join(f"{t['seed']}. {t['abbr']} ({t['wins']}-{t['losses']})" for t in east)
        west_str = ", ".join(f"{t['seed']}. {t['abbr']} ({t['wins']}-{t['losses']})" for t in west)
        lines.append(f"2025-26 NBA Standings top 8 — East: {east_str}")
        lines.append(f"2025-26 NBA Standings top 8 — West: {west_str}")
        _log.info("chat live_ctx: standings OK (%d east, %d west)", len(east), len(west))
    except Exception as exc:
        _log.warning("chat live_ctx: standings failed: %s", exc)

    # 2+3. ESPN scoreboard + news — share one client
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            scoreboard_resp, news_resp = await asyncio.gather(
                client.get(_ESPN_SCOREBOARD),
                client.get(_ESPN_NEWS),
                return_exceptions=True,
            )

            if not isinstance(scoreboard_resp, Exception) and scoreboard_resp.status_code == 200:
                events = scoreboard_resp.json().get("events", [])
                results: list[str] = []
                for ev in events[:12]:
                    comps = ev.get("competitions", [{}])[0]
                    competitors = comps.get("competitors", [])
                    if len(competitors) == 2:
                        home = competitors[0]
                        away = competitors[1]
                        status = ev.get("status", {}).get("type", {}).get("description", "")
                        h_score = home.get("score", "")
                        a_score = away.get("score", "")
                        h_name = home.get("team", {}).get("abbreviation", "")
                        a_name = away.get("team", {}).get("abbreviation", "")
                        if status == "Final" and h_score and a_score:
                            results.append(f"{a_name} {a_score} @ {h_name} {h_score} (Final)")
                        elif status not in ("Final", "Scheduled") and h_score:
                            results.append(f"{a_name} {a_score} @ {h_name} {h_score} ({status})")
                if results:
                    lines.append("Recent/live NBA scores: " + " | ".join(results))
                    _log.info("chat live_ctx: ESPN scores OK (%d games)", len(results))
                else:
                    _log.info("chat live_ctx: ESPN scores empty")
            else:
                _log.warning("chat live_ctx: ESPN scoreboard failed")

            if not isinstance(news_resp, Exception) and news_resp.status_code == 200:
                articles = news_resp.json().get("articles", [])[:5]
                headlines = [a.get("headline", "") for a in articles if a.get("headline")]
                if headlines:
                    lines.append("Recent NBA headlines: " + " | ".join(headlines))
                    _log.info("chat live_ctx: ESPN news OK (%d headlines)", len(headlines))
    except Exception as exc:
        _log.warning("chat live_ctx: ESPN fetch failed: %s", exc)

    lines.append("")
    ctx = "\n".join(lines)
    _log.info("chat live_ctx total length: %d chars", len(ctx))
    return ctx


def _strip_markdown(line: str) -> str:
    """Strip all markdown from a complete line of text."""
    import re
    stripped = line.strip()
    if re.match(r'^\|', stripped) or re.match(r'^[-|:\s]+$', stripped):
        return ''
    if re.match(r'^-{3,}$', stripped):
        return ''
    line = re.sub(r'^#{1,6}\s+', '', line)
    line = re.sub(r'\*{1,3}(.*?)\*{1,3}', r'\1', line)
    line = re.sub(r'_{1,3}(.*?)_{1,3}', r'\1', line)
    line = line.replace('*', '').replace('#', '').replace('_', '')
    line = re.sub(r'^\s*[-+]\s+', '', line)
    line = re.sub(r'^\s*\d+\.\s+', '', line)
    line = line.replace('\u2014', ',').replace('\u2013', ',')
    line = line.replace('|', '')
    return line


def _stream_text(text: str):
    """Yield SSE chunk events for a complete text string, line by line, stripped."""
    for line in text.split('\n'):
        clean = _strip_markdown(line)
        if clean.strip():
            yield json.dumps({'type': 'chunk', 'text': clean + ' '})


# ── Search tool for chat ──────────────────────────────────────────────────────

_SEARCH_TOOL_DEF = {
    "name": "search_nba_info",
    "description": (
        "Search for current NBA information. Use this to verify which team a player is on, "
        "confirm recent trades or transactions, check injury status, or get current season context "
        "that may have changed since training data. Use it whenever you are not certain a player "
        "is still on the team you expect, or when a question depends on recent moves."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query, e.g. 'Jimmy Butler current team 2026' or 'Lakers trade deadline moves'"
            }
        },
        "required": ["query"]
    }
}

_ESPN_SEARCH = "https://site.api.espn.com/apis/search/v2"

async def _execute_nba_search(query: str) -> str:
    """Run query against ESPN search + BallDontLie player API. Returns plain text results."""
    import logging
    _log = logging.getLogger(__name__)
    results: list[str] = []

    # 1. ESPN article search
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.get(_ESPN_SEARCH, params={"query": query, "limit": 5, "section": "nba"})
            if resp.status_code == 200:
                data = resp.json()
                for group in data.get("results", []):
                    for article in (group.get("contents") or [])[:2]:
                        headline = article.get("headline") or article.get("title") or ""
                        desc = article.get("description") or article.get("summary") or ""
                        if headline:
                            results.append(f"{headline}. {desc}".strip(". "))
                _log.info("chat search ESPN: %d results for '%s'", len(results), query)
    except Exception as exc:
        _log.warning("chat search ESPN failed: %s", exc)

    # 2. BallDontLie player lookup (current team)
    try:
        from app.services.nba_service import _fetch_data
        player_resp = await _fetch_data("/players", params={"search": query[:50], "per_page": 5})
        for p in (player_resp.get("data") or [])[:3]:
            team = p.get("team") or {}
            full_name = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
            if full_name and team.get("full_name"):
                results.append(
                    f"{full_name} currently plays for the {team['full_name']} ({team.get('abbreviation', '')})"
                )
    except Exception as exc:
        _log.warning("chat search BDL failed: %s", exc)

    if not results:
        return "No current information found for this query."
    return "\n".join(results)


@router.post("/chat/message")
@limiter.limit(_CLAUDE_LIMIT)
async def chat_message(request: Request, body: dict = Body(...), _key: str = Depends(verify_api_key)):
    """
    Stream a basketball chat response with web search tool use.
    Body: { "messages": [{"role": "user"|"assistant", "content": "..."}] }
    """
    import logging
    _log = logging.getLogger(__name__)
    messages = body.get("messages") or []
    if not messages:
        raise HTTPException(status_code=400, detail="No messages provided")

    async def generate():
        try:
            from app.core.config import get_settings
            settings = get_settings()
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=settings.anthropic_api_key)

            live_ctx = await _fetch_live_context()
            system_prompt = live_ctx + _CHAT_SYSTEM_BASE
            current_messages = list(messages)

            # Tool use loop: model can search up to 4 times before giving final answer
            for round_num in range(4):
                resp = await client.messages.create(
                    model=settings.claude_model,
                    max_tokens=2000,
                    system=system_prompt,
                    messages=current_messages,
                    tools=[_SEARCH_TOOL_DEF],
                )

                if resp.stop_reason != "tool_use":
                    # Final answer — stream it in chunks
                    for block in resp.content:
                        if hasattr(block, 'text') and block.text:
                            for event in _stream_text(block.text):
                                yield f"data: {event}\n\n"
                    break

                # Execute each tool call
                tool_results = []
                for block in resp.content:
                    if block.type == "tool_use":
                        query = block.input.get("query", "")
                        _log.info("chat tool_use round=%d query='%s'", round_num, query)
                        yield f"data: {json.dumps({'type': 'status', 'text': f'Checking current info: {query}'})}\n\n"
                        search_result = await _execute_nba_search(query)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": search_result
                        })

                current_messages = current_messages + [
                    {"role": "assistant", "content": [b.model_dump() for b in resp.content]},
                    {"role": "user", "content": tool_results},
                ]

            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Front Office ──────────────────────────────────────────────────────────────

@router.get("/frontoffice/roster/stream")
@limiter.limit(_CLAUDE_LIMIT)
async def get_roster_analysis_stream(request: Request, team_name: str = Query(..., description="Team name"), _key: str = Depends(verify_api_key)):
    """Stream roster breakdown and financial analysis for a team."""
    async def generate():
        try:
            async for event in analysis_service.analyze_roster_stream(team_name):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@router.get("/frontoffice/roster")
@limiter.limit(_CLAUDE_LIMIT)
async def get_roster_analysis(request: Request, team_name: str = Query(..., description="Team name"), _key: str = Depends(verify_api_key)):
    """Get roster breakdown and financial analysis for a team (non-streaming)."""
    try:
        return await analysis_service.analyze_roster(team_name)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/frontoffice/trade")
@limiter.limit(_CLAUDE_LIMIT)
async def analyze_trade(request: Request, body: dict = Body(...), _key: str = Depends(verify_api_key)):
    """
    Evaluate a trade proposal through an optional coaching archetype lens.

    Body:
    {
        "team_a": "Lakers",
        "team_a_players": [{"name": "LeBron James", "id": 237, "pos": "F", "age": 40}],
        "team_b": "Celtics",
        "team_b_players": [{"name": "Jayson Tatum", "id": 434, "pos": "F", "age": 27}],
        "archetype": "architect"  // optional
    }
    """
    try:
        session_id = request.headers.get("X-Pivot-Session")
        return await analysis_service.analyze_trade(
            team_a_name=body.get("team_a", "Team A"),
            team_a_players=body.get("team_a_players", []),
            team_b_name=body.get("team_b", "Team B"),
            team_b_players=body.get("team_b_players", []),
            archetype=body.get("archetype"),
            team_a_cap=body.get("team_a_cap"),
            team_b_cap=body.get("team_b_cap"),
            cap_context={
                "tax_line":          body.get("cap_tax"),
                "first_apron":       body.get("cap_first_apron"),
                "second_apron":      body.get("cap_second_apron"),
            },
            session_id=session_id,
        )
    except PlayerResolutionError as e:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "player_resolution",
                "message": str(e),
                "candidates": getattr(e, "candidates", None),
            },
        )
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
    if not body.get("game_id"):
        raise HTTPException(status_code=400, detail="game_id required — select a live game first.")
    try:
        session_id = request.headers.get("X-Pivot-Session")
        return await analysis_service.coach_adjustment(body, session_id=session_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/coach/timeout")
@limiter.limit(_CLAUDE_LIMIT)
async def timeout_play(request: Request, body: dict = Body(...), _key: str = Depends(verify_api_key)):
    """
    Generate a timeout play. Derives all game context from live box score.
    Expects: { "game_id": 12345, "my_team": "Lakers" }
    """
    if not body.get("game_id"):
        raise HTTPException(status_code=400, detail="game_id required — select a live game first.")
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
    if not body.get("game_id"):
        raise HTTPException(status_code=400, detail="game_id required — select a live game first.")
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
        async with httpx.AsyncClient(timeout=20) as client:
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


# ═══════════════════════════════════════════════════════════════════════════════
# LEGACY COMPATIBILITY ROUTES
# Match the URL contract used by dashboard.html so all features work without
# changing the frontend API base path.
# ═══════════════════════════════════════════════════════════════════════════════

def _season_to_int(season) -> int:
    """Convert '2024-25' → 2024, '2025-26' → 2025, or pass-through integers.

    BDL identifies seasons by their start year: season=2025 means 2025-26.
    """
    s = str(season).strip()
    if '-' in s:
        try:
            return int(s.split('-')[0])
        except ValueError:
            pass
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return get_current_season()


def _tok(text: str) -> str:
    return f"data: {json.dumps({'type': 'token', 'text': text})}\n\n"


def _done() -> str:
    return f"data: {json.dumps({'type': 'done'})}\n\n"


# ── Data endpoints ────────────────────────────────────────────────────────────

@router.get("/games")
@limiter.limit(_DATA_LIMIT)
async def compat_games(request: Request, response: Response, date: Optional[str] = Query(None)):
    """Legacy /games → /nba/games. Returns {data: [...]} for dashboard compatibility."""
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    if date and not validate_date_string(date):
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")
    try:
        games = await nba_service.get_games_by_date(date)
        return {"data": [g.model_dump() for g in games]}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/players")
@limiter.limit(_DATA_LIMIT)
async def compat_players(
    request: Request,
    search: Optional[str] = Query(None),
    page: int = Query(1),
    per_page: int = Query(25),
    team_ids: Optional[int] = Query(None),
):
    """Legacy /players — handles search=, team_ids=, and pagination."""
    try:
        from app.services.nba_service import _fetch_data, _parse_player
        if team_ids is not None:
            payload = await _fetch_data("/players", params={"team_ids[]": team_ids, "per_page": min(per_page, 100)})
            players = [_parse_player(p) for p in payload.get("data") or []]
            return {"data": [p.model_dump() for p in players]}
        if search:
            query = search.strip()
            tokens = query.split()
            players: list = []
            if len(tokens) >= 2:
                players = await nba_service.search_players(query, first_name=tokens[0], last_name=" ".join(tokens[1:]))
            if not players:
                players = await nba_service.search_players(query)
            return {"data": [p.model_dump() for p in players[:per_page]]}
        # Generic list — page through BDL directly
        payload = await _fetch_data("/players", params={"per_page": min(per_page, 100), "page": page})
        players = [_parse_player(p) for p in payload.get("data") or []]
        return {"data": [p.model_dump() for p in players]}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/players/{player_id}/stats")
@limiter.limit(_DATA_LIMIT)
async def compat_player_stats(
    request: Request,
    player_id: int,
    season: str = Query("2025-26"),
    per_page: int = Query(25),
):
    """Legacy /players/{id}/stats — converts season string, returns {data: [...]}."""
    try:
        season_int = _season_to_int(season)
        stats = await nba_service.get_player_stats(player_id, season_int)
        return {"data": [s.model_dump() for s in stats[:per_page]]}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/players/{player_id}/advanced-stats")
@limiter.limit(_DATA_LIMIT)
async def compat_player_advanced_stats(
    request: Request,
    player_id: int,
    season: str = Query("2025-26"),
):
    """Legacy /players/{id}/advanced-stats — fails silently (optional data)."""
    try:
        season_int = _season_to_int(season)
        stats = await nba_service.get_advanced_stats(player_ids=[player_id], seasons=[season_int], per_page=1)
        if stats:
            return stats[0].model_dump()
        return {}
    except Exception:
        return {}


@router.get("/games/{game_id}/stats")
@limiter.limit(_DATA_LIMIT)
async def compat_game_stats(request: Request, game_id: int):
    """Legacy /games/{id}/stats → /nba/games/{id}/boxscore."""
    try:
        return await nba_service.get_game_boxscore(game_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/teams/{team_id}")
@limiter.limit(_DATA_LIMIT)
async def compat_team(request: Request, team_id: int):
    """Legacy /teams/{id} → /nba/teams/{id}."""
    try:
        team = await nba_service.get_team_by_id(team_id)
        return team.model_dump()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ── Streaming AI endpoints ────────────────────────────────────────────────────

@router.post("/analyze-game")
@limiter.limit(_CLAUDE_LIMIT)
async def compat_analyze_game(request: Request):
    """Legacy SSE: stream game analysis. Accepts {game_id} or full game object."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    # Normalise game_id key — dashboard sends game_id, service expects id
    if "game_id" in body and "id" not in body:
        body = dict(body)
        body["id"] = body["game_id"]

    async def generate():
        try:
            result = await analysis_service.analyze_game(body)
            text = result.get("analysis") or result.get("content") or str(result)
            for i in range(0, len(text), 8):
                yield _tok(text[i:i+8])
            yield _done()
        except Exception as e:
            yield _tok(f"Analysis unavailable: {e}")
            yield _done()

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/analyze-player")
@limiter.limit(_CLAUDE_LIMIT)
async def compat_analyze_player(request: Request):
    """Legacy SSE: stream season player analysis."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    player_id = int(body.get("player_id") or 0)
    season_int = _season_to_int(body.get("season") or "2025")

    async def generate():
        try:
            async for event in analysis_service.analyze_player_stream(player_id, season_int):
                t = event.get("type", "")
                if t in ("chunk", "token") and event.get("text"):
                    yield _tok(event["text"])
                elif t == "done":
                    yield _done()
                    return
        except Exception as e:
            yield _tok(f"Analysis unavailable: {e}")
            yield _done()

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/analyze-player-live")
@limiter.limit(_CLAUDE_LIMIT)
async def compat_analyze_player_live(request: Request):
    """Legacy SSE: stream live-form player analysis (same pipeline as season)."""
    return await compat_analyze_player(request)


@router.post("/analyze-team")
@limiter.limit(_CLAUDE_LIMIT)
async def compat_analyze_team(request: Request):
    """Legacy SSE: stream team roster analysis."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    team_name = body.get("team_name") or ""

    async def generate():
        try:
            async for event in analysis_service.analyze_roster_stream(team_name):
                t = event.get("type", "")
                if t in ("chunk", "token") and event.get("text"):
                    yield _tok(event["text"])
                elif t == "done":
                    yield _done()
                    return
        except Exception as e:
            yield _tok(f"Analysis unavailable: {e}")
            yield _done()

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/team-dna")
@limiter.limit(_CLAUDE_LIMIT)
async def compat_team_dna(request: Request):
    """Legacy SSE: stream deep team DNA breakdown."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    team_name = body.get("team_name") or ""

    async def generate():
        try:
            async for event in analysis_service.analyze_team_dna(team_name):
                t = event.get("type", "")
                if t in ("chunk", "token") and event.get("text"):
                    yield _tok(event["text"])
                elif t == "done":
                    yield _done()
                    return
        except Exception as e:
            yield _tok(f"Analysis unavailable: {e}")
            yield _done()

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/front-office-eval")
@limiter.limit(_CLAUDE_LIMIT)
async def compat_front_office_eval(request: Request):
    """Legacy SSE: stream front-office player evaluation."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    player_name = body.get("player_name") or "Unknown Player"
    context = body.get("context") or ""
    ctx_labels = {
        "max_extension": "maximum contract extension decision",
        "trade_target": "trade target evaluation",
        "buyout_candidate": "buyout / waiver claim decision",
        "salary_dump": "salary dump trade",
    }
    ctx_label = ctx_labels.get(context, "general front-office evaluation")

    # Enrich prompt with real season averages so Claude doesn't hallucinate stats
    stats_block = ""
    try:
        parts = player_name.strip().split()
        players = await nba_service.search_players(
            name=player_name,
            first_name=parts[0] if len(parts) >= 2 else None,
            last_name=parts[-1] if len(parts) >= 2 else None,
        )
        if players:
            p = players[0]
            season = get_current_season()
            avg = await nba_service.get_season_averages(p.id, season)
            if avg:
                ts = avg.get("ts_pct")
                ts_str = f", {ts:.1%} TS" if ts else ""
                stats_block = (
                    f"\nCURRENT SEASON AVERAGES ({season}-{str(season+1)[-2:]} season):\n"
                    f"  {avg.get('pts',0):.1f} PPG | {avg.get('reb',0):.1f} RPG | "
                    f"{avg.get('ast',0):.1f} APG | {avg.get('fg_pct',0):.1%} FG{ts_str} | "
                    f"{avg.get('games_played',0)} GP\n"
                )
    except Exception:
        pass

    prompt = (
        f"FRONT OFFICE EVALUATION — {player_name.upper()}\nContext: {ctx_label}\n"
        f"{stats_block}\n"
        "Write a concise GM-level evaluation covering:\n"
        "1. TRADE VALUE — current market, what they command in a deal\n"
        "2. CONTRACT FIT — value relative to their likely salary tier\n"
        "3. UPSIDE — ceiling, development trajectory, age factor\n"
        "4. RISK — injury history, performance volatility, contract risks\n"
        "5. RECOMMENDATION — one clear directive (acquire / retain / move / avoid)\n\n"
        "Write like a GM memo. Specific. Stats required. No fluff."
    )

    async def generate():
        try:
            async for chunk in claude_service.analyze_stream(
                prompt=prompt,
                system_prompt="You are a senior NBA GM. Be direct, data-driven, and decisive. Every claim needs a stat.",
                override_max_tokens=800,
            ):
                if chunk:
                    yield _tok(chunk)
            yield _done()
        except Exception as e:
            yield _tok(f"Evaluation unavailable: {e}")
            yield _done()

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/compare-players")
@limiter.limit(_CLAUDE_LIMIT)
async def compat_compare_players(request: Request):
    """Legacy SSE: stream player comparison."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    player_a_id = int(body.get("player_a_id") or 0)
    player_b_id = int(body.get("player_b_id") or 0)
    context = body.get("context") or None

    async def generate():
        try:
            result = await analysis_service.compare_players(
                player_a_id, player_b_id, get_current_season(),
                compare_context=context,
            )
            text = result.get("analysis") or ""
            structured = result.get("structured") or {}
            if not text and structured:
                text = structured.get("reasoning") or structured.get("better_for_context") or str(result)
            for i in range(0, len(text), 8):
                yield _tok(text[i:i+8])
            yield _done()
        except Exception as e:
            yield _tok(f"Comparison unavailable: {e}")
            yield _done()

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/coach")
@limiter.limit(_CLAUDE_LIMIT)
async def compat_coach(request: Request):
    """Legacy SSE: stream play design or play development for a scenario."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    scenario = body.get("scenario") or ""
    archetype = body.get("archetype") or ""
    coaching_mode = body.get("coaching_mode") or "live"
    arch_str = f"\nCoaching archetype: {archetype}" if archetype else ""

    if coaching_mode == "developmental":
        sys = claude_service.DEV_COACH_SYS
        prompt = f"PLAY DEVELOPMENT\nConcept: {scenario}{arch_str}\n\nTeach and develop this play."
    else:
        sys = claude_service.LIVE_COACH_SYS
        prompt = f"TIMEOUT PLAY\nScenario: {scenario}{arch_str}\n\nCall the play now."

    async def generate():
        try:
            async for chunk in claude_service.analyze_stream(
                prompt=prompt, system_prompt=sys, override_max_tokens=700):
                if chunk:
                    yield _tok(chunk)
            yield _done()
        except Exception as e:
            yield _tok(f"Play design unavailable: {e}")
            yield _done()

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/defensive-scheme")
@limiter.limit(_CLAUDE_LIMIT)
async def compat_defensive_scheme(request: Request):
    """Legacy SSE: stream defensive scheme analysis or teaching."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    scheme = body.get("scheme") or "man_to_man"
    offensive_action = body.get("offensive_action") or "general half-court offense"
    archetype = body.get("archetype") or ""
    coaching_mode = body.get("coaching_mode") or "live"
    arch_str = f"\nCoaching archetype: {archetype}" if archetype else ""
    names = {
        "man_to_man": "Man-to-Man", "zone_2_3": "2-3 Zone", "zone_3_2": "3-2 Zone",
        "matchup_zone": "Matchup Zone", "full_court_press": "Full-Court Press",
        "switching_man": "Switching Man", "drop_coverage": "Drop Coverage",
        "hedge_hard": "Hard Hedge / ICE",
    }
    scheme_label = names.get(scheme, scheme.replace("_", " ").title())

    if coaching_mode == "developmental":
        sys = claude_service.DEV_DEF_SYS
        prompt = (
            f"DEFENSIVE TEACHING -- {scheme_label}{arch_str}\n"
            f"Concept to teach: {offensive_action}\n\n"
            "Install this scheme from scratch. Cover principles, progressions, rotations, "
            "communication calls, common breakdowns, and how to drill each piece."
        )
    else:
        sys = claude_service.LIVE_DEF_SYS
        prompt = (
            f"DEFENSIVE COUNTER -- {scheme_label}{arch_str}\n"
            f"Offensive action to stop: {offensive_action}\n\n"
            "Call the adjustment now. Player-by-player assignments, key rotation, primary counter."
        )

    async def generate():
        try:
            async for chunk in claude_service.analyze_stream(
                prompt=prompt, system_prompt=sys, override_max_tokens=800):
                if chunk:
                    yield _tok(chunk)
            yield _done()
        except Exception as e:
            yield _tok(f"Scheme analysis unavailable: {e}")
            yield _done()

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/lineup-analysis")
@limiter.limit(_CLAUDE_LIMIT)
async def compat_lineup_analysis(request: Request):
    """Legacy SSE: stream lineup chemistry and fit analysis."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    players = body.get("players") or []
    roster_str = ", ".join(p.get("name", "Unknown") for p in players) or "No players"
    prompt = (
        f"LINEUP ADJUSTMENT -- {roster_str}\n\n"
        "Analyze spacing, ball handling, defensive versatility, chemistry fit, "
        "optimal deployment situation, and the primary weakness to attack."
    )

    async def generate():
        try:
            async for chunk in claude_service.analyze_stream(
                prompt=prompt, system_prompt=claude_service.LIVE_LINEUP_SYS, override_max_tokens=800):
                if chunk:
                    yield _tok(chunk)
            yield _done()
        except Exception as e:
            yield _tok(f"Lineup analysis unavailable: {e}")
            yield _done()

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


_PROJ_SYS = (
    "You are an NBA statistician and projection analyst. "
    "Use historical trends, age curve, team context, and role trajectory. "
    "Specific projected ranges. Show your reasoning."
)

@router.post("/stat-projection")
@limiter.limit(_CLAUDE_LIMIT)
async def compat_stat_projection(request: Request):
    """Legacy SSE: stream stat projection or development projection for a player."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    player_name = body.get("player_name") or "Unknown Player"
    coaching_mode = body.get("coaching_mode") or "live"

    if coaching_mode == "developmental":
        sys = claude_service.DEV_PROJ_SYS
        prompt = (
            f"PLAYER PROJECTION -- {player_name.upper()}\n\n"
            "Map this player's development trajectory: current strengths, identified weaknesses, "
            "2-year development arc, peak profile. Include upside and floor scenarios with the "
            "key variables that drive variance."
        )
        max_tokens = 900
    else:
        sys = _PROJ_SYS
        prompt = (
            f"STAT PROJECTION -- {player_name.upper()}\n\n"
            "Project stats for the rest of this season and next season. "
            "Cover PPG, RPG, APG, TS%, usage. Include upside and downside scenarios "
            "with specific projected stat lines and the key variables driving variance."
        )
        max_tokens = 700

    async def generate():
        try:
            async for chunk in claude_service.analyze_stream(
                prompt=prompt, system_prompt=sys, override_max_tokens=max_tokens):
                if chunk:
                    yield _tok(chunk)
            yield _done()
        except Exception as e:
            yield _tok(f"Projection unavailable: {e}")
            yield _done()

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/chat")
@limiter.limit(_CLAUDE_LIMIT)
async def compat_chat(request: Request):
    """Legacy /chat → /chat/message. Re-uses the same streaming logic."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    return await chat_message(request, body)


# cache bust
