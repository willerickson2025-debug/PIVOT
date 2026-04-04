from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, computed_field


# ---------------------------------------------------------------------------
# Core Domain Models
# ---------------------------------------------------------------------------

class Team(BaseModel):
    """NBA team metadata."""
    model_config = ConfigDict(frozen=True)

    id: int
    name: str
    abbreviation: str
    city: str
    conference: str
    division: str


class Game(BaseModel):
    """Represents a single NBA game."""
    model_config = ConfigDict(frozen=True)

    id: int
    date: str
    status: str

    period: int | None = None
    time: str | None = None

    home_team: Team
    visitor_team: Team

    home_team_score: int
    visitor_team_score: int


class Player(BaseModel):
    """Basic player profile."""
    model_config = ConfigDict(frozen=True)

    id: int
    first_name: str
    last_name: str

    position: str | None = None
    team: Team | None = None
    nba_id: int | None = None


class Contract(BaseModel):
    """Player contract entry returned from BallDontLie contracts endpoint."""
    model_config = ConfigDict(frozen=True)

    id: int
    player_id: int
    season: int
    team_id: int
    cap_hit: int | None = None
    total_cash: int | None = None
    base_salary: int | None = None
    rank: int | None = None
    player: Player | None = None
    team: Team | None = None


class AdvancedStat(BaseModel):
    """Advanced per-game stat block from the NBA advanced stats endpoint."""
    model_config = ConfigDict(frozen=True)

    id: int
    pie: float | None = None
    pace: float | None = None
    assist_percentage: float | None = None
    assist_ratio: float | None = None
    defensive_rating: float | None = None
    defensive_rebound_percentage: float | None = None
    effective_field_goal_percentage: float | None = None
    net_rating: float | None = None
    offensive_rating: float | None = None
    offensive_rebound_percentage: float | None = None
    rebound_percentage: float | None = None
    true_shooting_percentage: float | None = None
    turnover_ratio: float | None = None
    usage_percentage: float | None = None
    player: Player | None = None
    team: Team | None = None
    game: dict | None = None


class LineupEntry(BaseModel):
    """Single lineup entry (starter/bench) for a game."""
    model_config = ConfigDict(frozen=True)

    id: int
    game_id: int
    starter: bool | None = None
    position: str | None = None
    player: Player | None = None
    team: Team | None = None


class PlayerStats(BaseModel):
    """Per-game statistical line for a player."""
    model_config = ConfigDict(frozen=True)

    player: Player
    game_id: int

    points: int
    rebounds: int
    assists: int
    steals: int
    blocks: int

    minutes: str | None = None

    fg_pct: float | None = Field(default=None, description="Field goal percentage")
    fg3_pct: float | None = Field(default=None, description="Three-point field goal percentage")
    ft_pct: float | None = Field(default=None, description="Free throw percentage")


# ---------------------------------------------------------------------------
# Analysis API Models
# ---------------------------------------------------------------------------

class AnalysisRequest(BaseModel):
    """Request payload for direct Claude analysis endpoints."""
    prompt: str
    context: str | None = None


class AnalysisResponse(BaseModel):
    """Standard response from the Claude analysis layer."""
    model_config = ConfigDict(frozen=True)

    analysis: str
    model: str
    tokens_used: int


class GameAnalysisResponse(BaseModel):
    """Analysis response containing game data."""
    model_config = ConfigDict(frozen=True)

    games: list[Game]
    analysis: str
    model: str
    tokens_used: int

    @computed_field
    @property
    def game_count(self) -> int:
        return len(self.games)


# ---------------------------------------------------------------------------
# System / Operational Models
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    """Service health check response."""
    model_config = ConfigDict(frozen=True)

    status: str
    environment: str
    version: str = "1.0.0"
