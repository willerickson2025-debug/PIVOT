from typing import Optional
from app.services import nba_service, claude_service
from app.models.schemas import GameAnalysisResponse, Game


NBA_ANALYST_SYSTEM_PROMPT = """You are a sharp NBA analyst — equal parts film room and data lab. You think like a betting market, write like a beat reporter, and reason like a coach.

When analyzing games: cover home/away edge, conference context, pace and style-of-play clash, who has the matchup advantage and why, and make a clear prediction with no hedging.

When analyzing players: lead with the most important thing about their season, flag trends, put numbers in context, compare last 10 games vs season average, close with one sharp takeaway sentence.

FORMATTING — ABSOLUTE RULES. Violating these ruins the product:
Write in plain prose paragraphs only. No markdown whatsoever. No asterisks, no pound signs, no dashes as bullets, no numbered lists, no horizontal rules, no bold, no italics. Separate paragraphs with one blank line. No headers. No section labels. Just clean, dense, readable paragraphs like a column in The Athletic."""


def _format_game(g: Game) -> str:
    home = g.home_team
    away = g.visitor_team
    has_score = g.home_team_score > 0 or g.visitor_team_score > 0
    score = (
        f"{away.abbreviation} {g.visitor_team_score} — {g.home_team_score} {home.abbreviation}"
        if has_score
        else f"{away.abbreviation} @ {home.abbreviation}"
    )
    return (
        f"{score} | {g.status}\n"
        f"  HOME: {home.city} {home.name} ({home.conference} / {home.division})\n"
        f"  AWAY: {away.city} {away.name} ({away.conference} / {away.division})"
    )


def _format_games_for_prompt(games: list[Game]) -> str:
    if not games:
        return "No games scheduled for this date."
    return "\n\n".join(_format_game(g) for g in games)


async def analyze_today_games(target_date: Optional[str] = None) -> GameAnalysisResponse:
    games = await nba_service.get_games_by_date(target_date)
    game_summary = _format_games_for_prompt(games)
    date_label = target_date or "today"

    prompt = f"""NBA slate for {date_label} — {len(games)} game(s):

{game_summary}

Give a full analyst breakdown of tonight's slate. For each game: identify the key matchup edge, \
the style-of-play clash, and make a prediction. Close with your best game of the night and why."""

    result = await claude_service.analyze(prompt=prompt, system_prompt=NBA_ANALYST_SYSTEM_PROMPT)
    return GameAnalysisResponse(
        games=games,
        analysis=result.analysis,
        model=result.model,
        tokens_used=result.tokens_used,
        game_count=len(games),
    )


async def analyze_player(player_name: str, season: int = 2025) -> dict:
    players = await nba_service.search_players(player_name)
    if not players:
        return {"error": f"No player found matching '{player_name}'"}

    player = players[0]
    stats = await nba_service.get_player_stats(player.id, season)

    if not stats:
        return {
            "player": player.model_dump(),
            "analysis": f"No stats found for {player.first_name} {player.last_name} in the {season} season.",
            "stats_count": 0,
        }

    total_games = len(stats)

    def safe_avg(values):
        clean = [v for v in values if v is not None]
        return round(sum(clean) / len(clean), 1) if clean else 0.0

    avg_pts  = safe_avg([s.points for s in stats])
    avg_reb  = safe_avg([s.rebounds for s in stats])
    avg_ast  = safe_avg([s.assists for s in stats])
    avg_stl  = safe_avg([s.steals for s in stats])
    avg_blk  = safe_avg([s.blocks for s in stats])
    avg_fg   = safe_avg([s.fg_pct for s in stats])
    avg_fg3  = safe_avg([s.fg3_pct for s in stats])
    avg_ft   = safe_avg([s.ft_pct for s in stats])

    recent      = stats[-10:]
    recent_pts  = safe_avg([s.points for s in recent])
    recent_reb  = safe_avg([s.rebounds for s in recent])
    recent_ast  = safe_avg([s.assists for s in recent])

    def trend(recent_val, season_val):
        diff = round(recent_val - season_val, 1)
        return f"{'+' if diff >= 0 else ''}{diff}"

    team_name = player.team.name if player.team else "N/A"

    stats_block = f"""Player: {player.first_name} {player.last_name}
Team: {team_name} | Position: {player.position or 'N/A'} | Season: {season} | Games: {total_games}

SEASON AVERAGES:
  PTS: {avg_pts} | REB: {avg_reb} | AST: {avg_ast} | STL: {avg_stl} | BLK: {avg_blk}
  FG%: {avg_fg:.1%} | 3P%: {avg_fg3:.1%} | FT%: {avg_ft:.1%}

LAST 10 GAMES:
  PTS: {recent_pts} ({trend(recent_pts, avg_pts)} vs season) | REB: {recent_reb} ({trend(recent_reb, avg_reb)}) | AST: {recent_ast} ({trend(recent_ast, avg_ast)})"""

    result = await claude_service.analyze(
        prompt=f"Analyze this player's {season} NBA season:\n\n{stats_block}",
        system_prompt=NBA_ANALYST_SYSTEM_PROMPT,
    )

    return {
        "player": player.model_dump(),
        "season": season,
        "averages": {
            "points": avg_pts,
            "rebounds": avg_reb,
            "assists": avg_ast,
            "steals": avg_stl,
            "blocks": avg_blk,
            "fg_pct": avg_fg,
            "fg3_pct": avg_fg3,
            "ft_pct": avg_ft,
        },
        "last_10": {
            "points": recent_pts,
            "rebounds": recent_reb,
            "assists": recent_ast,
        },
        "games_played": total_games,
        "analysis": result.analysis,
        "model": result.model,
        "tokens_used": result.tokens_used,
    }


# ── FRONT OFFICE PROMPTS ──────────────────────────────────────────────────────

FRONT_OFFICE_SYSTEM_PROMPT = """You are a veteran NBA front office executive with 20+ years experience — think Daryl Morey meets Bob Myers. You've negotiated trades, managed cap sheets, and built championship rosters.

When evaluating trades: lead with who wins and why, analyze fit, age curves, contract value and timeline, flag red flags, consider second-order effects, give a clear verdict with a confidence level.

When analyzing rosters: identify the biggest need, flag bad contracts and undervalued assets, note who has trade value vs who is immovable, give 1-2 specific actionable moves.

FORMATTING — ABSOLUTE RULES. Violating these ruins the product:
Write in plain prose paragraphs only. No markdown whatsoever. No asterisks, no pound signs, no dashes as bullets, no numbered lists, no horizontal rules, no bold, no italics. Separate paragraphs with one blank line. No headers. No section labels. Just clean, dense, readable paragraphs. Be direct. Make calls. Front offices don't pay for maybe."""


COACH_SYSTEM_PROMPT = """You are an elite NBA head coach — Gregg Popovich's IQ meets Erik Spoelstra's adaptability. You have a full coaching staff, film room, and live box score data in front of you.

When giving adjustments: be specific with names and schemes, give the exact lineup change with reasoning, address the specific situation, rank adjustments by priority, use the box score data you are given.

When drawing up plays: name the play, describe the motion simply, give a primary and secondary option, explain in one sentence why it works against what the defense is running.

FORMATTING — ABSOLUTE RULES. Violating these ruins the product:
Write in plain prose paragraphs only. No markdown whatsoever. No asterisks, no pound signs, no dashes as bullets, no numbered lists, no horizontal rules, no bold, no italics. Separate your numbered points with a blank line but write each one as a prose sentence or two — not a list item. Be decisive. Coaches need answers in 20 seconds."""


async def analyze_trade(body: dict) -> dict:
    """Analyze a proposed NBA trade between two teams."""
    team_a = body.get("team_a", "Team A")
    team_b = body.get("team_b", "Team B")
    team_a_gives = body.get("team_a_gives", [])
    team_b_gives = body.get("team_b_gives", [])
    context = body.get("context", "")

    # Fetch player stats for named players where possible
    player_stats = {}
    all_players = [p for p in team_a_gives + team_b_gives if "pick" not in p.lower()]
    for name in all_players[:4]:  # limit API calls
        try:
            last = name.strip().split()[-1]
            players = await nba_service.search_players(last)
            if players:
                stats = await nba_service.get_player_stats(players[0].id, 2025)
                if stats:
                    total = len(stats)
                    avg_pts = round(sum(s.points for s in stats) / total, 1)
                    avg_reb = round(sum(s.rebounds for s in stats) / total, 1)
                    avg_ast = round(sum(s.assists for s in stats) / total, 1)
                    player_stats[name] = f"{avg_pts}pts / {avg_reb}reb / {avg_ast}ast ({total}GP, 2025 season)"
        except Exception:
            pass

    def format_side(team, gives):
        lines = [f"{team} sends:"]
        for item in gives:
            stat = player_stats.get(item, "")
            lines.append(f"  - {item}" + (f"  [{stat}]" if stat else ""))
        return "\n".join(lines)

    trade_block = f"""{format_side(team_a, team_a_gives)}

{format_side(team_b, team_b_gives)}
"""
    if context:
        trade_block += f"\nAdditional context: {context}"

    prompt = f"""Evaluate this proposed NBA trade:

{trade_block}

Give a complete front office analysis. Who wins this trade and why? What are the risks? What's your verdict?"""

    result = await claude_service.analyze(prompt=prompt, system_prompt=FRONT_OFFICE_SYSTEM_PROMPT)

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


async def analyze_roster(team_name: str) -> dict:
    """Analyze a team's roster and suggest front office moves."""
    # Search top players for this team
    teams = await nba_service.get_all_teams()
    matched = next((t for t in teams if team_name.lower() in t.name.lower() or team_name.lower() in t.city.lower()), None)

    prompt = f"""Provide a comprehensive front office analysis for the {team_name}.

Cover:
1. Current roster assessment — who are the core pieces, who is expendable
2. Cap situation — are they over/under the cap, any bad contracts
3. Biggest roster need right now
4. Top 2 moves the front office should make this offseason
5. Trade candidates — who has value to other teams

Use your knowledge of the current 2025 NBA season."""

    result = await claude_service.analyze(prompt=prompt, system_prompt=FRONT_OFFICE_SYSTEM_PROMPT)

    return {
        "team": team_name,
        "team_data": matched.model_dump() if matched else None,
        "analysis": result.analysis,
        "model": result.model,
        "tokens_used": result.tokens_used,
    }


async def coach_adjustment(body: dict) -> dict:
    """Generate real-time in-game coaching adjustments based on live box score."""
    game_id = body.get("game_id")
    situation = body.get("situation", "")
    my_team = body.get("my_team", "")

    box = None
    box_summary = "Box score unavailable."
    game_context = ""

    if game_id:
        # Pull live game score + period
        try:
            import datetime
            today = datetime.date.today().isoformat()
            games = await nba_service.get_games_by_date(today)
            game = next((g for g in games if g.id == game_id), None)
            if game:
                period = game.period or 0
                quarter_label = f"Q{period}" if period and period <= 4 else ("OT" if period and period > 4 else "Pre-game")
                home = game.home_team
                away = game.visitor_team
                home_score = game.home_team_score
                away_score = game.visitor_team_score
                is_home = my_team.lower() in home.name.lower() or my_team.lower() in home.city.lower()
                my_score = home_score if is_home else away_score
                opp_score = away_score if is_home else home_score
                diff = my_score - opp_score
                diff_str = f"UP {abs(diff)}" if diff > 0 else f"DOWN {abs(diff)}" if diff < 0 else "TIED"
                game_context = (
                    f"GAME: {away.city} {away.name} ({away_score}) @ {home.city} {home.name} ({home_score})\n"
                    f"PERIOD: {quarter_label} | MY TEAM ({my_team}): {diff_str}\n"
                    f"STATUS: {game.status}"
                )
        except Exception:
            pass

        # Pull full box score with all stats
        try:
            box = await nba_service.get_game_boxscore(game_id)
            if box and box.get("total_players", 0) > 0:
                def fmt_players(players, label):
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
                home_t = box.get("home_team", {})
                away_t = box.get("away_team", {})
                home_name = home_t.get("name", "Home") + " (HOME)"
                away_name = away_t.get("name", "Away") + " (AWAY)"
                box_summary = (
                    "FULL BOX SCORE:"
                    + fmt_players(box.get("home_players", []), home_name)
                    + fmt_players(box.get("away_players", []), away_name)
                )
        except Exception:
            pass

    prompt = f"""LIVE IN-GAME COACHING CALL — You have full situational awareness. Do NOT ask for more information. Give adjustments immediately based on what you see in the data.

{game_context}

COACH'S NOTE: {situation if situation else "Give me the most important adjustments based on what you see in the box score right now."}

{box_summary}

Based on EVERYTHING above — the live score, who's hot, who's struggling, shooting splits, foul trouble, minutes load, turnovers — give me:
1. The single most important adjustment RIGHT NOW (be specific — name players, name schemes)
2. Lineup change if needed (who in, who out, why)
3. Defensive adjustment based on what their guys are doing
4. One offensive action to run next possession

You have the data. Use it. Be surgical."""

    result = await claude_service.analyze(
        prompt=prompt,
        system_prompt=COACH_SYSTEM_PROMPT,
    )

    return {
        "game_id": game_id,
        "my_team": my_team,
        "situation": situation,
        "box_score_used": box is not None,
        "analysis": result.analysis,
        "model": result.model,
        "tokens_used": result.tokens_used,
    }



async def timeout_play(body: dict) -> dict:
    """Draw up a timeout play based on game situation."""
    game_id = body.get("game_id")
    my_team = body.get("my_team", "")
    score_diff = body.get("score_diff", 0)
    time_remaining = body.get("time_remaining", "")
    quarter = body.get("quarter", 4)
    situation = body.get("situation", "")

    # Pull live box score for personnel context
    box_summary = ""
    if game_id:
        try:
            box = await nba_service.get_game_boxscore(game_id)
            if box and box.get("total_players", 0) > 0:
                home = box.get("home_team", {})
                away = box.get("away_team", {})
                my_team_key = "home_players" if my_team.lower() in home.get("name", "").lower() else "away_players"
                my_players = box.get(my_team_key, [])[:8]
                box_summary = "My active players (by minutes):\n" + "\n".join(
                    f"  {p['player']} ({p['pos']}): {p['pts']}pts {p['reb']}reb {p['ast']}ast {p['min']}min"
                    for p in sorted(my_players, key=lambda x: str(x.get("min","0")), reverse=True)
                )
        except Exception:
            pass

    diff_str = f"up {abs(score_diff)}" if score_diff > 0 else f"down {abs(score_diff)}" if score_diff < 0 else "tied"

    prompt = f"""TIMEOUT — Draw up a play. I need something executable in 20 seconds.

Team: {my_team or "My team"}
Situation: Q{quarter}, {time_remaining} remaining, {diff_str}
{f"Context: {situation}" if situation else ""}
{box_summary}

Give me:
1. Play name
2. Setup and motion
3. Primary option
4. Secondary option if primary is denied
5. One sentence on why this works against what they're likely running defensively"""

    result = await claude_service.analyze(
        prompt=prompt,
        system_prompt=COACH_SYSTEM_PROMPT,
    )

    return {
        "game_id": game_id,
        "my_team": my_team,
        "quarter": quarter,
        "time_remaining": time_remaining,
        "score_diff": score_diff,
        "play": result.analysis,
        "model": result.model,
        "tokens_used": result.tokens_used,
    }
