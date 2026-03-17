from pydantic import BaseModel
from typing import Optional


class Team(BaseModel):
    id: int
    name: str
    abbreviation: str
    city: str
    conference: str
    division: str


class Game(BaseModel):
    id: int
    date: str
    status: str
    period: Optional[int] = None
    time: Optional[str] = None
    home_team: Team
    visitor_team: Team
    home_team_score: int
    visitor_team_score: int


class Player(BaseModel):
    id: int
    first_name: str
    last_name: str
    position: Optional[str] = None
    team: Optional[Team] = None


class PlayerStats(BaseModel):
    player: Player
    game_id: int
    points: int
    rebounds: int
    assists: int
    steals: int
    blocks: int
    minutes: Optional[str] = None
    fg_pct: Optional[float] = None
    fg3_pct: Optional[float] = None
    ft_pct: Optional[float] = None


class AnalysisRequest(BaseModel):
    prompt: str
    context: Optional[str] = None


class AnalysisResponse(BaseModel):
    analysis: str
    model: str
    tokens_used: int


class GameAnalysisResponse(BaseModel):
    games: list[Game]
    analysis: str
    model: str
    tokens_used: int
    game_count: int


class HealthResponse(BaseModel):
    status: str
    environment: str
    version: str = "1.0.0"
