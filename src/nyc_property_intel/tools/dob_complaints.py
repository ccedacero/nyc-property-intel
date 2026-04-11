"""DOB complaints tool — complaints filed with the Dept of Buildings.

Queries the DOB Complaints Received dataset (Socrata `eabe-havv`) in
real-time. These are complaints filed by the public or other agencies
*before* DOB issues formal violations — they trigger inspections and
represent the earliest signal of construction or safety problems.

The gap between complaint filed and violation issued reveals inspector
response time. Unresolved complaints (no disposition) indicate open
safety concerns that DOB hasn't addressed.

Dataset: NYC Open Data `eabe-havv`
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

_SOCRATA_BASE = "https://data.cityofnewyork.us/resource"
_DATASET = "eabe-havv.json"

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

    Queries the DOB Complaints Received dataset (NYC Open Data) in real-time.
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
        bbl: 10-digit NYC BBL. Resolved to street address via PAD table.
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

    # ── Resolve to street address ─────────────────────────────────────
    house_number = ""
    street_name = ""
    resolved_address: str | None = None
    borough_code: str | None = None

    if bbl:
        from nyc_property_intel.utils import validate_bbl
        from nyc_property_intel.db import fetch_one

        try:
            b_code, _, _ = validate_bbl(bbl)
            borough_code = b_code
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
            borough_code = parsed["borough_code"]
            resolved_address = f"{house_number} {street_name}"
        except ToolError:
            resolved_address = address
            street_name = address

    # ── Build SoQL WHERE ─────────────────────────────────────────────
    # DOB complaints uses `house_street` for street name and `house_number`
    # as a separate field, plus `borough` as a single-letter code
    # (M=Manhattan, X=Bronx, K=Brooklyn, Q=Queens, S=Staten Island).
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
        parts.append(
            f"upper(status) like '%{_soql_escape(status.upper())}%'"
        )
    if since_year:
        parts.append(f"date_entered >= '{since_year}-01-01T00:00:00'")

    params: dict[str, str] = {
        "$where": " AND ".join(parts),
        "$order": "date_entered DESC",
        "$limit": str(limit),
        "$select": (
            "complaint_number,date_entered,house_number,house_street,borough,"
            "block,lot,complaint_category,unit,disposition_date,"
            "disposition_description,status,dobrundate"
        ),
    }

    client = _get_client()
    url = f"/{_DATASET}?{urllib.parse.urlencode(params)}"

    try:
        resp = await client.get(url)
        resp.raise_for_status()
    except httpx.TimeoutException as exc:
        raise ToolError("DOB Complaints / Socrata API timed out. Try again in a moment.") from exc
    except httpx.HTTPStatusError as exc:
        raise ToolError(
            f"Socrata API error (HTTP {exc.response.status_code})."
        ) from exc

    complaints: list[dict[str, Any]] = resp.json()

    # ── Enrich with category descriptions ────────────────────────────
    for c in complaints:
        cat_code = c.get("complaint_category")
        if cat_code and cat_code in _CATEGORY_DESCRIPTIONS:
            c["complaint_category_description"] = _CATEGORY_DESCRIPTIONS[cat_code]

    # ── Summarize ─────────────────────────────────────────────────────
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
        "address_queried": resolved_address,
        "bbl": bbl,
        "total_returned": len(complaints),
        "summary": {
            "open_or_active": open_count,
            "unresolved_no_disposition": unresolved,
            "top_categories": [
                {"category": cat, "count": cnt} for cat, cnt in top_categories
            ],
        },
        "category_reference": _CATEGORY_DESCRIPTIONS,
        "complaints": complaints,
        "data_source": "DOB Complaints Received (NYC Open Data eabe-havv)",
        "data_note": (
            "Real-time via Socrata API. "
            "Complaints precede formal violations — they trigger DOB inspections. "
            "Address matching uses street name + house number."
        ),
    }
