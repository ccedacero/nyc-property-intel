import asyncio
import time
from collections import deque

import httpx

from nyc_property_intel.config import settings

SOCRATA_BASE = "https://data.cityofnewyork.us/resource"


class RateLimiter:
    def __init__(self, max_per_hour: int = 5000):
        self.max_per_hour = max_per_hour
        self.timestamps: deque[float] = deque()

    async def acquire(self):
        now = time.monotonic()
        while self.timestamps and now - self.timestamps[0] > 3600:
            self.timestamps.popleft()
        if len(self.timestamps) >= self.max_per_hour:
            sleep_time = 3600 - (now - self.timestamps[0])
            await asyncio.sleep(sleep_time)
        self.timestamps.append(time.monotonic())


_limiter = RateLimiter(max_per_hour=settings.socrata_rate_limit_per_hour)

# Singleton client — reuses TCP connections rather than opening a new one
# per request. Lifecycle mirrors the MCP server lifespan; call close_client()
# on shutdown to drain keep-alive connections cleanly.
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=5.0),
            headers={"Accept": "application/json"},
        )
    return _client


async def close_client() -> None:
    """Close the shared httpx client. Call from the server lifespan."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
        _client = None


async def query_socrata(
    dataset_id: str,
    where: str,
    limit: int = 1000,
    order: str = ":id",
    select: str | None = None,
) -> list[dict]:
    """Query a NYC Open Data Socrata dataset."""
    await _limiter.acquire()

    params: dict[str, str | int] = {
        "$where": where,
        "$limit": limit,
        "$order": order,
    }
    if select:
        params["$select"] = select
    if settings.socrata_app_token:
        params["$$app_token"] = settings.socrata_app_token

    client = _get_client()
    resp = await client.get(
        f"{SOCRATA_BASE}/{dataset_id}.json",
        params=params,
    )
    resp.raise_for_status()
    return resp.json()
