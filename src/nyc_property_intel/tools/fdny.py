"""FDNY fire incident tool — fire incident history from NYC Open Data.

Queries the local fdny_incidents table (bulk-loaded from NYC Open Data
dataset 8m42-w767). Falls back to the Socrata API if the local table is
unavailable. Covers fire incidents, EMS calls, and other emergency
responses reported by FDNY since 2013.

Note: local data matches by zipcode + borough. Socrata fallback provides
finer-grained address matching when needed.

Dataset: NYC Open Data `8m42-w767`
Source: FDNY Fire Incident Reporting System
Update cadence: bulk refresh (local) or annual (Socrata)
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

_SOCRATA_DATASET = "8m42-w767"

# FDNY borough names in both Socrata and local data
_BOROUGH_CODE_TO_FDNY: dict[str, str] = {
    "1": "MANHATTAN",
    "2": "BRONX",
    "3": "BROOKLYN",
    "4": "QUEENS",
    "5": "RICHMOND",
}

# ── SQL ───────────────────────────────────────────────────────────────────────

# Local data has zipcode + incident_borough but no exact street address.
# Match by zipcode (required) + optional borough/classification/year filters.
_SQL_LOCAL = """\
SELECT
    starfire_incident_id,
    incident_datetime,
    alarm_box_location,
    incident_borough,
    zipcode,
    incident_classification,
    incident_classification_group,
    highest_alarm_level,
    engines_assigned_quantity,
    ladders_assigned_quantity,
    other_units_assigned_quantity
FROM fdny_incidents
WHERE zipcode = $1
  AND ($2::text IS NULL OR upper(incident_borough) = upper($2))
  AND ($3::text IS NULL OR upper(incident_classification) LIKE '%' || upper($3) || '%'
       OR upper(incident_classification_group) LIKE '%' || upper($3) || '%')
  AND ($4::text IS NULL OR incident_datetime >= $4)
ORDER BY incident_datetime DESC
LIMIT $5;
"""


def _since_prefix(since_year: int | None) -> str | None:
    return f"{since_year}-01-01" if since_year else None


def _soql_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "''").replace("%", "\\%")


async def _resolve_to_address(bbl: str) -> dict[str, str]:
    """Resolve a BBL to address + borough + zip using PAD table."""
    row = await fetch_one(
        """
        SELECT lhnd AS house_number, stname AS street_name,
               boro AS borough_code, zipcode AS zip_code
        FROM pad_adr WHERE bbl = $1 LIMIT 1
        """,
        bbl,
    )
    if row is None:
        raise ToolError(
            f"Could not find an address for BBL {bbl}. "
            "Try passing the street address directly."
        )
    return dict(row)


def _build_soql_where(
    zip_code: str | None,
    borough_fdny: str | None,
    incident_type: str | None,
    since_year: int | None,
) -> str:
    # FDNY dataset (8m42-w767) has no street address field — filter by zip/borough only.
    parts: list[str] = []
    if zip_code:
        parts.append(f"zipcode = '{_soql_escape(zip_code)}'")
    if borough_fdny:
        parts.append(f"upper(incident_borough) = upper('{_soql_escape(borough_fdny)}')")
    if incident_type:
        t = incident_type.upper()
        parts.append(
            f"(upper(incident_classification) like '%{_soql_escape(t)}%' "
            f"OR upper(incident_classification_group) like '%{_soql_escape(t)}%')"
        )
    if since_year:
        parts.append(f"incident_datetime >= '{int(since_year)}-01-01T00:00:00'")
    return " AND ".join(parts) if parts else "starfire_incident_id IS NOT NULL"


def _summarize_local(incidents: list[dict[str, Any]]) -> dict[str, Any]:
    fire_keywords = {"FIRE", "STRUCTURAL FIRE", "OUTSIDE FIRE", "VEHICLE FIRE"}
    structural_fires = sum(
        1 for i in incidents
        if any(k in (i.get("incident_classification") or "").upper() for k in fire_keywords)
    )
    class_counts: dict[str, int] = {}
    for i in incidents:
        cls = i.get("incident_classification_group") or "Unknown"
        class_counts[cls] = class_counts.get(cls, 0) + 1
    top_types = sorted(class_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    return {
        "structural_fires": structural_fires,
        "top_incident_types": [{"type": t, "count": c} for t, c in top_types],
    }


def _summarize_socrata(incidents: list[dict[str, Any]]) -> dict[str, Any]:
    structural_fires = sum(
        1 for r in incidents
        if "FIRE" in (r.get("incident_type_desc") or "").upper()
    )
    total_deaths = sum(
        int(r.get("deaths_civilian") or 0) + int(r.get("deaths_firefighter") or 0)
        for r in incidents
    )
    total_injuries = sum(
        int(r.get("injuries_civilian") or 0) + int(r.get("injuries_firefighter") or 0)
        for r in incidents
    )
    return {
        "structural_fires": structural_fires,
        "total_deaths": total_deaths,
        "total_injuries": total_injuries,
    }


@mcp.tool()
async def get_fdny_fire_incidents(
    address: str | None = None,
    bbl: str | None = None,
    incident_type: str | None = None,
    since_year: int | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Get FDNY fire and emergency incident history for a property address.

    Queries the local FDNY incident database (NYC Open Data dataset 8m42-w767).
    Returns fire incidents, structural fires, EMS responses, and other emergency
    calls associated with a property's zip code and borough. Falls back to the
    Socrata API for finer-grained address matching if local table unavailable.

    Use this to identify fire history, structural fire risk, repeated emergency
    responses, or patterns of emergency calls at a property's location.

    Provide either `address` OR `bbl` (not both). If BBL is given, the tool
    resolves it to a zip code before querying.

    Args:
        address: Street address, e.g. "37-06 80th Street, Queens" or
                 "350 5th Ave, Manhattan". Borough or zip code recommended.
        bbl: 10-digit NYC BBL, e.g. "4008020015". Alternative to address.
        incident_type: Filter by incident type keyword, e.g. "FIRE",
                       "STRUCTURAL", "EMS", "MEDICAL". Case-insensitive.
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

    house_number = ""
    street_name = ""
    borough_fdny: str | None = None
    zip_code: str | None = None
    resolved_address: str | None = None

    # ── Resolve BBL → address + zip ───────────────────────────────────
    if bbl:
        from nyc_property_intel.utils import validate_bbl
        try:
            validate_bbl(bbl)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

        addr_info = await _resolve_to_address(bbl)
        house_number = (addr_info.get("house_number") or "").strip()
        street_name = (addr_info.get("street_name") or "").strip()
        zip_code = addr_info.get("zip_code")
        borough_fdny = _BOROUGH_CODE_TO_FDNY.get(str(addr_info.get("borough_code", "")))
        resolved_address = f"{house_number} {street_name}"

    else:
        from nyc_property_intel.geoclient import parse_address
        try:
            parsed = parse_address(address)  # type: ignore[arg-type]
            house_number = parsed["house_number"]
            street_name = parsed["street"]
            borough_fdny = _BOROUGH_CODE_TO_FDNY.get(parsed.get("borough_code", ""))
            zip_code = parsed.get("zip_code")
            resolved_address = f"{house_number} {street_name}"
        except ToolError:
            resolved_address = address
            street_name = address or ""

        # If no zip from geoclient, try resolving via Geoclient for zip
        if not zip_code and resolved_address:
            try:
                from nyc_property_intel.geoclient import resolve_address_to_bbl
                from nyc_property_intel.db import fetch_one as _fetch_one
                addr_bbl = await resolve_address_to_bbl(resolved_address)
                row = await _fetch_one(
                    "SELECT zipcode FROM pad_adr WHERE bbl = $1 LIMIT 1", addr_bbl
                )
                if row:
                    zip_code = row["zipcode"]
            except (ToolError, Exception):
                pass

    # ── Local DB query (zip-based) ────────────────────────────────────
    if zip_code:
        try:
            incidents = await fetch_all(
                _SQL_LOCAL,
                zip_code,
                borough_fdny,
                incident_type,
                _since_prefix(since_year),
                limit,
            )
            return {
                "address_queried": resolved_address,
                "bbl": bbl,
                "zip_code": zip_code,
                "total_returned": len(incidents),
                "summary": _summarize_local(incidents),
                "incidents": [dict(i) for i in incidents],
                "data_source": "FDNY Fire Incident Reporting System — local DB (NYC Open Data 8m42-w767)",
                "data_note": (
                    "Local bulk dataset. Results filtered by zip code + borough — "
                    "includes all incidents in the zip, not just at this specific address."
                ),
            }
        except asyncpg.UndefinedTableError:
            logger.info("fdny_incidents table not found — falling back to Socrata")

    # ── Socrata fallback (zip/borough-level, no street address in dataset) ──
    logger.info("FDNY: using Socrata fallback (no zip resolved or table missing)")
    where = _build_soql_where(zip_code, borough_fdny, incident_type, since_year)
    try:
        incidents_raw: list[dict[str, Any]] = await query_socrata(
            _SOCRATA_DATASET,
            where=where,
            limit=limit,
            order="incident_datetime DESC",
            select=(
                "starfire_incident_id,incident_datetime,incident_borough,"
                "zipcode,alarm_box_location,incident_classification,"
                "incident_classification_group,highest_alarm_level,"
                "engines_assigned_quantity,ladders_assigned_quantity,"
                "other_units_assigned_quantity"
            ),
        )
    except SocrataError as exc:
        raise ToolError(str(exc)) from exc

    return {
        "address_queried": resolved_address,
        "bbl": bbl,
        "zip_code": zip_code,
        "total_returned": len(incidents_raw),
        "summary": _summarize_local(incidents_raw),
        "incidents": incidents_raw,
        "data_source": "FDNY Fire Incident Reporting System via Socrata API (8m42-w767)",
        "data_note": (
            "Socrata API fallback. The FDNY dataset has no street address field — "
            "results are filtered by zip code and/or borough, not exact address."
        ),
    }
