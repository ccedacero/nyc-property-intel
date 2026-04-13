import asyncio
import logging
import time
from collections import deque

import httpx

from nyc_property_intel.config import settings

logger = logging.getLogger(__name__)

SOCRATA_BASE = "https://data.cityofnewyork.us/resource"

_MAX_RETRIES = 3
_RETRY_DELAYS = (1.0, 2.0, 4.0)  # seconds between attempts


class SocrataError(Exception):
    """Raised when the Socrata API fails after all retries."""


class RateLimiter:
    def __init__(self, max_per_hour: int = 5000):
        self.max_per_hour = max_per_hour
        self.timestamps: deque[float] = deque()

    async def acquire(self):
        while True:
            now = time.monotonic()
            while self.timestamps and now - self.timestamps[0] > 3600:
                self.timestamps.popleft()
            if len(self.timestamps) >= self.max_per_hour:
                sleep_time = 3600 - (now - self.timestamps[0])
                await asyncio.sleep(sleep_time)
                continue
            break
        self.timestamps.append(time.monotonic())


_limiter = RateLimiter(max_per_hour=settings.socrata_rate_limit_per_hour)

# Singleton client — reuses TCP connections rather than opening a new one
# per request. Lifecycle mirrors the MCP server lifespan; call close_client()
# on shutdown to drain keep-alive connections cleanly.
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        headers: dict[str, str] = {"Accept": "application/json"}
        if settings.socrata_app_token:
            headers["X-App-Token"] = settings.socrata_app_token
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=5.0),
            headers=headers,
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
    """Query a NYC Open Data Socrata dataset.

    Retries up to 3 times on network timeouts and HTTP 5xx errors.
    Detects Socrata's silent-failure mode (200 OK with empty body and no
    X-SODA2-Fields header) and retries before raising SocrataError.

    Raises:
        SocrataError: After all retries are exhausted.
        httpx.HTTPStatusError: For non-retryable HTTP errors (4xx).
    """
    await _limiter.acquire()

    params: dict[str, str | int] = {
        "$where": where,
        "$limit": limit,
        "$order": order,
    }
    if select:
        params["$select"] = select
    # Also pass token as URL param — proxies (e.g. Railway) may strip custom headers
    if settings.socrata_app_token:
        params["$$app_token"] = settings.socrata_app_token

    url = f"{SOCRATA_BASE}/{dataset_id}.json"
    client = _get_client()
    last_exc: Exception | None = None

    for attempt in range(_MAX_RETRIES):
        try:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            last_exc = exc
            logger.warning(
                "Socrata network error on %s (attempt %d/%d): %s",
                dataset_id, attempt + 1, _MAX_RETRIES, type(exc).__name__,
            )
            if attempt < _MAX_RETRIES - 1:
                await asyncio.sleep(_RETRY_DELAYS[attempt])
            continue
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500 and attempt < _MAX_RETRIES - 1:
                last_exc = exc
                logger.warning(
                    "Socrata HTTP %d on %s (attempt %d/%d)",
                    exc.response.status_code, dataset_id, attempt + 1, _MAX_RETRIES,
                )
                await asyncio.sleep(_RETRY_DELAYS[attempt])
                continue
            raise

        data = resp.json()

        # Detect Socrata silent timeout: returns 200 [] without the X-SODA2-Fields
        # header that Socrata always includes on a successfully executed query
        # (even when the result set is legitimately empty).
        # Also guard against Socrata returning an error dict instead of a list.
        if (not isinstance(data, list) or not data) and "X-SODA2-Fields" not in resp.headers:
            logger.warning(
                "Socrata silent empty (no X-SODA2-Fields) on %s (attempt %d/%d)",
                dataset_id, attempt + 1, _MAX_RETRIES,
            )
            if attempt < _MAX_RETRIES - 1:
                await asyncio.sleep(_RETRY_DELAYS[attempt])
                continue
            raise SocrataError(
                "Socrata returned an empty response (possible internal timeout). "
                "Try again in a moment."
            )

        return data if isinstance(data, list) else []

    raise SocrataError(
        "Socrata API did not respond after 3 attempts. Try again in a moment."
    ) from last_exc
