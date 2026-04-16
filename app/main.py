from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api.routes import router
from app.core.config import get_settings
from app.core.http_client import GlobalHTTPClient
from app.core.limiter import limiter


@asynccontextmanager
async def lifespan(app: FastAPI):
    await GlobalHTTPClient.start()
    yield
    await GlobalHTTPClient.stop()


settings = get_settings()
app = FastAPI(title="PIVOT", version="1.0.0", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(router, prefix="/api/v1")

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
