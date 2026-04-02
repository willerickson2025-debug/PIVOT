"""
agent_service.py
================
Background intelligence agents that run on a schedule to pre-warm caches,
enrich game context, and sharpen Claude's analysis quality.

These agents run silently — nothing surfaces to the UI. They are triggered by
Railway cron hitting POST /api/v1/agents/nightly (or /pregame for same-day).

What they do
------------
- Pre-analyze tomorrow's game slate with enriched prompts and higher token
  budgets, so the first user load is instant.
- Pre-warm player caches for every player in tomorrow's matchups.
- Run a "quality pass" that re-evaluates slate analyses with a second Claude
  call focused on sharpening predictions and flagging vague language.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
from typing import Any
from zoneinfo import ZoneInfo

from app.core.cache import analysis_cache
from app.services import claude_service, nba_service
from app.services.analysis_service import (
    NBA_ANALYST_SYSTEM_PROMPT,
    _build_player_stat_block,
    _format_games_for_prompt,
    _render_stat_block,
)

logger = logging.getLogger(__name__)

_CENTRAL_TZ = "America/Chicago"
_DEFAULT_SEASON = 2025

# Cache TTLs
_SLATE_TTL    = 8 * 3600   # 8 hours — pre-built overnight, good through morning
_PLAYER_TTL   = 4 * 3600   # 4 hours — player form doesn't change that fast
_ENRICHED_TTL = 6 * 3600   # 6 hours — enriched matchup context


# ---------------------------------------------------------------------------
# Enriched Slate Agent
# ---------------------------------------------------------------------------

async def run_pregame_agent(target_date: str | None = None) -> dict[str, Any]:
    """
    Pre-analyze a game slate with an enriched prompt and elevated token budget.

    Uses a deeper prompt than the standard analyze_today_games() call — asks
    Claude to think like a professional handicapper, reference historical
    matchup patterns, and flag injury implications. Result is stored in cache
    so the first user hitting /analysis/today-games gets a near-instant response.

    Parameters
    ----------
    target_date:
        ISO date string. Defaults to tomorrow in US Central Time.

    Returns
    -------
    dict
        Run summary: date, games_found, players_warmed, errors.
    """
    if not target_date:
        tz = ZoneInfo(_CENTRAL_TZ)
        target_date = (datetime.datetime.now(tz) + datetime.timedelta(days=1)).strftime("%Y-%m-%d")

    logger.info("Pregame agent starting | date=%s", target_date)
    errors: list[str] = []

    # -----------------------------------------------------------------------
    # Step 1 — Fetch slate
    # -----------------------------------------------------------------------
    try:
        games = await nba_service.get_games_by_date(target_date)
    except Exception as exc:
        logger.error("Pregame agent: failed to fetch games | %s", exc)
        return {"status": "error", "error": str(exc)}

    if not games:
        logger.info("Pregame agent: no games on %s — nothing to do", target_date)
        return {"status": "ok", "date": target_date, "games_found": 0, "players_warmed": 0}

    game_summary = _format_games_for_prompt(games)

    # -----------------------------------------------------------------------
    # Step 2 — Enriched slate analysis (higher tokens, sharper prompt)
    # -----------------------------------------------------------------------
    enriched_prompt = (
        f"NBA slate for {target_date} — {len(games)} game(s):\n\n"
        f"{game_summary}\n\n"
        f"This is a pre-game intelligence brief. Go deeper than a standard breakdown.\n\n"
        f"For each game:\n"
        f"- Open with the single most important factor most analysts are sleeping on\n"
        f"- Cover pace differential, defensive rating matchup, and home/road splits\n"
        f"- Identify the x-factor player who decides the game (not the star — the role player)\n"
        f"- Give a sharp, unhedged prediction with a one-line rationale\n\n"
        f"Close with your best bet of the night — one game, one side, one reason."
    )

    try:
        result = await claude_service.analyze(
            prompt=enriched_prompt,
            system_prompt=NBA_ANALYST_SYSTEM_PROMPT,
            override_max_tokens=2048,
        )

        from app.models.schemas import GameAnalysisResponse
        cached_response = GameAnalysisResponse(
            games=games,
            analysis=result.analysis,
            model=result.model,
            tokens_used=result.tokens_used,
        )
        cache_key = f"slate:{target_date}"
        analysis_cache.set(cache_key, cached_response, ttl=_SLATE_TTL)
        logger.info("Pregame agent: slate cached | key=%s tokens=%d", cache_key, result.tokens_used)

    except Exception as exc:
        logger.error("Pregame agent: slate analysis failed | %s", exc)
        errors.append(f"slate: {exc}")

    # -----------------------------------------------------------------------
    # Step 3 — Pre-warm player cache for all rostered players
    # -----------------------------------------------------------------------
    player_names: list[str] = []
    for game in games:
        for team in [game.home_team, game.visitor_team]:
            players_raw = await _get_team_player_names(team.id)
            player_names.extend(players_raw)

    player_names = list(dict.fromkeys(player_names))  # dedupe, preserve order
    warmed = 0

    # Warm up to 20 players concurrently in batches of 4 to avoid rate limits
    for i in range(0, min(len(player_names), 20), 4):
        batch = player_names[i:i+4]
        results = await asyncio.gather(
            *[_warm_player(name) for name in batch],
            return_exceptions=True,
        )
        for name, r in zip(batch, results):
            if isinstance(r, Exception):
                logger.warning("Pregame agent: player warm failed | %s %s", name, r)
                errors.append(f"player:{name}: {r}")
            else:
                warmed += 1
        await asyncio.sleep(0.5)  # brief pause between batches

    logger.info(
        "Pregame agent complete | date=%s games=%d players_warmed=%d errors=%d",
        target_date, len(games), warmed, len(errors),
    )
    return {
        "status": "ok",
        "date": target_date,
        "games_found": len(games),
        "players_warmed": warmed,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Quality Pass Agent
# ---------------------------------------------------------------------------

async def run_quality_pass(target_date: str | None = None) -> dict[str, Any]:
    """
    Re-evaluate a cached slate analysis and return a sharpened version.

    Fetches the existing cached analysis and runs a second Claude call that
    acts as an editor — cutting vague language, strengthening predictions,
    and flagging any plays worth revisiting. Replaces the cache entry if the
    quality pass produces a meaningfully longer/sharper output.

    Parameters
    ----------
    target_date:
        ISO date string. Defaults to today.
    """
    if not target_date:
        tz = ZoneInfo(_CENTRAL_TZ)
        target_date = datetime.datetime.now(tz).strftime("%Y-%m-%d")

    cache_key = f"slate:{target_date}"
    existing = analysis_cache.get(cache_key)

    if not existing:
        logger.info("Quality pass: no cached slate for %s — skipping", target_date)
        return {"status": "skipped", "reason": "no_cached_slate"}

    quality_prompt = (
        f"Here is an NBA analyst breakdown for {target_date}:\n\n"
        f"{existing.analysis}\n\n"
        f"You are a senior editor reviewing this for a premium intelligence product. "
        f"Rewrite it with these goals:\n"
        f"1. Cut any sentence that doesn't add real information\n"
        f"2. Sharpen every prediction — add a specific reason if one is missing\n"
        f"3. Flag any games where the pick feels soft or contradicted by the data\n"
        f"4. If a player matchup is mentioned vaguely, name the specific coverage or scheme\n\n"
        f"Output the improved analysis only. No meta-commentary about what you changed."
    )

    try:
        result = await claude_service.analyze(
            prompt=quality_prompt,
            system_prompt=NBA_ANALYST_SYSTEM_PROMPT,
            override_max_tokens=2048,
        )

        if len(result.analysis) >= len(existing.analysis) * 0.85:
            from app.models.schemas import GameAnalysisResponse
            sharpened = GameAnalysisResponse(
                games=existing.games,
                analysis=result.analysis,
                model=result.model,
                tokens_used=result.tokens_used,
            )
            analysis_cache.set(cache_key, sharpened, ttl=_SLATE_TTL)
            logger.info("Quality pass: cache updated | date=%s", target_date)
            return {"status": "ok", "date": target_date, "improved": True}
        else:
            logger.info("Quality pass: output shorter than original — keeping original")
            return {"status": "ok", "date": target_date, "improved": False}

    except Exception as exc:
        logger.error("Quality pass failed | %s", exc)
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Nightly Full Run
# ---------------------------------------------------------------------------

async def run_nightly(target_date: str | None = None) -> dict[str, Any]:
    """
    Full nightly run: pregame analysis + quality pass on today's slate.

    Intended to be called at ~11 PM Central via Railway cron so tomorrow's
    analysis is ready at midnight and this morning's analysis has been sharpened.
    """
    logger.info("Nightly agent run starting")

    tz = ZoneInfo(_CENTRAL_TZ)
    today = datetime.datetime.now(tz).strftime("%Y-%m-%d")
    tomorrow = (datetime.datetime.now(tz) + datetime.timedelta(days=1)).strftime("%Y-%m-%d")

    pregame, quality = await asyncio.gather(
        run_pregame_agent(tomorrow),
        run_quality_pass(today),
        return_exceptions=True,
    )

    return {
        "pregame": pregame if not isinstance(pregame, Exception) else str(pregame),
        "quality_pass": quality if not isinstance(quality, Exception) else str(quality),
    }


# ---------------------------------------------------------------------------
# Internal Helpers
# ---------------------------------------------------------------------------

async def _get_team_player_names(team_id: int) -> list[str]:
    """Fetch top players for a team to warm the cache."""
    try:
        players = await nba_service.search_players("")
        team_players = [p for p in players if p.team and p.team.id == team_id]
        return [f"{p.first_name} {p.last_name}" for p in team_players[:8]]
    except Exception:
        return []


async def _warm_player(player_name: str) -> None:
    """Pre-build and cache a player stat block."""
    cache_key = f"player:{player_name.lower()}:{_DEFAULT_SEASON}"
    if analysis_cache.get(cache_key) is not None:
        return  # already warm

    try:
        player, agg = await _build_player_stat_block(player_name, _DEFAULT_SEASON)
        if agg["total_games"] == 0:
            return

        stat_block = _render_stat_block(player, _DEFAULT_SEASON, agg)
        result = await claude_service.analyze(
            prompt=f"Analyze this player's {_DEFAULT_SEASON} NBA season:\n\n{stat_block}",
            system_prompt=NBA_ANALYST_SYSTEM_PROMPT,
            override_max_tokens=1500,
        )
        payload = {
            "player": player.model_dump(),
            "season": _DEFAULT_SEASON,
            "averages": {
                "points": agg["avg_pts"], "rebounds": agg["avg_reb"],
                "assists": agg["avg_ast"], "steals": agg["avg_stl"],
                "blocks": agg["avg_blk"], "fg_pct": agg["avg_fg"],
                "fg3_pct": agg["avg_fg3"], "ft_pct": agg["avg_ft"],
            },
            "last_10": {
                "points": agg["recent_pts"], "rebounds": agg["recent_reb"],
                "assists": agg["recent_ast"], "steals": agg["recent_stl"],
                "blocks": agg["recent_blk"], "fg_pct": agg["recent_fg"],
                "fg3_pct": agg["recent_fg3"],
            },
            "games_played": agg["total_games"],
            "analysis": result.analysis,
            "model": result.model,
            "tokens_used": result.tokens_used,
        }
        analysis_cache.set(cache_key, payload, ttl=_PLAYER_TTL)
        logger.debug("Player warmed | name=%s", player_name)
    except Exception as exc:
        raise exc
