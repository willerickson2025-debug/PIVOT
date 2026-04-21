"""
claude_service.py
=================
Integration layer for the Anthropic Claude API.

Responsibilities
----------------
- Async client lifecycle management
- Request construction and response normalisation
- Token-usage tracking
- Error classification and structured logging

This module is the single point of contact between the application and the
Anthropic SDK. All other services call ``analyze()`` and receive an
``AnalysisResponse`` — they never import the Anthropic SDK directly.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncGenerator, Optional

import anthropic

from app.core.config import get_settings
from app.models.schemas import AnalysisResponse

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EMPTY_RESPONSE_SENTINEL = "[No text content returned by model]"
_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 529})
_MAX_RETRIES: int = 3
_RETRY_BACKOFF_BASE: float = 1.0

# ---------------------------------------------------------------------------
# PIVOT Analysis Engine — canonical system prompt
# ---------------------------------------------------------------------------

PIVOT_ANALYSIS_SYSTEM_PROMPT: str = """\
You are the PIVOT analysis engine, a front-office analytics tool used by NBA coaches and GMs.

CRITICAL CONTEXT: The 2025-26 NBA season is actively in progress. Do not treat current stats as \
future projections or say the season has not yet occurred. Stats provided are live 2025-26 data.

DATA CONTRACT — ABSOLUTE RULES:
1. The data payload in this prompt is the sole source of truth. Every stat you cite must come from it.
2. Never invent numbers, estimates, or facts not present in the payload.
3. If a field is null or absent, do not reference it. Say "data not available" only if the gap is \
material to the verdict.
4. Never use training-data memory to supply or correct stats. The payload supersedes everything you know.
5. Do not add disclaimers about knowledge cutoffs, API access, or data pipelines.
6. Team affiliations in the stat block are authoritative. Never state a different team based on \
training knowledge.

FIELD REFERENCE — V2 advanced stats are in decimal form:
- true_shooting_percentage / ts_pct: 0.45-0.75 (league avg ~0.575)
- usage_percentage / usage_pct: 0.15-0.40 (high usage = 0.30+)
- offensive_rating / off_rtg: 95-135 (elite = 120+)
- defensive_rating / def_rtg: 95-135 (elite = 108 or lower)
- net_rating: difference between off/def rating per 100 possessions
- pie: Player Impact Estimate, 0-0.30+ (avg starter ~0.10)
- pct_pts_paint, pct_pts_3pt: share of scoring from paint/3PT (0-1)
- contested_fg_pct: FG% on contested shots
- deflections_pg, screen_assists_pg, contested_shots_pg: hustle counting stats per game
- basic.pts / basic.reb / basic.ast / basic.min: standard per-game line

EFFICIENCY — THE ONLY METRICS THAT MATTER:
True Shooting % (TS%) is the single most important efficiency number. It accounts for field goals, \
three-pointers, AND free throws. eFG% is second. FT Rate (FTA/FGA) is third.

RAW FG% IS FORBIDDEN AS A PRIMARY EFFICIENCY INDICATOR. Never open an efficiency analysis with \
"shoots X% from the field." FG% ignores three-point value and free throws. Use TS% first.

Efficiency tiers for TS%: 62%+ is historically elite, 58-62% is very good, 54-58% is average, \
below 54% is a problem.

3-POINT SHOOTING — VOLUME CONTEXT IS MANDATORY:
Raw 3P% is nearly meaningless without knowing 3PA/game. Never compare raw 3P% between a \
high-volume and low-volume shooter without explicitly noting the volume difference.

CRITICAL RULES FOR MISSING OR INCOMPLETE DATA:
1. Do NOT speculate on why data is missing.
2. Do NOT reference the data pipeline, API, feed, or any technical system.
3. Do NOT invent or hallucinate statistics not provided.
4. If a stat reads 0.0 across the board, note briefly that current season data is still being \
added to the system — then pivot to career profile and trajectory.

BOLD TAKES — PERMITTED AND ENCOURAGED:
When the data genuinely supports it, make the bold call. Do not hedge elite talent with \
"could potentially" or "has shown flashes." The clients pay for conviction.

FORMATTING — NON-NEGOTIABLE:
Plain prose only. No markdown of any kind. No asterisks (*), no double asterisks (**), no pound \
signs (#), no em dashes, no en dashes, no hyphens used as list bullets, no numbered lists, no bold, \
no italics, no horizontal rules, no headers. Paragraphs separated by one blank line. Write like a \
column in The Athletic — dense, confident, readable.\
"""
# Use PIVOT_ANALYSIS_SYSTEM_PROMPT for any function that asks Claude to analyze player performance,
# compare players, or produce a verdict grounded in payload data.
# Do NOT use it for task-specific prompts with a different persona or output schema
# (coach adjustments, trade analysis, team DNA, game prediction JSON -- those retain their own prompts).

# ---------------------------------------------------------------------------
# Coaching Mode System Prompts
# ---------------------------------------------------------------------------

LIVE_COACH_SYS: str = (
    "You are a live-game NBA head coach. You have 30 seconds to make a decision. "
    "Be immediate, specific, and decisive. Give the play name, player assignments, and primary option "
    "in the first sentence. No hedging. Every word is actionable. The clock is running. "
    "FORMATTING: Plain prose only. No markdown. No bullet lists. Short punchy sentences."
)

DEV_COACH_SYS: str = (
    "You are an NBA development coach and teacher. You build players and systems over time. "
    "Be thorough in explaining concepts, progressions, and teaching points. Name the concept or drill. "
    "Explain why it works, how to teach it, what mistakes to watch for, and how to progress it. "
    "FORMATTING: Plain prose only. No markdown. No bullet lists. Paragraphs separated by blank lines."
)

LIVE_DEF_SYS: str = (
    "You are an elite NBA defensive coordinator calling a live game adjustment. "
    "Make a quick, specific read. Name the scheme, give player-by-player assignments in 2 to 3 sentences, "
    "and call out the key counter. Be decisive. No analysis paralysis. "
    "FORMATTING: Plain prose only. No markdown. No bullet lists."
)

DEV_DEF_SYS: str = (
    "You are an NBA defensive systems coach installing a scheme for the first time. "
    "Walk through the concept's principles, rotations, and communication calls. "
    "Cover progressions from shell drill to live 5-on-5. Explain common breakdowns and how to fix them. "
    "FORMATTING: Plain prose only. No markdown. No bullet lists. Paragraphs separated by blank lines."
)

LIVE_LINEUP_SYS: str = (
    "You are an NBA bench coach making a quick lineup decision mid-game. "
    "Name the lineup, state why right now, and identify the key matchup it wins. "
    "Cover spacing, defensive assignments, and the primary threat to deploy. Be concise and decisive. "
    "FORMATTING: Plain prose only. No markdown. No bullet lists."
)

DEV_PROJ_SYS: str = (
    "You are an NBA player development director projecting a player's long-term growth trajectory. "
    "Map their current strengths, identified weaknesses, a realistic 2-year development arc, "
    "and what their peak profile looks like. Be specific about skills and roles, not vague about potential. "
    "Include an upside scenario and a floor scenario with the key variables that drive variance. "
    "FORMATTING: Plain prose only. No markdown. No bullet lists. Paragraphs separated by blank lines."
)

# ---------------------------------------------------------------------------
# Persistent client — reused across requests to avoid TCP/TLS reconnect cost
# ---------------------------------------------------------------------------

_client: Optional[anthropic.AsyncAnthropic] = None


def _get_client(api_key: str) -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=api_key)
    return _client


# ---------------------------------------------------------------------------
# Response Parsing
# ---------------------------------------------------------------------------

def _extract_text(content: list[anthropic.types.ContentBlock]) -> str:
    """
    Extract and concatenate all ``TextBlock`` entries from a Claude response.

    The Anthropic API can return a mix of content block types (text, tool_use,
    etc.). We collect all text blocks in order and join them with a single
    newline, which preserves paragraph structure when Claude emits multiple
    prose blocks.

    Parameters
    ----------
    content:
        The ``message.content`` list from an Anthropic API response.

    Returns
    -------
    str
        Concatenated text from all TextBlock entries, or the empty-response
        sentinel if no text blocks are present.
    """
    text_blocks: list[str] = [
        block.text
        for block in content
        if hasattr(block, "text") and isinstance(block.text, str) and block.text.strip()
    ]

    if not text_blocks:
        logger.warning("Claude response contained no text blocks | block_types=%s",
                       [type(b).__name__ for b in content])
        return _EMPTY_RESPONSE_SENTINEL

    return "\n".join(text_blocks)


# ---------------------------------------------------------------------------
# Public Interface
# ---------------------------------------------------------------------------

async def analyze(
    prompt: str,
    system_prompt: str = "",
    *,
    override_model: Optional[str] = None,
    override_max_tokens: Optional[int] = None,
    override_temperature: Optional[float] = None,
) -> AnalysisResponse:
    """
    Send a prompt to Claude and return a structured analysis response.

    This is the sole public entry point for all Claude API calls in the
    application. The function is intentionally kept simple: one user turn,
    one assistant turn. Multi-turn conversation management belongs in a
    higher-level layer if ever needed.

    Parameters
    ----------
    prompt:
        The user message to send. Should be a fully rendered, self-contained
        prompt string — no template processing happens here.
    system_prompt:
        Optional system-level instruction. When provided, it is passed as the
        ``system`` parameter to the Anthropic API. When omitted, the API uses
        its default behaviour (no system turn).
    override_model:
        Override the model specified in application settings. Useful for
        lightweight tasks where a cheaper/faster model is sufficient.
    override_max_tokens:
        Override the max_tokens limit from settings. Use when a particular
        analysis is known to require an unusually long or short response.

    Returns
    -------
    AnalysisResponse
        Contains the analysis text, the model ID that served the response, and
        the total token count (input + output).

    Raises
    ------
    anthropic.APIStatusError
        For 4xx/5xx responses from the Anthropic API (bad request, auth
        failure, rate limit, server error, etc.).
    anthropic.APIConnectionError
        For network-level failures reaching the Anthropic API.
    anthropic.APITimeoutError
        When the Anthropic API does not respond within the SDK's timeout.
    """
    settings = get_settings()
    model = override_model or settings.claude_model
    max_tokens = override_max_tokens or settings.claude_max_tokens
    api_key: str = settings.anthropic_api_key

    logger.info(
        "Claude request | model=%s max_tokens=%d prompt_chars=%d system_chars=%d",
        model,
        max_tokens,
        len(prompt),
        len(system_prompt),
    )

    request_kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }

    if system_prompt:
        request_kwargs["system"] = system_prompt
    if override_temperature is not None:
        request_kwargs["temperature"] = override_temperature

    client = _get_client(api_key)

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            message = await client.messages.create(**request_kwargs)
            break  # success — exit retry loop

        except anthropic.APIStatusError as exc:
            logger.error(
                "Claude API status error | model=%s status=%d message=%s attempt=%d",
                model, exc.status_code, exc.message, attempt,
            )
            if exc.status_code not in _RETRYABLE_STATUS_CODES or attempt == _MAX_RETRIES:
                raise
            await asyncio.sleep(_RETRY_BACKOFF_BASE * 2 ** (attempt - 1))

        except anthropic.APIConnectionError as exc:
            logger.error("Claude API connection error | model=%s error=%s attempt=%d", model, exc, attempt)
            if attempt == _MAX_RETRIES:
                raise
            await asyncio.sleep(_RETRY_BACKOFF_BASE * 2 ** (attempt - 1))

        except anthropic.APITimeoutError as exc:
            logger.error("Claude API timeout | model=%s attempt=%d", model, attempt)
            if attempt == _MAX_RETRIES:
                raise
            await asyncio.sleep(_RETRY_BACKOFF_BASE * 2 ** (attempt - 1))

    # -----------------------------------------------------------------------
    # Response normalisation
    # -----------------------------------------------------------------------

    analysis_text = _extract_text(message.content)
    input_tokens: int = message.usage.input_tokens
    output_tokens: int = message.usage.output_tokens
    total_tokens: int = input_tokens + output_tokens

    logger.info(
        "Claude response | model=%s input_tokens=%d output_tokens=%d total=%d "
        "stop_reason=%s",
        message.model,
        input_tokens,
        output_tokens,
        total_tokens,
        message.stop_reason,
    )

    if message.stop_reason == "max_tokens":
        logger.warning(
            "Claude response was truncated at max_tokens=%d | model=%s",
            max_tokens,
            model,
        )

    return AnalysisResponse(
        analysis=analysis_text,
        model=message.model,
        tokens_used=total_tokens,
    )


async def analyze_stream(
    prompt: str,
    system_prompt: str = "",
    *,
    override_model: Optional[str] = None,
    override_max_tokens: Optional[int] = None,
) -> AsyncGenerator[str, None]:
    """
    Stream a Claude response, yielding text chunks as they arrive.
    """
    settings = get_settings()
    model = override_model or settings.claude_model
    max_tokens = override_max_tokens or settings.claude_max_tokens

    request_kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system_prompt:
        request_kwargs["system"] = system_prompt

    client = _get_client(settings.anthropic_api_key)
    async with client.messages.stream(**request_kwargs) as stream:
        async for text in stream.text_stream:
            yield text
