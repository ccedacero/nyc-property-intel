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

GEOCLIENT_BASE_URL = "https://api.nyc.gov/geoclient/v2"

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
    r"(?:\s*[,\s]\s*(?P<borough_city>Manhattan|Bronx|Brooklyn|Queens|Staten\s+Island|New\s+York))?"
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

# ── Street name normalization ─────────────────────────────────────────────────

_ORDINAL_ONES: dict[str, str] = {
    "1st": "First", "2nd": "Second", "3rd": "Third", "4th": "Fourth",
    "5th": "Fifth", "6th": "Sixth", "7th": "Seventh", "8th": "Eighth",
    "9th": "Ninth", "10th": "Tenth", "11th": "Eleventh", "12th": "Twelfth",
    "13th": "Thirteenth", "14th": "Fourteenth", "15th": "Fifteenth",
    "16th": "Sixteenth", "17th": "Seventeenth", "18th": "Eighteenth",
    "19th": "Nineteenth",
}

_ORDINAL_TENS: dict[str, str] = {
    "20th": "Twentieth", "30th": "Thirtieth", "40th": "Fortieth",
    "50th": "Fiftieth", "60th": "Sixtieth", "70th": "Seventieth",
    "80th": "Eightieth", "90th": "Ninetieth",
}

_TENS_PREFIX: dict[str, str] = {
    "2": "Twenty", "3": "Thirty", "4": "Forty", "5": "Fifty",
    "6": "Sixty", "7": "Seventy", "8": "Eighty", "9": "Ninety",
}

_STREET_SUFFIXES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bBlvd\b", re.IGNORECASE), "Boulevard"),
    (re.compile(r"\bAv(?:e)?\b", re.IGNORECASE), "Avenue"),
    (re.compile(r"\bSt\b", re.IGNORECASE), "Street"),
    (re.compile(r"\bDr\b", re.IGNORECASE), "Drive"),
    (re.compile(r"\bPl\b", re.IGNORECASE), "Place"),
    (re.compile(r"\bLn\b", re.IGNORECASE), "Lane"),
    (re.compile(r"\bCt\b", re.IGNORECASE), "Court"),
    (re.compile(r"\bPkwy\b", re.IGNORECASE), "Parkway"),
    (re.compile(r"\bExpy\b", re.IGNORECASE), "Expressway"),
    (re.compile(r"\bHwy\b", re.IGNORECASE), "Highway"),
    (re.compile(r"\bTerr?\b", re.IGNORECASE), "Terrace"),
    (re.compile(r"\bRd\b", re.IGNORECASE), "Road"),
]

_ORDINAL_RE = re.compile(r"\b(\d+)(st|nd|rd|th)\b", re.IGNORECASE)
_ORDINAL_SUFFIX_RE = re.compile(r"\b(\d+)(?:st|nd|rd|th)\b", re.IGNORECASE)


def _expand_ordinal(num: int, suffix: str) -> str:
    token = f"{num}{suffix.lower()}"
    if token in _ORDINAL_ONES:
        return _ORDINAL_ONES[token]
    if token in _ORDINAL_TENS:
        return _ORDINAL_TENS[token]
    if 20 < num < 100:
        tens_digit = str(num // 10)
        ones = num % 10
        if ones == 0:
            return _ORDINAL_TENS.get(f"{num}th", token)
        ones_token = f"{ones}{suffix.lower()}"
        ones_word = _ORDINAL_ONES.get(ones_token, ones_token)
        return _TENS_PREFIX.get(tens_digit, "") + "-" + ones_word
    # 100+ (e.g. 110th): GeoClient handles these fine as-is.
    return token


def normalize_geoclient_bbl(raw: str) -> str | None:
    """Normalize a BBL string from the GeoClient API to a 10-digit string.

    GeoClient sometimes returns BBLs with hyphens ("1-00835-0001") or with
    fewer digits than expected. Returns the normalized string, or None if
    the value cannot be coerced to a valid 10-digit BBL.
    """
    clean = raw.replace("-", "").strip()
    if clean.isdigit() and 1 <= len(clean) <= 10:
        return clean.zfill(10)
    return None


def _pad_street_name(street: str) -> str:
    """Prepare a street name for PAD ILIKE matching.

    PAD stores numeric ordinals ('80 STREET', not 'Eightieth Street'), so
    we strip ordinal suffixes and expand type abbreviations, then upper-case.
    '80th St' → '80 STREET', 'Eightieth Street' → 'Eightieth Street' (unchanged,
    but numeric forms are what PAD actually has so this handles user input).
    """
    stripped = _ORDINAL_SUFFIX_RE.sub(r"\1", street)
    for pattern, replacement in _STREET_SUFFIXES:
        stripped = pattern.sub(replacement, stripped)
    return stripped.upper().strip()


def normalize_street_name(street: str) -> str:
    """Expand ordinal abbreviations and street-type suffixes.

    Converts "5th Ave" → "Fifth Avenue" to improve GeoClient resolution
    and PAD ILIKE matching. Safe to call on already-canonical names.
    """
    expanded = _ORDINAL_RE.sub(
        lambda m: _expand_ordinal(int(m.group(1)), m.group(2)), street
    )
    for pattern, replacement in _STREET_SUFFIXES:
        expanded = pattern.sub(replacement, expanded)
    return expanded


def _strip_ordinals_for_geoclient(street: str) -> str:
    """Strip ordinal suffixes and expand type abbreviations for GeoClient.

    Produces the numeric form GeoClient natively recognises:
    "East 34th Street" → "East 34 Street".
    Used as a retry strategy when the spelled-out form fails.
    """
    stripped = _ORDINAL_SUFFIX_RE.sub(r"\1", street)
    for pattern, replacement in _STREET_SUFFIXES:
        stripped = pattern.sub(replacement, stripped)
    return stripped


def parse_address(
    address: str,
    borough_hint: str | None = None,
) -> dict[str, str]:
    """Parse a free-form NYC address into structured components.

    Args:
        address: A string like "123 Main St, Brooklyn, NY 11201"
                 or "45-67 Queens Blvd, Queens".
        borough_hint: Optional borough name supplied out-of-band (e.g. from
                      the caller's ``borough`` parameter). Used as a last-resort
                      fallback when the address itself contains no borough name
                      or recognisable zip code — avoids the need to mangle the
                      address string before parsing.

    Returns:
        Dict with keys: house_number, street, borough_code, borough_name.

    Raises:
        ToolError: If the address cannot be parsed.
    """
    if len(address) > 200:
        raise ToolError("Address is too long (max 200 characters).")

    # Truncate address in error messages to avoid echoing arbitrary input.
    _safe_addr = address[:120].replace('"', "'")

    match = _ADDRESS_RE.match(address.strip())
    if not match:
        raise ToolError(
            f"Could not parse address: \"{_safe_addr}\". "
            "Please provide a street address with a house number, "
            "e.g. \"123 Main St, Brooklyn, NY 11201\"."
        )

    house_number = match.group("house_number")
    street = match.group("street").strip().rstrip(",")
    borough_city = (match.group("borough_city") or "").strip()
    zip_code = match.group("zip")

    # Determine borough: address-embedded name → zip code → caller hint.
    # The hint is intentionally last so an explicit borough/zip in the address
    # always wins; the hint only fires when the address is ambiguous.
    borough_code: str | None = None

    if borough_city:
        borough_code = BOROUGH_NAME_TO_CODE.get(borough_city.lower())

    if borough_code is None and zip_code:
        zip_int = int(zip_code)
        for zip_range, code in _ZIP_TO_BOROUGH.items():
            if zip_int in zip_range:
                borough_code = code
                break

    if borough_code is None and borough_hint:
        borough_code = BOROUGH_NAME_TO_CODE.get(borough_hint.strip().lower())

    if borough_code is None:
        raise ToolError(
            f"Could not determine borough for \"{_safe_addr}\". "
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
            "Set NYC_GEOCLIENT_SUBSCRIPTION_KEY in your .env file. "
            "Register at https://api-portal.nyc.gov/"
        )

    borough_name = borough_code_to_name(borough_code)
    client = _get_client()

    params: dict[str, str] = {
        "houseNumber": house_number,
        "street": street,
        "borough": borough_name,
    }
    headers: dict[str, str] = {}

    if settings.nyc_geoclient_subscription_key:
        headers["Ocp-Apim-Subscription-Key"] = settings.nyc_geoclient_subscription_key
    else:
        params["app_id"] = settings.nyc_geoclient_app_id
        params["app_key"] = settings.nyc_geoclient_app_key

    try:
        resp = await client.get(
            "/address.json",
            params=params,
            headers=headers,
        )
        resp.raise_for_status()
    except httpx.TimeoutException as exc:
        raise ToolError(
            "NYC GeoClient API timed out. The service may be temporarily "
            "unavailable — please try again in a moment."
        ) from exc
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 403:
            raise ToolError(
                "NYC GeoClient returned 403 Forbidden. Check that your "
                "API credentials are valid and have not expired."
            ) from exc
        raise ToolError(
            f"NYC GeoClient API error (HTTP {exc.response.status_code}). "
            "Please try again."
        ) from exc

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

    house_num_clean = house_number.replace("-", "")
    try:
        int(house_num_clean)
    except ValueError:
        return None

    # PAD stores numeric ordinals ("80 STREET", not "Eightieth Street").
    # Build a PAD-friendly form from the raw street name the user typed.
    pad_street = _pad_street_name(street)
    street_upper = street.upper().strip()
    # Try both forms so "Eightieth Street" (GeoClient-normalised) also works.
    street_candidates = list(dict.fromkeys([pad_street, street_upper]))

    # For Queens hyphenated house numbers (e.g. "37-06") PAD stores lhnd/hhnd
    # in the same hyphenated format. Using the hyphen-stripped integer "3706"
    # breaks text range comparison because '-' (ASCII 45) < '0' (ASCII 48),
    # so "37-06" < "3706" and the hhnd >= clause always fails.
    # Use the original hyphenated form for range queries when present.
    range_house = house_number if "-" in house_number else house_num_clean

    for street_q in street_candidates:
        # Exact low-end match first (fast path).
        row = await fetch_one(
            """
            SELECT bbl
            FROM pad_adr
            WHERE boro = $1
              AND TRIM(stname) ILIKE '%' || $2 || '%'
              AND TRIM(lhnd) = $3
            LIMIT 1
            """,
            borough_code,
            street_q,
            house_number,
        )
        if row:
            return row["bbl"]

    for street_q in street_candidates:
        # Range fallback — covers addresses that are the high end of a range
        # (common in Queens where a single BBL spans e.g. 37-02 to 37-06).
        row = await fetch_one(
            """
            SELECT bbl
            FROM pad_adr
            WHERE boro = $1
              AND TRIM(stname) ILIKE '%' || $2 || '%'
              AND TRIM(lhnd) <= $3
              AND TRIM(hhnd) >= $3
            ORDER BY lhnd
            LIMIT 1
            """,
            borough_code,
            street_q,
            range_house,
        )
        if row:
            return row["bbl"]

    return None


# ── Public API ────────────────────────────────────────────────────────

async def resolve_address_to_bbl(
    address: str,
    borough_hint: str | None = None,
) -> str:
    """Resolve a free-form NYC address to a 10-digit BBL.

    Tries the GeoClient API first, then falls back to the local PAD
    table. Results are cached for 24 hours.

    Args:
        address: A string like "123 Main St, Brooklyn, NY 11201".
        borough_hint: Optional borough name to use when the address itself
                      contains no borough or recognisable zip code. Passed
                      directly to ``parse_address`` — do NOT append it to the
                      address string, as that breaks the address regex.

    Returns:
        A 10-digit BBL string, e.g. "3012340001".

    Raises:
        ToolError: If the address cannot be resolved by any method.
    """
    cache_key = address.strip().lower()
    if cache_key in _address_cache:
        logger.debug("Address cache hit: %s", address)
        return _address_cache[cache_key]

    parsed = parse_address(address, borough_hint=borough_hint)
    house_number = parsed["house_number"]
    street = parsed["street"]
    borough_code = parsed["borough_code"]

    street_normalized = normalize_street_name(street)

    bbl: str | None = None

    # Try GeoClient first.
    if settings.geoclient_configured:
        # Attempt 1: ordinals expanded ("34th" → "Thirty-Fourth Street")
        try:
            result = await _call_geoclient(house_number, street_normalized, borough_code)
            bbl = result.get("bbl")
            if bbl:
                bbl_clean = normalize_geoclient_bbl(bbl)
                if bbl_clean:
                    _address_cache[cache_key] = bbl_clean
                    logger.info("GeoClient resolved %s → BBL %s", address, bbl_clean)
                    return bbl_clean
        except ToolError:
            # Attempt 2: ordinal suffixes stripped ("34th" → "34 Street").
            # GeoClient natively handles numeric ordinals and rejects spelled-out
            # forms for some street names (e.g. "Thirty-Fourth" vs "34 Street").
            street_stripped = _strip_ordinals_for_geoclient(street)
            if street_stripped.lower() != street_normalized.lower():
                try:
                    result = await _call_geoclient(
                        house_number, street_stripped, borough_code
                    )
                    bbl = result.get("bbl")
                    if bbl:
                        bbl_clean = normalize_geoclient_bbl(bbl)
                        if bbl_clean:
                            _address_cache[cache_key] = bbl_clean
                            logger.info(
                                "GeoClient resolved %s → BBL %s (stripped form)",
                                address, bbl_clean,
                            )
                            return bbl_clean
                except ToolError:
                    pass

            logger.warning(
                "GeoClient failed for %s (street: %r → %r), trying PAD fallback",
                address, street, street_normalized,
            )

    # Fallback to local PAD table. Pass the original street name, not the
    # GeoClient-normalised form: PAD uses numeric ordinals ("80 STREET"),
    # while normalize_street_name produces spelled-out forms ("Eightieth Street").
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
