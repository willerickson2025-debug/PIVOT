import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from app.api.routes import router
from app.core.config import get_settings

settings = get_settings()
app = FastAPI(title="PIVOT", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(router, prefix="/api/v1")

@app.get("/")
async def root():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dashboard.html")
    if os.path.exists(path):
        return FileResponse(path)
    return {"status": "running", "error": "dashboard.html not found", "looked_at": path}

@app.get("/health")
async def health():
    return {"status": "ok"}
