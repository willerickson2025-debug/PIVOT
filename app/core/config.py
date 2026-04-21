from typing import Optional
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    app_name: str = "PIVOT"
    environment: str = "development"
    port: int = 8000
    anthropic_api_key: str
    claude_model: str = "claude-sonnet-4-6"
    claude_max_tokens: int = 2048
    balldontlie_api_key: str
    balldontlie_base_url: str = "https://api.balldontlie.io/v1"
    # Set PIVOT_API_KEY in Railway env to enforce key-based auth on analysis routes.
    # When unset, analysis routes are publicly accessible (rate limits still apply).
    pivot_api_key: Optional[str] = None

    class Config:
        env_file = ".env"
        case_sensitive = False


@lru_cache()
def get_settings() -> Settings:
    return Settings()
