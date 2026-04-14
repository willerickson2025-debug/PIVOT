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
from app.core.http_client import GlobalHTTPClient
from app.models.schemas import Game, GameAnalysisResponse
from app.services import claude_service, nba_service

# ---------------------------------------------------------------------------
# Shared ESPN injury cache — 90s TTL so all call sites share one response
# ---------------------------------------------------------------------------
_ESPN_INJURY_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"
_ESPN_INJURY_CACHE_TTL = 90  # seconds

async def _fetch_espn_injuries_raw() -> dict:
    """Fetch raw ESPN injury JSON, cached for 90s across all callers."""
    cached = analysis_cache.get("espn_injuries_raw")
    if cached is not None:
        return cached
    try:
        client = GlobalHTTPClient.get_client()
        r = await client.get(_ESPN_INJURY_URL, headers={"User-Agent": "Mozilla/5.0"})
        data = r.json()
        analysis_cache.set("espn_injuries_raw", data, ttl=_ESPN_INJURY_CACHE_TTL)
        return data
    except Exception:
        return {}


async def _get_roster_last_names(team_id: int) -> set[str]:
    """Cached BDL roster last names for a team — 6h TTL."""
    cache_key = f"roster_lastnames:{team_id}"
    cached = analysis_cache.get(cache_key)
    if cached is not None:
        return cached
    names = await nba_service.get_team_roster_last_names(team_id)
    if names:  # only cache non-empty results
        analysis_cache.set(cache_key, names, ttl=21600)  # 6 hours
    return names


async def _validated_injury_tags(
    team_display_name: str,
    team_id: int | None,
    espn_raw: dict,
    include_statuses: tuple[str, ...] = ("out", "doubtful", "questionable", "day-to-day", "probable"),
) -> list[str]:
    """
    Return injury tags for a team, filtered to players actually on the
    current BDL roster.  Eliminates traded/released players that ESPN still
    lists, e.g. Damian Lillard showing as a Portland injury after his trade.

    Falls back to unvalidated list if BDL roster fetch fails (empty set).
    """
    # Fetch BDL roster for validation (non-blocking — uses cache after first call)
    roster_lastnames: set[str] = set()
    if team_id:
        roster_lastnames = await _get_roster_last_names(team_id)

    team_kw = team_display_name.split()[-1].lower()
    tags: list[str] = []

    for tb in espn_raw.get("injuries", []):
        tname = tb.get("displayName", "").lower()
        if team_kw not in tname:
            continue
        for inj in tb.get("injuries", []):
            athlete = inj.get("athlete") or {}
            name = athlete.get("displayName", "")
            last_name = (athlete.get("lastName") or name.split()[-1] if name else "").strip().lower()
            status = inj.get("status", "")
            inj_type = inj.get("type") or inj.get("injuryType") or {}
            type_name = inj_type.get("name", "") if isinstance(inj_type, dict) else str(inj_type)
            type_lower = type_name.lower()
            comment = (inj.get("details") or {}).get("detail", "") or inj.get("shortComment", "")

            # Validate: skip if we have a roster and this player isn't on it
            if roster_lastnames and last_name and last_name not in roster_lastnames:
                logger.info(
                    "Injury validation: skipping %r (%s) — not on current %s roster",
                    name, status, team_display_name,
                )
                continue

            # Tag the player
            if "load" in type_lower or "rest" in type_lower or "load management" in comment.lower():
                tags.append(f"{name} (LOAD MANAGEMENT / REST — likely DNP)")
            elif "not injury" in type_lower:
                tags.append(f"{name} (DNP - Non-Injury: {status})")
            elif status.lower() in include_statuses:
                entry = f"{name} ({status}"
                if comment:
                    entry += f" — {comment}"
                entry += ")"
                tags.append(entry)
        break  # matched team, done

    return tags

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

def _today_context() -> str:
    """Return a live date string for injection into system prompts."""
    now = datetime.datetime.now(ZoneInfo("America/Chicago"))
    return now.strftime("%B %-d, %Y")


def _nba_analyst_system_prompt() -> str:
    return f"""You are the highest-paid NBA analyst in the country. Your clients are GMs, bettors, and executives who pay premium money for your edge. They don't want summaries. They want what you actually think.

CRITICAL CONTEXT: Today is {_today_context()}. The 2025-26 NBA season is actively in progress right now. Do not say the season "has not yet occurred" or treat it as a future event. It is happening. Stats provided are live 2025-26 season data.

STRICT DATA GROUNDING — NON-NEGOTIABLE:
The data payload in this prompt is the only reality. It completely overrides anything from your training about current rosters, trades, or player team assignments. If the data says a player is on a specific team, that is absolute fact — do not contradict it with pre-training knowledge. Before writing any team or player analysis, silently verify which players and teams are actually present in the provided data, then analyze only those.

TEAM AFFILIATIONS — CRITICAL: The team listed in the stat block header is authoritative. Never state a different team based on training knowledge. Players get traded constantly; your training data is not current. If the stat block says a player is on Team X, accept that as fact. If no team is listed, do not invent one from memory.

CRITICAL RULES FOR MISSING OR INCOMPLETE DATA:
You will receive a stat block with season averages and recent game logs. If any section of that data is zero, empty, or missing, follow these rules without exception:
1. Do NOT speculate on why the data is missing. Do not mention injuries, suspensions, rest, load management, two-way contracts, or any real-world explanation for absent numbers.
2. Do NOT reference the data pipeline, API, feed, or any technical system. You are an analyst, not a developer.
3. Do NOT invent or hallucinate statistics that were not provided.
4. If recent game logs are missing but season averages exist, analyze only the season averages and skip any recent-form commentary entirely.
5. If a stat reads 0.0 across the board, note briefly that their current season data is still being added to the system — then pivot immediately to what you do know about the player from their career profile and overall trajectory. Frame it as PIVOT expanding its coverage, not as a limitation.

EFFICIENCY — THE ONLY METRICS THAT MATTER:
True Shooting % (TS%) is the single most important efficiency number. It accounts for field goals, three-pointers, AND free throws — the complete picture of scoring value. eFG% is second. FT Rate (FTA/FGA) is third, because it tells you how aggressively a player gets to the line.

RAW FG% IS FORBIDDEN AS A PRIMARY EFFICIENCY INDICATOR. Never open an efficiency analysis with "shoots X% from the field." Never compare two players using raw FG% as the benchmark. FG% ignores three-point value and ignores free throws — both fatal flaws. A player who shoots 44% FG with 8 FTA/game and 6 3PA/game is almost certainly more efficient than a player who shoots 50% FG with 1 FTA/game and 0 3PA. The stat block puts TS% first — use it that way.

Efficiency tiers for TS%: 62%+ is historically elite (top of the league), 58–62% is very good, 54–58% is average, below 54% is a problem. eFG%: 56%+ elite, 52–56% solid, below 52% inefficient. FT Rate: above 0.40 means defenses can't stay in front of this player; below 0.20 means they're not creating contact or not being schemed against.

3-POINT SHOOTING — VOLUME CONTEXT IS MANDATORY:
Raw 3P% is nearly meaningless without knowing 3PA/game. High-volume three-point shooters (6+ 3PA/game) consistently shoot 34–38% — that is their correct operating range. Among the top-10 players in 3PA/game league-wide, virtually none shoot above 40%. A player averaging 36–38% on 8+ 3PA/game is an elite shooter by any serious metric. Never compare raw 3P% between a high-volume and low-volume shooter without explicitly noting the volume difference. A role player shooting 43% on 3 attempts is not a better shooter than a lead creator shooting 37% on 9 attempts.

INTERPRETING L10 TRENDS — CONTEXT IS MANDATORY:
A 10-game window is roughly 12% of a regular season. The stat block provides per-36 numbers for both the season and L10 — USE THEM. Per-game raw stats drop when minutes drop (blowouts, load management, lineup changes). Before characterizing any L10 vs season delta as a "decline" or "deterioration":
1. Check per-36 first — if per-36 is flat, the raw per-game drop is a minutes story, not a production story. State this explicitly.
2. Check the magnitude — require at least a 15–20% change from baseline in per-36 numbers before calling anything meaningful.
3. Check FT rate — a rising FTA/game alongside lower FG attempts often means defenses are fouling more aggressively (a sign of defensive respect, not player decline).
4. Check the opponent slate — late-season schedules cluster elite defensive teams. A shooting dip against top-5 defenses is expected.
5. Never use "sharp deterioration," "hitting a wall," "running out of gas," or "collapse" for a 10-game window unless the drop is severe (>25% below baseline) in per-36 numbers across multiple categories simultaneously.

You have watched more NBA film than anyone in this conversation. You know pace differentials, defensive rating trends, how teams perform on back-to-backs, which coaches make in-game adjustments and which ones don't, which stars disappear in fourth quarters. You use that knowledge.

When analyzing a player: lead with TS% or eFG% as the efficiency anchor. Then cover role/usage context (is their FTA rate up or down, what does that signal), the L10 trend with proper framing (sample size, opponent context, usage changes), and close with one sharp sentence on where this player actually stands right now. Do not catastrophize variance. Do not minimize real problems either — if the data shows a genuine multi-month decline in efficiency and volume, call it.

When analyzing a game: open with the sharpest thing you know about this matchup — the thing most people miss. Then cover the stylistic clash, the one player who will determine the outcome, and the specific reason one team wins. Close with a confident, unhedged prediction.

Every word must earn its place. If a sentence doesn't add information or edge, cut it. No throat-clearing. No "it's worth noting." No "at the end of the day." Start with the insight, not the setup.

BOLD TAKES — PERMITTED AND ENCOURAGED:
When the data genuinely supports it, make the bold call. If a player is on a GOAT trajectory, say so. If a rookie is generational, use that word. If a player is the best at their position right now, declare it. Do not hedge elite talent with "could potentially" or "has shown flashes." The clients pay for conviction. Historical comparisons to all-time greats are appropriate when the statistical case is real — a player averaging elite efficiency numbers and dominant two-way production can be compared to the players they actually resemble. The only requirement is that the data supports the claim.

FORMATTING — NON-NEGOTIABLE:
Plain prose only. No markdown. No asterisks, no pound signs, no dashes used as bullets, no numbered lists, no bold, no italics, no horizontal rules, no headers. Paragraphs separated by one blank line. Write like a column in The Athletic or a Sharp report — dense, confident, readable."""


def _front_office_system_prompt() -> str:
    return f"""You are a senior NBA front-office analyst. Your clients are GMs who pay for direct, actionable intelligence — not hedged commentary.

CRITICAL CONTEXT: Today is {_today_context()}. The 2025-26 NBA season is actively in progress. You have full knowledge of this season's rosters, contracts, and standings from your training data plus any context provided in the prompt.

ABSOLUTE PROHIBITIONS — violating any of these is a failure:
- Never say you lack access to data, stats, rosters, contracts, or salary figures.
- Never add disclaimers like "I don't have access to...", "as of my knowledge cutoff...", "I cannot verify...", or any variation. You know this information.
- Never hedge with "approximately" or "roughly" when you know the actual figure.
- Never refuse to name a specific player, contract, or trade recommendation.
- Never use placeholder language like "[player name]" or "[contract amount]".

You know who is on every NBA roster, what they're paid, and how they've performed this season. Use that knowledge directly and confidently. The standings and injury data provided in the prompt supplements what you already know.

When analyzing a roster: open with a one-sentence verdict on where this team stands. Then cover the core pieces, the problem contracts, the biggest need, and two specific actionable moves with named players and dollar figures.

When evaluating a trade: one sentence on who wins and why — then build every sentence to support that same conclusion.

Plain prose only. No markdown, no bullets, no numbered lists, no headers, no asterisks. Paragraphs separated by one blank line. Dense, confident, readable — like a Sharp memo."""


def _game_analyst_system_prompt() -> str:
    return f"""You are a precision NBA game analyst. Your only job is to translate the provided game data into clean, factual observations.

CRITICAL CONTEXT: Today is {_today_context()}. The 2025-26 NBA season is in progress.

STRICT DATA GROUNDING — ABSOLUTE RULES:
1. The data in this prompt is the only source of truth. Do not invent events, plays, momentum shifts, or individual moments that are not present in the provided stats.
2. Do not use training knowledge to fill gaps. If a stat is not in the payload, it did not happen. Say nothing about it.
3. If player-level stats are missing or sparse, focus entirely on team scores and game state — do not speculate about individual contributions.
4. Never reference the data pipeline, API, feed, or any technical system. You are an analyst, not a developer.
5. TEAM AFFILIATIONS — CRITICAL: Never state which team a player belongs to based on training knowledge alone. Players get traded constantly. The only authoritative source of a player's current team is the box score showing them in that team's lineup. If a player appears in a team's box score line, they play for that team — that is all you know for certain. Do not add team attributions from memory.
6. INJURED / ABSENT PLAYERS: If the prompt includes an injury report listing players as OUT or DOUBTFUL, and those players do not appear in the box score, treat their absence as confirmed and note its impact on the game. Do not speculate about players not mentioned in either the box score or the injury report.

OUTPUT STRUCTURE by game state:
- FINAL: Key performers (with exact stats), the decisive factor in the outcome, what each team did well or failed at, notable absences from the injury report that shaped the game, one sentence on implications.
- LIVE: Who is winning and why based on the actual numbers, who is producing, current trajectory.
- UPCOMING: Stylistic clash, key individual battles, injury-report absences and their impact, each team's edge, confident prediction.

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
    "on_off": (
        "Analyze this player's on/off court impact. Use their TS%, FT Rate, usage, assists, "
        "defensive stats, and positional role to reason through: what does the floor look like "
        "when they're on vs off — does their spacing change the offense, does their FT-drawing "
        "ability create possessions, does their defense anchor a scheme or create a liability? "
        "Draw on what you know about how this team plays and how the player's skill set "
        "interacts with that system. If they have high usage, what happens when they're resting "
        "and that usage must be redistributed? Be specific: name lineup combinations, name the "
        "opponents who feast on them in isolation, name the coverages that make them irrelevant. "
        "Close with a net impact verdict — is this player a net positive, a net negative, or "
        "does it depend on the specific matchup context?"
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

    # Volume / usage context from official season averages
    avg_fga  = round(float(official.get("fga")  or 0), 1)
    avg_fta  = round(float(official.get("fta")  or 0), 1)
    avg_fgm  = round(float(official.get("fgm")  or 0), 1)
    avg_fg3m = round(float(official.get("fg3m") or 0), 1)
    avg_fg3a = round(float(official.get("fg3a") or 0), 1)   # 3-point attempts per game
    avg_tov  = round(float(official.get("turnover") or 0), 1)

    def _parse_min_str(m: Any) -> float:
        """Parse BDL min field which can be '35:06' or a plain float/int."""
        try:
            parts = str(m or "0").split(":")
            return float(parts[0]) + (float(parts[1]) / 60 if len(parts) == 2 else 0.0)
        except Exception:
            return 0.0

    avg_min  = round(_parse_min_str(official.get("min") or 0), 1)

    # Advanced efficiency metrics (require volume data to be meaningful)
    if avg_fga > 0 and avg_pts > 0:
        ts_pct  = round(avg_pts / (2.0 * (avg_fga + 0.44 * avg_fta)), 3)
        efg_pct = round((avg_fgm + 0.5 * avg_fg3m) / avg_fga, 3)
        ft_rate = round(avg_fta / avg_fga, 3)   # FTA/FGA — how often defense fouls
    else:
        ts_pct = efg_pct = ft_rate = 0.0

    # Per-36 season averages (minutes-neutral production)
    def _per36(raw: float, mpg: float) -> float:
        return round(raw / mpg * 36, 1) if mpg > 0 else 0.0

    per36_pts = _per36(avg_pts, avg_min)
    per36_reb = _per36(avg_reb, avg_min)
    per36_ast = _per36(avg_ast, avg_min)

    # Recent form: last N games, including minutes parsing for per-36 context
    recent = stats[-_RECENT_FORM_WINDOW:]

    def _parse_min(m: Any) -> float:
        try:
            parts = str(m or "0").split(":")
            return float(parts[0]) + (float(parts[1]) / 60 if len(parts) == 2 else 0.0)
        except Exception:
            return 0.0

    recent_pts  = _safe_avg([s.points   for s in recent])
    recent_reb  = _safe_avg([s.rebounds for s in recent])
    recent_ast  = _safe_avg([s.assists  for s in recent])
    recent_stl  = _safe_avg([s.steals   for s in recent])
    recent_blk  = _safe_avg([s.blocks   for s in recent])
    recent_fg   = _safe_avg([s.fg_pct   for s in recent])
    recent_fg3  = _safe_avg([s.fg3_pct  for s in recent])
    recent_min  = round(_safe_avg([_parse_min(s.minutes) for s in recent if s.minutes]), 1)

    # L10 per-36 (minutes-neutral — catches blowout/rest effects)
    recent_pts_36 = _per36(recent_pts, recent_min)
    recent_reb_36 = _per36(recent_reb, recent_min)
    recent_ast_36 = _per36(recent_ast, recent_min)

    aggregates: dict[str, Any] = {
        "total_games":  total_games,
        "avg_pts":  avg_pts,  "avg_reb":  avg_reb,  "avg_ast":  avg_ast,
        "avg_stl":  avg_stl,  "avg_blk":  avg_blk,
        "avg_fg":   avg_fg,   "avg_fg3":  avg_fg3,  "avg_ft":   avg_ft,
        "avg_fga":  avg_fga,  "avg_fta":  avg_fta,  "avg_fg3a": avg_fg3a,
        "avg_tov":  avg_tov,  "avg_min":  avg_min,
        "ts_pct":   ts_pct,   "efg_pct":  efg_pct,  "ft_rate":  ft_rate,
        "per36_pts": per36_pts, "per36_reb": per36_reb, "per36_ast": per36_ast,
        "recent_pts": recent_pts, "recent_reb": recent_reb, "recent_ast": recent_ast,
        "recent_stl": recent_stl, "recent_blk": recent_blk,
        "recent_fg":  recent_fg,  "recent_fg3": recent_fg3,
        "recent_min": recent_min,
        "recent_pts_36": recent_pts_36, "recent_reb_36": recent_reb_36,
        "recent_ast_36": recent_ast_36,
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
    has_vol = agg.get("avg_fga", 0) > 0

    block = (
        f"Player: {player.first_name} {player.last_name}\n"
        f"Team: {team_name} | Position: {player.position or 'N/A'} | "
        f"Season: {season} | Games: {agg['total_games']} | MIN/G: {agg['avg_min']}\n"
        f"\n"
    )

    # ── EFFICIENCY FIRST — the only numbers that matter for scoring quality ──
    if has_vol:
        ts_tier = "ELITE" if agg['ts_pct'] >= 0.62 else ("VERY GOOD" if agg['ts_pct'] >= 0.58 else ("AVG" if agg['ts_pct'] >= 0.54 else "BELOW AVG"))
        block += (
            f"EFFICIENCY (primary lens):\n"
            f"  TS%: {agg['ts_pct']:.1%} [{ts_tier}] | eFG%: {agg['efg_pct']:.1%} | "
            f"FT Rate: {agg['ft_rate']:.2f} | FT%: {agg['avg_ft']:.1%}\n"
            f"  Shot diet: {agg['avg_fga']} FGA/G | {agg['avg_fg3a']} 3PA/G ({agg['avg_fg3']:.1%} 3P%) | "
            f"{agg['avg_fta']} FTA/G\n"
        )
    else:
        block += (
            f"EFFICIENCY: limited volume data — 3P%: {agg['avg_fg3']:.1%} | FT%: {agg['avg_ft']:.1%}\n"
        )

    # ── PRODUCTION per game ──
    block += (
        f"\nPRODUCTION (per game):\n"
        f"  PTS: {agg['avg_pts']} | REB: {agg['avg_reb']} | AST: {agg['avg_ast']} | "
        f"STL: {agg['avg_stl']} | BLK: {agg['avg_blk']} | TOV: {agg['avg_tov']}\n"
    )

    # ── PER 36 (minutes-neutral) ──
    if agg.get("avg_min", 0) > 0:
        block += (
            f"  Per 36 min: {agg['per36_pts']} PTS / {agg['per36_reb']} REB / {agg['per36_ast']} AST\n"
        )

    # ── LAST 10 with per-36 context ──
    recent_min = agg.get("recent_min", 0)
    min_note = f" | {recent_min} MPG this stretch" if recent_min > 0 else ""
    block += (
        f"\nLAST {_RECENT_FORM_WINDOW} GAMES (~12% sample{min_note}):\n"
        f"  PTS: {agg['recent_pts']} ({_trend_label(agg['recent_pts'], agg['avg_pts'])} vs season) | "
        f"REB: {agg['recent_reb']} ({_trend_label(agg['recent_reb'], agg['avg_reb'])}) | "
        f"AST: {agg['recent_ast']} ({_trend_label(agg['recent_ast'], agg['avg_ast'])})\n"
        f"  STL: {agg['recent_stl']} ({_trend_label(agg['recent_stl'], agg['avg_stl'])}) | "
        f"BLK: {agg['recent_blk']} ({_trend_label(agg['recent_blk'], agg['avg_blk'])})\n"
        f"  L10 3P%: {agg['recent_fg3']:.1%} ({_pct_trend_label(agg['recent_fg3'], agg['avg_fg3'])} vs season)\n"
    )
    if agg.get("recent_pts_36") and recent_min > 0:
        block += (
            f"  Per-36 (L10): {agg['recent_pts_36']} PTS / {agg['recent_reb_36']} REB / "
            f"{agg['recent_ast_36']} AST "
            f"[season per-36: {agg['per36_pts']} / {agg['per36_reb']} / {agg['per36_ast']}]\n"
        )
    return block


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
        system_prompt=_nba_analyst_system_prompt(),
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
            "error": f"We're still adding {player.first_name} {player.last_name}'s {season}-{str(season+1)[-2:]} data to the system — check back shortly as we expand our coverage.",
        }

    stat_block = _render_stat_block(player, season, agg)

    prompt = (
        f"Analyze this player's {season} NBA season:\n\n"
        f"{stat_block}\n\n"
        f"Cover in order:\n"
        f"1. EFFICIENCY ANCHOR — lead with TS% or eFG%. What does it say about this player's true scoring value? For 3-point shooting, cite 3PA/G alongside 3P% — volume context is mandatory.\n"
        f"2. ROLE & USAGE — what do FGA/G, FTA/G, FT Rate, and MIN/G reveal about how this player is being used and how defenses are scheming against them?\n"
        f"3. LAST {_RECENT_FORM_WINDOW} GAMES — compare per-36 L10 vs per-36 season first. If per-36 is flat but raw per-game is down, say so directly: that is a minutes story. Only flag a real concern if per-36 numbers have moved significantly.\n"
        f"4. VERDICT — one precise sentence on where this player stands right now.\n"
        f"Write it as connected prose, not a list."
    )

    result = await claude_service.analyze(
        prompt=prompt,
        system_prompt=_nba_analyst_system_prompt(),
        override_model=_FAST_MODEL,
        override_max_tokens=800,
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
            f"their {season} season data is still being added to our system. "
            f"Analyze based on career profile and what you know about this player."
        )

    prompt = (
        f"{section_directive}\n\n"
        f"{stat_context}\n\n"
        f"Go deep. Use your basketball knowledge beyond just the raw stats provided."
    )

    result = await claude_service.analyze(
        prompt=prompt,
        system_prompt=_nba_analyst_system_prompt(),
        override_model=_FAST_MODEL,
        override_max_tokens=900,
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
    async for chunk in claude_service.analyze_stream(prompt, system_prompt=_nba_analyst_system_prompt(), override_model=_FAST_MODEL, override_max_tokens=1000):
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
        system_prompt=_front_office_system_prompt(),
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

    # ── Enrich with live standings + injury report ────────────────────────────
    from app.services import standings_service as _standings_svc

    async def _get_team_standing():
        try:
            data = await _standings_svc.get_standings()
            abbr = matched_team.abbreviation if matched_team else ""
            by_abbr = {t["abbr"]: t for t in data.get("league", [])}
            rec = by_abbr.get(abbr, {})
            if rec:
                return (
                    f"{rec.get('name',team_name)} ({abbr}): "
                    f"{rec.get('wins',0)}-{rec.get('losses',0)} "
                    f"({rec.get('pct',0):.3f} PCT), "
                    f"#{rec.get('seed','?')} in {rec.get('conference','?')} Conference, "
                    f"{rec.get('gb',0)} GB"
                )
        except Exception:
            pass
        return ""

    async def _get_team_injuries():
        try:
            raw = await _fetch_espn_injuries_raw()
            team_kw = (matched_team.name if matched_team else team_name).split()[-1].lower()
            for tb in raw.get("injuries", []):
                tname = tb.get("displayName", "").lower()
                if team_kw in tname:
                    players = []
                    for inj in tb.get("injuries", []):
                        ath = inj.get("athlete", {})
                        status = inj.get("status", "")
                        comment = inj.get("shortComment", "")
                        entry = f"{ath.get('displayName','')} ({status}"
                        if comment:
                            entry += f" — {comment}"
                        entry += ")"
                        players.append(entry)
                    if players:
                        return ", ".join(players[:10])
        except Exception:
            pass
        return ""

    standing_str, injury_str = await asyncio.gather(_get_team_standing(), _get_team_injuries())

    context_lines = []
    if standing_str:
        context_lines.append(f"CURRENT STANDINGS:\n{standing_str}")
    if injury_str:
        context_lines.append(f"INJURY REPORT:\n{injury_str}")
    context_block = ("\n\n" + "\n\n".join(context_lines)) if context_lines else ""

    prompt = (
        f"FRONT OFFICE ANALYSIS — {team_name.upper()}"
        f"{context_block}\n\n"
        f"Using the real-time data above plus your full knowledge of the 2025-26 season, "
        f"write a sharp front-office memo covering:\n\n"
        f"1. WHERE THIS TEAM STANDS — verdict on their season and trajectory given their record\n"
        f"2. CORE PIECES — who is untouchable and why\n"
        f"3. CAP SITUATION — who is overpaid, who is a bargain, key contract timelines\n"
        f"4. BIGGEST NEED — the specific gap holding this team back\n"
        f"5. TOP 2 MOVES — name the exact players, teams, and deal structures\n"
        f"6. TRADE CANDIDATES — who has market value right now and why\n\n"
        f"Name specific players. Use real contract figures. Give a real verdict. No hedging."
    )

    result = await claude_service.analyze(
        prompt=prompt,
        system_prompt=_front_office_system_prompt(),
        override_max_tokens=1200,
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

    # Cache: 3 min live, 20 min final, 5 min upcoming (injuries/standings can change)
    cache_ttl = 180 if is_live else (1200 if is_final else 300)
    cache_key = f"game_analysis:{game_id}:{period}:{home_score}:{away_score}"
    cached = analysis_cache.get(cache_key)
    if cached:
        logger.info("Game analysis cache hit | game_id=%d", game_id)
        return cached

    # ── Injury context for all game types ────────────────────────────────────
    async def _fetch_injury_for_game() -> str:
        try:
            raw = await _fetch_espn_injuries_raw()
            inj_map: dict[str, list[str]] = {}
            for tb in raw.get("injuries", []):
                tname = tb.get("displayName", "")
                players = []
                for inj in tb.get("injuries", []):
                    ath = inj.get("athlete", {})
                    status = inj.get("status", "")
                    comment = inj.get("shortComment", "")
                    if status.lower() in ("out", "doubtful", "questionable"):
                        entry = f"{ath.get('displayName','')} ({status}"
                        if comment:
                            entry += f" — {comment}"
                        entry += ")"
                        players.append(entry)
                if players:
                    inj_map[tname] = players

            lines = []
            for tname, players in inj_map.items():
                if (home_name.split()[-1].lower() in tname.lower() or
                        home_abbr.lower() in tname.lower()):
                    lines.append(f"{home_name}: {', '.join(players[:6])}")
                elif (away_name.split()[-1].lower() in tname.lower() or
                        away_abbr.lower() in tname.lower()):
                    lines.append(f"{away_name}: {', '.join(players[:6])}")
            return "\n".join(lines)
        except Exception:
            return ""

    game_injury_ctx = await _fetch_injury_for_game()
    injury_block = f"\nINJURY REPORT:\n{game_injury_ctx}" if game_injury_ctx else ""

    if is_final:
        prompt = (
            f"POST-GAME RECAP — {score_line} FINAL\n"
            f"{away_name} (Away) vs {home_name} (Home)\n"
        )
        if has_box:
            prompt += _fmt(box.get("away_players", []), away_name)
            prompt += _fmt(box.get("home_players", []), home_name)
        if injury_block:
            prompt += f"\n{injury_block}"
        prompt += (
            "\n\nWrite a complete game breakdown. Cover:\n"
            "1. KEY PERFORMERS — name every player who impacted this game, stats and why they mattered\n"
            "2. TURNING POINT — the specific moment(s) that decided the outcome\n"
            "3. WHAT WON IT — the tactical or individual factor the winning team executed\n"
            "4. WHAT LOST IT — where the losing team broke down\n"
            "5. INJURY IMPACT — if notable players were out per the injury report, explain how their absence shaped the game\n"
            "6. IMPLICATIONS — what this result means for both franchises going forward\n"
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
        if injury_block:
            prompt += f"\n{injury_block}"
        prompt += (
            "\n\nWrite a live breakdown. Cover:\n"
            "1. CURRENT STATE — who is winning and why, what the score differential reflects\n"
            "2. KEY PERFORMERS — who is dominating this game right now and how\n"
            "3. TROUBLE SPOTS — who is struggling, who is in foul trouble, shooting cold\n"
            "4. THE CLOSE — who has the edge to close this out and why\n"
            "Be specific. Use the actual numbers. No hedging."
        )
    else:
        # ── Upcoming: enrich with live standings (parallel with injury already fetched) ──
        from app.services import standings_service as _standings_svc

        async def _fetch_standings_ctx() -> str:
            try:
                data = await _standings_svc.get_standings()
                by_abbr = {t["abbr"]: t for t in data.get("league", [])}
                hr = by_abbr.get(home_abbr, {})
                ar = by_abbr.get(away_abbr, {})
                lines = []
                if hr:
                    lines.append(
                        f"{home_name} ({home_abbr}): {hr.get('wins',0)}-{hr.get('losses',0)} "
                        f"({hr.get('pct',0):.3f} PCT), #{hr.get('seed','?')} {hr.get('conference','?')}, "
                        f"{hr.get('gb',0)} GB"
                    )
                if ar:
                    lines.append(
                        f"{away_name} ({away_abbr}): {ar.get('wins',0)}-{ar.get('losses',0)} "
                        f"({ar.get('pct',0):.3f} PCT), #{ar.get('seed','?')} {ar.get('conference','?')}, "
                        f"{ar.get('gb',0)} GB"
                    )
                return "\n".join(lines) if lines else ""
            except Exception:
                return ""

        standings_ctx = await _fetch_standings_ctx()  # standings has own 6h cache; fast after warmup

        context_block = ""
        if standings_ctx:
            context_block += f"\nCURRENT STANDINGS:\n{standings_ctx}"
        if game_injury_ctx:
            context_block += f"\n\nINJURY REPORT:\n{game_injury_ctx}"

        prompt = (
            f"PRE-GAME MATCHUP PREVIEW — {score_line}\n"
            f"{away_name} ({away_abbr}) at {home_name} ({home_abbr})\n"
            f"{context_block}\n\n"
            "Write a complete game preview grounded in the standings and injury data above. Cover:\n"
            "1. FORM & STAKES — what each team's record means right now, playoff implications\n"
            "2. STYLISTIC CLASH — how these teams play and where the styles conflict\n"
            "3. KEY BATTLES — the individual matchups that decide this game\n"
            "4. INJURY IMPACT — how the injury report changes the calculus (if injuries listed above)\n"
            "5. PREDICTION — a confident call with a specific reason\n"
            "Be specific. Name players, name schemes. No generic takes. Ground everything in the data provided."
        )

    result = await claude_service.analyze(
        prompt=prompt,
        system_prompt=_game_analyst_system_prompt(),
        override_model=_FAST_MODEL,
        override_max_tokens=2200,
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


async def predict_game(body: dict[str, Any]) -> dict[str, Any]:
    """
    Return a structured prediction for an upcoming game.

    Fetches live standings and injury data before calling Claude so the
    prediction is grounded in actual W-L records and roster availability.
    """
    import json as _json
    from app.services import standings_service as _standings_svc

    home_t    = body.get("home_team") or {}
    away_t    = body.get("visitor_team") or {}
    home_name = home_t.get("full_name") or home_t.get("name") or "Home"
    away_name = away_t.get("full_name") or away_t.get("name") or "Away"
    home_abbr = home_t.get("abbreviation") or home_name
    away_abbr = away_t.get("abbreviation") or away_name
    home_id   = home_t.get("id")   # BDL team ID — used for roster validation
    away_id   = away_t.get("id")
    game_id   = int(body.get("id") or 0)
    status    = (body.get("status") or "").lower()

    if "final" in status or any(q in status for q in ["qtr", "half", "in progress"]):
        return {"error": "Predictions only available for upcoming games."}

    cache_key = f"predict_game2:{game_id}"
    cached = analysis_cache.get(cache_key)
    if cached:
        logger.info("predict_game cache hit | game_id=%d", game_id)
        return cached

    # ── Fetch standings + validated injury lists in parallel ─────────────────
    # _validated_injury_tags checks each ESPN-listed player against the team's
    # current BDL roster, filtering out traded/released players (e.g. Lillard
    # still listed under Portland after his trade to Milwaukee).
    espn_raw = await _fetch_espn_injuries_raw()  # shared; cached after first call
    standings_data, home_inj_tags, away_inj_tags = await asyncio.gather(
        _standings_svc.get_standings(),
        _validated_injury_tags(home_name, home_id, espn_raw),
        _validated_injury_tags(away_name, away_id, espn_raw),
        return_exceptions=True,
    )

    # Build standings lookup: abbr -> full record dict (includes wins, losses, pct, seed, conference, rec)
    standings_lookup: dict[str, dict] = {}
    if isinstance(standings_data, dict):
        for team in standings_data.get("east", []) + standings_data.get("west", []):
            standings_lookup[team.get("abbr", "")] = team

    def _team_record(abbr: str) -> str:
        t = standings_lookup.get(abbr)
        if t:
            conf_seed = f"#{t.get('seed','?')} {t.get('conference','')}"
            gb_str = f", {t.get('gb',0)} GB" if t.get('gb', 0) > 0 else " (division leader)"
            return f"{t.get('wins','?')}-{t.get('losses','?')} ({conf_seed}{gb_str}, {t.get('pct',0):.3f} win%)"
        return "record unavailable"

    # home_inj_tags / away_inj_tags are already validated against BDL roster —
    # traded/released players have been stripped out.
    _home_tags = home_inj_tags if isinstance(home_inj_tags, list) else []
    _away_tags = away_inj_tags if isinstance(away_inj_tags, list) else []

    def _fmt_tags(tags: list[str]) -> str:
        if not tags:
            return "none reported"
        rest  = [p for p in tags if "LOAD" in p or "REST" in p or "DNP" in p]
        other = [p for p in tags if p not in rest]
        return "; ".join((rest + other)[:8])

    home_record   = _team_record(home_abbr)
    away_record   = _team_record(away_abbr)
    home_injuries = _fmt_tags(_home_tags)
    away_injuries = _fmt_tags(_away_tags)

    # Detect confirmed rest/load management on either side
    home_has_rest = any("LOAD" in p or "REST" in p or "DNP" in p for p in _home_tags)
    away_has_rest = any("LOAD" in p or "REST" in p or "DNP" in p for p in _away_tags)
    rest_warning = ""
    if home_has_rest or away_has_rest:
        teams_resting = []
        if home_has_rest: teams_resting.append(home_name)
        if away_has_rest: teams_resting.append(away_name)
        rest_warning = (
            f"\n⚠️  LOAD MANAGEMENT ALERT: {' and '.join(teams_resting)} "
            f"{'have' if len(teams_resting) > 1 else 'has'} confirmed rest/load management decisions. "
            f"This is the single most important factor — adjust confidence downward and reflect this in key_factor.\n"
        )

    prompt = (
        f"UPCOMING GAME: {away_name} at {home_name} (home)\n"
        f"{rest_warning}\n"
        f"CURRENT STANDINGS:\n"
        f"  {home_name} ({home_abbr}): {home_record}\n"
        f"  {away_name} ({away_abbr}): {away_record}\n\n"
        f"ROSTER AVAILABILITY (injuries + load management + rest):\n"
        f"  {home_name}: {home_injuries}\n"
        f"  {away_name}: {away_injuries}\n\n"
        f"INSTRUCTIONS:\n"
        f"1. If any star player is listed as LOAD MANAGEMENT or REST, treat them as OUT. Do not assume they play.\n"
        f"2. A team missing its top-1 or top-2 player(s) to rest must have its win probability substantially reduced.\n"
        f"3. Reference specific players and records in your reasoning — no generic statements.\n"
        f"4. Home court advantage is worth ~3 points but is overridden by major roster absences.\n\n"
        f"Return a JSON object with exactly these fields:\n"
        f"  pick: full team name — must be exactly \"{home_name}\" or \"{away_name}\"\n"
        f"  confidence: integer 55-95. Toss-up = 55-60. If a star is resting, cap confidence at 70 unless the backup unit is demonstrably stronger.\n"
        f"  key_factor: one sentence — if anyone is resting this MUST be the key factor\n"
        f"  reasoning: two sentences grounded strictly in the records and roster data above\n\n"
        f"Return only valid JSON. No markdown, no extra text."
    )

    system = (
        "You are a sharp NBA analyst making game predictions for coaches who need accuracy above all else. "
        "You are given real standings and live roster availability data — use every piece of it. "
        "CRITICAL: Load management and rest decisions are the single biggest swing factor in NBA predictions. "
        "A team missing its best player(s) to rest is NOT the same team as their season record suggests — "
        "adjust accordingly and always name the absent player(s) in your reasoning. "
        "Confidence calibration: 90-95 = strong edge (healthy rosters, clear talent gap), "
        "75-89 = solid lean, 60-74 = moderate lean, 55-59 = toss-up or rest-game uncertainty. "
        "Never inflate confidence when roster data is incomplete or players are flagged as resting. "
        "Return only a valid JSON object."
    )

    result = await claude_service.analyze(
        prompt=prompt,
        system_prompt=system,
        override_model=_FAST_MODEL,
        override_max_tokens=300,
        override_temperature=0.15,
    )

    raw = result.analysis.strip()
    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    try:
        parsed = _json.loads(raw)
        pick           = str(parsed.get("pick", home_name))
        raw_confidence = int(parsed.get("confidence", 95))
        if raw_confidence < 50:
            logger.warning(
                "predict_game low_confidence_before_clamp | game_id=%d raw=%d pick=%s "
                "— model expressed genuine uncertainty; training signal captured",
                game_id, raw_confidence, pick,
            )
        confidence = max(55, min(95, raw_confidence))
        key_factor = str(parsed.get("key_factor", ""))
        reasoning  = str(parsed.get("reasoning", ""))
    except Exception:
        logger.warning("predict_game JSON parse failed | raw=%r", raw[:200])
        return {"error": "Could not parse prediction."}

    response = {
        "game_id": game_id,
        "pick": pick,
        "confidence": confidence,
        "key_factor": key_factor,
        "reasoning": reasoning,
        "model": result.model,
        "tokens_used": result.tokens_used,
    }
    analysis_cache.set(cache_key, response, ttl=600)  # 10 min — short TTL so late scratches/rest decisions are reflected
    logger.info("predict_game complete | game_id=%d pick=%s confidence=%d", game_id, pick, confidence)
    return response


async def compare_players(
    player_a_id: int,
    player_b_id: int,
    season: int = _DEFAULT_SEASON,
) -> dict[str, Any]:
    """
    Compare two players using official BDL season averages + true L10 game logs.

    Step 1: Fetch player objects
    Step 2: Fetch official season averages (player_ids[] param — correct v1 format)
    Step 3: Fetch 10 most-recent game logs separately for accurate L10
    Step 4: Build prompt from real data only — no computed/derived stats if source is absent
    """
    logger.info("compare_players | a=%d b=%d season=%d", player_a_id, player_b_id, season)

    cache_key = f"compare2:{min(player_a_id, player_b_id)}:{max(player_a_id, player_b_id)}:{season}"
    cached = analysis_cache.get(cache_key)
    if cached:
        logger.info("compare_players cache hit | %s", cache_key)
        return cached

    # ── Step 1: fetch both player objects ──────────────────────────────────
    try:
        player_a, player_b = await asyncio.gather(
            nba_service.get_player_by_id(player_a_id),
            nba_service.get_player_by_id(player_b_id),
        )
    except Exception as exc:
        logger.warning("compare_players player fetch failed | %s", exc)
        return {"error": str(exc)}

    name_a = f"{player_a.first_name} {player_a.last_name}"
    name_b = f"{player_b.first_name} {player_b.last_name}"

    # ── Step 2 & 3: official averages + L10 in parallel ───────────────────
    avg_a, avg_b, recent_a_raw, recent_b_raw = await asyncio.gather(
        nba_service.get_season_averages(player_a_id, season),
        nba_service.get_season_averages(player_b_id, season),
        nba_service.get_recent_stats(player_a_id, season, n=10),
        nba_service.get_recent_stats(player_b_id, season, n=10),
    )

    def _parse_bdl_num(v: Any) -> Optional[float]:
        """Parse a BDL value that is normally a float but can be 'MM:SS' for minutes."""
        if v is None:
            return None
        s = str(v)
        try:
            parts = s.split(":")
            return float(parts[0]) + (float(parts[1]) / 60 if len(parts) == 2 else 0.0)
        except Exception:
            return None

    def _safe(d: dict, key: str, decimals: int = 1) -> Optional[float]:
        v = _parse_bdl_num(d.get(key))
        return round(v, decimals) if v is not None else None

    def _pct(d: dict, key: str) -> Optional[str]:
        v = d.get(key)
        return f"{float(v)*100:.1f}%" if v is not None else None

    def _l10_avg(games: list, attr: str) -> Optional[float]:
        vals = [getattr(g, attr) for g in games if getattr(g, attr, None) is not None and getattr(g, attr) > 0]
        return round(sum(vals) / len(vals), 1) if vals else None

    def _l10_min_avg(games: list) -> Optional[float]:
        """Parse 'MM:SS' or 'MM' minute strings and average them."""
        def parse(m: Any) -> Optional[float]:
            if not m or m in ("0", "0:00"):
                return None
            try:
                parts = str(m).split(":")
                return float(parts[0]) + (float(parts[1]) / 60 if len(parts) == 2 else 0.0)
            except Exception:
                return None
        vals = [v for g in games for v in [parse(g.minutes)] if v is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    def _build_block(name: str, player: Any, avg: dict, recent: list) -> tuple[str, dict]:
        gp        = avg.get("games_played")
        mpg       = _safe(avg, "min")
        pts       = _safe(avg, "pts")
        reb       = _safe(avg, "reb")
        ast       = _safe(avg, "ast")
        stl       = _safe(avg, "stl")
        blk       = _safe(avg, "blk")
        fg_pct    = _pct(avg, "fg_pct")
        fg3_pct   = _pct(avg, "fg3_pct")
        ft_pct    = _pct(avg, "ft_pct")
        fga       = _safe(avg, "fga")
        fg3a      = _safe(avg, "fg3a")
        fta       = _safe(avg, "fta")
        fgm       = _safe(avg, "fgm")
        fg3m      = _safe(avg, "fg3m")

        # TS% and eFG% only if we have all required fields
        ts_pct = None
        efg_pct = None
        if pts and fga and fta:
            denom = 2.0 * (fga + 0.44 * fta)
            ts_pct = f"{pts / denom * 100:.1f}%" if denom > 0 else None
        if fgm is not None and fg3m is not None and fga:
            efg_pct = f"{(fgm + 0.5 * fg3m) / fga * 100:.1f}%" if fga > 0 else None

        l10_pts = _l10_avg(recent, "points")
        l10_reb = _l10_avg(recent, "rebounds")
        l10_ast = _l10_avg(recent, "assists")
        l10_min = _l10_min_avg(recent)
        has_season = pts is not None

        lines = [f"{name} | {player.position or '?'} | {player.team.name if player.team else '?'}"]
        if has_season:
            lines.append(f"Season ({gp or '?'} GP, {mpg or '?'} MPG):")
            lines.append(f"  {pts} PTS | {reb} REB | {ast} AST | {stl} STL | {blk} BLK")
            if fga:
                lines.append(f"  FGA: {fga} | 3PA: {fg3a} ({fg3_pct}) | FTA: {fta} ({ft_pct})")
            if ts_pct:
                lines.append(f"  TS%: {ts_pct}" + (f" | eFG%: {efg_pct}" if efg_pct else ""))
            elif fg_pct:
                lines.append(f"  FG%: {fg_pct}")
        else:
            lines.append("Season averages: not yet available for this season.")

        if recent:
            lines.append(f"Last {len(recent)} games ({l10_min or '?'} MPG this stretch):")
            lines.append(f"  {l10_pts or '?'} PTS | {l10_reb or '?'} REB | {l10_ast or '?'} AST")
            if l10_min is None or (mpg and l10_min < mpg * 0.6):
                lines.append("  NOTE: MPG significantly lower recently — raw per-game dip may be a minutes story.")
        else:
            lines.append("Last 10 games: no data available.")

        payload = {
            "id": player.id,
            "name": name,
            "first_name": player.first_name,
            "last_name": player.last_name,
            "position": player.position or "",
            "team": player.team.abbreviation if player.team else "",
            "team_name": player.team.name if player.team else "",
            "nba_id": player.nba_id,
            "games_played": gp,
            "avg_pts": pts, "avg_reb": reb, "avg_ast": ast,
            "avg_stl": stl, "avg_blk": blk,
            "avg_fg": float(avg.get("fg_pct") or 0),
            "avg_fg3": float(avg.get("fg3_pct") or 0),
            "avg_ft": float(avg.get("ft_pct") or 0),
            "avg_fga": fga, "avg_fg3a": fg3a, "avg_fta": fta,
            "ts_pct": float(ts_pct.rstrip('%')) / 100 if ts_pct else None,
            "efg_pct": float(efg_pct.rstrip('%')) / 100 if efg_pct else None,
            "recent_pts": l10_pts, "recent_reb": l10_reb, "recent_ast": l10_ast,
        }

        return "\n".join(lines), payload

    block_a, payload_a = _build_block(name_a, player_a, avg_a, recent_a_raw)
    block_b, payload_b = _build_block(name_b, player_b, avg_b, recent_b_raw)

    # ── Step 4: prompt using only real data ───────────────────────────────
    COMPARE_SYSTEM = """You are a basketball analytics assistant for PIVOT, used by coaches who need defensible, data-driven insights.

STRICT RULES:
- Use ONLY the data provided in the input. Never invent stats or assumptions.
- If data is missing or insufficient, say "insufficient data" — do not estimate.
- Prioritize accuracy over fluency. Be concise and decisive.
- Do not access the internet or rely on memory between calls.
- You are interpreting structured data, not deciding facts.

INPUT: You will receive a JSON object containing player stats, derived metrics (pre-computed by the backend), and team context.

TASK: Compare the players using ONLY the provided data.

Return JSON only, in this exact format:
{
  "key_differences": [],
  "better_player": "",
  "reasoning": "",
  "limitation": ""
}

Keep reasoning under 120 words. Base every claim strictly on the provided metrics."""

    prompt = (
        f"HEAD-TO-HEAD: {name_a} vs {name_b} — {season} Season\n\n"
        f"PLAYER A — {block_a}\n\n"
        f"PLAYER B — {block_b}\n\n"
        f"Compare these two players using only the stats above. "
        f"Return JSON only with key_differences (array of short strings), better_player (name), reasoning (under 120 words), and limitation (what data is missing or inconclusive)."
    )

    result = await claude_service.analyze(
        prompt=prompt,
        system_prompt=COMPARE_SYSTEM,
        override_model=_FAST_MODEL,
        override_max_tokens=600,
        override_temperature=0.1,
    )

    # Parse structured JSON response
    import json as _json
    raw_text = result.analysis.strip()
    # Strip markdown code fences if present
    if raw_text.startswith("```"):
        raw_text = raw_text.split("```")[1]
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]
        raw_text = raw_text.strip()
    try:
        structured = _json.loads(raw_text)
    except Exception:
        # Fallback: wrap prose in structured format
        structured = {
            "key_differences": [],
            "better_player": "",
            "reasoning": raw_text[:500],
            "limitation": "Response could not be parsed as structured JSON.",
        }

    response = {
        "player_a": payload_a,
        "player_b": payload_b,
        "analysis": result.analysis,
        "structured": structured,
        "model": result.model,
        "tokens_used": result.tokens_used,
        "season": season,
    }
    analysis_cache.set(cache_key, response, ttl=3600)
    logger.info("compare_players complete | %s vs %s tokens=%d", name_a, name_b, result.tokens_used)
    return response

# ---------------------------------------------------------------------------
# Team DNA Analysis
# ---------------------------------------------------------------------------

TEAM_DNA_SYSTEM_PROMPT: str = """You are a professional NBA scout and tactician. Your specialty is breaking down how teams actually play — their offensive and defensive systems, shot diet, pace, spacing, and scheme identity.

CRITICAL CONTEXT: Today is April 2026. The 2025-26 NBA season is in progress. Use your full knowledge of how these franchises have played this season and historically.

When analyzing a team's DNA, be specific. Name the plays they run, the coverages they prefer, the personnel who drive the scheme. Use shot-diet language (3PT rate, paint frequency, mid-range reliance), pace terminology (possessions per 48), and defensive scheme names (drop coverage, switching, hedging, zone). Name the players who execute each piece.

Do not be generic. "They play fast and spread the floor" is not analysis. "They rank top-5 in pace, initiate 40% of possessions from the pick-and-roll with their point guard as the ball handler, and shoot above 40% of their field goal attempts from three" is analysis.

FORMATTING: Plain prose only. No markdown, no bullets, no headers, no asterisks. Dense, expert prose in paragraphs. Write like a scout memo — every sentence earns its place."""


async def analyze_team_dna(team_name: str) -> dict[str, Any]:
    """
    Generate a deep team identity breakdown: offense, defense, pace, shot diet,
    scheme tendencies, and vulnerability profile.
    """
    logger.info("analyze_team_dna | team=%s", team_name)

    cache_key = f"team_dna:{team_name.lower().strip()}"
    cached = analysis_cache.get(cache_key)
    if cached:
        logger.info("team_dna cache hit | team=%s", team_name)
        return cached

    # Enrich with live standings
    from app.services import standings_service as _standings_svc
    standings_ctx = ""
    try:
        data = await _standings_svc.get_standings()
        for t in data.get("league", []):
            if team_name.lower() in t.get("name", "").lower() or team_name.lower() in t.get("abbr", "").lower():
                standings_ctx = (
                    f"{t['name']} ({t['abbr']}): {t.get('rec','?')} | "
                    f"#{t.get('seed','?')} {t.get('conference','?')} | "
                    f"{t.get('pct',0):.3f} PCT | {t.get('gb',0)} GB"
                )
                break
    except Exception:
        pass

    context_block = f"\nCURRENT STANDING:\n{standings_ctx}" if standings_ctx else ""

    prompt = (
        f"TEAM DNA REPORT — {team_name.upper()}{context_block}\n\n"
        "Write a complete tactical identity breakdown for this franchise. Cover:\n\n"
        "OFFENSIVE IDENTITY — What is their primary offensive system? Pick-and-roll heavy, "
        "motion offense, iso-centric, pace-and-space? Who initiates? What is their 3PT rate "
        "(league average is ~37% of shots from three — are they above or below)? How much do "
        "they rely on the paint vs mid-range? Who are their primary playmakers and what actions "
        "do they run for them?\n\n"
        "DEFENSIVE IDENTITY — What scheme do they run (man, zone, switching, drop coverage)? "
        "How do they handle screens — hedge, switch, ICE? Where do they give up shots by "
        "design and where is the real vulnerability? Who anchors the defense?\n\n"
        "PACE & TEMPO — Fast, average, deliberate? How does their pace affect matchup "
        "dynamics? Do they push in transition or set up in the half court?\n\n"
        "SHOT DIET PROFILE — Describe their shot selection tendencies. 3PT focused, "
        "paint-heavy, balanced? Are they getting to the line at a high rate or not at all?\n\n"
        "SCHEME VULNERABILITIES — What style of opponent beats this team? What defensive "
        "coverage kills their offense? What offensive attack exploits their defense? "
        "Be specific: name the player matchup problems, the play types, the defensive rotations "
        "that break them.\n\n"
        "Close with one sentence that captures the essence of who this team is right now."
    )

    result = await claude_service.analyze(
        prompt=prompt,
        system_prompt=TEAM_DNA_SYSTEM_PROMPT,
        override_max_tokens=1400,
        override_temperature=0.1,
    )

    response = {
        "team": team_name,
        "standing": standings_ctx,
        "analysis": result.analysis,
        "model": result.model,
        "tokens_used": result.tokens_used,
    }
    analysis_cache.set(cache_key, response, ttl=21600)  # 6-hour cache — team identity is stable
    logger.info("team_dna complete | team=%s tokens=%d", team_name, result.tokens_used)
    return response


async def scout_note(
    name: str,
    team: str,
    pts: float,
    reb: float,
    ast: float,
    context: str = "general",
    age: Optional[int] = None,
    pos: Optional[str] = None,
) -> dict[str, Any]:
    """
    Generate a live 1-2 sentence scout note for a single player.
    context: 'mvp' | 'young-star' | 'general'
    """
    cache_key = f"scout_note:{name.lower()}:{context}:{pts}:{reb}:{ast}"
    cached = analysis_cache.get(cache_key)
    if cached:
        return cached

    meta_parts = [f"{name} ({team})"]
    if pos:
        meta_parts.append(pos)
    if age is not None:
        meta_parts.append(f"Age {age}")
    meta = " · ".join(meta_parts)

    if context == "mvp":
        instruction = (
            "Write exactly 1-2 sentences on why this player is or isn't an MVP contender right now. "
            "Lead with the sharpest insight. Cite specific numbers. No hedging."
        )
    elif context == "young-star":
        instruction = (
            "Write exactly 1-2 sentences on what makes this young player's development trajectory "
            "noteworthy. Focus on what's emerging or what the ceiling looks like. Cite numbers."
        )
    else:
        instruction = (
            "Write exactly 1-2 sentences evaluating this player's current impact. Be specific."
        )

    prompt = (
        f"PLAYER: {meta}\n"
        f"STATS: {pts} PPG · {reb} RPG · {ast} APG\n\n"
        f"{instruction}"
    )

    result = await claude_service.analyze(
        prompt=prompt,
        system_prompt=_nba_analyst_system_prompt(),
        override_max_tokens=120,
        override_temperature=0.3,
        override_model=_FAST_MODEL,
    )

    response = {"note": result.analysis.strip(), "model": result.model}
    analysis_cache.set(cache_key, response, ttl=3600)  # 1-hour cache per stat snapshot
    return response
