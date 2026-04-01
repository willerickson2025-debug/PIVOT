from __future__ import annotations

import httpx


class GlobalHTTPClient:
    """
    Application-lifetime shared httpx client.

    Wired into the FastAPI lifespan so the connection pool is reused across
    all requests instead of opening a new TCP connection per API call.
    """

    _client: httpx.AsyncClient | None = None

    @classmethod
    def get_client(cls) -> httpx.AsyncClient:
        if cls._client is None:
            # Fallback outside the FastAPI lifecycle (tests, scripts).
            cls._client = httpx.AsyncClient(timeout=12.0)
        return cls._client

    @classmethod
    async def start(cls) -> None:
        cls._client = httpx.AsyncClient(timeout=12.0)

    @classmethod
    async def stop(cls) -> None:
        if cls._client is not None:
            await cls._client.aclose()
            cls._client = None
