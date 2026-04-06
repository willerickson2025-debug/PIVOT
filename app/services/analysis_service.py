"""
analysis_service.py
===================
Business logic layer — bridges NBA data retrieval with Claude-powered analysis.

Responsibilities
----------------
- Prompt construction from domain data
- Stat aggregation and trend computation
- Routing to the correct Claude system prompt per analysis type
- Response shaping for API consumers

This module contains no HTTP code (that lives in nba_service.py) and no
Anthropic SDK calls (those live in claude_service.py).
"""

from __future__ import annotations

import asyncio
import datetime
import logging
from typing import Any, AsyncGenerator, Optional
from zoneinfo import ZoneInfo

from app.core.cache import analysis_cache
from app.models.schemas import Game, GameAnalysisResponse
from app.services import claude_service, nba_service

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_SEASON: int = 2025
_RECENT_FORM_WINDOW: int = 10   # number of most-recent games used for trend data
_MAX_TRADE_PLAYERS: int = 4     # max players to fetch live stats for in a trade

# Fast model for latency-sensitive paths (player/game analysis).
# Haiku is ~5-8x faster than Sonnet; plenty of reasoning for structured stat analysis.
_FAST_MODEL: str = "claude-haiku-4-5-20251001"


# ---------------------------------------------------------------------------
# System Prompts
# ---------------------------------------------------------------------------

NBA_ANALYST_SYSTEM_PROMPT: str = """You are the highest-paid NBA analyst in the country. Your clients are GMs, bettors, and executives who pay premium money for your edge. They don't want summaries. They want what you actually think.

CRITICAL CONTEXT: Today is April 2026. The 2025-26 NBA season is actively in progress right now. Do not say the season "has not yet occurred" or treat it as a future event. It is happening. Stats provided are live 2025-26 season data.

STRICT DATA GROUNDING — NON-NEGOTIABLE:
The data payload in this prompt is the only reality. It completely overrides anything from your training about current rosters, trades, or player team assignments. If the data says a player is on a specific team, that is absolute fact — do not contradict it with pre-training knowledge. Before writing any team or player analysis, silently verify which players and teams are actually present in the provided data, then analyze only those.

CRITICAL RULES FOR MISSING OR INCOMPLETE DATA:
You will receive a stat block with season averages and recent game logs. If any section of that data is zero, empty, or missing, follow these rules without exception:
1. Do NOT speculate on why the data is missing. Do not mention injuries, suspensions, rest, load management, two-way contracts, or any real-world explanation for absent numbers.
2. Do NOT reference the data pipeline, API, feed, or any technical system. You are an analyst, not a developer.
3. Do NOT invent or hallucinate statistics that were not provided.
4. If recent game logs are missing but season averages exist, analyze only the season averages and skip any recent-form commentary entirely.
5. If a stat reads 0.0 across the board, treat it as a data gap — acknowledge it in one sentence and pivot to what you do know about the player from their career profile.

You have watched more NBA film than anyone in this conversation. You know pace differentials, defensive rating trends, how teams perform on back-to-backs, which coaches make in-game adjustments and which ones don't, which stars disappear in fourth quarters. You use that knowledge.

When analyzing a game: open with the sharpest thing you know about this matchup — the thing most people miss. Then cover the stylistic clash, the one player who will determine the outcome, and the specific reason one team wins. Close with a confident, unhedged prediction.

When analyzing a player: open with what the numbers actually mean in context — not just what they are. Call out if a player is overrated, underrated, declining, or ascending. Reference the last 10 games trend vs season average and explain what it signals. End with one sentence that captures exactly where this player stands right now.

Every word must earn its place. If a sentence doesn't add information or edge, cut it. No throat-clearing. No "it's worth noting." No "at the end of the day." Start with the insight, not the setup.

FORMATTING — NON-NEGOTIABLE:
Plain prose only. No markdown. No asterisks, no pound signs, no dashes used as bullets, no numbered lists, no bold, no italics, no horizontal rules, no headers. Paragraphs separated by one blank line. Write like a column in The Athletic or a Sharp report — dense, confident, readable."""


FRONT_OFFICE_SYSTEM_PROMPT: str = """You are an experienced NBA front-office analyst writing for general managers and assistant GMs.

CRITICAL CONTEXT: Today is April 2026. The 2025-26 NBA season is actively in progress. Do not treat it as a future event.

STRICT DATA GROUNDING — NON-NEGOTIABLE:
The roster and contract data in this prompt is the only reality. It overrides your training data about current rosters, trades, and player locations. If the data places a player on a team, treat it as fact. Before writing any team or trade analysis, mentally map the exact players and salaries provided — analyze only what is in the payload, not what your training data says about who plays where.

When evaluating a trade: open with one sentence naming the winning team and why — this is your verdict. Every sentence that follows must support that same conclusion. Never contradict your opening verdict anywhere in the response. If you say Team A wins, all analysis must explain why Team A wins. Do not then pivot and argue that Team B wins or benefits more.

After the verdict, explain the main drivers: contract timelines, age curves, fit, and roster/chemistry effects. Clearly state key risks and any important assumptions.

When analyzing a roster: give a candid assessment, name the primary problem, and recommend practical, prioritized moves (trades, signings, or contract actions) to improve the roster.

Tone and formatting:
- Be pragmatic and human: short paragraphs, plain prose, and clear recommendations.
- Quantify where possible (years, dollars, sample sizes). Call out uncertainty when data is thin.
- Deliver actionable guidance a front office can act on; avoid overly poetic or "AI-sounding" phrasing.
- Plain prose only. No markdown. No asterisks, pound signs, dashes as bullets, bold, or headers.

This prompt should produce professional, readable, and useful responses appropriate for decision-making in an NBA front office.
"""


GAME_ANALYST_SYSTEM_PROMPT: str = """You are a precision NBA game analyst. Your only job is to translate the provided game data into clean, factual observations.

CRITICAL CONTEXT: Today is April 2026. The 2025-26 NBA season is in progress.

STRICT DATA GROUNDING — ABSOLUTE RULES:
1. The data in this prompt is the only source of truth. Do not invent events, plays, momentum shifts, or individual moments that are not present in the provided stats.
2. Do not use training knowledge to fill gaps. If a stat is not in the payload, it did not happen. Say nothing about it.
3. If player-level stats are missing or sparse, focus entirely on team scores and game state — do not speculate about individual contributions.
4. Never reference the data pipeline, API, feed, or any technical system. You are an analyst, not a developer.

OUTPUT STRUCTURE by game state:
- FINAL: Key performers (with exact stats), the decisive factor in the outcome, what each team did well or failed at, one sentence on implications.
- LIVE: Who is winning and why based on the actual numbers, who is producing, current trajectory.
- UPCOMING: Stylistic clash, key individual battles, each team's edge, confident prediction.

Be specific — name players, cite actual numbers. Dense, confident prose. No hedging.

FORMATTING: Plain prose only. No markdown, no bullets, no asterisks, no headers, no numbered lists. Paragraphs separated by one blank line."""


COACH_SYSTEM_PROMPT: str = """You are an elite NBA head coach with a championship pedigree. You have the live box score in front of you. Coaches pay for your input because you see things others miss and give answers without wasting time.

STRICT DATA GROUNDING: The box score provided is the only source of truth. Use the exact players, stats, and game state from the payload. Do not substitute players from your training data.

When making in-game adjustments: open with the single most important problem and fix. Then cover the specific players, the scheme, and why it works against what this opponent is running. Use the actual box score numbers — foul trouble changes lineups, a player 0-for-6 from three doesn't get ball screens, a player with 3 turnovers doesn't handle late-game possessions.

When drawing up a timeout play: name the play first. Describe the motion in plain terms. Name who screens, who gets the ball, the primary read, and the secondary read if the first is taken away. Close with one sentence on why this works against what the defense is likely running.

You never ask for more information. You work with what you have. You give answers, not questions.

FORMATTING: Plain prose only. No markdown, no asterisks, no bullets, no numbered lists, no headers. Dense, decisive prose paragraphs only. Coaches need answers in 20 seconds."""


# ---------------------------------------------------------------------------
# Section-Specific Analyst Prompts
# ---------------------------------------------------------------------------

SECTION_PROMPTS: dict[str, str] = {
    "offense": (
        "Give a deep offensive breakdown of this player. Cover scoring volume, shot selection, "
        "efficiency by zone, usage rate, creation vs off-ball role, pick and roll tendencies, "
        "and how defenses are currently scheming against them. Be specific with the numbers."
    ),
    "defense": (
        "Give a deep defensive breakdown. Cover on-ball defense, help defense, switching ability, "
        "defensive rebounding, steal and block tendencies, the matchup problems they create or solve, "
        "and their measurable impact on team defensive rating."
    ),
    "off_the_court": (
        "Analyze this player's off-court impact: leadership, locker room presence, media profile, "
        "coachability, history of team chemistry effects, and what kind of environment they thrive "
        "in vs struggle in."
    ),
    "injuries": (
        "Analyze this player's injury history and durability. Cover games-played trends over the "
        "last 3 seasons, known injury history, load management patterns, position-related injury "
        "risk at their age, and what to watch for going forward."
    ),
    "financials": (
        "Break down this player's financial situation: estimated contract value vs production, "
        "whether they are an overpaid or underpaid asset, trade value relative to salary, years "
        "remaining context, and what a front office should think about this contract."
    ),
}


# ---------------------------------------------------------------------------
# Shared Stat Utilities
# ---------------------------------------------------------------------------

def _safe_avg(values: list[Optional[float]]) -> float:
    """
    Compute the arithmetic mean of a list, ignoring ``None`` entries.

    Returns 0.0 when the list is empty or contains only ``None`` values,
    rather than raising ``ZeroDivisionError``.

    Parameters
    ----------
    values:
        List of numeric values, potentially containing ``None``.

    Returns
    -------
    float
        Mean of non-null values, rounded to 1 decimal place.
    """
    clean: list[float] = [v for v in values if v is not None]
    return round(sum(clean) / len(clean), 1) if clean else 0.0


def _trend_label(recent: float, season: float) -> str:
    """
    Return a signed difference string showing recent-form vs season average.

    Examples: ``"+3.2"``, ``"-1.5"``, ``"+0.0"``

    Parameters
    ----------
    recent:
        Average over the most recent N games.
    season:
        Full-season average.

    Returns
    -------
    str
        Signed delta, e.g. ``"+2.1"`` or ``"-0.8"``.
    """
    diff = round(recent - season, 1)
    sign = "+" if diff >= 0 else ""
    return f"{sign}{diff}"


def _pct_trend_label(recent: float, season: float) -> str:
    """
    Variant of ``_trend_label`` for percentage values stored as decimals.

    Multiplies both values by 100 before computing the delta so the output
    reads as a percentage-point difference, e.g. ``"+4.2%"`` rather than
    ``"+0.042"``.

    Parameters
    ----------
    recent:
        Recent field-goal (or other) percentage as a decimal (0.0–1.0).
    season:
        Season field-goal (or other) percentage as a decimal (0.0–1.0).

    Returns
    -------
    str
        Signed percentage-point delta, e.g. ``"+4.2%"``.
    """
    diff = round((recent - season) * 100, 1)
    sign = "+" if diff >= 0 else ""
    return f"{sign}{diff}%"


# ---------------------------------------------------------------------------
# Game Formatting Helpers
# ---------------------------------------------------------------------------

def _format_single_game(game: Game) -> str:
    """
    Render a single ``Game`` as a multi-line display string for prompt injection.

    Shows the score if either team has scored, otherwise shows the matchup in
    the conventional away @ home format.

    Parameters
    ----------
    game:
        Hydrated ``Game`` domain object.

    Returns
    -------
    str
        Three-line string: score line, home team detail, away team detail.
    """
    home = game.home_team
    away = game.visitor_team
    has_score = game.home_team_score > 0 or game.visitor_team_score > 0

    score_line = (
        f"{away.abbreviation} {game.visitor_team_score} — "
        f"{game.home_team_score} {home.abbreviation}"
        if has_score
        else f"{away.abbreviation} @ {home.abbreviation}"
    )

    return (
        f"{score_line} | {game.status}\n"
        f"  HOME: {home.city} {home.name} ({home.conference} / {home.division})\n"
        f"  AWAY: {away.city} {away.name} ({away.conference} / {away.division})"
    )


def _format_games_for_prompt(games: list[Game]) -> str:
    """
    Render all games in a date's slate as a single prompt-ready block.

    Parameters
    ----------
    games:
        List of ``Game`` objects for a given date.

    Returns
    -------
    str
        Newline-separated game display strings, or a no-games notice.
    """
    if not games:
        return "No games scheduled for this date."
    return "\n\n".join(_format_single_game(g) for g in games)


# ---------------------------------------------------------------------------
# Player Resolution
# ---------------------------------------------------------------------------

def _name_match_score(player: Any, query: str) -> int:
    """
    Score how well a player matches a query string. Higher = better match.

    Scoring tiers (mutually exclusive, highest wins):
      100 — full name exact match (case-insensitive)
       80 — full name contained in query or query contained in full name
       60 — both first and last name tokens appear in the query
       40 — last name exact match
       20 — last name contained in query
        0 — anything else

    Parameters
    ----------
    player:
        Player domain object with ``first_name`` and ``last_name`` attributes.
    query:
        The original search string entered by the caller.

    Returns
    -------
    int
        Match quality score. Higher is better.
    """
    q = query.lower().strip()
    tokens = q.split()
    first = (player.first_name or "").lower().strip()
    last = (player.last_name or "").lower().strip()
    full = f"{first} {last}".strip()

    # Exact full match
    if full == q:
        return 100

    # Two-token query: enforce strict first + last correctness.
    # This kills "Seth Curry" when searching "Stephen Curry" — partial
    # containment scoring no longer lets wrong first names compete.
    if len(tokens) >= 2:
        q_first = tokens[0]
        q_last = tokens[-1]

        # Last name must match exactly or the player is invalid
        if last != q_last:
            return 0

        # Exact first name match
        if first == q_first:
            return 95

        # Prefix match only — "Steph" → "Stephen", not "Seth"
        if first.startswith(q_first) or q_first.startswith(first):
            return 85

        # First name doesn't match at all → reject
        return 0

    # Single-token query ("Steph", "LeBron", "Giannis")
    if first.startswith(q) or q.startswith(first):
        return 80
    if last == q:
        return 60
    if last in q:
        return 40
    return 0


def _resolve_best_player(players: list[Any], query: str) -> Any:
    """
    Return the player from *players* whose name best matches *query*.

    Scores every candidate with ``_name_match_score`` and returns the highest
    scorer. Falls back to ``players[0]`` only when nothing scores above zero,
    logging a warning so the mismatch is visible. This prevents the class of
    bug where e.g. "LeBron James" resolves to "James Ennis III" because the
    API returns results sorted by first name alphabetically.

    Parameters
    ----------
    players:
        Non-empty list of Player objects returned by the search endpoint.
    query:
        The original search string entered by the caller.

    Returns
    -------
    Any
        Best-matching Player object.

    Raises
    ------
    ValueError
        If ``players`` is empty.
    """
    if not players:
        raise ValueError(f"No player found matching '{query}'")

    scored = sorted(players, key=lambda p: _name_match_score(p, query), reverse=True)
    best = scored[0]
    best_score = _name_match_score(best, query)

    logger.debug(
        "Player resolution scores | query=%r top=%s %s score=%d",
        query,
        best.first_name,
        best.last_name,
        best_score,
    )

    if best_score == 0:
        # Returning a zero-score match means none of the API results have any
        # name overlap with the query — silently returning players[0] in this
        # case produces confident wrong answers (e.g. "Steph" → Seth Curry).
        # Raise instead so the caller can surface a useful error to the user.
        raise ValueError(
            f"No player found matching '{query}' — closest was "
            f"{best.first_name} {best.last_name} but names don't match"
        )

    return best


# ---------------------------------------------------------------------------
# Player Stat Aggregation
# ---------------------------------------------------------------------------

async def _resolve_player_by_name(player_name: str) -> Any:
    """
    Resolve a player name string to a Player object via BallDontLie search.

    Used only by name-driven callers like ``analyze_trade`` where the caller
    has a name string but not a player ID. All user-facing analysis endpoints
    should use ``_build_player_stat_block(player_id, season)`` directly.

    Search strategy:
      1. first_name= + last_name= (explicit params, most precise)
      2. last_name= only fallback
      3. Single-token search=

    Raises
    ------
    ValueError
        If no player matching ``player_name`` can be found.
    """
    clean_name = player_name.strip()
    tokens = clean_name.split()

    if len(tokens) >= 2:
        first_tok = tokens[0]
        last_tok = " ".join(tokens[1:])
        players = await nba_service.search_players(clean_name, first_name=first_tok, last_name=last_tok)
        if not players:
            players = await nba_service.search_players(clean_name, last_name=last_tok)
    else:
        players = await nba_service.search_players(clean_name)

    if not players:
        raise ValueError(f"No player found matching '{player_name}'")

    return _resolve_best_player(players, clean_name)


async def _build_player_stat_block(player_id: int, season: int) -> tuple[Any, dict[str, Any]]:
    """
    Fetch a player by ID, retrieve their game logs and official averages, and
    return both the ``Player`` object and a dict of computed stat aggregates.

    Parameters
    ----------
    player_id:
        BallDontLie player ID — no name guessing, no scoring, no ambiguity.
    season:
        NBA season start year.

    Returns
    -------
    tuple[Player, dict]
        ``(player, aggregates)`` where ``aggregates`` contains keys:
        ``total_games``, ``avg_pts``, ``avg_reb``, ``avg_ast``, ``avg_stl``,
        ``avg_blk``, ``avg_fg``, ``avg_fg3``, ``avg_ft``, ``recent_pts``,
        ``recent_reb``, ``recent_ast``, ``recent_stl``, ``recent_blk``,
        ``recent_fg``, ``recent_fg3``.

    Raises
    ------
    httpx.HTTPStatusError
        If the player ID does not exist or the API returns an error.
    """
    player = await nba_service.get_player_by_id(player_id)

    logger.info(
        "Player fetched by id | player_id=%d matched=%s %s",
        player_id,
        player.first_name,
        player.last_name,
    )

    stats, official = await asyncio.gather(
        nba_service.get_player_stats(player.id, season),
        nba_service.get_season_averages(player.id, season),
    )

    # Always prefer the official games_played from the season averages endpoint —
    # the raw game logs can include playoff/preseason entries that inflate the count.
    # Fall back to len(stats) only if the official endpoint returns nothing.
    total_games = int(official.get("games_played") or 0) or len(stats)

    # Full-season averages: prefer the official endpoint values; fall back to
    # computing from raw game logs when the season-averages endpoint returns
    # nothing (common mid-season or for players with limited appearances).
    avg_pts = round(float(official.get("pts") or _safe_avg([s.points for s in stats])), 1)
    avg_reb = round(float(official.get("reb") or _safe_avg([s.rebounds for s in stats])), 1)
    avg_ast = round(float(official.get("ast") or _safe_avg([s.assists for s in stats])), 1)
    avg_stl = round(float(official.get("stl") or _safe_avg([s.steals for s in stats])), 1)
    avg_blk = round(float(official.get("blk") or _safe_avg([s.blocks for s in stats])), 1)
    avg_fg  = round(float(official.get("fg_pct") or _safe_avg([s.fg_pct for s in stats])), 3)
    avg_fg3 = round(float(official.get("fg3_pct") or _safe_avg([s.fg3_pct for s in stats])), 3)
    avg_ft  = round(float(official.get("ft_pct") or _safe_avg([s.ft_pct for s in stats])), 3)

    # Recent form: last N games by chronological order (stats are pre-sorted
    # ascending by game_id in nba_service.get_player_stats).
    recent = stats[-_RECENT_FORM_WINDOW:]

    recent_pts  = _safe_avg([s.points for s in recent])
    recent_reb  = _safe_avg([s.rebounds for s in recent])
    recent_ast  = _safe_avg([s.assists for s in recent])
    recent_stl  = _safe_avg([s.steals for s in recent])
    recent_blk  = _safe_avg([s.blocks for s in recent])
    recent_fg   = _safe_avg([s.fg_pct for s in recent])
    recent_fg3  = _safe_avg([s.fg3_pct for s in recent])

    aggregates: dict[str, Any] = {
        "total_games": total_games,
        "avg_pts": avg_pts,
        "avg_reb": avg_reb,
        "avg_ast": avg_ast,
        "avg_stl": avg_stl,
        "avg_blk": avg_blk,
        "avg_fg": avg_fg,
        "avg_fg3": avg_fg3,
        "avg_ft": avg_ft,
        "recent_pts": recent_pts,
        "recent_reb": recent_reb,
        "recent_ast": recent_ast,
        "recent_stl": recent_stl,
        "recent_blk": recent_blk,
        "recent_fg": recent_fg,
        "recent_fg3": recent_fg3,
    }

    return player, aggregates


def _render_stat_block(player: Any, season: int, agg: dict[str, Any]) -> str:
    """
    Format a fully assembled stat block string for injection into a Claude prompt.

    Parameters
    ----------
    player:
        ``Player`` domain object.
    season:
        NBA season start year.
    agg:
        Aggregates dict produced by ``_build_player_stat_block``.

    Returns
    -------
    str
        Multi-line stat block, plain text, no markdown.
    """
    team_name = player.team.name if player.team else "N/A"

    return (
        f"Player: {player.first_name} {player.last_name}\n"
        f"Team: {team_name} | Position: {player.position or 'N/A'} | "
        f"Season: {season} | Games: {agg['total_games']}\n"
        f"\n"
        f"SEASON AVERAGES:\n"
        f"  PTS: {agg['avg_pts']} | REB: {agg['avg_reb']} | AST: {agg['avg_ast']} | "
        f"STL: {agg['avg_stl']} | BLK: {agg['avg_blk']}\n"
        f"  FG%: {agg['avg_fg']:.1%} | 3P%: {agg['avg_fg3']:.1%} | FT%: {agg['avg_ft']:.1%}\n"
        f"\n"
        f"LAST {_RECENT_FORM_WINDOW} GAMES:\n"
        f"  PTS: {agg['recent_pts']} ({_trend_label(agg['recent_pts'], agg['avg_pts'])} vs season) | "
        f"REB: {agg['recent_reb']} ({_trend_label(agg['recent_reb'], agg['avg_reb'])}) | "
        f"AST: {agg['recent_ast']} ({_trend_label(agg['recent_ast'], agg['avg_ast'])})\n"
        f"  STL: {agg['recent_stl']} ({_trend_label(agg['recent_stl'], agg['avg_stl'])}) | "
        f"BLK: {agg['recent_blk']} ({_trend_label(agg['recent_blk'], agg['avg_blk'])})\n"
        f"  FG%: {agg['recent_fg']:.1%} ({_pct_trend_label(agg['recent_fg'], agg['avg_fg'])}) | "
        f"3P%: {agg['recent_fg3']:.1%} ({_pct_trend_label(agg['recent_fg3'], agg['avg_fg3'])})"
    )


# ---------------------------------------------------------------------------
# Public Analysis Functions
# ---------------------------------------------------------------------------

async def analyze_today_games(target_date: Optional[str] = None) -> GameAnalysisResponse:
    """
    Fetch the NBA slate for a date and return a Claude-generated analyst breakdown.

    Parameters
    ----------
    target_date:
        ISO-8601 date string (``"YYYY-MM-DD"``). Defaults to today in US
        Central Time when omitted.

    Returns
    -------
    GameAnalysisResponse
        Contains the game list, full analysis text, model metadata, and token usage.
    """
    date_label = target_date or "today"
    resolved_date = target_date or datetime.datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d")
    cache_key = f"slate:{resolved_date}"

    cached = analysis_cache.get(cache_key)
    if cached is not None:
        logger.info("Slate cache hit | date=%s", resolved_date)
        return cached

    logger.info("Analyzing game slate | date=%s", date_label)

    games = await nba_service.get_games_by_date(target_date)
    game_summary = _format_games_for_prompt(games)

    prompt = (
        f"NBA slate for {date_label} — {len(games)} game(s):\n\n"
        f"{game_summary}\n\n"
        f"Give a full analyst breakdown of tonight's slate. For each game: identify the key "
        f"matchup edge, the style-of-play clash, and make a prediction. Close with your best "
        f"game of the night and why."
    )

    result = await claude_service.analyze(
        prompt=prompt,
        system_prompt=NBA_ANALYST_SYSTEM_PROMPT,
        override_model=_FAST_MODEL,
        override_max_tokens=900,
    )

    logger.info(
        "Game slate analysis complete | games=%d tokens=%d",
        len(games),
        result.tokens_used,
    )

    response = GameAnalysisResponse(
        games=games,
        analysis=result.analysis,
        model=result.model,
        tokens_used=result.tokens_used,
    )
    analysis_cache.set(cache_key, response, ttl=3600)
    return response


async def analyze_player(
    player_id: int,
    season: int = _DEFAULT_SEASON,
) -> dict[str, Any]:
    """
    Generate a full player analysis for a given season.

    Parameters
    ----------
    player_id:
        BallDontLie player ID — passed directly from the frontend after
        the user selects from autocomplete. No name guessing.
    season:
        NBA season start year.

    Returns
    -------
    dict
        Player metadata, season and recent-form averages, analysis text, and
        token usage. Returns ``{"error": "..."}`` on lookup failure.
    """
    logger.info("Analyzing player | player_id=%d season=%d", player_id, season)

    try:
        player, agg = await _build_player_stat_block(player_id, season)
    except Exception as exc:
        logger.warning("Player lookup failed | player_id=%d error=%s", player_id, exc)
        return {"error": str(exc)}

    cache_key = f"player_analysis:{player.id}:{season}"
    cached = analysis_cache.get(cache_key)
    if cached is not None:
        logger.info("Player cache hit | player_id=%d key=%s", player.id, cache_key)
        return cached

    if agg["total_games"] == 0:
        return {
            "player": player.model_dump(),
            "season": season,
            "averages": None,
            "last_10": None,
            "games_played": 0,
            "analysis": None,
            "error": f"No {season}-{str(season+1)[-2:]} season data available for {player.first_name} {player.last_name}. The stats feed may be delayed or this player has not appeared in a game yet this season.",
        }

    stat_block = _render_stat_block(player, season, agg)

    result = await claude_service.analyze(
        prompt=f"Analyze this player's {season} NBA season:\n\n{stat_block}",
        system_prompt=NBA_ANALYST_SYSTEM_PROMPT,
        override_model=_FAST_MODEL,
        override_max_tokens=700,
    )

    logger.info(
        "Player analysis complete | player=%s %s tokens=%d",
        player.first_name,
        player.last_name,
        result.tokens_used,
    )

    payload = {
        "player": player.model_dump(),
        "season": season,
        "averages": {
            "points": agg["avg_pts"],
            "rebounds": agg["avg_reb"],
            "assists": agg["avg_ast"],
            "steals": agg["avg_stl"],
            "blocks": agg["avg_blk"],
            "fg_pct": agg["avg_fg"],
            "fg3_pct": agg["avg_fg3"],
            "ft_pct": agg["avg_ft"],
        },
        "last_10": {
            "points": agg["recent_pts"],
            "rebounds": agg["recent_reb"],
            "assists": agg["recent_ast"],
            "steals": agg["recent_stl"],
            "blocks": agg["recent_blk"],
            "fg_pct": agg["recent_fg"],
            "fg3_pct": agg["recent_fg3"],
        },
        "games_played": agg["total_games"],
        "analysis": result.analysis,
        "model": result.model,
        "tokens_used": result.tokens_used,
    }
    analysis_cache.set(cache_key, payload, ttl=3600)
    return payload


async def analyze_player_section(
    player_id: int,
    season: int,
    section: str,
) -> dict[str, Any]:
    """
    Generate a focused single-section analysis (offense, defense, financials, etc.)
    for a given player.

    Parameters
    ----------
    player_id:
        BallDontLie player ID.
    season:
        NBA season start year.
    section:
        Analysis section key. Must be one of the keys in ``SECTION_PROMPTS``.

    Returns
    -------
    dict
        Player metadata, section identifier, analysis text, and token usage.
        Returns ``{"error": "..."}`` on lookup failure or unknown section.
    """
    logger.info(
        "Analyzing player section | player_id=%d season=%d section=%s",
        player_id,
        season,
        section,
    )

    if section not in SECTION_PROMPTS:
        valid_sections = ", ".join(sorted(SECTION_PROMPTS.keys()))
        return {
            "error": f"Unknown section '{section}'. Valid sections: {valid_sections}"
        }

    try:
        player, agg = await _build_player_stat_block(player_id, season)
    except Exception as exc:
        logger.warning("Player lookup failed | player_id=%d error=%s", player_id, exc)
        return {"error": str(exc)}

    section_directive = SECTION_PROMPTS[section]

    if agg["total_games"] > 0:
        stat_context = f"Player data:\n{_render_stat_block(player, season, agg)}"
    else:
        stat_context = (
            f"Player: {player.first_name} {player.last_name} — "
            f"no {season} season stats on record."
        )

    prompt = (
        f"{section_directive}\n\n"
        f"{stat_context}\n\n"
        f"Go deep. Use your basketball knowledge beyond just the raw stats provided."
    )

    result = await claude_service.analyze(
        prompt=prompt,
        system_prompt=NBA_ANALYST_SYSTEM_PROMPT,
        override_model=_FAST_MODEL,
        override_max_tokens=500,
    )

    logger.info(
        "Player section analysis complete | player=%s %s section=%s tokens=%d",
        player.first_name,
        player.last_name,
        section,
        result.tokens_used,
    )

    return {
        "player": player.model_dump(),
        "section": section,
        "season": season,
        "analysis": result.analysis,
        "model": result.model,
        "tokens_used": result.tokens_used,
    }


async def analyze_player_stream(
    player_id: int,
    season: int = _DEFAULT_SEASON,
) -> AsyncGenerator[dict, None]:
    """
    Streaming version of analyze_player.

    Yields dicts:
      {"type": "meta", "player": ..., "averages": ..., "last_10": ..., "games_played": ...}
      {"type": "chunk", "text": "..."}
      {"type": "done"}

    If the result is cached, streams the cached analysis text character-by-character
    so the typewriter effect still plays.
    """
    try:
        player, agg = await _build_player_stat_block(player_id, season)
    except Exception as exc:
        yield {"type": "error", "message": str(exc)}
        return

    cache_key = f"player_analysis:{player.id}:{season}"
    cached = analysis_cache.get(cache_key)
    if cached is not None:
        logger.info("Player stream cache hit | player_id=%d key=%s", player.id, cache_key)
        yield {"type": "meta", "player": cached["player"], "averages": cached["averages"],
               "last_10": cached["last_10"], "games_played": cached["games_played"]}
        text = cached.get("analysis", "")
        chunk_size = 4
        for i in range(0, len(text), chunk_size):
            yield {"type": "chunk", "text": text[i:i + chunk_size]}
        yield {"type": "done"}
        return

    if agg["total_games"] == 0:
        yield {"type": "meta", "player": player.model_dump(), "averages": None,
               "last_10": None, "games_played": 0}
        prompt = (
            f"Provide a PIVOT intelligence report on {player.first_name} {player.last_name} "
            f"for the {season} NBA season. Note if this individual is not currently an active "
            f"NBA player and offer what is known — career context, current role, or a redirect."
        )
    else:
        yield {
            "type": "meta",
            "player": player.model_dump(),
            "averages": {
                "points": agg["avg_pts"], "rebounds": agg["avg_reb"], "assists": agg["avg_ast"],
                "steals": agg["avg_stl"], "blocks": agg["avg_blk"], "fg_pct": agg["avg_fg"],
                "fg3_pct": agg["avg_fg3"], "ft_pct": agg["avg_ft"],
            },
            "last_10": {
                "points": agg["recent_pts"], "rebounds": agg["recent_reb"], "assists": agg["recent_ast"],
                "steals": agg["recent_stl"], "blocks": agg["recent_blk"], "fg_pct": agg["recent_fg"],
                "fg3_pct": agg["recent_fg3"],
            },
            "games_played": agg["total_games"],
        }
        stat_block = _render_stat_block(player, season, agg)
        prompt = f"Analyze this player's {season} NBA season:\n\n{stat_block}"

    full_text = []
    async for chunk in claude_service.analyze_stream(prompt, system_prompt=NBA_ANALYST_SYSTEM_PROMPT, override_model=_FAST_MODEL, override_max_tokens=700):
        full_text.append(chunk)
        yield {"type": "chunk", "text": chunk}

    # Cache the full result
    analysis_text = "".join(full_text)
    if agg["total_games"] > 0:
        payload = {
            "player": player.model_dump(), "season": season,
            "averages": {
                "points": agg["avg_pts"], "rebounds": agg["avg_reb"], "assists": agg["avg_ast"],
                "steals": agg["avg_stl"], "blocks": agg["avg_blk"], "fg_pct": agg["avg_fg"],
                "fg3_pct": agg["avg_fg3"], "ft_pct": agg["avg_ft"],
            },
            "last_10": {
                "points": agg["recent_pts"], "rebounds": agg["recent_reb"], "assists": agg["recent_ast"],
                "steals": agg["recent_stl"], "blocks": agg["recent_blk"], "fg_pct": agg["recent_fg"],
                "fg3_pct": agg["recent_fg3"],
            },
            "games_played": agg["total_games"],
            "analysis": analysis_text, "model": "", "tokens_used": 0,
        }
        analysis_cache.set(cache_key, payload, ttl=3600)  # cache_key = player_analysis:{id}:{season}

    yield {"type": "done"}


async def analyze_trade(body: dict[str, Any]) -> dict[str, Any]:
    """
    Evaluate a proposed NBA trade from a front-office perspective.

    Attempts to fetch live stats for up to four named players involved in the
    trade. Stats are embedded in the prompt as supporting context. Pick assets
    (e.g. "2027 1st-round pick") are passed through as-is without a stats lookup.

    Parameters
    ----------
    body:
        Request body containing:
        - ``team_a`` (str): First team name.
        - ``team_b`` (str): Second team name.
        - ``team_a_gives`` (list[str]): Assets team A sends.
        - ``team_b_gives`` (list[str]): Assets team B sends.
        - ``context`` (str, optional): Additional context for the analysis.

    Returns
    -------
    dict
        Trade summary, per-player stats fetched, analysis text, and token usage.
    """
    team_a: str = body.get("team_a") or "Team A"
    team_b: str = body.get("team_b") or "Team B"
    team_a_gives: list[str] = body.get("team_a_gives") or []
    team_b_gives: list[str] = body.get("team_b_gives") or []
    context: str = body.get("context") or ""

    logger.info(
        "Analyzing trade | %s sends %s | %s sends %s",
        team_a,
        team_a_gives,
        team_b,
        team_b_gives,
    )

    # Fetch stats only for named players, skipping pick assets. Normalize keys
    all_assets: list[str] = team_a_gives + team_b_gives
    named_assets = [p for p in all_assets if "pick" not in (p or "").lower()]
    if len(named_assets) > _MAX_TRADE_PLAYERS:
        logger.info("Too many named players in trade payload — limiting stat lookups to %d of %d", _MAX_TRADE_PLAYERS, len(named_assets))
    named_players = named_assets[:_MAX_TRADE_PLAYERS]

    # Store stats keyed by normalized asset string (lowercase, stripped)
    player_stats: dict[str, str] = {}

    for name in named_players:
        key = (name or "").lower().strip()
        try:
            trade_player = await _resolve_player_by_name(name)
            _, agg = await _build_player_stat_block(trade_player.id, _DEFAULT_SEASON)
            if agg["total_games"] > 0:
                player_stats[key] = (
                    f"{agg['avg_pts']}pts / {agg['avg_reb']}reb / {agg['avg_ast']}ast "
                    f"({agg['total_games']}GP, {_DEFAULT_SEASON} season)"
                )
        except ValueError:
            logger.debug("No stats found for trade asset %r — skipping", name)
        except Exception as exc:
            logger.warning("Unexpected error fetching stats for %r: %s", name, exc)

    def _format_trade_side(team: str, gives: list[str]) -> str:
        lines = [f"{team} sends:"]
        for asset in gives:
            lookup = (asset or "").lower().strip()
            stat = player_stats.get(lookup, "")
            stat_suffix = f"  [{stat}]" if stat else ""
            lines.append(f"  - {asset}{stat_suffix}")
        return "\n".join(lines)

    trade_block = (
        f"{_format_trade_side(team_a, team_a_gives)}\n\n"
        f"{_format_trade_side(team_b, team_b_gives)}"
    )

    if context:
        trade_block += f"\n\nAdditional context: {context}"

    prompt = (
        f"Evaluate this proposed NBA trade:\n\n"
        f"{trade_block}\n\n"
        f"State which team wins this trade in your first sentence, then explain "
        f"why that same team wins throughout the rest of your analysis. Your "
        f"opening verdict and your full analysis must agree — do not name one "
        f"winner and then argue for the other. Cover contract fit, age curves, "
        f"roster impact, and key risks."
    )

    result = await claude_service.analyze(
        prompt=prompt,
        system_prompt=FRONT_OFFICE_SYSTEM_PROMPT,
    )

    logger.info("Trade analysis complete | tokens=%d", result.tokens_used)

    return {
        "team_a": team_a,
        "team_b": team_b,
        "team_a_gives": team_a_gives,
        "team_b_gives": team_b_gives,
        "player_stats": player_stats,
        "analysis": result.analysis,
        "model": result.model,
        "tokens_used": result.tokens_used,
    }


async def analyze_roster(team_name: str) -> dict[str, Any]:
    """
    Generate a front-office assessment of a team's roster, cap situation,
    and strategic priorities.

    Parameters
    ----------
    team_name:
        Full team name, city, or abbreviation. Used for both team lookup and
        as context in the prompt.

    Returns
    -------
    dict
        Team metadata (if found in the team list), analysis text, and token usage.
    """
    logger.info("Analyzing roster | team=%r", team_name)

    teams = await nba_service.get_all_teams()
    name_lower = team_name.lower()

    matched_team = next(
        (
            t for t in teams
            if name_lower in t.name.lower()
            or name_lower in t.city.lower()
            or name_lower in t.abbreviation.lower()
        ),
        None,
    )

    if matched_team:
        logger.debug("Matched team lookup | query=%r matched=%s", team_name, matched_team.name)
    else:
        logger.debug("No exact team match for %r; proceeding with prompt as-is", team_name)

    prompt = (
        f"Provide a comprehensive front office analysis for the {team_name}.\n\n"
        f"Cover:\n"
        f"1. Current roster assessment — who are the core pieces, who is expendable\n"
        f"2. Cap situation — are they over or under the cap, any bad contracts\n"
        f"3. Biggest roster need right now\n"
        f"4. Top 2 moves the front office should make this offseason\n"
        f"5. Trade candidates — who has value to other teams\n\n"
        f"Use your knowledge of the current {_DEFAULT_SEASON} NBA season."
    )

    result = await claude_service.analyze(
        prompt=prompt,
        system_prompt=FRONT_OFFICE_SYSTEM_PROMPT,
    )

    logger.info("Roster analysis complete | team=%r tokens=%d", team_name, result.tokens_used)

    return {
        "team": team_name,
        "team_data": matched_team.model_dump() if matched_team else None,
        "analysis": result.analysis,
        "model": result.model,
        "tokens_used": result.tokens_used,
    }


async def analyze_game(body: dict[str, Any]) -> dict[str, Any]:
    """
    Generate a PIVOT analysis for a single game — adapts to game state:
    pre-game preview, live breakdown, or post-game recap.

    Accepts the full game object from the frontend so team/score data is always
    available even when the BallDontLie boxscore endpoint is unavailable.
    """
    game_id: int = int(body.get("id") or 0)
    logger.info("Game analysis | game_id=%d", game_id)

    # Seed from the game object passed by the frontend (always available)
    home_t_seed = body.get("home_team") or {}
    away_t_seed = body.get("visitor_team") or {}

    home_name  = home_t_seed.get("full_name") or home_t_seed.get("name") or "Home"
    away_name  = away_t_seed.get("full_name") or away_t_seed.get("name") or "Away"
    home_abbr  = home_t_seed.get("abbreviation") or ""
    away_abbr  = away_t_seed.get("abbreviation") or ""
    home_score = int(body.get("home_team_score") or 0)
    away_score = int(body.get("visitor_team_score") or 0)
    status_raw = (body.get("status") or "").lower()
    period     = int(body.get("period") or 0)
    clock      = body.get("time") or ""

    # Attempt to enrich with live boxscore data — fail gracefully
    box: dict[str, Any] = {}
    if game_id:
        try:
            box = await nba_service.get_game_boxscore(game_id) or {}
            # Prefer live scores from boxscore when available
            bi = box.get("game_info") or {}
            if bi.get("home_team_score"):
                home_score = int(bi["home_team_score"])
            if bi.get("away_team_score"):
                away_score = int(bi["away_team_score"])
            if bi.get("period"):
                period = int(bi["period"])
            if bi.get("time"):
                clock = bi["time"]
            if bi.get("status"):
                status_raw = bi["status"].lower()
        except Exception as exc:
            logger.warning("Boxscore fetch failed for game_id=%d, using frontend data | %s", game_id, exc)
            box = {}

    has_score = home_score > 0 or away_score > 0
    is_final  = "final" in status_raw or "complete" in status_raw
    is_live   = has_score and not is_final

    if is_final:
        game_type = "FINAL"
    elif is_live:
        ql = f"Q{period}" if period <= 4 else ("OT" if period == 5 else f"OT{period-4}")
        game_type = f"LIVE — {ql} {clock}".strip()
    else:
        game_type = "UPCOMING"

    score_line = (
        f"{away_abbr} {away_score} — {home_score} {home_abbr}"
        if has_score else f"{away_abbr} @ {home_abbr}"
    )

    def _fmt(players: list[dict], label: str) -> str:
        if not players:
            return ""
        # Only include players who actually played meaningful minutes or scored
        def _min_int(m: str) -> int:
            try:
                return int(str(m).split(":")[0])
            except (ValueError, AttributeError):
                return 0
        significant = [
            p for p in players
            if _min_int(p.get("min", "0")) >= 10 or int(p.get("pts", 0)) >= 10
        ]
        if not significant:
            significant = players[:5]  # fallback: at least show top 5
        lines = [f"\n{label}:"]
        for p in significant[:8]:
            lines.append(
                f"  {p['player']} ({p['pos']}): "
                f"{p['pts']}pts {p['reb']}reb {p['ast']}ast "
                f"{p['fg']}FG {p['fg3']}3P {p['min']}min {p['pf']}PF"
            )
        return "\n".join(lines)

    has_box = box.get("total_players", 0) > 0

    # Cache: 3 min live, 20 min final, 10 min upcoming
    cache_ttl = 180 if is_live else (1200 if is_final else 600)
    cache_key = f"game_analysis:{game_id}:{period}:{home_score}:{away_score}"
    cached = analysis_cache.get(cache_key)
    if cached:
        logger.info("Game analysis cache hit | game_id=%d", game_id)
        return cached

    if is_final:
        prompt = (
            f"POST-GAME RECAP — {score_line} FINAL\n"
            f"{away_name} (Away) vs {home_name} (Home)\n"
        )
        if has_box:
            prompt += _fmt(box.get("away_players", []), away_name)
            prompt += _fmt(box.get("home_players", []), home_name)
        prompt += (
            "\n\nWrite a complete game breakdown. Cover:\n"
            "1. KEY PERFORMERS — name every player who impacted this game, stats and why they mattered\n"
            "2. TURNING POINT — the specific moment(s) that decided the outcome\n"
            "3. WHAT WON IT — the tactical or individual factor the winning team executed\n"
            "4. WHAT LOST IT — where the losing team broke down\n"
            "5. IMPLICATIONS — what this result means for both franchises going forward\n"
            "Be specific. Name players, name plays, name quarters. No filler."
        )
    elif is_live:
        prompt = (
            f"LIVE GAME ANALYSIS — {score_line} ({game_type})\n"
            f"{away_name} (Away) vs {home_name} (Home)\n"
        )
        if has_box:
            prompt += _fmt(box.get("away_players", []), away_name)
            prompt += _fmt(box.get("home_players", []), home_name)
        prompt += (
            "\n\nWrite a live breakdown. Cover:\n"
            "1. CURRENT STATE — who is winning and why, what the score differential reflects\n"
            "2. KEY PERFORMERS — who is dominating this game right now and how\n"
            "3. TROUBLE SPOTS — who is struggling, who is in foul trouble, shooting cold\n"
            "4. THE CLOSE — who has the edge to close this out and why\n"
            "Be specific. Use the actual numbers. No hedging."
        )
    else:
        prompt = (
            f"PRE-GAME MATCHUP PREVIEW — {score_line}\n"
            f"{away_name} at {home_name}\n\n"
            "Write a complete preview. Cover:\n"
            "1. STYLISTIC CLASH — how these teams play and where the styles conflict\n"
            "2. KEY BATTLES — the individual matchups that will decide this game\n"
            "3. EDGES — where each team has a clear advantage tonight\n"
            "4. X-FACTOR — the player or trend that could swing it\n"
            "5. PREDICTION — a confident call with a reason\n"
            "Be specific. Name players, name schemes. No generic takes."
        )

    result = await claude_service.analyze(
        prompt=prompt,
        system_prompt=GAME_ANALYST_SYSTEM_PROMPT,
        override_model=_FAST_MODEL,
        override_max_tokens=900,
        override_temperature=0.1,
    )

    logger.info("Game analysis complete | game_id=%d type=%s tokens=%d", game_id, game_type, result.tokens_used)

    response = {
        "game_id": game_id,
        "game_type": game_type,
        "score_line": score_line,
        "home_team": home_name,
        "away_team": away_name,
        "analysis": result.analysis,
        "model": result.model,
        "tokens_used": result.tokens_used,
    }
    analysis_cache.set(cache_key, response, cache_ttl)
    return response


async def coach_adjustment(body: dict[str, Any]) -> dict[str, Any]:
    """
    Generate live in-game coaching adjustments based on the current box score.

    Fetches game context and player stat lines concurrently, then asks Claude
    to prescribe specific lineup, offensive, and defensive adjustments based
    on the actual live data.

    Parameters
    ----------
    body:
        Request body containing:
        - ``game_id`` (int, optional): BallDontLie game ID for live data.
        - ``situation`` (str, optional): Coach's free-text situation description.
        - ``my_team`` (str, optional): Team name for score-differential framing.

    Returns
    -------
    dict
        Game context used, box score availability flag, analysis text, and
        token usage.
    """
    game_id: Optional[int] = body.get("game_id")
    situation: str = body.get("situation") or ""
    my_team: str = body.get("my_team") or ""

    logger.info(
        "Coach adjustment | game_id=%s my_team=%r situation=%r",
        game_id,
        my_team,
        situation[:80] if situation else "",
    )

    game_context: str = ""
    box_summary: str = "Box score unavailable."
    box_available: bool = False

    if game_id:
        try:
            box = await nba_service.get_game_boxscore(game_id)

            if box and box.get("total_players", 0) > 0:
                box_available = True
                game_info = box.get("game_info") or {}
                home_t = box.get("home_team") or {}
                away_t = box.get("away_team") or {}

                home_score = int(game_info.get("home_team_score") or 0)
                away_score = int(game_info.get("away_team_score") or 0)
                period = int(game_info.get("period") or 0)
                clock = game_info.get("time") or ""

                if period == 0:
                    quarter_label = "Pre-game"
                elif period <= 4:
                    quarter_label = f"Q{period}"
                else:
                    ot_num = period - 4
                    quarter_label = "OT" if ot_num == 1 else f"OT{ot_num}"

                home_name = home_t.get("name") or "Home"
                away_name = away_t.get("name") or "Away"
                home_abbr = home_t.get("abbreviation") or ""

                is_home = my_team.lower() in (home_name + " " + home_abbr).lower()
                my_score = home_score if is_home else away_score
                opp_score = away_score if is_home else home_score
                diff = my_score - opp_score
                diff_str = f"UP {abs(diff)}" if diff > 0 else f"DOWN {abs(diff)}" if diff < 0 else "TIED"

                clock_str = f" | Clock: {clock}" if clock else ""
                game_context = (
                    f"SCORE: {away_name} {away_score} — {home_score} {home_name}\n"
                    f"PERIOD: {quarter_label}{clock_str} | MY TEAM ({my_team or home_name}): {diff_str}"
                )

                def _fmt_player_lines(players: list[dict], label: str) -> str:
                    lines = [f"\n{label}:"]
                    for p in players[:10]:
                        lines.append(
                            f"  {p['player']} ({p['pos']}): "
                            f"{p['pts']}pts {p['reb']}reb {p['ast']}ast "
                            f"{p['stl']}stl {p['blk']}blk "
                            f"{p['fg']}FG {p['fg3']}3P "
                            f"{p['min']}min {p['to']}TO {p['pf']}PF"
                        )
                    return "\n".join(lines)

                box_summary = (
                    "FULL BOX SCORE:"
                    + _fmt_player_lines(box.get("home_players") or [], home_name + " (HOME)")
                    + _fmt_player_lines(box.get("away_players") or [], away_name + " (AWAY)")
                )
        except Exception as exc:
            logger.warning("Failed to fetch box score | game_id=%s error=%s", game_id, exc)

    situation_line = (
        situation
        or "Give me the most important adjustments based on what you see in the box score right now."
    )

    prompt = (
        f"LIVE IN-GAME COACHING CALL — You have full situational awareness. "
        f"Do NOT ask for more information. Give adjustments immediately based on what you see.\n\n"
        f"{game_context}\n\n"
        f"COACH'S NOTE: {situation_line}\n\n"
        f"{box_summary}\n\n"
        f"Based on the live data — who's hot, who's in foul trouble, shooting splits, turnovers, minutes — "
        f"give me the single most important adjustment right now (name players and schemes), any lineup change needed, "
        f"a defensive rotation based on what their guys are doing, and the exact offensive action to run next possession. "
        f"Name players by last name. Use the actual numbers. Be surgical."
    )

    result = await claude_service.analyze(
        prompt=prompt,
        system_prompt=COACH_SYSTEM_PROMPT,
        override_max_tokens=850,
    )

    logger.info(
        "Coach adjustment complete | game_id=%s box_used=%s tokens=%d",
        game_id,
        box_available,
        result.tokens_used,
    )

    return {
        "game_id": game_id,
        "my_team": my_team,
        "situation": situation,
        "box_score_used": box_available,
        "analysis": result.analysis,
        "model": result.model,
        "tokens_used": result.tokens_used,
    }


async def timeout_play(body: dict[str, Any]) -> dict[str, Any]:
    """
    Draw up a specific in-bounds or half-court play for use coming out of a timeout.

    Fetches the live box score to derive all game context (score, period, clock)
    automatically — no manual inputs required. Designs an executable play with
    primary and secondary options based on who is hot/cold in the current game.

    Parameters
    ----------
    body:
        Request body containing:
        - ``game_id`` (int, optional): BallDontLie game ID for live data.
        - ``my_team`` (str, optional): Team name for roster filtering.

    Returns
    -------
    dict
        Live game context, drawn-up play text, model metadata, and token usage.
    """
    game_id: Optional[int] = body.get("game_id")
    my_team: str = body.get("my_team") or ""

    # All game context derived from live box score
    score_diff: int = 0
    time_remaining: str = ""
    quarter: int = 4
    box_summary: str = ""
    game_context: str = ""

    logger.info("Timeout play | game_id=%s team=%r", game_id, my_team)

    if game_id:
        try:
            box = await nba_service.get_game_boxscore(game_id)

            if box and box.get("total_players", 0) > 0:
                game_info = box.get("game_info") or {}
                home_t = box.get("home_team") or {}
                away_t = box.get("away_team") or {}

                home_score = int(game_info.get("home_team_score") or 0)
                away_score = int(game_info.get("away_team_score") or 0)
                period = int(game_info.get("period") or 0)
                clock = game_info.get("time") or ""

                home_name = home_t.get("name") or "Home"
                home_abbr = home_t.get("abbreviation") or ""
                away_name = away_t.get("name") or "Away"

                is_home = my_team.lower() in (home_name + " " + home_abbr).lower()
                my_score = home_score if is_home else away_score
                opp_score = away_score if is_home else home_score
                score_diff = my_score - opp_score
                quarter = period if period > 0 else 4
                time_remaining = clock

                diff_str = (
                    f"UP {abs(score_diff)}" if score_diff > 0
                    else f"DOWN {abs(score_diff)}" if score_diff < 0
                    else "TIED"
                )
                quarter_label = (
                    f"Q{quarter}" if quarter <= 4
                    else ("OT" if quarter == 5 else f"OT{quarter - 4}")
                )
                clock_str = f" | {clock}" if clock else ""
                game_context = (
                    f"SCORE: {away_name} {away_score} — {home_score} {home_name}\n"
                    f"PERIOD: {quarter_label}{clock_str} | MY TEAM ({my_team or home_name}): {diff_str}"
                )

                my_players_key = "home_players" if is_home else "away_players"
                my_players: list[dict] = sorted(
                    (box.get(my_players_key) or [])[:8],
                    key=lambda p: str(p.get("min") or "0"),
                    reverse=True,
                )
                player_lines = "\n".join(
                    f"  {p['player']} ({p['pos']}): "
                    f"{p['pts']}pts {p['reb']}reb {p['ast']}ast "
                    f"{p['fg']}FG {p['fg3']}3P {p['min']}min {p['pf']}PF"
                    for p in my_players
                )
                box_summary = f"My active players:\n{player_lines}"
        except Exception as exc:
            logger.warning(
                "Failed to fetch box score for timeout play | game_id=%s error=%s",
                game_id,
                exc,
            )

    diff_str = (
        f"up {abs(score_diff)}" if score_diff > 0
        else f"down {abs(score_diff)}" if score_diff < 0
        else "tied"
    )

    prompt_parts = [
        "TIMEOUT — Draw up a play. Executable in 20 seconds.\n",
        f"Team: {my_team or 'My team'}",
    ]
    if game_context:
        prompt_parts.append(game_context)
    else:
        prompt_parts.append(f"Situation: Q{quarter}, {diff_str}")
    if box_summary:
        prompt_parts.append(box_summary)
    prompt_parts += [
        "",
        "Give me the play name, the full motion (screens, cuts, ball movement), the primary read, and the secondary read.",
        "Then on the very last line of your response — after all prose — output a court diagram in this exact format (no spaces, valid JSON):",
        'DIAGRAM:{"p":[{"n":1,"x":50,"y":72},{"n":2,"x":76,"y":58},{"n":3,"x":24,"y":58},{"n":4,"x":64,"y":42},{"n":5,"x":50,"y":36}],"moves":[{"n":2,"tx":84,"ty":74,"type":"cut"},{"n":3,"tx":14,"ty":52,"type":"cut"},{"n":5,"tx":62,"ty":28,"type":"screen"}],"ball":{"from":1,"to":4}}',
        "Coordinate system: x=0 left sideline, x=100 right sideline, y=0 half court line, y=100 baseline. Basket is at x=50, y=89.",
        "Player roles: n=1 PG (primary ball handler), n=2 SG, n=3 SF, n=4 PF, n=5 C.",
        "move.type options: cut, screen, curl, dribble. Use actual player positions from the box score to assign roles.",
        "The DIAGRAM line must be the absolute last line. It is machine-parsed — output valid JSON only.",
    ]

    result = await claude_service.analyze(
        prompt="\n".join(prompt_parts),
        system_prompt=COACH_SYSTEM_PROMPT,
        override_max_tokens=900,
    )

    # Parse the court diagram JSON from the response
    import re as _re, json as _json
    play_text = result.analysis
    diagram = None
    diag_match = _re.search(r'DIAGRAM:(\{.+\})\s*$', play_text, _re.MULTILINE)
    if diag_match:
        try:
            diagram = _json.loads(diag_match.group(1))
            play_text = play_text[:diag_match.start()].rstrip()
        except (ValueError, KeyError):
            pass

    logger.info(
        "Timeout play complete | game_id=%s team=%r tokens=%d diagram=%s",
        game_id,
        my_team,
        result.tokens_used,
        "yes" if diagram else "no",
    )

    return {
        "game_id": game_id,
        "my_team": my_team,
        "quarter": quarter,
        "time_remaining": time_remaining,
        "score_diff": score_diff,
        "play": play_text,
        "diagram": diagram,
        "model": result.model,
        "tokens_used": result.tokens_used,
    }