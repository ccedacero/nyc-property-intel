"""Comparable sales search tool — find comps and market statistics.

Searches DOF rolling sales data for comparable transactions by zip code,
building class, square footage, and time period. Optionally returns
quarterly market trend statistics.
"""

from __future__ import annotations

import logging

import asyncpg
from mcp.server.fastmcp.exceptions import ToolError

from nyc_property_intel.app import mcp
from nyc_property_intel.db import fetch_all, fetch_one
from nyc_property_intel.utils import (
    data_freshness_note,
    escape_like,
    format_currency,
    validate_bbl,
)

logger = logging.getLogger(__name__)

# ── SQL queries ──────────────────────────────────────────────────────

_SQL_REF_PROPERTY = """\
SELECT postcode, bldgclass, bldgarea
FROM pluto_latest WHERE bbl = $1;
"""

# Comps query WITH reference CTE (when bbl is provided).
# Parameters: $1=bbl, $2=zip_code, $3=building_class, $4=min_sqft,
#             $5=months, $6=min_price, $7=max_sqft, $8=max_price, $9=limit
_SQL_COMPS_WITH_REF = """\
WITH ref AS (
    SELECT postcode, bldgclass, bldgarea
    FROM pluto_latest WHERE bbl = $1
)
SELECT s.bbl, s.address, s.neighborhood, s.saleprice, s.saledate,
    s.grosssquarefeet, s.landsquarefeet,
    s.residentialunits, s.commercialunits, s.totalunits,
    s.buildingclassattimeofsale, s.yearbuilt,
    CASE WHEN s.grosssquarefeet > 0
        THEN s.saleprice::numeric / s.grosssquarefeet
        ELSE NULL END AS price_per_sqft
FROM dof_sales s, ref
WHERE s.zipcode = COALESCE($2, ref.postcode)
  AND s.saleprice > COALESCE($6, 10000)
  AND s.saledate >= CURRENT_DATE - make_interval(months => $5)
  AND ($3::text IS NULL OR s.buildingclassattimeofsale LIKE $3 || '%')
  AND ($4::int IS NULL OR s.grosssquarefeet >= $4)
  AND ($7::int IS NULL OR s.grosssquarefeet <= $7)
  AND ($8::int IS NULL OR s.saleprice <= $8)
  AND s.bbl != COALESCE($1, '')
ORDER BY s.saledate DESC
LIMIT $9;
"""

# Comps query WITHOUT reference CTE (when no bbl is provided).
# Parameters: $1=zip_code, $2=building_class, $3=min_sqft,
#             $4=months, $5=min_price, $6=max_sqft, $7=max_price, $8=limit
_SQL_COMPS_NO_REF = """\
SELECT s.bbl, s.address, s.neighborhood, s.saleprice, s.saledate,
    s.grosssquarefeet, s.landsquarefeet,
    s.residentialunits, s.commercialunits, s.totalunits,
    s.buildingclassattimeofsale, s.yearbuilt,
    CASE WHEN s.grosssquarefeet > 0
        THEN s.saleprice::numeric / s.grosssquarefeet
        ELSE NULL END AS price_per_sqft
FROM dof_sales s
WHERE s.zipcode = $1
  AND s.saleprice > COALESCE($5, 10000)
  AND s.saledate >= CURRENT_DATE - make_interval(months => $4)
  AND ($2::text IS NULL OR s.buildingclassattimeofsale LIKE $2 || '%')
  AND ($3::int IS NULL OR s.grosssquarefeet >= $3)
  AND ($6::int IS NULL OR s.grosssquarefeet <= $6)
  AND ($7::int IS NULL OR s.saleprice <= $7)
ORDER BY s.saledate DESC
LIMIT $8;
"""

# Market statistics by quarter.
# Parameters: $1=zip_code, $2=neighborhood (unused, kept for flexibility),
#             $3=building_class, $4=months
_SQL_STATS = """\
SELECT
    DATE_TRUNC('quarter', saledate) AS quarter,
    COUNT(*) AS num_sales,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY saleprice) AS median_price,
    AVG(saleprice) AS avg_price,
    PERCENTILE_CONT(0.5) WITHIN GROUP (
        ORDER BY CASE WHEN grosssquarefeet > 0 THEN saleprice::numeric / grosssquarefeet END
    ) AS median_ppsf,
    MIN(saleprice) AS min_price,
    MAX(saleprice) AS max_price
FROM dof_sales
WHERE saleprice > 10000
  AND saledate >= CURRENT_DATE - make_interval(months => $4)
  AND ($1::text IS NULL OR zipcode = $1)
  AND ($2::text IS NULL OR neighborhood ILIKE '%' || $2 || '%')
  AND ($3::text IS NULL OR buildingclassattimeofsale LIKE $3 || '%')
GROUP BY DATE_TRUNC('quarter', saledate)
ORDER BY quarter DESC
LIMIT 20;
"""


@mcp.tool()
async def search_comps(
    bbl: str | None = None,
    zip_code: str | None = None,
    building_class: str | None = None,
    min_sqft: int | None = None,
    max_sqft: int | None = None,
    months: int = 12,
    min_price: int | None = None,
    max_price: int | None = None,
    limit: int = 20,
    include_stats: bool = True,
) -> dict:
    """Find comparable property sales and neighborhood market statistics.

    Can search by zip code, building class, size, and time period. If a
    reference BBL is provided, uses that property's characteristics as
    defaults. Returns individual sales with price per sqft and quarterly
    market trends.
    """

    # ── Validate inputs ───────────────────────────────────────────────
    ref_property: dict | None = None

    if bbl is not None:
        try:
            validate_bbl(bbl)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

        # Look up reference property for default characteristics
        ref_property = await fetch_one(_SQL_REF_PROPERTY, bbl)
        if ref_property is None:
            raise ToolError(
                f"Reference property BBL {bbl} not found in PLUTO. "
                "Verify the BBL or provide zip_code directly."
            )

    # Determine effective zip code
    effective_zip = zip_code
    if effective_zip is None and ref_property is not None:
        effective_zip = ref_property.get("postcode")

    if effective_zip is None:
        raise ToolError(
            "A zip_code is required. Provide one directly, or provide a "
            "reference bbl so the zip code can be looked up automatically."
        )

    if months < 1 or months > 120:
        raise ToolError("months must be between 1 and 120.")

    if limit < 1 or limit > 100:
        raise ToolError("limit must be between 1 and 100.")

    # Escape LIKE metacharacters so user input can't broaden the pattern.
    safe_building_class = escape_like(building_class) if building_class else None

    result: dict = {
        "search_criteria": {
            "zip_code": effective_zip,
            "building_class": building_class,
            "min_sqft": min_sqft,
            "max_sqft": max_sqft,
            "months": months,
            "min_price": min_price,
            "max_price": max_price,
        },
    }

    if ref_property is not None:
        result["reference_property"] = {
            "bbl": bbl,
            "postcode": ref_property.get("postcode"),
            "building_class": ref_property.get("bldgclass"),
            "building_area": ref_property.get("bldgarea"),
        }

    # ── Comparable sales ──────────────────────────────────────────────
    try:
        if bbl is not None:
            comps = await fetch_all(
                _SQL_COMPS_WITH_REF,
                bbl,                   # $1
                zip_code,              # $2 (explicit zip; NULL falls back to ref)
                safe_building_class,   # $3
                min_sqft,              # $4
                months,                # $5
                min_price,             # $6
                max_sqft,              # $7
                max_price,             # $8
                limit,                 # $9
            )
        else:
            comps = await fetch_all(
                _SQL_COMPS_NO_REF,
                effective_zip,         # $1
                safe_building_class,   # $2
                min_sqft,              # $3
                months,                # $4
                min_price,             # $5
                max_sqft,              # $6
                max_price,             # $7
                limit,                 # $8
            )

        null_sqft_count = 0
        for comp in comps:
            comp["saleprice_formatted"] = format_currency(comp.get("saleprice"))
            ppsf = comp.get("price_per_sqft")
            comp["price_per_sqft_formatted"] = (
                f"${ppsf:,.2f}" if ppsf is not None else "N/A"
            )
            if not comp.get("grosssquarefeet"):
                null_sqft_count += 1

        result["comps"] = comps
        result["num_comps_found"] = len(comps)
        if null_sqft_count > 0:
            result["sqft_note"] = (
                f"{null_sqft_count} comp(s) have no gross sqft recorded — "
                "common for condo/co-op unit sales. Price per sqft is N/A for those records."
            )
    except asyncpg.UndefinedTableError:
        logger.info("dof_sales table not loaded yet — skipping comps")
        result["comps"] = []
        result["num_comps_found"] = 0
        result["comps_note"] = (
            "Sales data table (dof_sales) is not yet loaded. "
            "This data will be available after Phase B data ingestion."
        )

    # ── Market statistics ─────────────────────────────────────────────
    if include_stats:
        try:
            # Use the neighborhood from the first comp if available for
            # the stats query, otherwise pass None.
            neighborhood_filter: str | None = None

            stats = await fetch_all(
                _SQL_STATS,
                effective_zip,         # $1
                neighborhood_filter,   # $2
                safe_building_class,   # $3
                months,                # $4
            )

            for row in stats:
                row["median_price_formatted"] = format_currency(
                    row.get("median_price")
                )
                row["avg_price_formatted"] = format_currency(row.get("avg_price"))
                median_ppsf = row.get("median_ppsf")
                row["median_ppsf_formatted"] = (
                    f"${median_ppsf:,.2f}" if median_ppsf is not None else "N/A"
                )

            result["quarterly_stats"] = stats
        except asyncpg.UndefinedTableError:
            logger.info("dof_sales table not loaded yet — skipping stats")
            result["quarterly_stats"] = []
            result["stats_note"] = (
                "Sales data table (dof_sales) is not yet loaded. "
                "This data will be available after Phase B data ingestion."
            )

    result["data_as_of"] = data_freshness_note("dof_sales")

    return result
