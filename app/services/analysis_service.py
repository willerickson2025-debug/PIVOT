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

from app.core.cache import analysis_cache, get_cache_ttl
from app.core.season import get_current_season
from app.core.http_client import GlobalHTTPClient
from app.core.session import SessionEvent, build_context_block, record as session_record
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


def _get_player_injury_status(player_name: str, espn_raw: dict) -> str:
    """
    Look up a single player's current injury status in the raw ESPN injury JSON.

    Matches by last name first; if two players share a last name the first name
    initial is used as a tiebreaker.  Returns a human-readable string:
      "Out — right knee"
      "Questionable — hamstring"
      "Day-To-Day — load management / rest"
      "Active"
    """
    if not espn_raw or not player_name:
        return "Active"

    name_parts = player_name.lower().strip().split()
    last_name  = name_parts[-1] if name_parts else ""
    first_init = name_parts[0][0] if len(name_parts) > 1 and name_parts[0] else ""

    for team_block in espn_raw.get("injuries", []):
        for inj in team_block.get("injuries", []):
            athlete = inj.get("athlete") or {}
            a_display = athlete.get("displayName", "").lower().strip()
            a_last = (
                athlete.get("lastName") or (a_display.split()[-1] if a_display else "")
            ).lower().strip()

            if a_last != last_name:
                continue
            # First-initial tiebreaker when multiple players share a last name
            if first_init and a_display and not a_display.startswith(first_init):
                continue

            status = (inj.get("status") or "").strip()
            inj_type = inj.get("type") or inj.get("injuryType") or {}
            type_name = inj_type.get("name", "") if isinstance(inj_type, dict) else str(inj_type)
            type_lower = type_name.lower()
            comment = (inj.get("details") or {}).get("detail", "") or inj.get("shortComment", "") or ""

            if "load" in type_lower or "rest" in type_lower:
                return "Day-To-Day — load management / rest"

            if not status or status.lower() in ("active",):
                return "Active"

            parts = [status.title()]
            if comment:
                parts.append(comment)
            return " — ".join(parts)

    return "Active"


# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Season resolved at call time via get_current_season() — see app/core/season.py
_RECENT_FORM_WINDOW: int = 10   # number of most-recent games used for trend data
_MAX_TRADE_PLAYERS: int = 4     # max players to fetch live stats for in a trade

# Fast model for latency-sensitive paths (player/game analysis).
# Haiku is ~5-8x faster than Sonnet; plenty of reasoning for structured stat analysis.
_FAST_MODEL: str = "claude-haiku-4-5-20251001"

# ---------------------------------------------------------------------------
# Token tier constants
# ---------------------------------------------------------------------------
# Centralised so any future tuning is a single-line change.
#
# TIER_BLURB   — leaderboard cards (MVP Race, Young Stars): 1-2 sentences max.
# TIER_NOTE    — detailed scout note with full stat block: 2-3 sentences.
# TIER_VERDICT — structured analysis verdicts (compare, trade per-team): room
#                for 2-4 sentences per field plus JSON overhead.
# TIER_COACH   — in-game coach prose: 3-4 paragraphs of tactical depth.
# TIER_PLAY    — play diagrams: prose + JSON diagram on the same response.
# ---------------------------------------------------------------------------
_TOKENS_BLURB:   int = 140    # leaderboard blurbs (MVP Race, Young Stars)
_TOKENS_NOTE:    int = 300    # detailed scout note (player_id + full stats)
_TOKENS_GAME:    int = 900    # game slate / upcoming matchup analysis prose
_TOKENS_VERDICT: int = 750    # compare / trade structured JSON verdicts
_TOKENS_PLAYER:  int = 1000   # single player analysis prose (stream + non-stream)
_TOKENS_COACH:   int = 1400   # coach adjustment & live tactical response
_TOKENS_PLAY:    int = 1000   # timeout & defensive play draw (prose + diagram JSON)
_TOKENS_ROSTER:  int = 2200   # team roster / front-office memo
_TOKENS_DNA:     int = 1800   # team identity / DNA breakdown
_TOKENS_SECTION: int = 1600   # deep player section analysis (offense/defense/financials)
_TOKENS_PREDICT: int = 2800   # full game prediction (matchup + projection)
_TOKENS_GAME_BOX: int = 3400  # live game box score analysis (both teams, all players)


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
        override_max_tokens=_TOKENS_GAME,
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
    analysis_cache.set(cache_key, response, ttl=get_cache_ttl())
    return response


async def analyze_player(
    player_id: int,
    season: int = 0,
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
    season = season or get_current_season()
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
        override_max_tokens=_TOKENS_PLAYER,
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
    analysis_cache.set(cache_key, payload, ttl=get_cache_ttl())
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
        override_max_tokens=_TOKENS_SECTION,
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


async def analyze_player_section_stream(
    player_id: int,
    season: int,
    section: str,
) -> AsyncGenerator[dict, None]:
    """
    Streaming version of analyze_player_section.

    Yields dicts:
      {"type": "start", "player": ..., "section": "..."}
      {"type": "chunk", "text": "..."}
      {"type": "done"}
    """
    if section not in SECTION_PROMPTS:
        valid_sections = ", ".join(sorted(SECTION_PROMPTS.keys()))
        yield {"type": "error", "message": f"Unknown section '{section}'. Valid sections: {valid_sections}"}
        return

    try:
        player, agg = await _build_player_stat_block(player_id, season)
    except Exception as exc:
        yield {"type": "error", "message": str(exc)}
        return

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

    yield {"type": "start", "player": player.model_dump(), "section": section}

    async for chunk in claude_service.analyze_stream(
        prompt,
        system_prompt=_nba_analyst_system_prompt(),
        override_model=_FAST_MODEL,
        override_max_tokens=_TOKENS_SECTION,
    ):
        yield {"type": "chunk", "text": chunk}

    yield {"type": "done"}


async def analyze_player_stream(
    player_id: int,
    season: int = 0,
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
    season = season or get_current_season()
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
        # For past seasons with no data, return a clean short error rather than
        # calling Claude (which hallucinates details about non-existent seasons).
        current = get_current_season()
        if season != current:
            yield {
                "type": "error",
                "message": f"No stats found for {player.first_name} {player.last_name} in the {season}–{str(season+1)[-2:]} season.",
            }
            return
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
    async for chunk in claude_service.analyze_stream(prompt, system_prompt=_nba_analyst_system_prompt(), override_model=_FAST_MODEL, override_max_tokens=_TOKENS_PLAYER):
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
        analysis_cache.set(cache_key, payload, ttl=get_cache_ttl())  # cache_key = player_analysis:{id}:{season}

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
            _season = get_current_season()
            _, agg = await _build_player_stat_block(trade_player.id, _season)
            if agg["total_games"] > 0:
                player_stats[key] = (
                    f"{agg['avg_pts']}pts / {agg['avg_reb']}reb / {agg['avg_ast']}ast "
                    f"({agg['total_games']}GP, {_season} season)"
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


async def analyze_roster_stream(team_name: str):
    """
    Stream a front-office assessment of a team's roster, cap situation,
    and strategic priorities as SSE-style dicts.

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

    logger.info("Roster analysis streaming | team=%r", team_name)
    yield {"type": "meta", "team": team_name, "team_data": matched_team.model_dump() if matched_team else None}
    async for chunk in claude_service.analyze_stream(
        prompt=prompt,
        system_prompt=_front_office_system_prompt(),
        override_max_tokens=_TOKENS_ROSTER,
    ):
        yield {"type": "chunk", "text": chunk}
    yield {"type": "done"}


async def analyze_roster(team_name: str) -> dict[str, Any]:
    """Non-streaming wrapper kept for backwards compatibility."""
    full_text = ""
    meta: dict[str, Any] = {}
    async for evt in analyze_roster_stream(team_name):
        if evt["type"] == "meta":
            meta = evt
        elif evt["type"] == "chunk":
            full_text += evt["text"]
    logger.info("Roster analysis complete | team=%r", team_name)
    return {
        "team": meta.get("team", team_name),
        "team_data": meta.get("team_data"),
        "analysis": full_text,
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

    _JSON_SUFFIX = (
        "\n\nReturn your entire response as a single JSON object with exactly these fields:\n"
        "{\n"
        "  \"analysis\": \"full prose analysis — use \\\\n\\\\n between paragraphs\",\n"
        "  \"lineup_matchup\": [\n"
        "    {\"pos\": \"PG\", \"home\": \"Full Name\", \"away\": \"Full Name\"},\n"
        "    {\"pos\": \"SG\", \"home\": \"Full Name\", \"away\": \"Full Name\"},\n"
        "    {\"pos\": \"SF\", \"home\": \"Full Name\", \"away\": \"Full Name\"},\n"
        "    {\"pos\": \"PF\", \"home\": \"Full Name\", \"away\": \"Full Name\"},\n"
        "    {\"pos\": \"C\",  \"home\": \"Full Name\", \"away\": \"Full Name\"}\n"
        "  ],\n"
        "  \"stat_predictions\": [\n"
        "    {\"stat\": \"Team Points\",   \"home_val\": 0, \"away_val\": 0, \"edge\": \"home|away|even\", \"note\": \"one-line context\"},\n"
        "    {\"stat\": \"Rebounds\",      \"home_val\": 0, \"away_val\": 0, \"edge\": \"home|away|even\", \"note\": \"\"},\n"
        "    {\"stat\": \"Assists\",       \"home_val\": 0, \"away_val\": 0, \"edge\": \"home|away|even\", \"note\": \"\"},\n"
        "    {\"stat\": \"Turnovers\",     \"home_val\": 0, \"away_val\": 0, \"edge\": \"home|away|even\", \"note\": \"\"},\n"
        "    {\"stat\": \"3-Pointers\",    \"home_val\": 0, \"away_val\": 0, \"edge\": \"home|away|even\", \"note\": \"\"},\n"
        "    {\"stat\": \"FG%\",           \"home_val\": 0.0, \"away_val\": 0.0, \"edge\": \"home|away|even\", \"note\": \"\"}\n"
        "  ],\n"
        "  \"defensive_schemes\": [\n"
        "    {\"team\": \"team name\", \"scheme\": \"coverage name or style\", \"vulnerability\": \"how opponents attack it\", \"key_player\": \"player name\"}\n"
        "    // one object per team — describe their primary defensive identity, any unique coverages, and the specific exploit\n"
        "  ],\n"
        "  \"offensive_actions\": [\n"
        "    {\"team\": \"team name\", \"action\": \"name the play type or action\", \"detail\": \"who runs it, how, and why it works or is being stopped\"}\n"
        "    // 2-3 entries per team — dribble handoffs, screen types, spacing sets, pick-and-roll reads, etc.\n"
        "  ],\n"
        "  \"lineup_dependencies\": [\n"
        "    {\"team\": \"team name\", \"pairing\": \"Player A + Player B (or unit description)\", \"effect\": \"what this pairing enables tactically\", \"risk\": \"what breaks if one is unavailable\"}\n"
        "    // 1-2 key pairings or units per team — starters, bench anchors, or closing lineups\n"
        "  ]\n"
        "}\n"
        "For final/live games: lineup_matchup = actual players; stat_predictions = actual/pace-projected totals; tactical sections = what was observed in this game.\n"
        "For upcoming games: lineup_matchup = projected starters accounting for injuries; stat_predictions = projected totals; tactical sections = what to expect based on season tendencies.\n"
        "No markdown, no text outside the JSON."
    )

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
            "\n\nWrite a complete game breakdown covering:\n"
            "1. KEY PERFORMERS — every player who impacted this game, stats and why they mattered\n"
            "2. TURNING POINT — the specific moment(s) that decided the outcome\n"
            "3. WHAT WON IT — the tactical or individual factor the winner executed\n"
            "4. WHAT LOST IT — where the losing team broke down\n"
            "5. DEFENSIVE SCHEMES — what coverages each team ran, how the opponent attacked them, and which vulnerabilities were exposed\n"
            "6. OFFENSIVE ACTIONS — the specific play types and actions each team leaned on (handoffs, screen types, spacing sets, pick-and-roll reads)\n"
            "7. LINEUP DEPENDENCIES — which pairings drove efficiency and what broke down when they weren't on the floor\n"
            "8. INJURY IMPACT — how any absences shaped the game\n"
            "9. IMPLICATIONS — what this result means for both franchises\n"
            "Be specific. Name players, plays, quarters. No filler."
        )
        prompt += _JSON_SUFFIX
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
            "\n\nWrite a live breakdown covering:\n"
            "1. CURRENT STATE — who is winning and why, what the differential reflects\n"
            "2. KEY PERFORMERS — who is dominating right now and how\n"
            "3. TROUBLE SPOTS — who is struggling, in foul trouble, shooting cold\n"
            "4. DEFENSIVE SCHEMES — what coverages are working or breaking down live\n"
            "5. OFFENSIVE ACTIONS — the specific sets and actions each team is running successfully right now\n"
            "6. LINEUP DEPENDENCIES — which units are winning their minutes and who needs to be on the floor\n"
            "7. THE CLOSE — who has the edge to close this out and why\n"
            "Use the actual numbers. No hedging."
        )
        prompt += _JSON_SUFFIX
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

        standings_ctx = await _fetch_standings_ctx()

        context_block = ""
        if standings_ctx:
            context_block += f"\nCURRENT STANDINGS:\n{standings_ctx}"
        if game_injury_ctx:
            context_block += f"\n\nINJURY REPORT:\n{game_injury_ctx}"

        prompt = (
            f"PRE-GAME MATCHUP PREVIEW — {score_line}\n"
            f"{away_name} ({away_abbr}) at {home_name} ({home_abbr})\n"
            f"{context_block}\n\n"
            "Write a complete game preview covering:\n"
            "1. FORM & STAKES — what each team's record means, playoff implications\n"
            "2. DEFENSIVE SCHEMES — each team's primary coverage identity, unique wrinkles, and how the opponent will try to exploit them\n"
            "3. OFFENSIVE ACTIONS — the specific sets, handoffs, screen types, and spacing actions each team deploys; name the plays and players\n"
            "4. LINEUP DEPENDENCIES — the key pairings and units that drive each team's efficiency; what happens when they're disrupted by injury or foul trouble\n"
            "5. KEY BATTLES — the individual matchups that decide this game\n"
            "6. INJURY IMPACT — how the injury report changes the tactical calculus\n"
            "7. PREDICTION — confident call with specific reasoning\n"
            "Name players, name schemes. No generic takes."
        )
        prompt += _JSON_SUFFIX

    _ANALYSIS_SYSTEM = (
        "You are an elite NBA analyst writing game reports for coaches who make real tactical decisions. "
        "Always return a single valid JSON object as specified — no text outside it. "
        "The 'analysis' field is your full prose narrative. "
        "lineup_matchup must list all 5 positions. "
        "stat_predictions must have exactly 6 entries. "
        "defensive_schemes: one entry per team — name the specific coverage (e.g. drop coverage, ICE, switch-everything, zone, sag-and-pack) and the concrete vulnerability opponents exploit. "
        "offensive_actions: 2-3 entries per team — name the exact action (dribble handoff, flare screen, ghost screen, horns set, Spain PnR, etc.) and which players execute it. "
        "lineup_dependencies: 1-2 entries per team — name the pairing or unit, what it enables tactically, and what the risk is if a key player is out. "
        "Never use generic descriptions. Name players, name actions, name coverages. No filler."
    )

    result = await claude_service.analyze(
        prompt=prompt,
        system_prompt=_ANALYSIS_SYSTEM,
        override_model=_FAST_MODEL,
        override_max_tokens=_TOKENS_GAME_BOX,
        override_temperature=0.1,
    )

    logger.info("Game analysis complete | game_id=%d type=%s tokens=%d", game_id, game_type, result.tokens_used)

    # Parse structured fields from JSON response
    import json as _json
    analysis_text = result.analysis
    lineup_matchup: list = []
    stat_predictions: list = []
    defensive_schemes: list = []
    offensive_actions: list = []
    lineup_dependencies: list = []
    try:
        raw = result.analysis.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        parsed = _json.loads(raw)
        analysis_text       = str(parsed.get("analysis", result.analysis))
        lineup_matchup      = parsed.get("lineup_matchup") or []
        stat_predictions    = parsed.get("stat_predictions") or []
        defensive_schemes   = parsed.get("defensive_schemes") or []
        offensive_actions   = parsed.get("offensive_actions") or []
        lineup_dependencies = parsed.get("lineup_dependencies") or []
    except Exception:
        logger.warning("analyze_game JSON parse failed, falling back to raw text | game_id=%d", game_id)

    response = {
        "game_id": game_id,
        "game_type": game_type,
        "score_line": score_line,
        "home_team": home_name,
        "away_team": away_name,
        "analysis": analysis_text,
        "lineup_matchup": lineup_matchup,
        "stat_predictions": stat_predictions,
        "defensive_schemes": defensive_schemes,
        "offensive_actions": offensive_actions,
        "lineup_dependencies": lineup_dependencies,
        "model": result.model,
        "tokens_used": result.tokens_used,
    }
    analysis_cache.set(cache_key, response, cache_ttl)
    return response


async def coach_adjustment(body: dict[str, Any], session_id: Optional[str] = None) -> dict[str, Any]:
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

                # Inject injury report for both teams
                try:
                    _espn = await _fetch_espn_injuries_raw()
                    _home_inj, _away_inj = await asyncio.gather(
                        _validated_injury_tags(home_name, None, _espn),
                        _validated_injury_tags(away_name, None, _espn),
                    )
                    if _home_inj or _away_inj:
                        _inj_lines = []
                        if _home_inj:
                            _inj_lines.append(f"  {home_name}: {', '.join(_home_inj)}")
                        if _away_inj:
                            _inj_lines.append(f"  {away_name}: {', '.join(_away_inj)}")
                        box_summary += "\n\nINJURY REPORT (factor absent players into all lineup/rotation decisions):\n" + "\n".join(_inj_lines)
                except Exception:
                    pass
        except Exception as exc:
            logger.warning("Failed to fetch box score | game_id=%s error=%s", game_id, exc)

    situation_line = (
        situation
        or "Give me the most important adjustments based on what you see in the box score right now."
    )

    session_ctx = build_context_block(session_id)
    prompt = (
        f"{session_ctx}"
        f"LIVE IN-GAME COACHING CALL — You have full situational awareness. "
        f"Do NOT ask for more information. Give adjustments immediately based on what you see.\n\n"
        f"{game_context}\n\n"
        f"COACH'S NOTE: {situation_line}\n\n"
        f"{box_summary}\n\n"
        f"Based on the live data — who's hot, who's in foul trouble, shooting splits, turnovers, minutes — "
        f"respond in 3-4 focused paragraphs: (1) the single most important adjustment right now with specific players and scheme, "
        f"(2) any lineup change needed and why, "
        f"(3) the defensive rotation based on what their guys are doing, "
        f"(4) the exact offensive action to run next possession. "
        f"Name players by last name. Cite the actual numbers. Be surgical."
    )

    result = await claude_service.analyze(
        prompt=prompt,
        system_prompt=COACH_SYSTEM_PROMPT,
        override_max_tokens=_TOKENS_COACH,
    )

    session_record(session_id, SessionEvent(
        type="coach",
        summary=f"Coach call {my_team or 'team'} game {game_id}: {situation_line[:60]}",
        entities=[my_team] if my_team else [],
    ))

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


async def coach_live_adjustment(body: dict[str, Any], session_id: Optional[str] = None) -> dict[str, Any]:
    """
    Tactical engine for Coach Mode: pulls full live game state, detects scoring
    runs, foul trouble, clock situations, and hot/cold players, then returns
    structured JSON with prioritized adjustments.

    Parameters
    ----------
    body:
        - ``game_id`` (int): BallDontLie game ID.
        - ``my_team`` (str): Team name for perspective framing.
        - ``situation`` (str, optional): Coach's free-text context.
    """
    game_id: Optional[int] = body.get("game_id")
    my_team: str = body.get("my_team") or ""
    situation: str = body.get("situation") or ""

    if not game_id:
        return {"error": "game_id required"}

    import json as _json

    state = await nba_service.get_live_game_state(game_id)

    home_name = state["home_team"]["name"]
    away_name = state["away_team"]["name"]
    home_abbr = state["home_team"]["abbreviation"]

    is_home = my_team.lower() in (home_name + " " + home_abbr).lower() if my_team else True
    my_name  = home_name if is_home else away_name
    opp_name = away_name if is_home else home_name
    my_score  = state["home_score"] if is_home else state["away_score"]
    opp_score = state["away_score"] if is_home else state["home_score"]
    diff = my_score - opp_score
    diff_str = f"UP {abs(diff)}" if diff > 0 else (f"DOWN {abs(diff)}" if diff < 0 else "TIED")

    my_timeouts  = state["home_timeouts"] if is_home else state["away_timeouts"]
    opp_timeouts = state["away_timeouts"] if is_home else state["home_timeouts"]
    my_bonus  = state["home_in_bonus"] if is_home else state["away_in_bonus"]
    opp_bonus = state["away_in_bonus"] if is_home else state["home_in_bonus"]

    my_players  = state["home_players"] if is_home else state["away_players"]
    opp_players = state["away_players"] if is_home else state["home_players"]

    # ── Quarter summary — detect scoring runs ─────────────────────────────
    def _fmt_q_summary(qs: list[dict]) -> str:
        lines = []
        for q in qs:
            home_pts = q["home"]
            away_pts = q["away"]
            my_pts   = home_pts if is_home else away_pts
            opp_pts  = away_pts if is_home else home_pts
            marker = " ◄ RUN" if abs(my_pts - opp_pts) >= 10 else ""
            lines.append(f"  {q['period']}: {my_name} {my_pts} — {opp_pts} {opp_name}{marker}")
        return "\n".join(lines)

    q_block = _fmt_q_summary(state["quarter_summary"]) if state["quarter_summary"] else "  No completed periods yet."

    # ── Run detection: has opponent outscored us 8+ in last period? ───────
    run_alert = ""
    if state["quarter_summary"]:
        last_q = state["quarter_summary"][-1]
        my_last  = last_q["home"] if is_home else last_q["away"]
        opp_last = last_q["away"] if is_home else last_q["home"]
        if opp_last - my_last >= 8:
            run_alert = (
                f"\n⚠️  OPPONENT RUN: {opp_name} outscored {my_name} {opp_last}-{my_last} "
                f"in {last_q['period']}. Pattern response needed immediately.\n"
            )
        elif my_last - opp_last >= 8:
            run_alert = (
                f"\n✓ WE ARE ON A RUN: {my_name} outscored {opp_name} {my_last}-{opp_last} "
                f"in {last_q['period']}. Sustain the pressure.\n"
            )

    # ── Clock management flags ────────────────────────────────────────────
    clock_flags = []
    period = state["period"]
    clock  = state["clock"]
    # 2-for-1 opportunity: ~35-45s left in a quarter
    try:
        if ":" in clock:
            mins, secs = clock.split(":")
            total_secs = int(mins) * 60 + int(secs)
            if 30 <= total_secs <= 50 and period in (1, 2, 3):
                clock_flags.append(f"2-FOR-1 OPPORTUNITY: ~{clock} left in {state['period_label']} — push pace to get an extra possession.")
            if total_secs <= 90 and period == 4 and abs(diff) <= 5:
                clock_flags.append(f"LATE GAME: {clock} left in Q4, game within {abs(diff)}. Every possession is critical — control the clock.")
            if total_secs <= 30 and period == 4 and diff < 0:
                clock_flags.append(f"URGENT FOUL: DOWN {abs(diff)} with {clock} left. Must foul immediately if not already.")
    except (ValueError, AttributeError):
        pass

    # ── Player stat lines ─────────────────────────────────────────────────
    def _fmt_players(players: list[dict], label: str) -> str:
        if not players:
            return ""
        lines = [f"\n{label}:"]
        for p in players[:8]:
            fg_str = f"{p['fgm']}/{p['fga']}" if p["fga"] > 0 else "0/0"
            lines.append(
                f"  {p['player']} ({p['pos']}): {p['pts']}pts {p['reb']}reb {p['ast']}ast "
                f"{p['to']}TO {p['pf']}PF {fg_str}FG +{p['plus_minus']} | {p['min']}min"
            )
        return "\n".join(lines)

    foul_block = ""
    if state["foul_trouble"]:
        names = ", ".join(f"{f['player']} ({f['pf']} PF)" for f in state["foul_trouble"])
        foul_block = f"\nFOUL TROUBLE: {names}"

    hot_block = ""
    if state["hot_shooters"]:
        names = ", ".join(f"{h['player']} ({h['fgm']}/{h['fga']}FG, {h['pts']}pts)" for h in state["hot_shooters"])
        hot_block = f"\nHOT: {names}"

    cold_block = ""
    if state["cold_shooters"]:
        names = ", ".join(f"{c['player']} ({c['fgm']}/{c['fga']}FG)" for c in state["cold_shooters"])
        cold_block = f"\nCOLD: {names}"

    to_block = ""
    if state["high_turnover_players"]:
        names = ", ".join(f"{t['player']} ({t['to']} TO)" for t in state["high_turnover_players"])
        to_block = f"\nHIGH TURNOVERS: {names}"

    situation_line = situation or "Give me the most critical adjustment right now."

    clock_flag_block = "\n".join(clock_flags) if clock_flags else ""

    # Fetch injury report for both teams
    inj_block = ""
    try:
        _espn = await _fetch_espn_injuries_raw()
        _my_inj, _opp_inj = await asyncio.gather(
            _validated_injury_tags(my_name, None, _espn),
            _validated_injury_tags(opp_name, None, _espn),
        )
        if _my_inj or _opp_inj:
            _parts = []
            if _my_inj:
                _parts.append(f"  {my_name}: {', '.join(_my_inj)}")
            if _opp_inj:
                _parts.append(f"  {opp_name}: {', '.join(_opp_inj)}")
            inj_block = "\nINJURY REPORT (factor absent players into all rotation/lineup calls):\n" + "\n".join(_parts)
    except Exception:
        pass

    session_ctx = build_context_block(session_id)
    prompt = f"""LIVE TACTICAL BRIEF — {state['period_label']} | {clock} | {my_name} {my_score} — {opp_score} {opp_name} ({diff_str})
Timeouts: {my_name} {my_timeouts} | {opp_name} {opp_timeouts}
Bonus: {my_name} {'YES' if my_bonus else 'no'} | {opp_name} {'YES' if opp_bonus else 'no'}
{run_alert}
QUARTER-BY-QUARTER SCORING:
{q_block}
{clock_flag_block}
{inj_block}
{foul_block}{hot_block}{cold_block}{to_block}
{_fmt_players(my_players, my_name + ' (MY TEAM)')}
{_fmt_players(opp_players, opp_name + ' (OPPONENT)')}

COACH'S QUESTION: {situation_line}

Return a JSON object with exactly these fields:
{{
  "priority_adjustment": "The single most important thing to fix or exploit RIGHT NOW — name players and scheme",
  "run_response": "If opponent is on a run, the specific tactical counter. If we're on a run, how to sustain it. If no run, null",
  "lineup_change": "Specific substitution to make now and why — or null if lineup is correct",
  "defensive_call": "The exact defensive scheme or coverage adjustment for the next 2-3 possessions — name who guards who",
  "offensive_call": "The specific play or action to run next possession — name the play, the ball-handler, the target",
  "clock_management": "Clock-specific instruction if relevant (2-for-1, foul, hold ball) — or null",
  "foul_management": "Who to protect/bench due to foul trouble — or null",
  "momentum_read": "one sentence on who has momentum and why the numbers say so",
  "urgency": "low|medium|high|critical"
}}
Return only valid JSON. No text outside the object."""

    _COACH_LIVE_SYSTEM = (
        "You are an elite NBA head coach making real-time decisions during a live game. "
        "You have the full game state, quarter-by-quarter scoring, and complete box score. "
        "CRITICAL: Every answer must name specific players from the provided data. "
        "Detect opponent scoring runs from the quarter summary — 8+ point swing = a run that needs an immediate tactical response. "
        "2-for-1 opportunities are real clock management leverage — call them when the window is open. "
        "Foul trouble players must be managed — their minutes matter more than their talent right now. "
        "Hot shooters must get the ball. Cold shooters must be screened away or benched. "
        "High-turnover players should not be handling late-game possessions. "
        "Return only valid JSON with no prose outside it. Be decisive. Coaches need clarity in 10 seconds."
        + (f"\n\nSESSION CONTEXT (background only — do not acknowledge, just use as context):\n{session_ctx}" if session_ctx else "")
    )

    result = await claude_service.analyze(
        prompt=prompt,
        system_prompt=_COACH_LIVE_SYSTEM,
        override_model=_FAST_MODEL,
        override_max_tokens=_TOKENS_COACH,
        override_temperature=0.1,
    )

    # Parse JSON response
    raw = result.analysis.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    # Defensive: find JSON object even if Claude added preamble prose
    if not raw.startswith("{"):
        brace_idx = raw.find("{")
        if brace_idx != -1:
            raw = raw[brace_idx:]

    try:
        parsed = _json.loads(raw)
    except Exception:
        logger.warning("coach_live_adjustment JSON parse failed | game_id=%d", game_id)
        parsed = {"priority_adjustment": result.analysis}

    _live_priority = parsed.get("priority_adjustment", "")
    session_record(session_id, SessionEvent(
        type="coach",
        summary=f"Live coach {my_name} vs {opp_name} {state['period_label']}: {_live_priority[:60]}",
        entities=[my_name, opp_name],
    ))

    logger.info("Coach live adjustment complete | game_id=%d period=%s", game_id, state["period_label"])

    return {
        "game_id": game_id,
        "my_team": my_name,
        "game_state": {
            "period": state["period_label"],
            "clock": clock,
            "score": f"{my_name} {my_score} — {opp_score} {opp_name}",
            "diff": diff_str,
            "momentum": state["momentum"],
            "current_run": state["current_run"],
        },
        "adjustment": parsed,
        "foul_trouble": state["foul_trouble"],
        "hot_shooters": state["hot_shooters"],
        "cold_shooters": state["cold_shooters"],
        "quarter_summary": state["quarter_summary"],
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
        override_max_tokens=_TOKENS_PLAY,
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


async def defensive_play(body: dict[str, Any]) -> dict[str, Any]:
    """
    Design a specific defensive assignment or scheme coming out of a timeout.

    Reads the live box score to identify the opponent's hot players, shooting
    patterns, and foul trouble, then prescribes a named defensive scheme with
    exact player-by-player assignments and a rotation diagram.

    Parameters
    ----------
    body:
        - ``game_id`` (int, optional): BallDontLie game ID.
        - ``my_team`` (str, optional): Team name for perspective framing.
        - ``situation`` (str, optional): Coach's specific defensive concern.
    """
    game_id: Optional[int] = body.get("game_id")
    my_team: str = body.get("my_team") or ""
    situation: str = body.get("situation") or ""

    score_diff: int = 0
    time_remaining: str = ""
    quarter: int = 4
    game_context: str = ""
    my_players_lines: str = ""
    opp_players_lines: str = ""
    threat_block: str = ""

    logger.info("Defensive play | game_id=%s team=%r", game_id, my_team)

    if game_id:
        try:
            box = await nba_service.get_game_boxscore(game_id)
            state = await nba_service.get_live_game_state(game_id)

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
                opp_score_val = away_score if is_home else home_score
                score_diff = my_score - opp_score_val
                quarter = period if period > 0 else 4
                time_remaining = clock
                diff_str = f"UP {abs(score_diff)}" if score_diff > 0 else (f"DOWN {abs(score_diff)}" if score_diff < 0 else "TIED")
                quarter_label = f"Q{quarter}" if quarter <= 4 else ("OT" if quarter == 5 else f"OT{quarter - 4}")
                game_context = (
                    f"SCORE: {away_name} {away_score} — {home_score} {home_name}\n"
                    f"PERIOD: {quarter_label} {clock} | MY TEAM ({my_team or home_name}): {diff_str}"
                )

                my_key  = "home_players" if is_home else "away_players"
                opp_key = "away_players" if is_home else "home_players"

                def _fmt(players: list[dict], label: str) -> str:
                    lines = [f"{label}:"]
                    for p in players[:8]:
                        fg = f"{p.get('fgm',0)}/{p.get('fga',0)}" if isinstance(p.get('fga'), int) else p.get('fg','')
                        lines.append(
                            f"  {p['player']} ({p.get('pos','')}) #{p.get('jersey','')}:"
                            f" {p['pts']}pts {p['reb']}reb {p['ast']}ast {p.get('fg3','') or ''} {fg}FG {p.get('min','?')}min {p['pf']}PF"
                        )
                    return "\n".join(lines)

                my_players_lines  = _fmt(box.get(my_key) or [], "MY DEFENDERS")
                opp_players_lines = _fmt(box.get(opp_key) or [], "OPPONENT THREATS")

                # Identify the top scoring threats on the opponent
                opp_players = box.get(opp_key) or []
                threats = sorted(opp_players, key=lambda p: p["pts"], reverse=True)[:3]
                hot = state.get("hot_shooters") or []
                opp_hot = [h for h in hot if h.get("team") != (home_name if is_home else away_name)]
                if threats or opp_hot:
                    parts = [f"{t['player']} ({t['pts']}pts, {t.get('fg','?')}FG)" for t in threats]
                    if opp_hot:
                        parts += [f"{h['player']} ({h['fgm']}/{h['fga']}FG — HOT)" for h in opp_hot]
                    threat_block = f"\nKEY THREATS TO STOP: {', '.join(parts)}"

        except Exception as exc:
            logger.warning("Failed to fetch data for defensive play | game_id=%s error=%s", game_id, exc)

    situation_line = situation or "Design the best defensive assignment for the next possession."

    _DEF_SCHEME_NAMES = (
        "man-to-man, switching man, ICE coverage (force baseline on ball screens), "
        "drop coverage (sag the big under screens), hedge-and-recover, show-and-recover, "
        "box-and-one, triangle-and-two, 2-3 zone, 1-3-1 zone, full-court press, "
        "3/4 court trap, deny-the-entry, pack-the-paint, blitz the ball screen"
    )

    prompt_parts = [
        "DEFENSIVE TIMEOUT — Design the exact defensive scheme. Executable in 20 seconds.\n",
        f"Team: {my_team or 'My team'}",
    ]
    if game_context:
        prompt_parts.append(game_context)
    if threat_block:
        prompt_parts.append(threat_block)
    if my_players_lines:
        prompt_parts.append(my_players_lines)
    if opp_players_lines:
        prompt_parts.append(opp_players_lines)
    prompt_parts += [
        f"\nCOACH'S CALL: {situation_line}",
        "",
        "Respond with:",
        "1. SCHEME NAME — pick the exact defensive scheme from this list or name a variation: " + _DEF_SCHEME_NAMES,
        "2. WHY THIS SCHEME — one sentence on why it neutralizes the specific threat right now",
        "3. ASSIGNMENTS — name every defender and exactly who/what they guard:",
        "   - Who takes the primary ball-handler",
        "   - Who takes the best scorer",
        "   - Big's positioning (drop, hedge, switch, or protect rim)",
        "   - Help-side responsibilities",
        "   - Any traps, denials, or special instructions",
        "4. ROTATION RULE — one sentence on what triggers the first rotation and who covers",
        "5. ADJUSTMENT — what the opponent will try to counter with, and how you pre-empt it",
        "",
        "Name every player by last name. Use their actual stats from the box score.",
        "Then on the very last line — after all prose — output a defensive court diagram in this exact format (no spaces, valid JSON):",
        'DIAGRAM:{"p":[{"n":1,"x":50,"y":72},{"n":2,"x":76,"y":58},{"n":3,"x":24,"y":58},{"n":4,"x":64,"y":42},{"n":5,"x":50,"y":36}],"assignments":[{"defender":1,"marks":"PG"},{"defender":2,"marks":"SG"},{"defender":3,"marks":"SF"},{"defender":4,"marks":"PF"},{"defender":5,"marks":"C"}],"zones":[]}',
        "Use x=50,y=50 as the paint; basket at x=50,y=89. Adjust defender positions to reflect the called scheme (e.g. sagging bigs, help-side positions, zone).",
        "The DIAGRAM line must be the absolute last line. Valid JSON only.",
    ]

    _DEF_SYSTEM = (
        "You are an elite NBA head coach designing a defensive scheme in a live timeout. "
        "Every answer names specific players from the box score data provided. "
        "Be decisive — coaches need a clear, executable scheme in 20 seconds. "
        "Choose the scheme that best neutralizes the opponent's current hot hand and scoring pattern. "
        "Account for your own foul trouble — players with 3+ fouls cannot be primary defenders on drives. "
        "Use the actual player names and stats. No generic filler. Plain prose, no markdown."
    )

    result = await claude_service.analyze(
        prompt="\n".join(prompt_parts),
        system_prompt=_DEF_SYSTEM,
        override_max_tokens=_TOKENS_PLAY,
        override_temperature=0.1,
    )

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
        "Defensive play complete | game_id=%s team=%r tokens=%d diagram=%s",
        game_id, my_team, result.tokens_used, "yes" if diagram else "no",
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


async def predict_game(body: dict[str, Any], session_id: Optional[str] = None) -> dict[str, Any]:
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

    session_ctx = build_context_block(session_id)
    prompt = (
        f"UPCOMING GAME: {away_name} at {home_name} (home)\n"
        f"{rest_warning}\n"
        f"CURRENT STANDINGS:\n"
        f"  {home_name} ({home_abbr}): {home_record}\n"
        f"  {away_name} ({away_abbr}): {away_record}\n\n"
        f"ROSTER AVAILABILITY (injuries + load management + rest):\n"
        f"  {home_name}: {home_injuries}\n"
        f"  {away_name}: {away_injuries}\n\n"
        f"CRITICAL RULES:\n"
        f"1. LOAD MANAGEMENT / REST players are OUT — never assume they play.\n"
        f"2. Missing a top-2 player drastically reduces win probability — quantify this explicitly.\n"
        f"3. Name specific players in every section. Zero generic statements.\n"
        f"4. Home court = ~3 point advantage unless overridden by major absences.\n"
        f"5. Write as a scout who watched both teams play this week.\n\n"
        f"Return a JSON object with EXACTLY these fields. Each section must meet its minimum sentence requirement:\n\n"
        f"  pick: full team name — must be exactly \"{home_name}\" or \"{away_name}\"\n"
        f"  confidence: integer 55-95\n"
        f"  key_factor: 1 sentence — the single swing factor; must name any resting stars\n"
        f"  form_analysis: 4-5 sentences — what each team's record actually signals right now: "
        f"current seeding position, recent win/loss streak, whether the record over- or understates their quality, "
        f"playoff implications, and which team is playing with more urgency.\n"
        f"  injury_impact: 4-5 sentences — go player by player through every listed absence: "
        f"what role they fill, who replaces them, how the rotation changes in minutes and scheme, "
        f"which injuries are truly game-altering vs minor, and the net health advantage for one side.\n"
        f"  matchup_breakdown: 4-5 sentences — the tactical clash: pace each team prefers and which side wins "
        f"that battle, defensive scheme matchup (zone vs man, switch-heavy vs drop coverage), "
        f"the specific positional mismatches (e.g. who guards who at each position), "
        f"and which team's system survives best when short-handed.\n"
        f"  player_battles: 3-4 sentences — name the 2-3 specific 1v1 matchups that will decide this game, "
        f"who has the edge in each, and how those battles connect to the final outcome.\n"
        f"  prediction_rationale: 4-5 sentences — your final call: synthesize everything above into a "
        f"confident directional argument, describe the most likely game script (lead changes, 4th quarter dynamics), "
        f"state what would have to go wrong for your pick to lose, and give one sharp closing sentence.\n"
        f"  outlook: 2 sentences — projected final score range (e.g. 'PHX wins 112-104') and "
        f"the specific sequence of events most likely to produce it.\n"
        f"  lineup_matchup: array of 5 objects — projected starting lineup matchup by position:\n"
        f"    [{{\"pos\":\"PG\",\"home\":\"Full Name\",\"away\":\"Full Name\"}}, ...SF, SG, PF, C]\n"
        f"    Account for injuries — if a starter is out, use their replacement.\n"
        f"  stat_predictions: array of 6 objects — projected team totals for this game:\n"
        f"    [{{\"stat\":\"Team Points\",\"home_val\":0,\"away_val\":0,\"edge\":\"home|away|even\",\"note\":\"brief context\"}}, ...]\n"
        f"    Include: Team Points, Rebounds, Assists, Turnovers, 3-Pointers, FG%\n"
        f"    Base projections on season averages adjusted for tonight's injuries/rest decisions.\n"
        f"  defensive_schemes: array — one entry per team:\n"
        f"    [{{\"team\":\"team name\",\"scheme\":\"specific coverage name\",\"vulnerability\":\"how opponent attacks it\",\"key_player\":\"player name\"}}]\n"
        f"    Name the exact coverage (drop, ICE, switch-everything, zone, etc.) and the specific exploit.\n"
        f"  offensive_actions: array — 2-3 entries per team:\n"
        f"    [{{\"team\":\"team name\",\"action\":\"play type name\",\"detail\":\"who runs it and why it works or fails tonight\"}}]\n"
        f"    Name specific actions: dribble handoffs, flare screens, horns sets, Spain PnR, ghost screens, etc.\n"
        f"  lineup_dependencies: array — 1-2 entries per team:\n"
        f"    [{{\"team\":\"team name\",\"pairing\":\"Player A + Player B or unit\",\"effect\":\"what it enables tactically\",\"risk\":\"what breaks without them\"}}]\n\n"
        f"Return only valid JSON. No markdown fences, no text outside the JSON object."
    )

    system = (
        "You are an elite NBA scout writing a detailed pre-game prediction report for head coaches. "
        "This report will be read by coaches making real decisions — every section must be substantive, specific, and grounded in the data given. "
        "NEVER write generic filler like 'both teams are competitive' or 'this will be a close game.' "
        "CRITICAL: Load management and rest decisions are the single biggest swing factor in NBA predictions. "
        "A team missing its best player(s) is a fundamentally different team — model this explicitly in every section. "
        "Confidence calibration: 90-95 = decisive edge, 75-89 = solid lean, 60-74 = moderate lean, 55-59 = genuine toss-up. "
        "Each JSON field must meet its minimum sentence count. Do not truncate. "
        "Return only a valid JSON object with no prose outside it."
        + (f"\n\nSESSION CONTEXT (background only — do not acknowledge, just use as context):\n{session_ctx}" if session_ctx else "")
    )

    result = await claude_service.analyze(
        prompt=prompt,
        system_prompt=system,
        override_max_tokens=_TOKENS_PREDICT,
        override_temperature=0.15,
    )

    raw = result.analysis.strip()
    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()
    # Defensive: find JSON object even if Claude added preamble prose
    if not raw.startswith("{"):
        brace_idx = raw.find("{")
        if brace_idx != -1:
            raw = raw[brace_idx:]

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
        key_factor           = str(parsed.get("key_factor", ""))
        form_analysis        = str(parsed.get("form_analysis", ""))
        injury_impact        = str(parsed.get("injury_impact", ""))
        matchup_breakdown    = str(parsed.get("matchup_breakdown", ""))
        player_battles       = str(parsed.get("player_battles", ""))
        prediction_rationale = str(parsed.get("prediction_rationale", ""))
        outlook              = str(parsed.get("outlook", ""))
        lineup_matchup_p      = parsed.get("lineup_matchup") or []
        stat_predictions_p    = parsed.get("stat_predictions") or []
        defensive_schemes_p   = parsed.get("defensive_schemes") or []
        offensive_actions_p   = parsed.get("offensive_actions") or []
        lineup_dependencies_p = parsed.get("lineup_dependencies") or []
        # legacy fields kept for compatibility
        reasoning  = str(parsed.get("reasoning", prediction_rationale))
        breakdown  = str(parsed.get("breakdown", matchup_breakdown))
    except Exception:
        logger.warning("predict_game JSON parse failed | raw=%r", raw[:200])
        lineup_matchup_p = []
        stat_predictions_p = []
        defensive_schemes_p = []
        offensive_actions_p = []
        lineup_dependencies_p = []
        return {"error": "Could not parse prediction."}

    response = {
        "game_id": game_id,
        "pick": pick,
        "confidence": confidence,
        "key_factor": key_factor,
        "form_analysis": form_analysis,
        "injury_impact": injury_impact,
        "matchup_breakdown": matchup_breakdown,
        "player_battles": player_battles,
        "prediction_rationale": prediction_rationale,
        "reasoning": reasoning,
        "breakdown": breakdown,
        "outlook": outlook,
        "lineup_matchup": lineup_matchup_p,
        "stat_predictions": stat_predictions_p,
        "defensive_schemes": defensive_schemes_p,
        "offensive_actions": offensive_actions_p,
        "lineup_dependencies": lineup_dependencies_p,
        "model": result.model,
        "tokens_used": result.tokens_used,
    }
    session_record(session_id, SessionEvent(
        type="predict",
        summary=f"Predict {away_name} @ {home_name}: {pick} wins ({confidence}% conf) — {key_factor[:60]}",
        entities=[home_name, away_name],
        concern=key_factor[:80] if home_has_rest or away_has_rest else None,
    ))

    analysis_cache.set(cache_key, response, ttl=600)  # 10 min — short TTL so late scratches/rest decisions are reflected
    logger.info("predict_game complete | game_id=%d pick=%s confidence=%d", game_id, pick, confidence)
    return response


_ARCHETYPE_LENSES: dict[str, dict] = {
    "architect": {
        "name": "The Architect",
        "system": (
            "You are analyzing through the lens of a systems-first, data-driven coach. "
            "Prioritize: True Shooting %, eFG%, assist-to-turnover ratio, defensive stats (STL+BLK), "
            "role clarity, and consistency (L10 vs season delta). De-emphasize raw scoring volume. "
            "Ask: which player executes a system more reliably?"
        ),
        "prompt": (
            "Apply an analytics-first lens. Weight efficiency metrics (TS%, eFG%, AST/TO) and "
            "two-way consistency above raw scoring. Identify which player is the better system fit."
        ),
    },
    "motivator": {
        "name": "The Motivator",
        "system": (
            "You are analyzing through the lens of a culture-first, player-development coach. "
            "Prioritize: recent form trajectory (L10 trend), hustle stats (STL, BLK, REB relative to position), "
            "minutes played (effort/availability), and competitive resilience. "
            "Ask: which player elevates those around them and shows up consistently?"
        ),
        "prompt": (
            "Apply a culture and momentum lens. Emphasize recent form, hustle stats, availability, "
            "and competitive character. Identify who makes their teammates better."
        ),
    },
    "tactician": {
        "name": "The Tactician",
        "system": (
            "You are analyzing through the lens of a scheme-obsessed tactician. "
            "Prioritize: positional versatility (position listed), 3-point attempt rate and accuracy, "
            "assist rate, pick-and-roll threat (pts + ast combo), and mismatch-creation potential. "
            "Ask: which player creates more tactical optionality and breaks down defenses?"
        ),
        "prompt": (
            "Apply a schematic lens. Emphasize versatility, spacing (3PA/3P%), playmaking (AST), "
            "and tactical flexibility. Identify which player gives a coach more in-game options."
        ),
    },
    "players_coach": {
        "name": "The Player's Coach",
        "system": (
            "You are analyzing through the lens of a player-empowerment coach who values autonomy and development. "
            "Prioritize: offensive creation (AST, FGA volume), recent trajectory (is this player getting better?), "
            "free throw rate (getting to the line = aggression), and overall usage. "
            "Ask: which player has more upside and benefits most from trust and freedom?"
        ),
        "prompt": (
            "Apply a player-development lens. Emphasize offensive creation, aggression (FTA), "
            "and growth trajectory. Identify who has the higher ceiling given autonomy."
        ),
    },
    "disciplinarian": {
        "name": "The Disciplinarian",
        "system": (
            "You are analyzing through the lens of a standards-first, accountability coach. "
            "Prioritize: defensive stats (STL+BLK+REB), FT% (free throw execution = discipline), "
            "consistency between season averages and L10, and low turnover production. "
            "Ask: which player meets the standard every night and can be trusted in high-stakes moments?"
        ),
        "prompt": (
            "Apply a discipline and accountability lens. Prioritize defensive production, "
            "free throw execution, consistency (season vs L10), and low turnovers. "
            "Identify who you can trust in the biggest moments."
        ),
    },
    "innovator": {
        "name": "The Innovator",
        "system": (
            "You are analyzing through the lens of an unconventional, creativity-first coach. "
            "Prioritize: 3-point volume and efficiency, multi-category production (pts+reb+ast combined), "
            "positional mismatch potential, and outlier or unexpected stats. "
            "Ask: which player breaks conventional defensive schemes and enables positionless basketball?"
        ),
        "prompt": (
            "Apply an unconventional lens. Weight 3-point creation, multi-positional versatility, "
            "and surprising stat combinations. Identify which player enables a positionless, "
            "scheme-breaking attack."
        ),
    },
    "closer": {
        "name": "The Closer",
        "system": (
            "You are analyzing through the lens of a results-obsessed, pressure-moment coach. "
            "Prioritize: scoring volume (PTS, FGA), free throw rate (FTA — getting to the line under pressure), "
            "FT% (executing when it matters), and recent form (L10 — who is hot right now). "
            "Ask: who do you give the ball to with the game on the line?"
        ),
        "prompt": (
            "Apply a clutch, high-leverage lens. Emphasize scoring volume, free throw rate and accuracy, "
            "and recent form. Identify who you trust most with the game on the line."
        ),
    },
}


# ---------------------------------------------------------------------------
# Archetype Stat Map + Spotlight Builder
# ---------------------------------------------------------------------------
#
# ARCHETYPE_STAT_MAP declares which metrics each lens foregrounds and why.
# _archetype_spotlight() uses it to build a focused summary block that is
# prepended to the standard stat block in compare/trade prompts — so Claude
# reads the lens-relevant numbers first, before the full detail block.
#
# Design rule: every spotlight is self-contained; it re-extracts values from
# the enriched player dict so it can be called at prompt-build time without
# touching the cached stat_block.
# ---------------------------------------------------------------------------

ARCHETYPE_STAT_MAP: dict[str, dict] = {
    "architect": {
        "label":   "ARCHITECT LENS — EFFICIENCY & SYSTEM FIT",
        "focus":   "TS%, eFG%, AST/TO, FT Rate, STL/BLK, season-to-L10 consistency delta",
        "reason":  "Systems coaches want efficiency and reliability, not raw volume.",
    },
    "motivator": {
        "label":   "MOTIVATOR LENS — TRAJECTORY & HUSTLE",
        "focus":   "L10 recent form first, trend deltas (↑/↓), availability (GP/MPG), hustle (STL/BLK)",
        "reason":  "Culture coaches care who is building momentum and showing up every night.",
    },
    "tactician": {
        "label":   "TACTICIAN LENS — SCHEME IMPACT & SPACING",
        "focus":   "3PA, 3P%, AST, PTS+AST combined (P&R proxy), positional versatility",
        "reason":  "Tacticians need to know: does this player break defenses and create options?",
    },
    "players_coach": {
        "label":   "PLAYER'S COACH LENS — CREATION & DEVELOPMENT",
        "focus":   "FGA (volume), FTA (aggression), AST, usage proxy, L10 growth trend",
        "reason":  "Development coaches ask: is this player getting more creative and attacking?",
    },
    "disciplinarian": {
        "label":   "DISCIPLINARIAN LENS — DEFENSE & ACCOUNTABILITY",
        "focus":   "STL, BLK, REB, TOV (ball security), FT% (execution), season-L10 variance",
        "reason":  "Accountability coaches trust players who defend, protect the ball, and hit FTs.",
    },
    "innovator": {
        "label":   "INNOVATOR LENS — POSITIONLESS VERSATILITY",
        "focus":   "3PA + 3P% (floor-spacing), combined production (PTS+REB+AST), position",
        "reason":  "Innovators want players who break conventional schemes and play multiple roles.",
    },
    "closer": {
        "label":   "CLOSER LENS — PRESSURE SCORING & HOT HAND",
        "focus":   "L10 first (who's hot NOW), PTS volume, FTA, FT%, season scoring baseline",
        "reason":  "Closers need the player who is producing under pressure RIGHT NOW.",
    },
}


def _archetype_spotlight(ep: dict, archetype_key: str) -> str:
    """
    Return a lens-specific foregrounded metric summary to prepend to the full
    stat block in Claude prompts.  Ordering of sub-sections matches each
    archetype's documented priorities in ARCHETYPE_STAT_MAP.

    Returns an empty string for unknown archetype keys.
    """
    spec = ARCHETYPE_STAT_MAP.get(archetype_key)
    if not spec:
        return ""

    label = spec["label"]
    lines = [label]

    def _s(v: Optional[float], suffix: str = "", prefix: str = "") -> str:
        return f"{prefix}{v}{suffix}" if v is not None else "—"

    def _delta_str(d: Optional[float]) -> str:
        if d is None:
            return "—"
        sign = "+" if d >= 0 else ""
        return f"{sign}{d}"

    if archetype_key == "architect":
        # ── Efficiency first ──────────────────────────────────────────────
        ts     = ep.get("ts_pct")
        efg    = ep.get("efg_pct")
        ast_to = ep.get("ast_to")
        fga    = ep.get("fga")
        fta    = ep.get("fta")
        ft_rate = round(fta / fga, 2) if fga and fta and fga > 0 else None
        stl    = ep.get("stl")
        blk    = ep.get("blk")

        eff: list[str] = []
        if ts     is not None: eff.append(f"TS% {ts}%")
        if efg    is not None: eff.append(f"eFG% {efg}%")
        if ast_to is not None: eff.append(f"AST/TO {ast_to}")
        if ft_rate is not None: eff.append(f"FT Rate {ft_rate}")
        if stl    is not None: eff.append(f"STL {stl}")
        if blk    is not None: eff.append(f"BLK {blk}")
        if eff:
            lines.append("  Efficiency: " + " | ".join(eff))

        # Consistency delta
        pts_d = ep.get("pts_delta"); reb_d = ep.get("reb_delta"); ast_d = ep.get("ast_delta")
        drift: list[str] = []
        if pts_d is not None: drift.append(f"PTS {_delta_str(pts_d)}")
        if reb_d is not None: drift.append(f"REB {_delta_str(reb_d)}")
        if ast_d is not None: drift.append(f"AST {_delta_str(ast_d)}")
        if drift:
            lines.append(f"  L10 vs season drift: {' | '.join(drift)}")

    elif archetype_key == "motivator":
        # ── Recent trajectory first ───────────────────────────────────────
        l10_pts = ep.get("l10_pts"); l10_reb = ep.get("l10_reb"); l10_ast = ep.get("l10_ast")
        l10_stl = ep.get("l10_stl"); l10_blk = ep.get("l10_blk")
        l10_n   = ep.get("l10_n", 10)
        if l10_pts is not None:
            lines.append(
                f"  L{l10_n} (recent): {_s(l10_pts)} PTS | {_s(l10_reb)} REB | {_s(l10_ast)} AST"
                f" | {_s(l10_stl)} STL | {_s(l10_blk)} BLK"
            )

        pts_d = ep.get("pts_delta"); reb_d = ep.get("reb_delta"); ast_d = ep.get("ast_delta")
        trend: list[str] = []
        if pts_d is not None: trend.append(f"PTS {_delta_str(pts_d)}")
        if reb_d is not None: trend.append(f"REB {_delta_str(reb_d)}")
        if ast_d is not None: trend.append(f"AST {_delta_str(ast_d)}")
        if trend:
            up_count = sum(1 for d in [pts_d, reb_d, ast_d] if d is not None and d > 0)
            total    = sum(1 for d in [pts_d, reb_d, ast_d] if d is not None)
            momentum = "↑ BUILDING" if up_count > total // 2 else "↓ FADING"
            lines.append(f"  Trajectory: {' | '.join(trend)} — {momentum}")

        gp = ep.get("gp"); mpg = ep.get("mpg")
        if gp or mpg:
            lines.append(f"  Availability: {_s(gp)} GP | {_s(mpg)} MPG")

    elif archetype_key == "tactician":
        # ── Spacing and playmaking ────────────────────────────────────────
        fg3a    = ep.get("fg3a"); fg3_pct = ep.get("fg3_pct")
        ast     = ep.get("ast");  pts = ep.get("pts")
        pos     = ep.get("position") or "?"

        spacing: list[str] = []
        if fg3a    is not None: spacing.append(f"3PA {fg3a}")
        if fg3_pct is not None: spacing.append(f"3P% {fg3_pct}%")
        if spacing:
            lines.append("  Spacing: " + " | ".join(spacing))

        creation: list[str] = []
        if ast is not None: creation.append(f"AST {ast}")
        if pts is not None and ast is not None:
            creation.append(f"PTS+AST {round(pts + ast, 1)} (P&R value proxy)")
        if creation:
            lines.append("  Creation: " + " | ".join(creation))

        lines.append(f"  Position: {pos} — tactical versatility")

    elif archetype_key == "players_coach":
        # ── Volume, aggression, creation upside ──────────────────────────
        fga = ep.get("fga"); fta = ep.get("fta"); ast = ep.get("ast")
        usg = ep.get("usage_proxy")
        pts_d = ep.get("pts_delta"); ast_d = ep.get("ast_delta")

        vol: list[str] = []
        if fga is not None: vol.append(f"FGA {fga}")
        if fta is not None: vol.append(f"FTA {fta} (aggression)")
        if ast is not None: vol.append(f"AST {ast}")
        if usg is not None: vol.append(f"USG {usg} poss/40")
        if vol:
            lines.append("  Creation volume: " + " | ".join(vol))

        growth: list[str] = []
        if pts_d is not None: growth.append(f"PTS {_delta_str(pts_d)}")
        if ast_d is not None: growth.append(f"AST {_delta_str(ast_d)}")
        if growth:
            lines.append(f"  Growth (L10 vs season): {' | '.join(growth)}")

    elif archetype_key == "disciplinarian":
        # ── Defense + accountability + consistency ────────────────────────
        stl    = ep.get("stl");  blk = ep.get("blk"); reb = ep.get("reb")
        tov    = ep.get("tov");  ft_pct = ep.get("ft_pct")
        pts_d  = ep.get("pts_delta"); reb_d = ep.get("reb_delta")

        defense: list[str] = []
        if stl is not None: defense.append(f"STL {stl}")
        if blk is not None: defense.append(f"BLK {blk}")
        if reb is not None: defense.append(f"REB {reb}")
        if tov is not None: defense.append(f"TOV {tov} (ball security)")
        if defense:
            lines.append("  Defense & accountability: " + " | ".join(defense))

        if ft_pct is not None:
            lines.append(f"  FT execution: {ft_pct}%")

        consistency: list[str] = []
        if pts_d is not None: consistency.append(f"PTS {_delta_str(pts_d)}")
        if reb_d is not None: consistency.append(f"REB {_delta_str(reb_d)}")
        if consistency:
            deltas = [d for d in [pts_d, reb_d] if d is not None]
            tag = "CONSISTENT" if all(abs(d) < 2.5 for d in deltas) else "INCONSISTENT"
            lines.append(f"  Season-to-L10 variance: {' | '.join(consistency)} — {tag}")

    elif archetype_key == "innovator":
        # ── Stretch + multi-category versatility ─────────────────────────
        fg3a    = ep.get("fg3a"); fg3_pct = ep.get("fg3_pct")
        pts     = ep.get("pts"); reb = ep.get("reb"); ast = ep.get("ast")
        pos     = ep.get("position") or "?"

        stretch: list[str] = []
        if fg3a    is not None: stretch.append(f"3PA {fg3a}")
        if fg3_pct is not None: stretch.append(f"3P% {fg3_pct}%")
        if stretch:
            lines.append("  Floor-spacing: " + " | ".join(stretch))

        if pts is not None and reb is not None and ast is not None:
            combined = round(pts + reb + ast, 1)
            lines.append(f"  Combined production (PTS+REB+AST): {combined}")

        lines.append(f"  Position: {pos} — positionless/mismatch potential")

    elif archetype_key == "closer":
        # ── L10 hot hand first, then pressure scoring ─────────────────────
        l10_pts = ep.get("l10_pts"); l10_ts = ep.get("l10_ts_pct")
        l10_n   = ep.get("l10_n", 10)
        pts     = ep.get("pts"); fga = ep.get("fga")
        fta     = ep.get("fta"); ft_pct = ep.get("ft_pct")

        if l10_pts is not None:
            hot: list[str] = [f"L{l10_n}: {l10_pts} PTS"]
            if l10_ts is not None: hot.append(f"TS% {l10_ts}%")
            lines.append("  HOT HAND — " + " | ".join(hot))

        pressure: list[str] = []
        if pts    is not None: pressure.append(f"{pts} PTS/g (season)")
        if fga    is not None: pressure.append(f"{fga} FGA")
        if fta    is not None: pressure.append(f"{fta} FTA")
        if ft_pct is not None: pressure.append(f"FT% {ft_pct}%")
        if pressure:
            lines.append("  Pressure scoring: " + " | ".join(pressure))

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Player enrichment — single shared utility for all Claude stat payloads
# ---------------------------------------------------------------------------

def _parse_min(m: Any) -> Optional[float]:
    """Parse a BDL minutes value ('MM:SS', 'MM', or float) to decimal minutes."""
    if not m or m in ("0", "0:00", "00"):
        return None
    try:
        parts = str(m).split(":")
        return float(parts[0]) + (float(parts[1]) / 60 if len(parts) == 2 else 0.0)
    except Exception:
        return None


def _avg(vals: list) -> Optional[float]:
    clean = [v for v in vals if v is not None]
    return round(sum(clean) / len(clean), 2) if clean else None


def _build_stat_block(ep: dict) -> str:
    """
    Build the canonical prompt-ready text block from an enriched player dict.
    This is what gets injected into every Claude prompt — one format, everywhere.
    """
    name     = ep.get("name", "?")
    pos      = ep.get("position") or "?"
    team     = ep.get("team_name") or ep.get("team") or "?"
    gp       = ep.get("gp")
    mpg      = ep.get("mpg")

    lines = [f"{name} | {pos} | {team}"]

    # ── Injury status (shown prominently when not Active) ─────────────────
    inj_status = ep.get("injury_status", "Active")
    if inj_status and inj_status != "Active":
        lines.append(f"INJURY STATUS: {inj_status}  ⚠")

    # ── Stat values ───────────────────────────────────────────────────────
    pts  = ep.get("pts");  reb = ep.get("reb");  ast = ep.get("ast")
    stl  = ep.get("stl"); blk  = ep.get("blk"); tov = ep.get("tov")
    fga  = ep.get("fga"); fg3a = ep.get("fg3a"); fta = ep.get("fta")
    fg3_pct = ep.get("fg3_pct"); ft_pct = ep.get("ft_pct")
    ts   = ep.get("ts_pct");  efg = ep.get("efg_pct")
    ast_to  = ep.get("ast_to"); usg = ep.get("usage_proxy")

    l10_pts = ep.get("l10_pts"); l10_reb = ep.get("l10_reb"); l10_ast = ep.get("l10_ast")
    l10_stl = ep.get("l10_stl"); l10_blk = ep.get("l10_blk"); l10_tov = ep.get("l10_tov")
    l10_ts  = ep.get("l10_ts_pct"); l10_ast_to = ep.get("l10_ast_to")
    l10_min = ep.get("l10_min"); l10_n = ep.get("l10_n", 10)

    trend_flag = ep.get("trend_flag")  # "trending_down" | "trending_up" | None

    # ── Trend NOTE (prominent header when form diverges ≥30% from season) ─
    if trend_flag and pts and l10_pts:
        pct_delta = (l10_pts - pts) / pts * 100
        if trend_flag == "trending_down":
            lines.append(
                f"⚠ TRENDING DOWN: L10 {l10_pts} PTS vs {pts} season ({pct_delta:+.0f}%). "
                f"Recent form is the primary lens — season totals are context only."
            )
        else:
            lines.append(
                f"↑ TRENDING UP: L10 {l10_pts} PTS vs {pts} season ({pct_delta:+.0f}%). "
                f"Recent form is the primary lens — season totals are context only."
            )

    # ── Helper: build season block lines ─────────────────────────────────
    def _season_lines(label: str) -> list[str]:
        out = []
        if pts is not None:
            gp_str  = f"{gp} GP, " if gp else ""
            mpg_str = f"{mpg} MPG" if mpg else "? MPG"
            out.append(f"{label} ({gp_str}{mpg_str}):")
            out.append(f"  {pts} PTS | {reb} REB | {ast} AST | {stl} STL | {blk} BLK | {tov} TOV")
            if fga:
                fg3_str = f"{fg3a} 3PA ({fg3_pct}%)" if fg3_pct is not None else f"{fg3a} 3PA"
                ft_str  = f"{fta} FTA ({ft_pct}%)" if ft_pct is not None else f"{fta} FTA"
                out.append(f"  FGA: {fga} | {fg3_str} | {ft_str}")
            eff_parts = []
            if ts      is not None: eff_parts.append(f"TS% {ts}%")
            if efg     is not None: eff_parts.append(f"eFG% {efg}%")
            if ast_to  is not None: eff_parts.append(f"AST/TO {ast_to}")
            if usg     is not None: eff_parts.append(f"USG {usg} poss/40")
            if eff_parts:
                out.append("  " + " | ".join(eff_parts))
        else:
            out.append(f"{label}: not yet available.")
        return out

    # ── Helper: build L10 block lines ─────────────────────────────────────
    def _l10_lines(label: str) -> list[str]:
        out = []
        if l10_pts is not None:
            min_str = f"{l10_min} MPG" if l10_min else "? MPG"
            out.append(f"{label} ({min_str}):")
            out.append(f"  {l10_pts} PTS | {l10_reb} REB | {l10_ast} AST | {l10_stl} STL | {l10_blk} BLK | {l10_tov} TOV")
            eff_l10 = []
            if l10_ts     is not None: eff_l10.append(f"TS% {l10_ts}%")
            if l10_ast_to is not None: eff_l10.append(f"AST/TO {l10_ast_to}")
            if eff_l10:
                out.append("  " + " | ".join(eff_l10))
            # Secondary delta commentary when not already captured by trend NOTE
            if not trend_flag:
                trends = []
                if pts and l10_pts:
                    d = round(l10_pts - pts, 1)
                    if abs(d) >= 2: trends.append(f"PTS {'+' if d>0 else ''}{d}")
                if reb and l10_reb:
                    d = round(l10_reb - reb, 1)
                    if abs(d) >= 1: trends.append(f"REB {'+' if d>0 else ''}{d}")
                if ast and l10_ast:
                    d = round(l10_ast - ast, 1)
                    if abs(d) >= 1: trends.append(f"AST {'+' if d>0 else ''}{d}")
                if trends:
                    arrow = "↑" if any('+' in t for t in trends) else "↓"
                    out.append(f"  vs season: {', '.join(trends)} {arrow}")
            if mpg and l10_min and l10_min < mpg * 0.65:
                out.append("  NOTE: MPG significantly lower recently — per-game dip may be a minutes story.")
        else:
            out.append(f"{label}: no data available.")
        return out

    # ── Section ordering: L10 first when form diverges significantly ──────
    if trend_flag:
        # Primary: recent form  /  Secondary: season as context
        lines += _l10_lines(f"Last {l10_n} games — PRIMARY")
        lines += _season_lines("Season avg — context only")
    else:
        # Standard order: season first, L10 below
        lines += _season_lines("Season")
        lines += _l10_lines(f"Last {l10_n} games")

    # ── On/off note ───────────────────────────────────────────────────────
    lines.append("On/off net rating: not available (requires NBA.com advanced stats).")

    return "\n".join(lines)


async def enrich_player(
    player_id: int,
    season: int = 0,
) -> dict[str, Any]:
    """
    Single shared utility — assembles the full stat payload for a player.

    Every Claude feature (compare, trade, scout notes, roster analysis) should
    call this instead of building its own stat block. Results are cached for
    30 minutes so parallel calls within one request are free.

    Returns
    -------
    dict with keys:
        identity   — id, name, first_name, last_name, team, team_name, position, nba_id
        season     — pts, reb, ast, stl, blk, tov, pf, oreb, dreb, mpg, gp,
                     fga, fgm, fg3a, fg3m, fta, ftm, fg_pct, fg3_pct, ft_pct
        derived    — ts_pct, efg_pct, ast_to, usage_proxy
        l10        — l10_pts, l10_reb, l10_ast, l10_stl, l10_blk, l10_tov,
                     l10_ts_pct, l10_ast_to, l10_min, l10_n
        deltas     — pts_delta, reb_delta, ast_delta  (L10 − season; + = trending up)
        stat_block — pre-formatted text string ready for direct Claude prompt injection
    """
    season = season or get_current_season()
    cache_key = f"enrich:{player_id}:{season}"
    cached = analysis_cache.get(cache_key)
    if cached:
        return cached

    # Fetch in parallel: player identity, season averages, L10 full game logs, ESPN injuries
    try:
        player, avg, recent, espn_raw = await asyncio.gather(
            nba_service.get_player_by_id(player_id),
            nba_service.get_season_averages(player_id, season),
            nba_service.get_recent_stats_full(player_id, season, n=10),
            _fetch_espn_injuries_raw(),
        )
    except Exception as exc:
        logger.warning("enrich_player fetch failed | player_id=%d error=%s", player_id, exc)
        return {"id": player_id, "name": "Unknown", "stat_block": "Player data unavailable."}

    name = f"{player.first_name} {player.last_name}"
    injury_status = _get_player_injury_status(name, espn_raw)

    # ── Validate: guard before any Claude call — empty avg = no season data ──
    if not avg:
        logger.warning(
            "enrich_player: no season averages | player_id=%d season=%d", player_id, season
        )
        return {
            "id": player_id,
            "name": name,
            "season": season,
            "stats_unavailable": True,
            "unavailable_message": (
                f"Stats unavailable for {name} in the {season} season. "
                f"They may not have played yet or BallDontLie has no data for this period."
            ),
            "stat_block": f"STATS UNAVAILABLE: No {season} season data found for {name}.",
        }

    # ── Helper: parse BDL number (handles 'MM:SS' minutes strings) ─────────
    def _s(key: str, decimals: int = 1) -> Optional[float]:
        v = avg.get(key)
        if v is None:
            return None
        try:
            parts = str(v).split(":")
            val = float(parts[0]) + (float(parts[1]) / 60 if len(parts) == 2 else 0.0)
            return round(val, decimals)
        except Exception:
            return None

    def _pct_display(key: str) -> Optional[float]:
        v = avg.get(key)
        return round(float(v) * 100, 1) if v is not None else None

    # ── Season averages ───────────────────────────────────────────────────
    pts  = _s("pts");   reb  = _s("reb");  ast = _s("ast")
    stl  = _s("stl");  blk  = _s("blk");  tov = _s("turnover")
    pf   = _s("pf");   oreb = _s("oreb"); dreb = _s("dreb")
    fga  = _s("fga");  fgm  = _s("fgm");  fg3a = _s("fg3a")
    fg3m = _s("fg3m"); fta  = _s("fta");  ftm  = _s("ftm")
    mpg  = _s("min");  gp   = avg.get("games_played")
    fg_pct  = _pct_display("fg_pct")
    fg3_pct = _pct_display("fg3_pct")
    ft_pct  = _pct_display("ft_pct")

    # ── Derived: TS%, eFG%, AST/TO, usage proxy ──────────────────────────
    ts_pct: Optional[float] = None
    efg_pct: Optional[float] = None
    ast_to: Optional[float] = None
    usage_proxy: Optional[float] = None

    if pts is not None and fga is not None and fta is not None:
        denom = 2.0 * (fga + 0.44 * fta)
        if denom > 0:
            ts_pct = round(pts / denom * 100, 1)

    if fgm is not None and fg3m is not None and fga and fga > 0:
        efg_pct = round((fgm + 0.5 * fg3m) / fga * 100, 1)

    if ast is not None and tov is not None and tov > 0:
        ast_to = round(ast / tov, 2)

    # Usage proxy: (FGA + 0.44*FTA + TOV) per 40 min — comparable across players
    if fga is not None and fta is not None and tov is not None and mpg and mpg > 0:
        usage_proxy = round((fga + 0.44 * fta + tov) / mpg * 40, 1)

    # ── L10 averages from full game logs ──────────────────────────────────
    def _l10(key: str) -> Optional[float]:
        vals = [g[key] for g in recent if g.get(key) is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    l10_min_vals = [_parse_min(g.get("min")) for g in recent]
    l10_min_vals = [v for v in l10_min_vals if v is not None]
    l10_min = round(sum(l10_min_vals) / len(l10_min_vals), 1) if l10_min_vals else None
    l10_n   = len(recent)

    l10_pts = _l10("pts");  l10_reb = _l10("reb"); l10_ast = _l10("ast")
    l10_stl = _l10("stl"); l10_blk = _l10("blk"); l10_tov = _l10("tov")
    l10_fga = _l10("fga"); l10_fgm = _l10("fgm")
    l10_fg3m= _l10("fg3m"); l10_fta = _l10("fta"); l10_ftm = _l10("ftm")

    # L10 derived
    l10_ts_pct: Optional[float] = None
    l10_ast_to: Optional[float] = None
    if l10_pts and l10_fga and l10_fta:
        d = 2.0 * (l10_fga + 0.44 * l10_fta)
        if d > 0:
            l10_ts_pct = round(l10_pts / d * 100, 1)
    if l10_ast and l10_tov and l10_tov > 0:
        l10_ast_to = round(l10_ast / l10_tov, 2)

    # ── Deltas (L10 − season) ─────────────────────────────────────────────
    def _delta(l10_val, season_val) -> Optional[float]:
        if l10_val is not None and season_val is not None:
            return round(l10_val - season_val, 1)
        return None

    ep: dict[str, Any] = {
        # identity
        "id":         player.id,
        "name":       name,
        "first_name": player.first_name,
        "last_name":  player.last_name,
        "position":   player.position or "",
        "team":       player.team.abbreviation if player.team else "",
        "team_name":  player.team.name if player.team else "",
        "nba_id":     player.nba_id,
        # season
        "pts": pts,  "reb": reb,  "ast": ast,
        "stl": stl,  "blk": blk,  "tov": tov,
        "pf":  pf,   "oreb": oreb, "dreb": dreb,
        "mpg": mpg,  "gp": gp,
        "fga": fga,  "fgm": fgm,  "fg3a": fg3a, "fg3m": fg3m,
        "fta": fta,  "ftm": ftm,
        "fg_pct": fg_pct, "fg3_pct": fg3_pct, "ft_pct": ft_pct,
        # derived
        "ts_pct":      ts_pct,
        "efg_pct":     efg_pct,
        "ast_to":      ast_to,
        "usage_proxy": usage_proxy,
        # L10
        "l10_pts": l10_pts, "l10_reb": l10_reb, "l10_ast": l10_ast,
        "l10_stl": l10_stl, "l10_blk": l10_blk, "l10_tov": l10_tov,
        "l10_ts_pct": l10_ts_pct, "l10_ast_to": l10_ast_to,
        "l10_min": l10_min, "l10_n": l10_n,
        # deltas
        "pts_delta": _delta(l10_pts, pts),
        "reb_delta": _delta(l10_reb, reb),
        "ast_delta": _delta(l10_ast, ast),
        # trend flag — set when L10 pts deviate ≥30% from season avg
        "trend_flag": (
            "trending_down" if (pts and l10_pts and pts > 0 and (l10_pts - pts) / pts <= -0.30)
            else "trending_up" if (pts and l10_pts and pts > 0 and (l10_pts - pts) / pts >= 0.30)
            else None
        ),
        # injury
        "injury_status": injury_status,
    }
    ep["stat_block"] = _build_stat_block(ep)

    analysis_cache.set(cache_key, ep, ttl=get_cache_ttl(default_ttl=1800))  # 10 min during game windows, 30 min otherwise
    logger.info("enrich_player complete | %s pts=%s ts=%s usg=%s", name, pts, ts_pct, usage_proxy)
    return ep


# ---------------------------------------------------------------------------
# Compare Contexts
# ---------------------------------------------------------------------------
# Each entry defines the framing question and key considerations Claude should
# weight when the user selects that situation.  The question replaces the
# abstract "who is better?" with a specific evaluative lens so the verdict
# is actually useful for a decision-maker.
# ---------------------------------------------------------------------------

_COMPARE_CONTEXTS: dict[str, dict] = {
    "contender_starter": {
        "label": "Starting role on a contender",
        "question": "Which player better fits as a starter on a championship-contending team?",
        "considerations": (
            "Weight: two-way efficiency (TS% + STL/BLK), consistency (season-to-L10 delta), "
            "durability (GP), role clarity, and performance under defensive pressure. "
            "A contender starter must produce reliably against elite defenses every night."
        ),
    },
    "next_to_star_pg": {
        "label": "Fit next to a star PG",
        "question": "Which player is the better fit playing off a star point guard who dominates ball-handling?",
        "considerations": (
            "Weight: off-ball scoring (3PA and 3P% are paramount — spacing unlocks the PG), "
            "cutting/FTA without needing creation touches, defensive versatility. "
            "Low AST is expected and acceptable; what matters is that the player makes the PG's job easier."
        ),
    },
    "next_to_star_big": {
        "label": "Fit next to a dominant big",
        "question": "Which player fits better alongside a dominant interior presence?",
        "considerations": (
            "Weight: perimeter spacing (3PA/3P% — the big needs the paint clear), "
            "AST/playmaking from the perimeter, defensive switching range, "
            "and avoiding redundant skill sets (e.g., two paint-heavy players)."
        ),
    },
    "rebuild_development": {
        "label": "Rebuild / developmental situation",
        "question": "Which player has more upside in a low-pressure environment with expanded minutes and freedom?",
        "considerations": (
            "Weight: age trajectory, FTA (aggression and initiative at the line), "
            "AST (developing playmaking), L10 trend delta (is production growing?), "
            "usage proxy (can they handle more). A player on an ascending curve wins this."
        ),
    },
    "best_value": {
        "label": "Best available value",
        "question": "Which player delivers more production relative to their usage and role?",
        "considerations": (
            "Weight: TS% (efficiency per possession), multi-category contribution, "
            "usage-adjusted output (high production at moderate usage = high value), "
            "consistency (low season-to-L10 variance). Ignore raw volume; reward efficiency."
        ),
    },
    "playoff_minutes": {
        "label": "Playoff rotation minutes",
        "question": "Which player earns and keeps rotation minutes when playoff defenses tighten?",
        "considerations": (
            "Weight: defensive impact (STL+BLK), FT rate (getting fouled in pressure moments "
            "generates possessions), low turnovers (TOV), and L10 consistency — "
            "players who wilt in the last 10 games are a liability in a seven-game series."
        ),
    },
    "max_contract_decision": {
        "label": "Max contract decision",
        "question": "Which player justifies the long-term commitment of a maximum contract?",
        "considerations": (
            "Weight: usage-adjusted efficiency (TS% at high FGA volume), multi-category dominance, "
            "durability (GP), age relative to their trajectory peak, "
            "and L10 trend (is this player ascending or declining heading into a long commitment?)."
        ),
    },
}


async def compare_players(
    player_a_id: int,
    player_b_id: int,
    season: int = 0,
    archetype: Optional[str] = None,
    compare_context: Optional[str] = None,
    session_id: Optional[str] = None,
) -> dict[str, Any]:
    """Compare two players using the shared enrich_player utility."""
    season = season or get_current_season()
    logger.info("compare_players | a=%d b=%d season=%d context=%s", player_a_id, player_b_id, season, compare_context)

    cache_key = f"compare2:{min(player_a_id, player_b_id)}:{max(player_a_id, player_b_id)}:{season}:{archetype or 'default'}:{compare_context or 'none'}"
    cached = analysis_cache.get(cache_key)
    if cached:
        logger.info("compare_players cache hit | %s", cache_key)
        return cached

    try:
        ep_a, ep_b = await asyncio.gather(
            enrich_player(player_a_id, season),
            enrich_player(player_b_id, season),
        )
    except Exception as exc:
        logger.warning("compare_players enrich failed | %s", exc)
        return {"error": str(exc)}

    if not ep_a.get("name") or not ep_b.get("name"):
        return {"error": "Could not resolve one or both players."}

    for _ep in (ep_a, ep_b):
        if _ep.get("stats_unavailable"):
            return {"error": _ep["unavailable_message"]}

    name_a, name_b = ep_a["name"], ep_b["name"]

    # Build frontend payload (maintains API contract with existing dashboard keys)
    def _payload(ep: dict) -> dict:
        return {
            "id":          ep["id"],
            "name":        ep["name"],
            "first_name":  ep["first_name"],
            "last_name":   ep["last_name"],
            "position":    ep["position"],
            "team":        ep["team"],
            "team_name":   ep["team_name"],
            "nba_id":      ep.get("nba_id"),
            "games_played": ep.get("gp"),
            "avg_pts":     ep.get("pts"),  "avg_reb": ep.get("reb"), "avg_ast": ep.get("ast"),
            "avg_stl":     ep.get("stl"),  "avg_blk": ep.get("blk"),
            "avg_fg":      (ep.get("fg_pct") or 0) / 100,
            "avg_fg3":     (ep.get("fg3_pct") or 0) / 100,
            "avg_ft":      (ep.get("ft_pct") or 0) / 100,
            "avg_fga":     ep.get("fga"),  "avg_fg3a": ep.get("fg3a"), "avg_fta": ep.get("fta"),
            "ts_pct":      ep.get("ts_pct") / 100 if ep.get("ts_pct") is not None else None,
            "efg_pct":     ep.get("efg_pct") / 100 if ep.get("efg_pct") is not None else None,
            "recent_pts":  ep.get("l10_pts"), "recent_reb": ep.get("l10_reb"), "recent_ast": ep.get("l10_ast"),
            # new fields surfaced to frontend
            "ast_to":      ep.get("ast_to"),
            "usage_proxy": ep.get("usage_proxy"),
            "pts_delta":   ep.get("pts_delta"),
            "trend_flag":  ep.get("trend_flag"),
        }

    payload_a = _payload(ep_a)
    payload_b = _payload(ep_b)

    # ── Prompt: use enriched stat_block from each player ──────────────────
    lens = _ARCHETYPE_LENSES.get(archetype) if archetype else None
    ctx_spec = _COMPARE_CONTEXTS.get(compare_context) if compare_context else None

    COMPARE_SYSTEM = """You are a basketball analytics assistant for PIVOT, used by coaches who need defensible, data-driven insights.

STRICT RULES:
- Use ONLY the data provided in the input. Never invent stats or assumptions.
- If data is missing or insufficient, say "insufficient data" — do not estimate.
- Prioritize accuracy over fluency. Be concise and decisive.
- Do not access the internet or rely on memory between calls.
- You are interpreting structured data, not deciding facts.
- INJURY STATUS: if a player's stat block includes an INJURY STATUS line, factor it explicitly into the comparison. An injured player's current contribution is compromised; their recent stats may understate healthy production or mask a real decline. The verdict must account for availability risk.
- TRENDING DOWN / TRENDING UP: if a player's stat block starts with a ⚠ TRENDING DOWN or ↑ TRENDING UP line, that means their recent form deviates ≥30% from their season average. The stat block will already show L10 as the primary section. You MUST weight the L10 stats over the season totals in your analysis — the season figures are context only. Name the trend explicitly in your reasoning and adjust the verdict accordingly (a trending-down player's season average overstates their current value; a trending-up player may be breaking out).
- CONTEXT FRAMING: when a comparison context is provided, your verdict must be specific to that situation — not a generic "who is better" answer. The better_for_context field must name the player AND explain why they win for THAT specific context, citing the relevant stats.

INPUT: You will receive player stats, derived metrics (pre-computed by the backend), a comparison context, and key considerations for that context.

TASK: Compare the players through the lens of the provided context using ONLY the provided data.

Return JSON only, in this exact format:
{
  "key_differences": [],
  "better_for_context": "Player Name is the better [context] because [specific stat-backed reason]",
  "reasoning": "",
  "limitation": ""
}

The better_for_context field must be a complete sentence naming the player and the context-specific reason. Write reasoning in 2-3 focused paragraphs (under 250 words total), with each paragraph covering a distinct analytical point. Base every claim strictly on the provided metrics."""

    if lens:
        COMPARE_SYSTEM += f"\n\nCOACHING LENS — {lens['name']}:\n{lens['system']}"

    def _lens_block(ep: dict) -> str:
        spotlight = _archetype_spotlight(ep, archetype) if archetype else ""
        return spotlight + ep["stat_block"] if spotlight else ep["stat_block"]

    context_block = ""
    if ctx_spec:
        context_block = (
            f"\nCOMPARISON CONTEXT: {ctx_spec['label']}\n"
            f"QUESTION: {ctx_spec['question']}\n"
            f"KEY CONSIDERATIONS: {ctx_spec['considerations']}\n"
        )

    session_ctx = build_context_block(session_id)

    prompt = (
        f"{session_ctx}"
        f"HEAD-TO-HEAD: {name_a} vs {name_b} — {season} Season\n"
        f"{context_block}\n"
        f"PLAYER A — {_lens_block(ep_a)}\n\n"
        f"PLAYER B — {_lens_block(ep_b)}\n\n"
        f"Compare these two players"
        + (f" specifically for: {ctx_spec['question']}" if ctx_spec else " using only the stats above")
        + f". Return JSON only with key_differences (array of short strings), "
          f"better_for_context (full sentence naming the player and the context-specific reason), "
          f"reasoning (2-3 focused paragraphs under 250 words — each paragraph covers a distinct point), "
          f"and limitation (what data is missing or inconclusive)."
    )

    if lens:
        prompt += f"\n\n{lens['prompt']}"

    result = await claude_service.analyze(
        prompt=prompt,
        system_prompt=COMPARE_SYSTEM,
        override_max_tokens=_TOKENS_VERDICT,
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
            "better_for_context": "",
            "reasoning": raw_text[:500],
            "limitation": "Response could not be parsed as structured JSON.",
        }

    # Normalise: some cached/old responses may still have better_player — promote it
    if "better_player" in structured and "better_for_context" not in structured:
        structured["better_for_context"] = structured.pop("better_player")

    response = {
        "player_a": payload_a,
        "player_b": payload_b,
        "analysis": result.analysis,
        "structured": structured,
        "model": result.model,
        "tokens_used": result.tokens_used,
        "season": season,
        "compare_context": compare_context,
        "context_label": ctx_spec["label"] if ctx_spec else None,
    }
    analysis_cache.set(cache_key, response, ttl=get_cache_ttl())
    logger.info("compare_players complete | %s vs %s context=%s tokens=%d", name_a, name_b, compare_context, result.tokens_used)

    # Record session event
    verdict = structured.get("better_for_context", "")
    limitation = structured.get("limitation", "")
    summary = f"Compared {name_a} vs {name_b}"
    if compare_context:
        summary += f" ({compare_context})"
    if verdict:
        summary += f" → {verdict[:80]}"
    concern = limitation[:80] if limitation and "insufficient" not in limitation.lower() else None
    session_record(session_id, SessionEvent(
        type="compare",
        summary=summary[:120],
        entities=[name_a, name_b],
        concern=concern,
    ))

    return response

# ---------------------------------------------------------------------------
# Team DNA Analysis
# ---------------------------------------------------------------------------

TEAM_DNA_SYSTEM_PROMPT: str = """You are a professional NBA scout and tactician. Your specialty is breaking down how teams actually play — their offensive and defensive systems, shot diet, pace, spacing, and scheme identity.

CRITICAL CONTEXT: Today is April 2026. The 2025-26 NBA season is in progress. Use your full knowledge of how these franchises have played this season and historically.

When analyzing a team's DNA, be specific. Name the plays they run, the coverages they prefer, the personnel who drive the scheme. Use shot-diet language (3PT rate, paint frequency, mid-range reliance), pace terminology (possessions per 48), and defensive scheme names (drop coverage, switching, hedging, zone). Name the players who execute each piece.

Do not be generic. "They play fast and spread the floor" is not analysis. "They rank top-5 in pace, initiate 40% of possessions from the pick-and-roll with their point guard as the ball handler, and shoot above 40% of their field goal attempts from three" is analysis.

FORMATTING: Plain prose only. No markdown, no bullets, no headers, no asterisks. Dense, expert prose in paragraphs. Write like a scout memo — every sentence earns its place."""


async def analyze_team_dna(team_name: str):
    """
    Generate a deep team identity breakdown: offense, defense, pace, shot diet,
    scheme tendencies, and vulnerability profile.
    """
    logger.info("analyze_team_dna | team=%s", team_name)

    cache_key = f"team_dna:{team_name.lower().strip()}"
    cached = analysis_cache.get(cache_key)
    if cached:
        logger.info("team_dna cache hit | team=%s", team_name)
        yield {"type": "chunk", "text": cached.get("analysis", "")}
        yield {"type": "done"}
        return

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

    full_text = ""
    async for chunk in claude_service.analyze_stream(
        prompt=prompt,
        system_prompt=TEAM_DNA_SYSTEM_PROMPT,
        override_max_tokens=_TOKENS_DNA,
        override_temperature=0.1,
    ):
        full_text += chunk
        yield {"type": "chunk", "text": chunk}

    response = {
        "team": team_name,
        "standing": standings_ctx,
        "analysis": full_text,
    }
    analysis_cache.set(cache_key, response, ttl=21600)  # 6-hour cache — team identity is stable
    logger.info("team_dna complete | team=%s", team_name)
    yield {"type": "done"}


async def scout_note(
    name: str,
    team: str,
    pts: float,
    reb: float,
    ast: float,
    context: str = "general",
    age: Optional[int] = None,
    pos: Optional[str] = None,
    player_id: Optional[int] = None,
    session_id: Optional[str] = None,
) -> dict[str, Any]:
    """
    Generate a live 1-2 sentence scout note for a single player.
    context: 'mvp' | 'young-star' | 'general'

    If player_id is provided, enriches with full stat block (TS%, AST/TO, L10 trend).
    Falls back to pts/reb/ast only when no ID is available.
    """
    cache_key = f"scout_note:{name.lower()}:{context}:{player_id or pts}:{reb}:{ast}"
    cached = analysis_cache.get(cache_key)
    if cached:
        return cached

    if context == "mvp":
        instruction = (
            "Write exactly 1-2 sentences on why this player is or isn't an MVP contender right now. "
            "Lead with the sharpest insight. Cite specific numbers from the stats provided. No hedging."
        )
        _max_tokens = _TOKENS_BLURB
    elif context == "young-star":
        instruction = (
            "Write exactly 1-2 sentences on what makes this young player's development trajectory "
            "noteworthy. Focus on what's emerging or what the ceiling looks like. Cite numbers."
        )
        _max_tokens = _TOKENS_BLURB
    else:
        if player_id:
            # Full stat block is available — ask for real depth
            instruction = (
                "Write 2-3 sentences evaluating this player's current standing. "
                "Lead with their efficiency anchor (TS% or eFG%), then address the recent-form trend "
                "(L10 vs season), then close with a sharp one-sentence verdict on where they stand right now. "
                "Cite specific numbers. No hedging."
            )
            _max_tokens = _TOKENS_NOTE
        else:
            instruction = (
                "Write 1-2 sentences evaluating this player's current impact. Cite the specific stats provided. Be precise."
            )
            _max_tokens = _TOKENS_BLURB

    # Use full enriched stat block if we have a player ID
    stat_section = f"STATS: {pts} PPG · {reb} RPG · {ast} APG"
    if player_id:
        try:
            ep = await enrich_player(player_id)
            if ep.get("stats_unavailable"):
                return {"error": ep["unavailable_message"]}
            stat_section = f"FULL STATS:\n{ep['stat_block']}"
        except Exception:
            pass  # fall back to basic stats

    meta_parts = [f"{name} ({team})"]
    if pos:
        meta_parts.append(pos)
    if age is not None:
        meta_parts.append(f"Age {age}")
    meta = " · ".join(meta_parts)

    # Inject session context for depth notes (blurb contexts are too short to benefit)
    session_ctx = build_context_block(session_id) if _max_tokens >= _TOKENS_NOTE else ""
    prompt = (
        f"{session_ctx}"
        f"PLAYER: {meta}\n"
        f"{stat_section}\n\n"
        f"{instruction}"
    )

    result = await claude_service.analyze(
        prompt=prompt,
        system_prompt=_nba_analyst_system_prompt(),
        override_max_tokens=_max_tokens,
        override_temperature=0.3,
        override_model=_FAST_MODEL,
    )

    note_text = result.analysis.strip()
    session_record(session_id, SessionEvent(
        type="scout",
        summary=f"Scout {name} ({context}): {note_text[:80]}",
        entities=[name, team],
    ))

    response = {"note": note_text, "model": result.model}
    analysis_cache.set(cache_key, response, ttl=get_cache_ttl())
    return response


# ---------------------------------------------------------------------------
# Trade Analyzer
# ---------------------------------------------------------------------------

_TRADE_SYSTEM = """You are a senior NBA front office analyst for PIVOT. You evaluate trades with front-office precision.

STRICT RULES:
- Use ONLY the player data, contract data, and cap context provided. Never invent figures.
- Be concrete and decisive — "insufficient data" if a key figure is missing.
- No fluff. Every sentence must carry a specific insight.
- Return valid JSON only, in the exact schema requested.

EVALUATION FRAMEWORK — assess ALL five dimensions:
1. TALENT DELTA: raw skill gap between the assets on each side (use provided stats). If a player's stat block includes an INJURY STATUS line, treat it as a material factor — an Out or Questionable player is not delivering full value; factor availability risk into the talent assessment and explicitly name the injury in your verdict. If a player's stat block starts with ⚠ TRENDING DOWN or ↑ TRENDING UP, their L10 stats (labeled PRIMARY) reflect current actual value — weight these over the season totals when assessing talent. A trending-down player is worth less than their season line implies; explicitly call this out in your talent assessment.
2. ROLE FIT: does this player fill a positional or schematic need?
3. AGE/TRAJECTORY: are you acquiring a peak player or buying/selling at the right time?
4. CAP IMPACT: use the provided salary, years, and contract type for each player.
   - Compute the net salary delta for each team (salary_in − salary_out).
   - Note if a team crosses or retreats from the luxury tax, first apron, or second apron after the trade.
   - Flag max/supermax deals as high-commitment; rookie-scale as high-value; expiring as flexibility assets.
   - "Taking on more salary" vs "shedding salary" must be stated explicitly with the dollar figures.
5. WIN-NOW vs REBUILD: does the move match each team's timeline given their current payroll tier?"""


async def analyze_trade(
    team_a_name: str,
    team_a_players: list[dict],  # [{name, id, pos, age, salary, years, contract_type}]
    team_b_name: str,
    team_b_players: list[dict],
    archetype: Optional[str] = None,
    team_a_cap: Optional[float] = None,   # current total payroll $M
    team_b_cap: Optional[float] = None,
    cap_context: Optional[dict] = None,   # {tax_line, first_apron, second_apron}
    session_id: Optional[str] = None,
) -> dict[str, Any]:
    """
    Evaluate a trade between two teams through an optional coaching archetype lens.
    Uses enrich_player() for full stat depth (TS%, AST/TO, usage, L10 delta).
    """
    import hashlib as _hashlib

    def _player_key(p: dict) -> str:
        return f"{p.get('name','?')}:{p.get('id','')}"

    raw_key = f"trade:{team_a_name}:{','.join(_player_key(p) for p in team_a_players)}:{team_b_name}:{','.join(_player_key(p) for p in team_b_players)}:{archetype or 'default'}"
    cache_key = "trade:" + _hashlib.md5(raw_key.encode()).hexdigest()
    cached = analysis_cache.get(cache_key)
    if cached:
        logger.info("analyze_trade cache hit | %s", cache_key)
        return cached

    # Enrich every player that has a BDL id via the shared utility (parallel)
    all_players = [(p, "a") for p in team_a_players] + [(p, "b") for p in team_b_players]

    async def _safe_enrich(p: dict) -> dict:
        pid = p.get("id")
        if pid:
            try:
                ep = await enrich_player(int(pid), get_current_season())
                if ep.get("stats_unavailable"):
                    # Surface the message in the stat_block so Claude and the
                    # caller both see it; don't silently pass empty data.
                    return {
                        "id": pid, "name": ep.get("name", p.get("name", "?")),
                        "position": p.get("pos") or p.get("position") or "?",
                        "age": p.get("age"),
                        "stats_unavailable": True,
                        "unavailable_message": ep["unavailable_message"],
                        "stat_block": ep["stat_block"],
                    }
                # Preserve age from the frontend payload if not in enriched data
                if p.get("age") and not ep.get("age"):
                    ep = {**ep, "age": p["age"]}
                return ep
            except Exception:
                pass
        # Fallback: return lightweight dict with just what the frontend sent
        return {
            "id": pid, "name": p.get("name", "?"),
            "position": p.get("pos") or p.get("position") or "?",
            "age": p.get("age"),
            "stat_block": f"{p.get('name','?')} — season stats unavailable.",
        }

    enriched_all = await asyncio.gather(*[_safe_enrich(p) for p, _ in all_players])
    a_n = len(team_a_players)
    a_enriched = list(enriched_all[:a_n])
    b_enriched = list(enriched_all[a_n:])

    # Guard: if any player has no season data, return the message before calling Claude
    for _ep in a_enriched + b_enriched:
        if _ep.get("stats_unavailable"):
            return {"error": _ep["unavailable_message"]}

    cc = cap_context or {}
    tax_line     = cc.get("tax_line")
    first_apron  = cc.get("first_apron")
    second_apron = cc.get("second_apron")

    def _contract_line(p_input: dict) -> str:
        """Build a one-line contract summary from the raw player dict sent by the frontend."""
        salary = p_input.get("salary")
        years  = p_input.get("years")
        ctype  = p_input.get("contract_type")
        name   = p_input.get("name", "?")
        parts  = [f"  CONTRACT — {name}:"]
        if salary is not None:
            parts.append(f"${salary}M AAV")
        if years is not None:
            parts.append(f"{years} yr{'s' if years != 1 else ''} remaining")
        if ctype:
            parts.append(f"({ctype})")
        if salary is None and years is None:
            parts.append("contract data unavailable")
        return " ".join(parts)

    # Map raw input player dicts by name so we can attach contract lines to enriched blocks
    input_by_name: dict[str, dict] = {}
    for p in team_a_players + team_b_players:
        input_by_name[p.get("name", "")] = p

    def _trade_block(players: list[dict], raw_inputs: list[dict]) -> str:
        """Stat block (with archetype spotlight prepended) + contract line for each player."""
        if not players:
            return "  (no players)"
        lines = []
        for ep, raw in zip(players, raw_inputs):
            base_stat = ep.get("stat_block") or f"  {ep.get('name','?')} — stats unavailable"
            spotlight = _archetype_spotlight(ep, archetype) if archetype else ""
            stat = (spotlight + base_stat) if spotlight else base_stat
            contract = _contract_line(raw)
            lines.append(f"{stat}\n{contract}")
        return "\n\n".join(lines)

    def _cap_situation(team_name: str, cap_used: Optional[float], salary_out: float, salary_in: float) -> str:
        if cap_used is None:
            return f"{team_name}: current payroll unknown — cap impact cannot be computed."
        net = round(salary_in - salary_out, 1)
        new_total = round(cap_used + net, 1)
        direction = f"+${net}M (takes on more)" if net > 0 else f"-${abs(net)}M (sheds salary)" if net < 0 else "salary-neutral"
        lines = [f"{team_name}: current payroll ${cap_used}M → ${new_total}M after trade ({direction})"]
        thresholds = []
        if tax_line:
            if cap_used < tax_line <= new_total:
                thresholds.append(f"CROSSES luxury tax line (${tax_line}M)")
            elif cap_used >= tax_line > new_total:
                thresholds.append(f"DROPS BELOW luxury tax line (${tax_line}M)")
        if first_apron:
            if cap_used < first_apron <= new_total:
                thresholds.append(f"CROSSES first apron (${first_apron}M) — hard-cap implications")
            elif cap_used >= first_apron > new_total:
                thresholds.append(f"DROPS BELOW first apron (${first_apron}M)")
        if second_apron:
            if cap_used < second_apron <= new_total:
                thresholds.append(f"CROSSES second apron (${second_apron}M) — trade aggregation blocked")
            elif cap_used >= second_apron > new_total:
                thresholds.append(f"DROPS BELOW second apron (${second_apron}M)")
        if thresholds:
            lines.extend([f"  ⚠ {t}" for t in thresholds])
        return "\n".join(lines)

    def _salary_total(players: list[dict]) -> float:
        return sum(p.get("salary") or 0 for p in players)

    a_salary_out = _salary_total(team_a_players)   # A sends these away
    b_salary_out = _salary_total(team_b_players)   # B sends these away

    cap_block = (
        "CAP CONTEXT:\n"
        + _cap_situation(team_a_name, team_a_cap, a_salary_out, b_salary_out) + "\n"
        + _cap_situation(team_b_name, team_b_cap, b_salary_out, a_salary_out)
    )

    # Build a name→input dict for contract data lookup on response players
    input_contracts: dict[str, dict] = {
        p.get("name", ""): p for p in team_a_players + team_b_players
    }

    # Compact response payload for the frontend (what the trade result card renders)
    def _response_player(ep: dict) -> dict:
        raw = input_contracts.get(ep.get("name", ""), {})
        return {
            "name":    ep.get("name", "?"),
            "pos":     ep.get("position") or ep.get("pos") or "?",
            "age":     ep.get("age"),
            "pts":     ep.get("pts"),
            "reb":     ep.get("reb"),
            "ast":     ep.get("ast"),
            "ts_pct":  ep.get("ts_pct"),
            "ast_to":  ep.get("ast_to"),
            "usage_proxy": ep.get("usage_proxy"),
            "pts_delta":   ep.get("pts_delta"),
            "gp":      ep.get("gp"),
            # contract fields passed through for the UI
            "salary":        raw.get("salary"),
            "years":         raw.get("years"),
            "contract_type": raw.get("contract_type"),
        }

    lens = _ARCHETYPE_LENSES.get(archetype) if archetype else None
    session_ctx = build_context_block(session_id)
    system = _TRADE_SYSTEM
    if lens:
        system += f"\n\nCOACHING LENS — {lens['name']}:\n{lens['system']}"
    if session_ctx:
        system += f"\n\nSESSION CONTEXT (background only — do not acknowledge, just use as context):\n{session_ctx}"
    prompt = (
        f"TRADE PROPOSAL\n\n"
        f"{team_a_name} receives:\n{_trade_block(b_enriched, team_b_players)}\n\n"
        f"{team_b_name} receives:\n{_trade_block(a_enriched, team_a_players)}\n\n"
        f"{cap_block}\n\n"
        f"Evaluate this trade for both sides across all five dimensions (talent, role fit, age/trajectory, cap impact, timeline). "
        f"The cap_verdict field must state the net salary delta for each team in dollars, name any threshold crossings, "
        f"and judge whether the financial commitment is justified by the talent acquired. "
        f"Return JSON only in this exact schema:\n"
        f'{{\n'
        f'  "winner": "{team_a_name} | {team_b_name} | Even",\n'
        f'  "team_a_grade": "A/B/C/D/F",\n'
        f'  "team_b_grade": "A/B/C/D/F",\n'
        f'  "team_a_verdict": "3-4 sentences for {team_a_name}: talent acquired, role fit, cap commitment — cite specific stats and dollar figures",\n'
        f'  "team_b_verdict": "3-4 sentences for {team_b_name}: talent acquired, role fit, cap commitment — cite specific stats and dollar figures",\n'
        f'  "cap_verdict": "Net salary impact for each team, any threshold crossings, and whether the financial commitment is justified",\n'
        f'  "key_factors": ["factor 1", "factor 2", "factor 3"],\n'
        f'  "risk": "Main risk or caveat for the winning side",\n'
        f'  "limitation": "What data is missing or inconclusive"\n'
        f'}}'
    )

    if lens:
        prompt += f"\n\n{lens['prompt']}"

    result = await claude_service.analyze(
        prompt=prompt,
        system_prompt=system,
        override_model=_FAST_MODEL,
        override_max_tokens=_TOKENS_VERDICT,
        override_temperature=0.15,
    )

    import json as _json
    raw = result.analysis.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    # Defensive: find JSON object even if Claude added preamble prose
    if not raw.startswith("{"):
        brace_idx = raw.find("{")
        if brace_idx != -1:
            raw = raw[brace_idx:]
    try:
        structured = _json.loads(raw)
    except Exception:
        structured = {
            "winner": "?",
            "team_a_grade": "?", "team_b_grade": "?",
            "team_a_verdict": raw[:300], "team_b_verdict": "",
            "cap_verdict": "",
            "key_factors": [], "risk": "", "limitation": "Response could not be parsed.",
        }

    winner      = structured.get("winner", "")
    cap_verdict = structured.get("cap_verdict", "")
    _trade_concern = (
        cap_verdict[:80]
        if cap_verdict and any(
            kw in cap_verdict.lower()
            for kw in ["luxury tax", "apron", "concern", "risk", "crosses"]
        )
        else None
    )
    _trade_names = [p.get("name", "") for p in team_a_players + team_b_players]
    session_record(session_id, SessionEvent(
        type="trade",
        summary=f"Trade {team_a_name} ↔ {team_b_name}: {winner}"[:120],
        entities=_trade_names + [team_a_name, team_b_name],
        concern=_trade_concern,
    ))

    response = {
        "team_a": team_a_name,
        "team_b": team_b_name,
        "team_a_players": [_response_player(ep) for ep in b_enriched],  # what A receives
        "team_b_players": [_response_player(ep) for ep in a_enriched],  # what B receives
        "structured": structured,
        "archetype": archetype,
        "model": result.model,
        "tokens_used": result.tokens_used,
    }
    analysis_cache.set(cache_key, response, ttl=get_cache_ttl(default_ttl=1800))
    logger.info("analyze_trade complete | %s vs %s tokens=%d", team_a_name, team_b_name, result.tokens_used)
    return response
