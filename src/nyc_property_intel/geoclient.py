"""NYC GeoClient (Geocoder) integration.

Resolves street addresses to BBLs using the NYC GeoClient v2 API
with an in-memory TTL cache and a PAD-table fallback for addresses
the API cannot resolve.

Usage from tool modules:

    from nyc_property_intel.geoclient import resolve_address_to_bbl

    bbl = await resolve_address_to_bbl("123 Main St, Brooklyn, NY 11201")
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx
from cachetools import TTLCache
from mcp.server.fastmcp.exceptions import ToolError

from nyc_property_intel.config import settings
from nyc_property_intel.utils import BOROUGH_NAME_TO_CODE, borough_code_to_name

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────

GEOCLIENT_BASE_URL = "https://api.nyc.gov/geo/geoclient/v2"

# Cache resolved addresses for 24 hours (addresses don't change often).
_address_cache: TTLCache[str, str] = TTLCache(maxsize=2048, ttl=86400)

# ── HTTP client (lazy singleton) ──────────────────────────────────────

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Return the module-level httpx client, creating it on first use."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=GEOCLIENT_BASE_URL,
            timeout=httpx.Timeout(15.0, connect=5.0),
            headers={"Accept": "application/json"},
        )
    return _client


async def close_client() -> None:
    """Close the httpx client. Called during server shutdown."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
        _client = None


# ── Address parsing ───────────────────────────────────────────────────

# Matches: "123 Main St", "123-45 Main St" (Queens style), with optional
# unit/apt, borough/city, state, zip.
_ADDRESS_RE = re.compile(
    r"^\s*(?P<house_number>\d+(?:-\d+)?)\s+"
    r"(?P<street>.+?)"
    r"(?:\s*,\s*(?P<borough_city>[A-Za-z ]+?))?"
    r"(?:\s*,?\s*(?:NY|New York)\s*)?"
    r"(?:\s*(?P<zip>\d{5}))?\s*$",
    re.IGNORECASE,
)

# Zip code → borough code mapping for NYC zip ranges.
_ZIP_TO_BOROUGH: dict[range, str] = {
    range(10001, 10283): "1",  # Manhattan
    range(10451, 10476): "2",  # Bronx
    range(11201, 11257): "3",  # Brooklyn
    range(11004, 11110): "4",  # Queens (partial)
    range(11351, 11698): "4",  # Queens (continued)
    range(10301, 10315): "5",  # Staten Island
}


def parse_address(address: str) -> dict[str, str]:
    """Parse a free-form NYC address into structured components.

    Args:
        address: A string like "123 Main St, Brooklyn, NY 11201"
                 or "45-67 Queens Blvd, Queens".

    Returns:
        Dict with keys: house_number, street, borough_code, borough_name.

    Raises:
        ToolError: If the address cannot be parsed.
    """
    match = _ADDRESS_RE.match(address.strip())
    if not match:
        raise ToolError(
            f"Could not parse address: \"{address}\". "
            "Please provide a street address with a house number, "
            "e.g. \"123 Main St, Brooklyn, NY 11201\"."
        )

    house_number = match.group("house_number")
    street = match.group("street").strip().rstrip(",")
    borough_city = (match.group("borough_city") or "").strip()
    zip_code = match.group("zip")

    # Determine borough: try city/borough name first, then zip code.
    borough_code: str | None = None

    if borough_city:
        borough_code = BOROUGH_NAME_TO_CODE.get(borough_city.lower())

    if borough_code is None and zip_code:
        zip_int = int(zip_code)
        for zip_range, code in _ZIP_TO_BOROUGH.items():
            if zip_int in zip_range:
                borough_code = code
                break

    if borough_code is None:
        raise ToolError(
            f"Could not determine borough for \"{address}\". "
            "Please include a borough name (Manhattan, Brooklyn, etc.) "
            "or a valid NYC zip code."
        )

    return {
        "house_number": house_number,
        "street": street,
        "borough_code": borough_code,
        "borough_name": borough_code_to_name(borough_code),
    }


# ── GeoClient API call ───────────────────────────────────────────────

async def _call_geoclient(
    house_number: str,
    street: str,
    borough_code: str,
) -> dict[str, Any]:
    """Call the GeoClient /search endpoint and return the response dict.

    Raises:
        ToolError: On API errors or missing credentials.
    """
    if not settings.geoclient_configured:
        raise ToolError(
            "NYC GeoClient API credentials are not configured. "
            "Set NYC_GEOCLIENT_APP_ID and NYC_GEOCLIENT_APP_KEY in your .env file. "
            "Register at https://api-portal.nyc.gov/"
        )

    borough_name = borough_code_to_name(borough_code)
    client = _get_client()

    try:
        resp = await client.get(
            "/address.json",
            params={
                "houseNumber": house_number,
                "street": street,
                "borough": borough_name,
                "app_id": settings.nyc_geoclient_app_id,
                "app_key": settings.nyc_geoclient_app_key,
            },
        )
        resp.raise_for_status()
    except httpx.TimeoutException:
        raise ToolError(
            "NYC GeoClient API timed out. The service may be temporarily "
            "unavailable — please try again in a moment."
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 403:
            raise ToolError(
                "NYC GeoClient returned 403 Forbidden. Check that your "
                "API credentials are valid and have not expired."
            )
        raise ToolError(
            f"NYC GeoClient API error (HTTP {exc.response.status_code}). "
            "Please try again."
        )

    data = resp.json()
    result = data.get("address", {})

    # GeoClient returns errors in the response body, not via HTTP status.
    if "message" in result and "bbl" not in result:
        msg = result["message"]
        raise ToolError(
            f"GeoClient could not resolve this address: {msg}. "
            "Check the house number and street name."
        )

    return result


# ── PAD fallback ──────────────────────────────────────────────────────

async def _pad_fallback(
    house_number: str,
    street: str,
    borough_code: str,
) -> str | None:
    """Try resolving via the local PAD table when GeoClient fails.

    Handles Queens-style hyphenated house numbers by casting to int
    for the low/high range comparison.

    Returns:
        A 10-digit BBL string, or None if not found.
    """
    # Lazy import to avoid circular dependency at module load time.
    from nyc_property_intel.db import fetch_one

    # Normalize house number: strip hyphens for Queens addresses.
    # "45-67" → 4567 for PAD range comparison.
    house_num_clean = house_number.replace("-", "")
    try:
        house_num_int = int(house_num_clean)
    except ValueError:
        return None

    # Normalize street name for matching.
    street_upper = street.upper().strip()

    row = await fetch_one(
        """
        SELECT bbl
        FROM pad
        WHERE borough_code = $1
          AND street_name = $2
          AND low_house_number <= $3
          AND high_house_number >= $3
        ORDER BY low_house_number
        LIMIT 1
        """,
        borough_code,
        street_upper,
        house_num_int,
    )

    if row:
        return row["bbl"]
    return None


# ── Public API ────────────────────────────────────────────────────────

async def resolve_address_to_bbl(address: str) -> str:
    """Resolve a free-form NYC address to a 10-digit BBL.

    Tries the GeoClient API first, then falls back to the local PAD
    table. Results are cached for 24 hours.

    Args:
        address: A string like "123 Main St, Brooklyn, NY 11201".

    Returns:
        A 10-digit BBL string, e.g. "3012340001".

    Raises:
        ToolError: If the address cannot be resolved by any method.
    """
    cache_key = address.strip().lower()
    if cache_key in _address_cache:
        logger.debug("Address cache hit: %s", address)
        return _address_cache[cache_key]

    parsed = parse_address(address)
    house_number = parsed["house_number"]
    street = parsed["street"]
    borough_code = parsed["borough_code"]

    bbl: str | None = None

    # Try GeoClient first.
    if settings.geoclient_configured:
        try:
            result = await _call_geoclient(house_number, street, borough_code)
            bbl = result.get("bbl")
            if bbl and len(bbl) == 10:
                _address_cache[cache_key] = bbl
                logger.info("GeoClient resolved %s → BBL %s", address, bbl)
                return bbl
        except ToolError:
            logger.warning("GeoClient failed for %s, trying PAD fallback", address)

    # Fallback to local PAD table.
    bbl = await _pad_fallback(house_number, street, borough_code)
    if bbl:
        _address_cache[cache_key] = bbl
        logger.info("PAD fallback resolved %s → BBL %s", address, bbl)
        return bbl

    raise ToolError(
        f"Could not resolve \"{address}\" to a BBL. "
        "Please verify the address is correct and includes a valid "
        "NYC house number and street name."
    )
