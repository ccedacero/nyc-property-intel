"""DOB complaints tool — complaints filed with the Dept of Buildings.

Primary data source: local PostgreSQL `dob_complaints` table (loaded from
NYC Open Data `eabe-havv` via nycdb). Falls back to the Socrata API if the
table is not yet populated (e.g. before the first Railway sync).

Local queries use the BIN (Building Identification Number) from `pad_adr`
for exact, indexed lookups — far more accurate than address string matching.

Dataset: NYC Open Data `eabe-havv`
Update cadence: updated daily
"""

from __future__ import annotations

import logging
from typing import Any

import asyncpg
from mcp.server.fastmcp.exceptions import ToolError

from nyc_property_intel.app import mcp
from nyc_property_intel.socrata import SocrataError, query_socrata

logger = logging.getLogger(__name__)

# ── Socrata fallback constants ────────────────────────────────────────
_SOCRATA_DATASET = "eabe-havv"

# DOB complaint category codes → human-readable descriptions (partial list).
_CATEGORY_DESCRIPTIONS: dict[str, str] = {
    "01": "Construction without permit",
    "02": "Elevator - defective/dangerous",
    "03": "Plumbing - defective/leaking",
    "04": "Illegal conversion",
    "05": "Boiler - defective/dangerous",
    "06": "Structural integrity concerns",
    "07": "Facade/exterior unsafe",
    "09": "Fire egress blocked",
    "10": "Work without permit",
    "11": "Electrical - defective/dangerous",
    "45": "Occupied with no CO or TCO",
    "71": "Illegal use/occupancy",
}


def _soql_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "''").replace("%", "\\%")


# ── Local DB queries ──────────────────────────────────────────────────

async def _query_local_by_bin(
    bin_val: str,
    category: str | None,
    status: str | None,
    since_year: int | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Query dob_complaints by BIN — exact indexed lookup."""
    from nyc_property_intel.db import fetch_all

    conditions = ["bin = $1"]
    params: list[Any] = [bin_val]
    idx = 2

    if since_year:
        conditions.append(f"dateentered >= ${idx}::date")
        params.append(f"{since_year}-01-01")
        idx += 1
    if category:
        conditions.append(f"complaintcategory = ${idx}")
        params.append(category)
        idx += 1
    if status:
        conditions.append(f"upper(status) LIKE ${idx}")
        params.append(f"%{status.upper()}%")
        idx += 1

    params.append(limit)
    sql = f"""
        SELECT complaintnumber, dateentered, housenumber, housestreet, zipcode,
               bin, complaintcategory, unit, dispositiondate, dispositioncode,
               status, dobrundate
        FROM dob_complaints
        WHERE {' AND '.join(conditions)}
        ORDER BY dateentered DESC
        LIMIT ${idx}
    """
    return await fetch_all(sql, *params)


async def _query_local_by_address(
    house_number: str,
    street_name: str,
    category: str | None,
    status: str | None,
    since_year: int | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Query dob_complaints by address string matching (fallback when no BIN)."""
    from nyc_property_intel.db import fetch_all

    conditions = [f"upper(housestreet) LIKE $1"]
    params: list[Any] = [f"%{street_name.upper()}%"]
    idx = 2

    if house_number:
        hn_clean = house_number.replace("-", "").lstrip("0") or house_number
        conditions.append(f"(housenumber = ${idx} OR housenumber = ${idx + 1})")
        params.extend([house_number, hn_clean])
        idx += 2

    if since_year:
        conditions.append(f"dateentered >= ${idx}::date")
        params.append(f"{since_year}-01-01")
        idx += 1
    if category:
        conditions.append(f"complaintcategory = ${idx}")
        params.append(category)
        idx += 1
    if status:
        conditions.append(f"upper(status) LIKE ${idx}")
        params.append(f"%{status.upper()}%")
        idx += 1

    params.append(limit)
    sql = f"""
        SELECT complaintnumber, dateentered, housenumber, housestreet, zipcode,
               bin, complaintcategory, unit, dispositiondate, dispositioncode,
               status, dobrundate
        FROM dob_complaints
        WHERE {' AND '.join(conditions)}
        ORDER BY dateentered DESC
        LIMIT ${idx}
    """
    return await fetch_all(sql, *params)


# ── Socrata fallback ──────────────────────────────────────────────────

async def _query_socrata_fallback(
    house_number: str,
    street_name: str,
    borough_code: str | None,
    category: str | None,
    status: str | None,
    since_year: int | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Fall back to Socrata API when local dob_complaints table is unavailable."""
    _BOROUGH_TO_DOB: dict[str, str] = {
        "1": "M", "2": "X", "3": "K", "4": "Q", "5": "S"
    }
    parts: list[str] = [
        f"upper(house_street) like upper('%{_soql_escape(street_name)}%')"
    ]
    if house_number:
        hn_clean = house_number.replace("-", "").lstrip("0") or house_number
        parts.append(
            f"(house_number = '{_soql_escape(house_number)}' "
            f"OR house_number = '{_soql_escape(hn_clean)}')"
        )
    if borough_code:
        dob_boro = _BOROUGH_TO_DOB.get(borough_code)
        if dob_boro:
            parts.append(f"borough = '{dob_boro}'")
    if category:
        parts.append(f"complaint_category = '{_soql_escape(category)}'")
    if status:
        parts.append(f"upper(status) like '%{_soql_escape(status.upper())}%'")
    if since_year:
        parts.append(f"date_entered >= '{int(since_year)}-01-01T00:00:00'")

    return await query_socrata(
        _SOCRATA_DATASET,
        where=" AND ".join(parts),
        limit=limit,
        order="date_entered DESC",
        select=(
            "complaint_number,date_entered,house_number,house_street,borough,"
            "block,lot,complaint_category,unit,disposition_date,"
            "disposition_description,status,dobrundate"
        ),
    )


# ── Summarize helpers ─────────────────────────────────────────────────

def _summarize_local(complaints: list[dict[str, Any]]) -> dict[str, Any]:
    """Build summary dict from local DB rows."""
    open_count = sum(
        1 for c in complaints
        if (c.get("status") or "").upper() not in ("CLOSED", "RESOLVED", "DISMISSED")
    )
    unresolved = sum(1 for c in complaints if not c.get("dispositiondate"))

    cat_counts: dict[str, int] = {}
    for c in complaints:
        code = c.get("complaintcategory") or "Unknown"
        label = _CATEGORY_DESCRIPTIONS.get(code, code)
        c["complaint_category_description"] = label if code in _CATEGORY_DESCRIPTIONS else None
        cat_counts[label] = cat_counts.get(label, 0) + 1

    top_categories = sorted(cat_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    return {
        "open_or_active": open_count,
        "unresolved_no_disposition": unresolved,
        "top_categories": [{"category": cat, "count": cnt} for cat, cnt in top_categories],
    }


def _summarize_socrata(complaints: list[dict[str, Any]]) -> dict[str, Any]:
    """Build summary dict from Socrata rows."""
    for c in complaints:
        cat_code = c.get("complaint_category")
        if cat_code and cat_code in _CATEGORY_DESCRIPTIONS:
            c["complaint_category_description"] = _CATEGORY_DESCRIPTIONS[cat_code]

    open_count = sum(
        1 for c in complaints
        if (c.get("status") or "").upper() not in ("CLOSED", "RESOLVED", "DISMISSED")
    )
    unresolved = sum(1 for c in complaints if not c.get("disposition_date"))

    cat_counts: dict[str, int] = {}
    for c in complaints:
        cat = c.get("complaint_category_description") or c.get("complaint_category") or "Unknown"
        cat_counts[cat] = cat_counts.get(cat, 0) + 1

    top_categories = sorted(cat_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    return {
        "open_or_active": open_count,
        "unresolved_no_disposition": unresolved,
        "top_categories": [{"category": cat, "count": cnt} for cat, cnt in top_categories],
    }


# ── Main tool ─────────────────────────────────────────────────────────

@mcp.tool()
async def get_dob_complaints(
    address: str | None = None,
    bbl: str | None = None,
    category: str | None = None,
    status: str | None = None,
    since_year: int | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    """Get DOB complaints filed against a property with the Dept of Buildings.

    Queries the DOB Complaints Received dataset (NYC Open Data `eabe-havv`).
    Complaints are filed *before* formal violations are issued — they trigger
    DOB inspections and are the earliest public signal of construction, safety,
    or code issues at a building.

    Key insight: compare this with `get_property_issues` violations. If a
    property has many complaints but few violations, DOB may not be inspecting.
    If complaints are recent and unresolved, it flags active safety concerns.

    Common complaint categories: illegal construction (01), elevator (02),
    plumbing (03), illegal conversion (04), boiler (05), structural (06),
    facade (07), fire egress (09), work without permit (10), electrical (11).

    Provide either `address` OR `bbl` (not both).

    Args:
        address: Street address, e.g. "350 5th Ave, Manhattan".
        bbl: 10-digit NYC BBL. Resolved via BIN lookup for accurate matching.
        category: Filter by complaint category code, e.g. "01" for
                  construction without permit, "04" for illegal conversion.
        status: Filter by status keyword, e.g. "OPEN", "CLOSED",
                "REFERRED TO DA".
        since_year: Return only complaints from this year onward.
        limit: Max complaints to return (1–100, default 25).
    """
    if not address and not bbl:
        raise ToolError("Provide either address or bbl.")
    if address and bbl:
        raise ToolError("Provide either address or bbl, not both.")
    if limit < 1 or limit > 100:
        raise ToolError("limit must be between 1 and 100.")
    if since_year is not None and (since_year < 2000 or since_year > 2030):
        raise ToolError("since_year must be between 2000 and 2030.")
    if category is not None and len(category) > 10:
        raise ToolError("category must be 10 characters or fewer (e.g. '01', '04').")
    if status is not None and len(status) > 50:
        raise ToolError("status must be 50 characters or fewer.")

    # ── Resolve address components ────────────────────────────────────
    house_number = ""
    street_name = ""
    resolved_address: str | None = None
    borough_code: str | None = None
    bin_val: str | None = None

    if bbl:
        from nyc_property_intel.utils import validate_bbl
        from nyc_property_intel.db import fetch_one

        try:
            b_code, _, _ = validate_bbl(bbl)
            borough_code = b_code
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

        row = await fetch_one(
            "SELECT lhnd AS house_number, stname AS street_name, bin "
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
        bin_val = str(row["bin"]).strip() if row.get("bin") else None
        resolved_address = f"{house_number} {street_name}"
    else:
        from nyc_property_intel.geoclient import parse_address

        try:
            parsed = parse_address(address)  # type: ignore[arg-type]
            house_number = parsed["house_number"]
            street_name = parsed["street"]
            borough_code = parsed["borough_code"]
            resolved_address = f"{house_number} {street_name}"
        except ToolError:
            resolved_address = address
            street_name = address

    # ── Query: local DB first, fall back to Socrata ───────────────────
    complaints: list[dict[str, Any]]
    data_source_used: str

    try:
        if bin_val:
            # Best path: exact BIN lookup (indexed)
            complaints = await _query_local_by_bin(
                bin_val, category, status, since_year, limit
            )
        else:
            # Address path: string matching in local DB
            complaints = await _query_local_by_address(
                house_number, street_name, category, status, since_year, limit
            )
        summary = _summarize_local(complaints)
        data_source_used = "local"

    except asyncpg.UndefinedTableError:
        # Table not yet loaded — fall back to Socrata API
        logger.warning(
            "dob_complaints table not found locally — falling back to Socrata API"
        )
        try:
            complaints = await _query_socrata_fallback(
                house_number, street_name, borough_code, category, status, since_year, limit
            )
        except SocrataError as exc:
            raise ToolError(str(exc)) from exc
        summary = _summarize_socrata(complaints)
        data_source_used = "socrata"

    # ── Build response ────────────────────────────────────────────────
    data_note = (
        "Local PostgreSQL (DOB Complaints, NYC Open Data eabe-havv). "
        "BIN-based exact match via PAD table."
        if data_source_used == "local" and bin_val
        else
        "Local PostgreSQL (DOB Complaints, NYC Open Data eabe-havv). "
        "Address string match."
        if data_source_used == "local"
        else
        "Real-time via Socrata API (local table not yet loaded). "
        "Address matching is approximate."
    )

    return {
        "address_queried": resolved_address,
        "bbl": bbl,
        "bin": bin_val,
        "total_returned": len(complaints),
        "summary": summary,
        "category_reference": _CATEGORY_DESCRIPTIONS,
        "complaints": complaints,
        "data_source": "DOB Complaints Received (NYC Open Data eabe-havv)",
        "data_note": data_note,
    }
