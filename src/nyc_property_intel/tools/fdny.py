"""FDNY fire incident tool — fire incident history from NYC Open Data.

Queries the FDNY Fire Incident Reporting System (NFIRS) dataset via the
Socrata Open Data API. Covers fire incidents, EMS calls, and other
emergency responses reported by FDNY since 2013.

Dataset: NYC Open Data `8m42-w767`
Source: FDNY Fire Incident Reporting System
Update cadence: updated annually
"""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp.exceptions import ToolError

from nyc_property_intel.app import mcp
from nyc_property_intel.socrata import SocrataError, query_socrata

logger = logging.getLogger(__name__)

_DATASET = "8m42-w767"

# FDNY uses "MANHATTAN", "BRONX", "BROOKLYN", "QUEENS", "RICHMOND" (Staten Island)
_BOROUGH_CODE_TO_FDNY: dict[str, str] = {
    "1": "MANHATTAN",
    "2": "BRONX",
    "3": "BROOKLYN",
    "4": "QUEENS",
    "5": "RICHMOND",
}


async def _resolve_to_address(bbl: str) -> dict[str, str]:
    """Resolve a BBL to a street address + borough using the PLUTO/PAD tables."""
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


def _soql_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "''").replace("%", "\\%")


def _build_soql_where(
    house_number: str,
    street_name: str,
    borough_fdny: str | None,
    incident_type: str | None,
    since_year: int | None,
) -> str:
    """Build a SoQL WHERE clause for the FDNY dataset."""
    street_upper = street_name.upper().strip()
    parts: list[str] = [
        f"upper(incident_address) like upper('%{_soql_escape(street_upper)}%')"
    ]

    if house_number:
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
        parts.append(f"incident_date_time >= '{int(since_year)}-01-01T00:00:00'")

    return " AND ".join(parts)


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
    if incident_type is not None and len(incident_type) > 100:
        raise ToolError("incident_type must be 100 characters or fewer.")

    # ── Resolve BBL → address ─────────────────────────────────────────
    house_number = ""
    street_name = ""
    borough_fdny: str | None = None
    resolved_address: str | None = None

    if bbl:
        from nyc_property_intel.utils import validate_bbl

        try:
            validate_bbl(bbl)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

        addr_info = await _resolve_to_address(bbl)
        house_number = addr_info.get("house_number", "")
        street_name = addr_info.get("street_name", "")
        borough_fdny = _BOROUGH_CODE_TO_FDNY.get(addr_info.get("borough_code", ""))
        resolved_address = f"{house_number} {street_name}"

    else:
        from nyc_property_intel.geoclient import parse_address

        try:
            parsed = parse_address(address)  # type: ignore[arg-type]
        except ToolError:
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

    try:
        incidents: list[dict[str, Any]] = await query_socrata(
            _DATASET,
            where=where,
            limit=limit,
            order="incident_date_time DESC",
            select=(
                "incident_date_time,incident_type_desc,incident_address,"
                "borough_desc,zip_code,highest_level_desc,property_use_desc,"
                "action_taken1_desc,total_incident_duration,units_onscene,"
                "fire_spread_desc,deaths_firefighter,deaths_civilian,"
                "injuries_firefighter,injuries_civilian"
            ),
        )
    except SocrataError as exc:
        raise ToolError(str(exc)) from exc

    # ── Summarize results ─────────────────────────────────────────────
    total = len(incidents)

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
