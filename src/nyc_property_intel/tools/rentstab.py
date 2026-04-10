"""Rent stabilization tool — unit counts and trends.

Returns rent-stabilized unit counts by year (2007-2017) from the
Rent Stabilization Unit Counts dataset (taxbills.nyc) for a given BBL.
"""

from __future__ import annotations

import logging

import asyncpg
from mcp.server.fastmcp.exceptions import ToolError

from nyc_property_intel.app import mcp
from nyc_property_intel.db import fetch_one
from nyc_property_intel.utils import data_freshness_note, validate_bbl

logger = logging.getLogger(__name__)

_YEARS = list(range(2007, 2018))

_SQL_RENTSTAB = """\
SELECT ucbbl, borough, address, ownername,
    uc2007, uc2008, uc2009, uc2010, uc2011, uc2012,
    uc2013, uc2014, uc2015, uc2016, uc2017,
    est2007, est2008, est2009, est2010, est2011, est2012,
    est2013, est2014, est2015, est2016, est2017,
    unitsres, unitstotal, yearbuilt
FROM rentstab WHERE ucbbl = $1;"""


@mcp.tool()
async def get_rent_stabilization(bbl: str) -> dict:
    """Get rent stabilization history for a property.

    Shows stabilized unit counts from 2007-2017 and whether counts are
    estimated or confirmed by DHCR. Use this to check if a building is
    rent-stabilized and track unit count changes over time.
    """
    try:
        validate_bbl(bbl)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc

    try:
        row = await fetch_one(_SQL_RENTSTAB, bbl)
    except asyncpg.UndefinedTableError:
        return {
            "bbl": bbl,
            "is_rent_stabilized": None,
            "note": "Rent stabilization data table not loaded. Available after Phase B data ingestion.",
        }

    if not row:
        return {
            "bbl": bbl,
            "is_rent_stabilized": False,
            "note": "No rent stabilization records found — property is likely not rent-stabilized.",
            "data_as_of": data_freshness_note("rentstab"),
        }

    # Build year-by-year unit counts
    unit_counts = []
    for year in _YEARS:
        count = row.get(f"uc{year}")
        estimated = row.get(f"est{year}")
        if count is not None:
            unit_counts.append({
                "year": year,
                "stabilized_units": count,
                "is_estimated": bool(estimated),
            })

    # Determine trend from most recent non-null years
    counts_with_values = [uc for uc in unit_counts if uc["stabilized_units"] > 0]
    trend = None
    if len(counts_with_values) >= 2:
        first = counts_with_values[0]["stabilized_units"]
        last = counts_with_values[-1]["stabilized_units"]
        if last < first:
            trend = "declining"
        elif last > first:
            trend = "increasing"
        else:
            trend = "stable"

    latest = counts_with_values[-1] if counts_with_values else None

    return {
        "bbl": bbl,
        "is_rent_stabilized": True,
        "address": row.get("address"),
        "owner_name": row.get("ownername"),
        "total_residential_units": row.get("unitsres"),
        "total_units": row.get("unitstotal"),
        "year_built": row.get("yearbuilt"),
        "latest_stabilized_units": latest["stabilized_units"] if latest else None,
        "latest_year": latest["year"] if latest else None,
        "trend": trend,
        "unit_counts_by_year": unit_counts,
        "data_as_of": data_freshness_note("rentstab"),
    }
