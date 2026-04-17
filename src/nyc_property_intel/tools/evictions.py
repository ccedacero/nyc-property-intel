"""Evictions tool — marshal eviction executions via NYC Open Data.

Primary data source: local PostgreSQL `marshal_evictions_all` table (loaded
from Displacement Alert / JustFix via nycdb). Falls back to the Socrata API
(`6z8x-wfk4`) if the local table is not yet available (e.g. before the first
Railway sync), or always for address-based queries where Socrata's full
dataset (126K rows) gives better coverage.

BBL-based queries use the BBL index on `marshal_evictions_all` — fast and
exact. Address-based queries use Socrata for maximum dataset coverage.

Dataset: NYC Open Data `6z8x-wfk4`
Update cadence: updated monthly
"""

from __future__ import annotations

import logging
from typing import Any

import asyncpg
from mcp.server.fastmcp.exceptions import ToolError

from nyc_property_intel.app import mcp
from nyc_property_intel.socrata import SocrataError, query_socrata
from nyc_property_intel.utils import normalize_filter

logger = logging.getLogger(__name__)

_SOCRATA_DATASET = "6z8x-wfk4"


def _soql_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "''").replace("%", "\\%")


# ── Local DB query (BBL path) ─────────────────────────────────────────

async def _query_local_by_bbl(
    bbl: str,
    eviction_type: str | None,
    since_year: int | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Query marshal_evictions_all by BBL — indexed exact lookup."""
    from nyc_property_intel.db import fetch_all

    conditions = ["bbl = $1"]
    params: list[Any] = [bbl]
    idx = 2

    if eviction_type:
        conditions.append(f"upper(residentialcommercialind) = ${idx}")
        params.append(eviction_type.upper())
        idx += 1
    if since_year:
        # Cast both sides to text for a safe ISO-string comparison that works
        # whether executeddate is stored as date or text in the loaded table.
        conditions.append(f"executeddate::text >= ${idx}")
        params.append(f"{since_year}-01-01")
        idx += 1

    params.append(limit)
    sql = f"""
        SELECT courtindexnumber, docketnumber, evictionaddress, evictionaptnum,
               executeddate, marshalfirstname, marshallastname,
               residentialcommercialind, borough, evictionzip
        FROM marshal_evictions_all
        WHERE {' AND '.join(conditions)}
        ORDER BY executeddate DESC
        LIMIT ${idx}
    """
    return await fetch_all(sql, *params)


# ── Socrata query (address path + fallback) ───────────────────────────

async def _query_socrata(
    house_number: str,
    street_name: str,
    eviction_type: str | None,
    since_year: int | None,
    limit: int,
) -> list[dict[str, Any]]:
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
        parts.append(f"executed_date >= '{int(since_year)}-01-01T00:00:00'")

    return await query_socrata(
        _SOCRATA_DATASET,
        where=" AND ".join(parts),
        limit=limit,
        order="executed_date DESC",
        select=(
            "court_index_number,docket_number,eviction_address,eviction_apt_num,"
            "executed_date,marshal_first_name,marshal_last_name,"
            "residential_commercial_ind,borough,eviction_zip"
        ),
    )


# ── Summarize ─────────────────────────────────────────────────────────

def _summarize(evictions: list[dict[str, Any]], local: bool) -> dict[str, Any]:
    res_key = "residentialcommercialind" if local else "residential_commercial_ind"
    apt_key = "evictionaptnum" if local else "eviction_apt_num"

    residential = sum(
        1 for e in evictions
        if (e.get(res_key) or "").upper() == "RESIDENTIAL"
    )
    unique_units = len({
        e.get(apt_key) for e in evictions if e.get(apt_key)
    })
    return {
        "residential_evictions": residential,
        "commercial_evictions": len(evictions) - residential,
        "unique_units_affected": unique_units,
    }


# ── Main tool ─────────────────────────────────────────────────────────

@mcp.tool()
async def get_evictions(
    address: str | None = None,
    bbl: str | None = None,
    eviction_type: str | None = None,
    since_year: int | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    """Get marshal eviction execution records for a property address.

    Returns evictions that were *executed* (marshal removed tenant), not just
    filed. Covers residential and commercial evictions citywide from 2017.

    BBL queries use a local indexed database for fast, exact lookups.
    Address queries use the Socrata API for full 126K-row dataset coverage.

    Use this to assess tenant instability and cash-flow risk. Many executed
    evictions at a building may indicate distressed management, problematic
    tenants, or an owner pushing out rent-stabilized tenants.

    Provide either `address` OR `bbl` (not both).

    Args:
        address: Street address, e.g. "123 Main St, Brooklyn".
        bbl: 10-digit NYC BBL. Queried directly via BBL index.
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

    normalized_eviction_type = normalize_filter(eviction_type)
    evictions: list[dict[str, Any]]
    resolved_address: str | None = None
    data_source_used: str

    # ── BBL path: local DB (indexed, fast) ───────────────────────────
    if bbl:
        from nyc_property_intel.utils import validate_bbl
        from nyc_property_intel.db import fetch_one

        try:
            validate_bbl(bbl)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

        # Resolve address for display
        row = await fetch_one(
            "SELECT lhnd AS house_number, stname AS street_name "
            "FROM pad_adr WHERE bbl = $1 LIMIT 1",
            bbl,
        )
        if row:
            hn = (row["house_number"] or "").strip()
            sn = (row["street_name"] or "").strip()
            resolved_address = f"{hn} {sn}"

        try:
            evictions = await _query_local_by_bbl(bbl, normalized_eviction_type, since_year, limit)
            data_source_used = "local"
        except asyncpg.UndefinedTableError:
            # Local table not yet loaded — fall back to Socrata
            logger.warning(
                "marshal_evictions_all not found locally — falling back to Socrata"
            )
            house_number = (row["house_number"] or "").strip() if row else ""
            street_name = (row["street_name"] or "").strip() if row else bbl
            try:
                evictions = await _query_socrata(
                    house_number, street_name, normalized_eviction_type, since_year, limit
                )
            except SocrataError as exc:
                raise ToolError(str(exc)) from exc
            data_source_used = "socrata_fallback"

    # ── Address path: Socrata (full dataset) ─────────────────────────
    else:
        house_number = ""
        street_name = ""

        from nyc_property_intel.geoclient import parse_address
        try:
            parsed = parse_address(address)  # type: ignore[arg-type]
            house_number = parsed["house_number"]
            street_name = parsed["street"]
            resolved_address = f"{house_number} {street_name}"
        except ToolError:
            resolved_address = address
            street_name = address

        try:
            evictions = await _query_socrata(
                house_number, street_name, normalized_eviction_type, since_year, limit
            )
        except SocrataError as exc:
            raise ToolError(str(exc)) from exc
        data_source_used = "socrata"

    # ── Summarize ─────────────────────────────────────────────────────
    is_local = data_source_used == "local"
    summary = _summarize(evictions, local=is_local)

    data_note = (
        "Local PostgreSQL (marshal_evictions_all), BBL index lookup. "
        "Data from Displacement Alert / JustFix. ~108K records, 2017–present."
        if is_local
        else
        "Real-time via Socrata API (NYC Open Data 6z8x-wfk4). "
        "Full 126K-record dataset, 2017–present. Address matching is approximate."
    )

    return {
        "address_queried": resolved_address,
        "bbl": bbl,
        "total_returned": len(evictions),
        "summary": summary,
        "evictions": evictions,
        "data_source": "NYC Evictions — Marshal Executions (NYC Open Data 6z8x-wfk4)",
        "data_note": data_note,
    }
