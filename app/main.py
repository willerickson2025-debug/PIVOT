from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api.routes import router
from app.api.routes_advanced import advanced_router
from app.core.config import get_settings
from app.core.http_client import GlobalHTTPClient
from app.core.limiter import limiter

logger = logging.getLogger(__name__)


async def _prewarm_cache() -> None:
    try:
        from app.api.routes import _build_leaderboard
        from app.core.cache import cache_set
        from app.core.season import get_current_season
        from app.services import nba_service
        import datetime

        season = get_current_season()
        for sort in ("pie", "pts"):
            rk = f"leaderboard:10:{sort}:{season}"
            data = await _build_leaderboard(10, sort, season)
            await cache_set(rk, data, 1800)

        today = datetime.date.today().isoformat()
        await nba_service.get_games_by_date(today)
        await nba_service.get_all_teams()
        logger.info("Cache prewarm complete")
    except Exception as exc:
        logger.warning("Cache prewarm failed: %s", exc)


async def _periodic_refresh() -> None:
    while True:
        await asyncio.sleep(1800)
        try:
            from app.api.routes import _build_leaderboard
            from app.core.cache import cache_set
            from app.core.season import get_current_season

            season = get_current_season()
            for sort in ("pie", "pts"):
                rk = f"leaderboard:10:{sort}:{season}"
                data = await _build_leaderboard(10, sort, season)
                await cache_set(rk, data, 1800)
            logger.info("Periodic cache refresh complete")
        except Exception as exc:
            logger.warning("Periodic cache refresh failed: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await GlobalHTTPClient.start()
    # Prewarm id_bridge in the background — failure is non-fatal
    try:
        from app.services import id_bridge
        asyncio.create_task(id_bridge.prewarm())
    except Exception as exc:
        logger.warning("id_bridge prewarm skipped: %s", exc)
    asyncio.create_task(_prewarm_cache())
    asyncio.create_task(_periodic_refresh())
    yield
    await GlobalHTTPClient.stop()


settings = get_settings()
app = FastAPI(title="PIVOT", version="1.0.0", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
_CORS_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:3001",
    "https://pivotintelligence.ai",
    "https://www.pivotintelligence.ai",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_origin_regex=r"https://.*\.up\.railway\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router, prefix="/api/v1")
app.include_router(advanced_router, prefix="/api/v1/advanced")

_root = os.path.dirname(os.path.dirname(__file__))
_static = os.path.join(_root, "static")
os.makedirs(_static, exist_ok=True)
app.mount("/static", StaticFiles(directory=_static), name="static")


@app.get("/")
async def root():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dashboard.html")
    if os.path.exists(path):
        return FileResponse(path, headers={"Cache-Control": "no-store, no-cache, must-revalidate"})
    return {"status": "running", "error": "dashboard.html not found", "looked_at": path}


@app.get("/health")
async def health():
    return {"status": "ok"}
