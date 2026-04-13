"""311 Service Requests tool — neighborhood complaint signals via NYC Open Data.

Queries the local nyc_311_complaints table (bulk-loaded from NYC Open Data
dataset erm2-nwe9). Falls back to the Socrata API if the local table is
unavailable. Covers noise, illegal dumping, graffiti, rodents, illegal
parking, street conditions, and ~200 other complaint types filed near or
at a specific address.

311 complaints are a leading neighborhood-quality indicator: they surface
issues before agencies respond and before violations are issued.

Dataset: NYC Open Data `erm2-nwe9`
Update cadence: bulk refresh (local) or real-time (Socrata fallback)
"""

from __future__ import annotations

import logging
from typing import Any

import asyncpg
from mcp.server.fastmcp.exceptions import ToolError

from nyc_property_intel.app import mcp
from nyc_property_intel.db import fetch_all, fetch_one
from nyc_property_intel.socrata import SocrataError, query_socrata

logger = logging.getLogger(__name__)

_SOCRATA_DATASET = "erm2-nwe9"

# ── SQL ───────────────────────────────────────────────────────────────────────

# Direct BBL lookup — fastest path, exact match
_SQL_BY_BBL = """\
SELECT unique_key, created_date, closed_date, complaint_type, descriptor,
       incident_address, borough, status, resolution_description, agency_name
FROM nyc_311_complaints
WHERE bbl = $1
  AND ($2::text IS NULL OR upper(complaint_type) LIKE '%' || upper($2) || '%')
  AND ($3::text IS NULL OR created_date >= $3)
  AND ($4::text IS NULL OR upper(status) = upper($4))
ORDER BY created_date DESC
LIMIT $5;
"""

# Address text search — matches house number + street name
_SQL_BY_ADDRESS = """\
SELECT unique_key, created_date, closed_date, complaint_type, descriptor,
       incident_address, borough, status, resolution_description, agency_name
FROM nyc_311_complaints
WHERE upper(incident_address) LIKE '%' || upper($1) || '%'
  AND ($2::text IS NULL OR upper(incident_address) LIKE '%' || upper($2) || '%')
  AND ($3::text IS NULL OR upper(complaint_type) LIKE '%' || upper($3) || '%')
  AND ($4::text IS NULL OR created_date >= $4)
  AND ($5::text IS NULL OR upper(status) = upper($5))
ORDER BY created_date DESC
LIMIT $6;
"""


def _since_prefix(since_year: int | None) -> str | None:
    """Convert since_year to an ISO date prefix for text comparison."""
    return f"{since_year}-01-01" if since_year else None


def _soql_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "''").replace("%", "\\%")


def _summarize(complaints: list[dict[str, Any]]) -> dict[str, Any]:
    open_count = sum(1 for c in complaints if (c.get("status") or "").upper() == "OPEN")
    type_counts: dict[str, int] = {}
    for c in complaints:
        ct = c.get("complaint_type") or "Unknown"
        type_counts[ct] = type_counts.get(ct, 0) + 1
    top_types = sorted(type_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    return {
        "open": open_count,
        "closed": len(complaints) - open_count,
        "top_complaint_types": [{"type": t, "count": c} for t, c in top_types],
    }


async def _query_local_by_bbl(
    bbl: str,
    complaint_type: str | None,
    since_year: int | None,
    status: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    return await fetch_all(
        _SQL_BY_BBL,
        bbl,
        complaint_type,
        _since_prefix(since_year),
        status,
        limit,
    )


async def _query_local_by_address(
    street_name: str,
    house_number: str | None,
    complaint_type: str | None,
    since_year: int | None,
    status: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    return await fetch_all(
        _SQL_BY_ADDRESS,
        street_name,
        house_number,
        complaint_type,
        _since_prefix(since_year),
        status,
        limit,
    )


async def _query_socrata_fallback(
    street_name: str,
    house_number: str,
    complaint_type: str | None,
    since_year: int | None,
    status: str | None,
    limit: int,
) -> list[dict[str, Any]]:
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

    return await query_socrata(
        _SOCRATA_DATASET,
        where=" AND ".join(parts),
        limit=limit,
        order="created_date DESC",
        select=(
            "unique_key,created_date,closed_date,complaint_type,descriptor,"
            "incident_address,borough,status,resolution_description,agency_name"
        ),
    )


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

    Queries the local 311 database (NYC Open Data). Covers noise, rodents,
    illegal dumping, graffiti, heat/hot water, illegal parking, street
    conditions, and ~200 other complaint types.

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

    house_number = ""
    street_name = ""
    resolved_address: str | None = None
    data_source = "NYC 311 Service Requests — local DB (NYC Open Data erm2-nwe9)"
    data_note = "Local bulk dataset. Address matching is approximate."

    # ── BBL path: direct lookup ───────────────────────────────────────
    if bbl:
        from nyc_property_intel.utils import validate_bbl
        try:
            validate_bbl(bbl)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

        # Try local DB by BBL first (fastest, most accurate)
        try:
            complaints = await _query_local_by_bbl(
                bbl, complaint_type, since_year, status, limit
            )
            return {
                "address_queried": bbl,
                "bbl": bbl,
                "total_returned": len(complaints),
                "summary": _summarize(complaints),
                "complaints": [dict(c) for c in complaints],
                "data_source": data_source,
                "data_note": data_note,
            }
        except asyncpg.UndefinedTableError:
            logger.info("nyc_311_complaints not found — falling back to Socrata")

        # BBL had no 311 hits or table missing: resolve address for Socrata fallback
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

    # ── Address path ──────────────────────────────────────────────────
    else:
        from nyc_property_intel.geoclient import parse_address
        try:
            parsed = parse_address(address)  # type: ignore[arg-type]
            house_number = parsed["house_number"]
            street_name = parsed["street"]
            resolved_address = f"{house_number} {street_name}"
        except ToolError:
            resolved_address = address
            street_name = address or ""

    # ── Local address search ──────────────────────────────────────────
    try:
        complaints = await _query_local_by_address(
            street_name, house_number or None,
            complaint_type, since_year, status, limit,
        )
        return {
            "address_queried": resolved_address,
            "bbl": bbl,
            "total_returned": len(complaints),
            "summary": _summarize(complaints),
            "complaints": [dict(c) for c in complaints],
            "data_source": data_source,
            "data_note": data_note,
        }
    except asyncpg.UndefinedTableError:
        logger.info("nyc_311_complaints not found — falling back to Socrata")

    # ── Socrata fallback ──────────────────────────────────────────────
    try:
        complaints_raw = await _query_socrata_fallback(
            street_name, house_number, complaint_type, since_year, status, limit
        )
    except SocrataError as exc:
        raise ToolError(str(exc)) from exc

    return {
        "address_queried": resolved_address,
        "bbl": bbl,
        "total_returned": len(complaints_raw),
        "summary": _summarize(complaints_raw),
        "complaints": complaints_raw,
        "data_source": "NYC 311 Service Requests via Socrata API (erm2-nwe9)",
        "data_note": "Real-time via Socrata API (local table unavailable).",
    }
