from fastapi import Security, HTTPException, status
from fastapi.security import APIKeyHeader

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(api_key: str = Security(API_KEY_HEADER)) -> str:
    from app.core.config import get_settings
    settings = get_settings()
    pivot_key = getattr(settings, "pivot_api_key", None)
    if not pivot_key:
        return "unauthenticated"
    if api_key != pivot_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing API key.")
    return api_key
