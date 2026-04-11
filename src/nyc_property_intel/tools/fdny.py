"""FDNY fire incident tool — fire incident history from NYC Open Data.

Queries the FDNY Fire Incident Reporting System (NFIRS) dataset via the
Socrata Open Data API. Covers fire incidents, EMS calls, and other
emergency responses reported by FDNY since 2013.

Dataset: NYC Open Data `8m42-w767`
Source: FDNY Fire Incident Reporting System
Update cadence: updated daily
"""

from __future__ import annotations

import logging
import urllib.parse
from typing import Any

import httpx
from mcp.server.fastmcp.exceptions import ToolError

from nyc_property_intel.app import mcp
from nyc_property_intel.config import settings

logger = logging.getLogger(__name__)

# ── Socrata API ───────────────────────────────────────────────────────

_SOCRATA_BASE = "https://data.cityofnewyork.us/resource"
_FDNY_DATASET = "8m42-w767.json"

# Lazy singleton HTTP client for Socrata queries.
_socrata_client: httpx.AsyncClient | None = None


def _get_socrata_client() -> httpx.AsyncClient:
    global _socrata_client
    if _socrata_client is None or _socrata_client.is_closed:
        headers = {"Accept": "application/json"}
        if settings.socrata_app_token:
            headers["X-App-Token"] = settings.socrata_app_token
        _socrata_client = httpx.AsyncClient(
            base_url=_SOCRATA_BASE,
            timeout=httpx.Timeout(20.0, connect=5.0),
            headers=headers,
        )
    return _socrata_client


async def close_socrata_client() -> None:
    """Close the Socrata HTTP client. Called during server shutdown."""
    global _socrata_client
    if _socrata_client is not None and not _socrata_client.is_closed:
        await _socrata_client.aclose()
        _socrata_client = None


# ── Borough name normalization ────────────────────────────────────────

# FDNY uses "MANHATTAN", "BRONX", "BROOKLYN", "QUEENS", "RICHMOND" (Staten Island)
_BOROUGH_CODE_TO_FDNY: dict[str, str] = {
    "1": "MANHATTAN",
    "2": "BRONX",
    "3": "BROOKLYN",
    "4": "QUEENS",
    "5": "RICHMOND",
}


# ── Address resolution ────────────────────────────────────────────────


async def _resolve_to_address(bbl: str) -> dict[str, str]:
    """Resolve a BBL to a street address + borough using the PLUTO/PAD tables.

    Returns:
        Dict with keys: house_number, street_name, borough_code, zip_code.

    Raises:
        ToolError: If no address can be found for the BBL.
    """
    # Lazy import to avoid circular dependency.
    from nyc_property_intel.db import fetch_one

    row = await fetch_one(
        """
        SELECT
            lhnd  AS house_number,
            stname AS street_name,
            boro  AS borough_code,
            zipcode AS zip_code
        FROM pad_adr
        WHERE bbl = $1
        LIMIT 1
        """,
        bbl,
    )
    if row is None:
        raise ToolError(
            f"Could not find an address for BBL {bbl}. "
            "Try passing the street address directly."
        )
    return dict(row)


# ── Socrata query helpers ─────────────────────────────────────────────


def _build_soql_where(
    house_number: str,
    street_name: str,
    borough_fdny: str | None,
    incident_type: str | None,
    since_year: int | None,
) -> str:
    """Build a SoQL WHERE clause for the FDNY dataset.

    The FDNY `incident_address` field stores addresses like:
    "37-06  80 ST" or "350  5 AV". We match on street_name and
    optionally house number using LIKE.
    """
    # Normalize street name: remove punctuation, uppercase, abbreviate common words.
    street_upper = street_name.upper().strip()

    parts: list[str] = []

    # Street match (case-insensitive via UPPER on both sides in SoQL).
    # SoQL uses `upper()` function for case-insensitive comparison.
    # Match street name anywhere in incident_address.
    parts.append(
        f"upper(incident_address) like upper('%{_soql_escape(street_upper)}%')"
    )

    if house_number:
        # FDNY pads house numbers and may use hyphens; match loosely.
        hn_clean = house_number.replace("-", "").lstrip("0") or house_number
        parts.append(
            f"(upper(incident_address) like '%{_soql_escape(house_number)}%' "
            f"OR upper(incident_address) like '%{_soql_escape(hn_clean)}%')"
        )

    if borough_fdny:
        parts.append(
            f"upper(borough_desc) = '{_soql_escape(borough_fdny)}'"
        )

    if incident_type:
        t = incident_type.upper()
        parts.append(
            f"upper(incident_type_desc) like '%{_soql_escape(t)}%'"
        )

    if since_year:
        parts.append(f"incident_date_time >= '{since_year}-01-01T00:00:00'")

    return " AND ".join(parts)


def _soql_escape(value: str) -> str:
    """Escape single quotes and percent signs for SoQL string literals."""
    # In SoQL, escape single quote by doubling it.
    return value.replace("'", "''").replace("%", "\\%")


# ── Main tool ─────────────────────────────────────────────────────────


@mcp.tool()
async def get_fdny_fire_incidents(
    address: str | None = None,
    bbl: str | None = None,
    incident_type: str | None = None,
    since_year: int | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Get FDNY fire and emergency incident history for a property address.

    Queries the FDNY Fire Incident Reporting System (NYC Open Data dataset
    8m42-w767) in real-time via the Socrata API. Returns fire incidents,
    structural fires, EMS responses, and other emergency calls associated
    with a specific address.

    Use this to identify fire history, structural fire risk, repeated
    emergency responses, or civilian/firefighter casualties at a property.

    Provide either `address` OR `bbl` (not both). If BBL is given, the
    tool resolves it to a street address before querying.

    Args:
        address: Street address, e.g. "37-06 80th Street, Queens" or
                 "350 5th Ave, Manhattan". Borough or zip code recommended
                 for unambiguous results.
        bbl: 10-digit NYC BBL, e.g. "4008020015". Alternative to address.
        incident_type: Filter by incident type keyword, e.g. "FIRE",
                       "STRUCTURAL FIRE", "EMS". Case-insensitive.
        since_year: Return only incidents from this year onward, e.g. 2018.
                    Data available from 2013.
        limit: Max incidents to return (1–100, default 20).
    """
    if not address and not bbl:
        raise ToolError("Provide either address or bbl.")
    if address and bbl:
        raise ToolError("Provide either address or bbl, not both.")
    if limit < 1 or limit > 100:
        raise ToolError("limit must be between 1 and 100.")
    if since_year is not None and (since_year < 2013 or since_year > 2030):
        raise ToolError("since_year must be between 2013 and 2030.")

    # ── Resolve BBL → address ─────────────────────────────────────────
    house_number = ""
    street_name = ""
    borough_fdny: str | None = None
    resolved_address: str | None = None

    if bbl:
        from nyc_property_intel.utils import validate_bbl

        try:
            borough_code, _, _ = validate_bbl(bbl)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

        addr_info = await _resolve_to_address(bbl)
        house_number = addr_info.get("house_number", "")
        street_name = addr_info.get("street_name", "")
        borough_fdny = _BOROUGH_CODE_TO_FDNY.get(addr_info.get("borough_code", ""))
        resolved_address = f"{house_number} {street_name}"

    else:
        # Parse the provided address string.
        # We'll use geoclient's parser for structured components.
        from nyc_property_intel.geoclient import parse_address

        try:
            parsed = parse_address(address)  # type: ignore[arg-type]
        except ToolError:
            # If parse fails, fall back to using the raw address as street name.
            resolved_address = address
            street_name = address
        else:
            house_number = parsed["house_number"]
            street_name = parsed["street"]
            borough_fdny = _BOROUGH_CODE_TO_FDNY.get(parsed["borough_code"])
            resolved_address = f"{house_number} {street_name}"

    # ── Build and execute Socrata query ───────────────────────────────
    where = _build_soql_where(
        house_number, street_name, borough_fdny, incident_type, since_year
    )

    params: dict[str, str] = {
        "$where": where,
        "$order": "incident_date_time DESC",
        "$limit": str(limit),
        "$select": (
            "incident_date_time,incident_type_desc,incident_address,"
            "borough_desc,zip_code,highest_level_desc,property_use_desc,"
            "action_taken1_desc,total_incident_duration,units_onscene,"
            "fire_spread_desc,deaths_firefighter,deaths_civilian,"
            "injuries_firefighter,injuries_civilian"
        ),
    }

    client = _get_socrata_client()
    url = f"/{_FDNY_DATASET}?{urllib.parse.urlencode(params)}"

    try:
        resp = await client.get(url)
        resp.raise_for_status()
    except httpx.TimeoutException as exc:
        raise ToolError(
            "FDNY / Socrata API timed out. Try again in a moment."
        ) from exc
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 403:
            raise ToolError(
                "Socrata API returned 403. Set SOCRATA_APP_TOKEN in .env for "
                "higher rate limits."
            ) from exc
        raise ToolError(
            f"Socrata API error (HTTP {exc.response.status_code})."
        ) from exc

    incidents: list[dict[str, Any]] = resp.json()

    # ── Summarize results ─────────────────────────────────────────────
    total = len(incidents)

    # Count fatalities / injuries across all returned incidents.
    total_deaths = sum(
        int(r.get("deaths_civilian") or 0) + int(r.get("deaths_firefighter") or 0)
        for r in incidents
    )
    total_injuries = sum(
        int(r.get("injuries_civilian") or 0)
        + int(r.get("injuries_firefighter") or 0)
        for r in incidents
    )
    structural_fires = sum(
        1
        for r in incidents
        if "FIRE" in (r.get("incident_type_desc") or "").upper()
    )

    return {
        "address_queried": resolved_address,
        "bbl": bbl,
        "total_returned": total,
        "summary": {
            "structural_fires": structural_fires,
            "total_deaths": total_deaths,
            "total_injuries": total_injuries,
        },
        "incidents": incidents,
        "data_source": "FDNY Fire Incident Reporting System (NYC Open Data 8m42-w767)",
        "data_note": "Real-time via Socrata API. Coverage from 2013. "
        "Address matching is approximate — verify BBL or full address for accuracy.",
    }
