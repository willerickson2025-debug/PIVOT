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
