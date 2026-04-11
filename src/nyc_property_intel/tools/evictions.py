"""Evictions tool — marshal eviction executions via NYC Open Data.

Queries the NYC Evictions dataset (Socrata `6z8x-wfk4`) in real-time.
Covers residential and commercial evictions executed by city marshals,
including executed date, docket number, and residential/commercial type.

An eviction record means the eviction was *executed* (marshal physically
removed the tenant), not just filed. High eviction rates at a building
signal tenant instability, cash-flow risk, and potential landlord distress.

Dataset: NYC Open Data `6z8x-wfk4`
Update cadence: updated monthly
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

_SOCRATA_BASE = "https://data.cityofnewyork.us/resource"
_DATASET = "6z8x-wfk4.json"

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        headers = {"Accept": "application/json"}
        if settings.socrata_app_token:
            headers["X-App-Token"] = settings.socrata_app_token
        _client = httpx.AsyncClient(
            base_url=_SOCRATA_BASE,
            timeout=httpx.Timeout(20.0, connect=5.0),
            headers=headers,
        )
    return _client


async def close_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
        _client = None


def _soql_escape(value: str) -> str:
    return value.replace("'", "''").replace("%", "\\%")


@mcp.tool()
async def get_evictions(
    address: str | None = None,
    bbl: str | None = None,
    eviction_type: str | None = None,
    since_year: int | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    """Get marshal eviction execution records for a property address.

    Queries the NYC Evictions dataset (NYC Open Data) in real-time. Returns
    evictions that were *executed* (marshal removed tenant), not just filed.
    Covers residential and commercial evictions citywide.

    Use this to assess tenant instability and cash-flow risk. A building
    with many executed evictions may indicate distressed management,
    problematic tenants, or an owner pushing out rent-stabilized tenants.

    Provide either `address` OR `bbl` (not both).

    Args:
        address: Street address, e.g. "123 Main St, Brooklyn".
        bbl: 10-digit NYC BBL. Resolved to street address via PAD table.
        eviction_type: Filter by type: "Residential" or "Commercial".
        since_year: Return only evictions from this year onward (2017–present).
        limit: Max records to return (1–100, default 25).
    """
    if not address and not bbl:
        raise ToolError("Provide either address or bbl.")
    if address and bbl:
        raise ToolError("Provide either address or bbl, not both.")
    if limit < 1 or limit > 100:
        raise ToolError("limit must be between 1 and 100.")
    if since_year is not None and (since_year < 2017 or since_year > 2030):
        raise ToolError("since_year must be between 2017 and 2030.")

    # ── Resolve to street address ─────────────────────────────────────
    house_number = ""
    street_name = ""
    resolved_address: str | None = None

    if bbl:
        from nyc_property_intel.utils import validate_bbl
        from nyc_property_intel.db import fetch_one

        try:
            validate_bbl(bbl)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

        row = await fetch_one(
            "SELECT lhnd AS house_number, stname AS street_name "
            "FROM pad_adr WHERE bbl = $1 LIMIT 1",
            bbl,
        )
        if row is None:
            raise ToolError(
                f"Could not find an address for BBL {bbl}. "
                "Try passing the street address directly."
            )
        house_number = row["house_number"]
        street_name = row["street_name"]
        resolved_address = f"{house_number} {street_name}"
    else:
        from nyc_property_intel.geoclient import parse_address

        try:
            parsed = parse_address(address)  # type: ignore[arg-type]
            house_number = parsed["house_number"]
            street_name = parsed["street"]
            resolved_address = f"{house_number} {street_name}"
        except ToolError:
            resolved_address = address
            street_name = address

    # ── Build SoQL WHERE ─────────────────────────────────────────────
    # The evictions dataset uses `eviction_address` for the street address.
    parts: list[str] = [
        f"upper(eviction_address) like upper('%{_soql_escape(street_name)}%')"
    ]
    if house_number:
        hn_clean = house_number.replace("-", "").lstrip("0") or house_number
        parts.append(
            f"(upper(eviction_address) like '%{_soql_escape(house_number)}%' "
            f"OR upper(eviction_address) like '%{_soql_escape(hn_clean)}%')"
        )
    if eviction_type:
        parts.append(
            f"upper(residential_commercial_ind) = '{_soql_escape(eviction_type.upper())}'"
        )
    if since_year:
        parts.append(f"executed_date >= '{since_year}-01-01T00:00:00'")

    params: dict[str, str] = {
        "$where": " AND ".join(parts),
        "$order": "executed_date DESC",
        "$limit": str(limit),
        "$select": (
            "court_index_number,docket_number,eviction_address,eviction_apt_num,"
            "executed_date,marshal_first_name,marshal_last_name,"
            "residential_commercial_ind,borough,zip"
        ),
    }

    client = _get_client()
    url = f"/{_DATASET}?{urllib.parse.urlencode(params)}"

    try:
        resp = await client.get(url)
        resp.raise_for_status()
    except httpx.TimeoutException as exc:
        raise ToolError("NYC Evictions / Socrata API timed out. Try again in a moment.") from exc
    except httpx.HTTPStatusError as exc:
        raise ToolError(
            f"Socrata API error (HTTP {exc.response.status_code})."
        ) from exc

    evictions: list[dict[str, Any]] = resp.json()

    # ── Summarize ─────────────────────────────────────────────────────
    residential = sum(
        1 for e in evictions
        if (e.get("residential_commercial_ind") or "").upper() == "RESIDENTIAL"
    )
    commercial = len(evictions) - residential

    # Count unique apartments to show unit-level spread
    unique_units = len({
        e.get("eviction_apt_num") for e in evictions
        if e.get("eviction_apt_num")
    })

    return {
        "address_queried": resolved_address,
        "bbl": bbl,
        "total_returned": len(evictions),
        "summary": {
            "residential_evictions": residential,
            "commercial_evictions": commercial,
            "unique_units_affected": unique_units,
        },
        "evictions": evictions,
        "data_source": "NYC Evictions — Marshal Executions (NYC Open Data 6z8x-wfk4)",
        "data_note": (
            "Real-time via Socrata API. Data from 2017. "
            "Records are executed evictions (marshal removed tenant), not filings. "
            "Address matching is approximate."
        ),
    }
