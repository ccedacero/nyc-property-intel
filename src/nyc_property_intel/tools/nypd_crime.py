"""NYPD crime tool — complaint data near a property via NYC Open Data.

Queries the NYPD Complaint Data Historic dataset (Socrata `5uac-w243`)
using a geospatial radius search centered on the property's coordinates
(resolved from PLUTO lat/lon). Returns felonies, misdemeanors, and
violations within a configurable radius (default 300 m ≈ 3 city blocks).

Radius-based querying is more accurate than address string matching
for crime data because incidents are geocoded to the crime scene, not
the complainant's address.

Dataset: NYC Open Data `5uac-w243` (NYPD Complaint Data Historic, 2006–prior year)
Update cadence: updated annually (prior calendar year)
"""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp.exceptions import ToolError

from nyc_property_intel.app import mcp
from nyc_property_intel.socrata import SocrataError, query_socrata

logger = logging.getLogger(__name__)

_DATASET = "5uac-w243"


def _soql_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "''").replace("%", "\\%")


async def _resolve_lat_lon(
    bbl: str | None,
    address: str | None,
) -> tuple[float, float, str]:
    """Return (lat, lon, resolved_label) for a BBL or address.

    Priority:
      1. BBL → PLUTO lat/lon (most accurate, direct lot centroid)
      2. BBL → PAD address → GeoClient lat/lon
      3. Address → GeoClient lat/lon
      4. Address → PAD BBL → PLUTO lat/lon

    Raises ToolError if coordinates cannot be determined.
    """
    from nyc_property_intel.db import fetch_one

    # ── BBL path ──────────────────────────────────────────────────────
    if bbl:
        row = await fetch_one(
            "SELECT latitude, longitude, address FROM pluto_latest WHERE bbl = $1 LIMIT 1",
            bbl,
        )
        if row and row["latitude"] and row["longitude"]:
            label = row.get("address") or bbl
            try:
                return float(row["latitude"]), float(row["longitude"]), str(label)
            except (ValueError, TypeError):
                pass  # Fall through to geoclient fallback

        # Fallback: PAD address → geoclient
        pad_row = await fetch_one(
            "SELECT lhnd AS house_number, stname AS street_name, boro "
            "FROM pad_adr WHERE bbl = $1 LIMIT 1",
            bbl,
        )
        if pad_row:
            from nyc_property_intel.utils import BOROUGH_CODE_TO_NAME
            boro_name = BOROUGH_CODE_TO_NAME.get(str(pad_row["boro"]), "")
            addr_str = f"{pad_row['house_number']} {pad_row['street_name']}, {boro_name}"
            coords = await _geoclient_coords(addr_str)
            if coords:
                return coords[0], coords[1], addr_str

        raise ToolError(
            f"Could not determine coordinates for BBL {bbl}. "
            "Try passing the street address directly."
        )

    # ── Address path ──────────────────────────────────────────────────
    assert address is not None
    coords = await _geoclient_coords(address)
    if coords:
        return coords[0], coords[1], address

    # Last resort: resolve address → BBL → PLUTO
    from nyc_property_intel.geoclient import resolve_address_to_bbl
    try:
        resolved_bbl = await resolve_address_to_bbl(address)
        row = await fetch_one(
            "SELECT latitude, longitude FROM pluto_latest WHERE bbl = $1 LIMIT 1",
            resolved_bbl,
        )
        if row and row["latitude"] and row["longitude"]:
            try:
                return float(row["latitude"]), float(row["longitude"]), address
            except (ValueError, TypeError):
                pass
    except ToolError:
        pass

    raise ToolError(
        f"Could not determine coordinates for \"{address}\". "
        "Include a borough name or zip code and verify the house number."
    )


async def _geoclient_coords(address: str) -> tuple[float, float] | None:
    """Try GeoClient to get lat/lon for an address. Returns None on failure."""
    from nyc_property_intel.geoclient import parse_address, _call_geoclient
    try:
        parsed = parse_address(address)
        result = await _call_geoclient(
            parsed["house_number"], parsed["street"], parsed["borough_code"]
        )
        lat = result.get("latitude") or result.get("latitudeInternalLabel")
        lon = result.get("longitude") or result.get("longitudeInternalLabel")
        if lat and lon:
            return float(lat), float(lon)
    except (ToolError, ValueError, KeyError, TypeError):
        pass
    return None


@mcp.tool()
async def get_nypd_crime(
    address: str | None = None,
    bbl: str | None = None,
    radius_meters: int = 300,
    law_category: str | None = None,
    offense: str | None = None,
    since_year: int | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Get NYPD crime complaints within a radius of a property.

    Queries the NYPD Complaint Data Historic dataset (NYC Open Data) using a
    geospatial radius search centered on the property's lat/lon. Returns all
    complaint types — felonies, misdemeanors, and violations — filed within
    the specified radius.

    Uses the property's PLUTO coordinates (lot centroid) for accuracy.
    Default radius of 300 m covers roughly 3 city blocks in any direction.

    Use this to assess neighborhood safety for buyers, lenders, or underwriters.
    Compare felony vs misdemeanor breakdown, trend over years, and dominant
    offense types (assault, burglary, grand larceny, etc.).

    Provide either `address` OR `bbl` (not both).

    Args:
        address: Street address, e.g. "350 5th Ave, Manhattan".
        bbl: 10-digit NYC BBL. Coordinates resolved from PLUTO.
        radius_meters: Search radius in meters (50–800, default 300 ≈ 3 blocks).
        law_category: Filter by "FELONY", "MISDEMEANOR", or "VIOLATION".
                      Case-insensitive.
        offense: Filter by offense keyword, e.g. "ASSAULT", "BURGLARY",
                 "GRAND LARCENY", "ROBBERY". Case-insensitive.
        since_year: Return only complaints from this year onward (2006–present).
        limit: Max complaints to return (1–200, default 50).
    """
    if not address and not bbl:
        raise ToolError("Provide either address or bbl.")
    if address and bbl:
        raise ToolError("Provide either address or bbl, not both.")
    if not (50 <= radius_meters <= 800):
        raise ToolError("radius_meters must be between 50 and 800.")
    if limit < 1 or limit > 200:
        raise ToolError("limit must be between 1 and 200.")
    if since_year is not None and (since_year < 2006 or since_year > 2030):
        raise ToolError("since_year must be between 2006 and 2030.")
    if law_category is not None and law_category.upper() not in ("FELONY", "MISDEMEANOR", "VIOLATION"):
        raise ToolError("law_category must be 'FELONY', 'MISDEMEANOR', or 'VIOLATION'.")
    if offense is not None and len(offense) > 100:
        raise ToolError("offense must be 100 characters or fewer.")

    # ── Resolve coordinates ───────────────────────────────────────────
    lat, lon, label = await _resolve_lat_lon(bbl, address)

    # ── Build SoQL WHERE ─────────────────────────────────────────────
    # within_circle(location_field, lat, lon, radius_meters)
    parts: list[str] = [
        f"within_circle(lat_lon, {lat}, {lon}, {radius_meters})"
    ]
    if law_category:
        parts.append(
            f"upper(law_cat_cd) = '{_soql_escape(law_category.upper())}'"
        )
    if offense:
        parts.append(
            f"upper(ofns_desc) like '%{_soql_escape(offense.upper())}%'"
        )
    if since_year:
        parts.append(f"cmplnt_fr_dt >= '{int(since_year)}-01-01T00:00:00'")

    try:
        incidents: list[dict[str, Any]] = await query_socrata(
            _DATASET,
            where=" AND ".join(parts),
            limit=limit,
            order="cmplnt_fr_dt DESC",
            select=(
                "cmplnt_num,cmplnt_fr_dt,ofns_desc,pd_desc,law_cat_cd,"
                "crm_atpt_cptd_cd,prem_typ_desc,boro_nm,addr_pct_cd,"
                "loc_of_occur_desc,latitude,longitude"
            ),
        )
    except SocrataError as exc:
        raise ToolError(str(exc)) from exc

    # ── Summarize ─────────────────────────────────────────────────────
    felonies = sum(1 for i in incidents if (i.get("law_cat_cd") or "").upper() == "FELONY")
    misdemeanors = sum(1 for i in incidents if (i.get("law_cat_cd") or "").upper() == "MISDEMEANOR")
    violations = sum(1 for i in incidents if (i.get("law_cat_cd") or "").upper() == "VIOLATION")

    offense_counts: dict[str, int] = {}
    for i in incidents:
        o = i.get("ofns_desc") or "UNKNOWN"
        offense_counts[o] = offense_counts.get(o, 0) + 1
    top_offenses = sorted(offense_counts.items(), key=lambda x: x[1], reverse=True)[:8]

    # Year breakdown
    year_counts: dict[str, int] = {}
    for i in incidents:
        dt = i.get("cmplnt_fr_dt") or ""
        year = dt[:4] if len(dt) >= 4 else "unknown"
        year_counts[year] = year_counts.get(year, 0) + 1

    return {
        "address_queried": label,
        "bbl": bbl,
        "coordinates": {"latitude": lat, "longitude": lon},
        "radius_meters": radius_meters,
        "total_returned": len(incidents),
        "summary": {
            "felonies": felonies,
            "misdemeanors": misdemeanors,
            "violations": violations,
            "top_offenses": [
                {"offense": o, "count": c} for o, c in top_offenses
            ],
            "by_year": dict(sorted(year_counts.items(), reverse=True)),
        },
        "incidents": incidents,
        "data_source": "NYPD Complaint Data Historic (NYC Open Data 5uac-w243)",
        "data_note": (
            f"Geospatial radius search: {radius_meters} m from property coordinates. "
            "Historic dataset covers 2006 through prior calendar year. "
            "For current year, results may be incomplete."
        ),
    }
