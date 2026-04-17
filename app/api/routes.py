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
async def get_team_roster(abbr: str = Query(..., description="Team abbreviation, e.g. LAL")):
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

    # 2. ESPN scoreboard — recent results + today's games
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(_ESPN_SCOREBOARD)
            if resp.status_code == 200:
                data = resp.json()
                events = data.get("events", [])
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
                    _log.info("chat live_ctx: ESPN scores empty (no final/live games found)")
            else:
                _log.warning("chat live_ctx: ESPN scoreboard HTTP %d", resp.status_code)
    except Exception as exc:
        _log.warning("chat live_ctx: ESPN scoreboard failed: %s", exc)

    # 3. ESPN news headlines for current context
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(_ESPN_NEWS)
            if resp.status_code == 200:
                data = resp.json()
                articles = data.get("articles", [])[:5]
                headlines = [a.get("headline", "") for a in articles if a.get("headline")]
                if headlines:
                    lines.append("Recent NBA headlines: " + " | ".join(headlines))
                    _log.info("chat live_ctx: ESPN news OK (%d headlines)", len(headlines))
    except Exception as exc:
        _log.warning("chat live_ctx: ESPN news failed: %s", exc)

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

