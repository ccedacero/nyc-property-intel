"""311 Service Requests tool — neighborhood complaint signals via NYC Open Data.

Queries the NYC 311 Service Requests dataset (Socrata `erm2-nwe9`) in
real-time. Covers noise, illegal dumping, graffiti, rodents, illegal
parking, street conditions, and ~200 other complaint types filed near
or at a specific address.

311 complaints are a leading neighborhood-quality indicator: they surface
issues before agencies respond and before violations are issued.

Dataset: NYC Open Data `erm2-nwe9`
Update cadence: real-time (updated daily)
"""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp.exceptions import ToolError

from nyc_property_intel.app import mcp
from nyc_property_intel.socrata import SocrataError, query_socrata

logger = logging.getLogger(__name__)

_DATASET = "erm2-nwe9"


def _soql_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "''").replace("%", "\\%")


@mcp.tool()
async def get_311_complaints(
    address: str | None = None,
    bbl: str | None = None,
    complaint_type: str | None = None,
    since_year: int | None = None,
    status: str | None = None,
    limit: int = 30,
) -> dict[str, Any]:
    """Get 311 service request complaints filed at or near a property address.

    Queries the NYC 311 Service Requests dataset (NYC Open Data) in real-time.
    Covers noise, rodents, illegal dumping, graffiti, heat/hot water, illegal
    parking, street conditions, and ~200 other complaint types.

    311 data is a leading-indicator for neighborhood quality and building
    distress — complaints are filed *before* violations are issued. High
    complaint volume at an address is a red flag for active tenant issues.

    Provide either `address` OR `bbl` (not both).

    Args:
        address: Street address, e.g. "37-06 80th Street, Queens".
        bbl: 10-digit NYC BBL. Resolved to street address via PAD table.
        complaint_type: Filter by complaint type keyword, e.g. "NOISE",
                        "RODENT", "HEAT", "ILLEGAL PARKING". Case-insensitive.
        since_year: Return only complaints from this year onward (2010–present).
        status: Filter by status: "Open" or "Closed".
        limit: Max complaints to return (1–100, default 30).
    """
    if not address and not bbl:
        raise ToolError("Provide either address or bbl.")
    if address and bbl:
        raise ToolError("Provide either address or bbl, not both.")
    if limit < 1 or limit > 100:
        raise ToolError("limit must be between 1 and 100.")
    if since_year is not None and (since_year < 2010 or since_year > 2030):
        raise ToolError("since_year must be between 2010 and 2030.")
    if status is not None and status.upper() not in ("OPEN", "CLOSED"):
        raise ToolError("status must be 'Open' or 'Closed'.")
    if complaint_type is not None and len(complaint_type) > 100:
        raise ToolError("complaint_type must be 100 characters or fewer.")

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
    parts: list[str] = [
        f"upper(incident_address) like upper('%{_soql_escape(street_name)}%')"
    ]
    if house_number:
        hn_clean = house_number.replace("-", "").lstrip("0") or house_number
        parts.append(
            f"(upper(incident_address) like '%{_soql_escape(house_number)}%' "
            f"OR upper(incident_address) like '%{_soql_escape(hn_clean)}%')"
        )
    if complaint_type:
        parts.append(
            f"upper(complaint_type) like '%{_soql_escape(complaint_type.upper())}%'"
        )
    if since_year:
        parts.append(f"created_date >= '{int(since_year)}-01-01T00:00:00'")
    if status:
        parts.append(f"upper(status) = '{_soql_escape(status.upper())}'")

    try:
        complaints: list[dict[str, Any]] = await query_socrata(
            _DATASET,
            where=" AND ".join(parts),
            limit=limit,
            order="created_date DESC",
            select=(
                "unique_key,created_date,closed_date,complaint_type,descriptor,"
                "incident_address,borough,status,resolution_description,agency_name"
            ),
        )
    except SocrataError as exc:
        raise ToolError(str(exc)) from exc

    # ── Summarize ─────────────────────────────────────────────────────
    open_count = sum(1 for c in complaints if (c.get("status") or "").upper() == "OPEN")
    type_counts: dict[str, int] = {}
    for c in complaints:
        ct = c.get("complaint_type") or "Unknown"
        type_counts[ct] = type_counts.get(ct, 0) + 1
    top_types = sorted(type_counts.items(), key=lambda x: x[1], reverse=True)[:5]

    return {
        "address_queried": resolved_address,
        "bbl": bbl,
        "total_returned": len(complaints),
        "summary": {
            "open": open_count,
            "closed": len(complaints) - open_count,
            "top_complaint_types": [
                {"type": t, "count": c} for t, c in top_types
            ],
        },
        "complaints": complaints,
        "data_source": "NYC 311 Service Requests (NYC Open Data erm2-nwe9)",
        "data_note": "Real-time via Socrata API. Address matching is approximate.",
    }
