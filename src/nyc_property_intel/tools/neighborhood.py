"""Neighborhood statistics tool — area-level property and market analytics.

Aggregates property stock, sales activity, violations, and rent stabilization
data at the zip code or neighborhood level. Useful for market research,
area comparisons, and investment targeting.
"""

from __future__ import annotations

from typing import Any
from __future__ import annotations

import logging

import asyncpg
from mcp.server.fastmcp.exceptions import ToolError

from nyc_property_intel.app import mcp
from nyc_property_intel.db import fetch_all, fetch_one
from nyc_property_intel.utils import data_freshness_note, escape_like, format_currency

logger = logging.getLogger(__name__)

# ── SQL queries ──────────────────────────────────────────────────────

_SQL_PROPERTY_STOCK = """\
SELECT
    COUNT(*) AS total_properties,
    COUNT(*) FILTER (WHERE unitsres > 0) AS residential_properties,
    COUNT(*) FILTER (WHERE comarea > 0) AS commercial_properties,
    SUM(unitsres) AS total_residential_units,
    SUM(unitstotal) AS total_units,
    ROUND(AVG(numfloors) FILTER (WHERE numfloors > 0), 1) AS avg_floors,
    ROUND(AVG(yearbuilt) FILTER (WHERE yearbuilt > 1800))::int AS avg_year_built,
    ROUND(AVG(lotarea) FILTER (WHERE lotarea > 0)) AS avg_lot_area,
    ROUND(AVG(bldgarea) FILTER (WHERE bldgarea > 0)) AS avg_building_area,
    COUNT(*) FILTER (WHERE landmark IS NOT NULL AND landmark != '') AS landmark_count,
    COUNT(*) FILTER (WHERE histdist IS NOT NULL AND histdist != '') AS historic_district_count,
    -- Building class distribution (top 5)
    (SELECT jsonb_agg(row_to_json(t)) FROM (
        SELECT bldgclass, COUNT(*) AS cnt
        FROM mv_property_profile
        WHERE postcode = $1
        GROUP BY bldgclass ORDER BY cnt DESC LIMIT 5
    ) t) AS top_building_classes,
    -- Zoning distribution (top 5)
    (SELECT jsonb_agg(row_to_json(t)) FROM (
        SELECT zonedist1 AS zone, COUNT(*) AS cnt
        FROM mv_property_profile
        WHERE postcode = $1 AND zonedist1 IS NOT NULL
        GROUP BY zonedist1 ORDER BY cnt DESC LIMIT 5
    ) t) AS top_zoning_districts
FROM mv_property_profile
WHERE postcode = $1;"""

_SQL_SALES_SUMMARY = """\
SELECT
    COUNT(*) AS total_sales,
    COUNT(*) FILTER (WHERE saleprice > 10000) AS market_sales,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY saleprice)
        FILTER (WHERE saleprice > 10000) AS median_price,
    AVG(saleprice) FILTER (WHERE saleprice > 10000) AS avg_price,
    PERCENTILE_CONT(0.5) WITHIN GROUP (
        ORDER BY CASE WHEN grosssquarefeet > 0 AND saleprice > 10000
            THEN saleprice::numeric / grosssquarefeet END
    ) AS median_ppsf,
    MIN(saleprice) FILTER (WHERE saleprice > 10000) AS min_price,
    MAX(saleprice) FILTER (WHERE saleprice > 10000) AS max_price
FROM dof_sales
WHERE zipcode = $1
  AND saledate >= CURRENT_DATE - make_interval(months => $2);"""

_SQL_SALES_QUARTERLY = """\
SELECT
    DATE_TRUNC('quarter', saledate) AS quarter,
    COUNT(*) AS num_sales,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY saleprice) AS median_price,
    PERCENTILE_CONT(0.5) WITHIN GROUP (
        ORDER BY CASE WHEN grosssquarefeet > 0
            THEN saleprice::numeric / grosssquarefeet END
    ) AS median_ppsf
FROM dof_sales
WHERE saleprice > 10000
  AND saledate >= CURRENT_DATE - make_interval(months => $2)
  AND ($1::text IS NULL OR zipcode = $1)
  AND ($3::text IS NULL OR neighborhood ILIKE '%' || $3 || '%')
  AND ($4::text IS NULL OR buildingclassattimeofsale LIKE $4 || '%')
GROUP BY DATE_TRUNC('quarter', saledate)
ORDER BY quarter DESC
LIMIT 20;"""

_SQL_VIOLATION_AREA = """\
SELECT
    COUNT(*) AS total_properties_with_violations,
    SUM(hpd_total) AS total_hpd_violations,
    SUM(hpd_class_c) AS total_hpd_class_c,
    SUM(hpd_open) AS total_hpd_open,
    SUM(dob_total) AS total_dob_violations,
    ROUND(AVG(hpd_total) FILTER (WHERE hpd_total > 0), 1) AS avg_hpd_per_building,
    MAX(GREATEST(hpd_most_recent, dob_most_recent)) AS most_recent_violation
FROM mv_violation_summary v
JOIN mv_property_profile p ON v.bbl = p.bbl
WHERE p.postcode = $1;"""

_SQL_RENTSTAB_AREA = """\
SELECT
    COUNT(*) AS stabilized_buildings,
    SUM(uc2017) AS total_stabilized_units_2017,
    SUM(uc2007) AS total_stabilized_units_2007,
    SUM(unitsres) AS total_residential_units
FROM rentstab
WHERE zipcode = $1;"""

_SQL_LIENS_AREA = """\
SELECT COUNT(DISTINCT bbl) AS properties_with_liens
FROM dof_tax_lien_sale_list
WHERE bbl IN (
    SELECT bbl FROM mv_property_profile WHERE postcode = $1
);"""

_SQL_NEIGHBORHOOD_SALES_SUMMARY = """\
SELECT
    COUNT(*) AS total_sales,
    COUNT(*) FILTER (WHERE saleprice > 10000) AS market_sales,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY saleprice)
        FILTER (WHERE saleprice > 10000) AS median_price,
    AVG(saleprice) FILTER (WHERE saleprice > 10000) AS avg_price,
    PERCENTILE_CONT(0.5) WITHIN GROUP (
        ORDER BY CASE WHEN grosssquarefeet > 0 AND saleprice > 10000
            THEN saleprice::numeric / grosssquarefeet END
    ) AS median_ppsf,
    MIN(saleprice) FILTER (WHERE saleprice > 10000) AS min_price,
    MAX(saleprice) FILTER (WHERE saleprice > 10000) AS max_price
FROM dof_sales
WHERE neighborhood ILIKE '%' || $1 || '%'
  AND saledate >= CURRENT_DATE - make_interval(months => $2);"""


@mcp.tool()
async def search_neighborhood_stats(
    zip_code: str | None = None,
    neighborhood: str | None = None,
    building_class: str | None = None,
    months: int = 24,
    include_quarterly_trends: bool = True,
    include_violations: bool = True,
    include_rent_stabilization: bool = True,
) -> dict[str, Any]:
    """Get aggregate neighborhood statistics for market research and area analysis.

    Combines property stock data, sales market activity, violation patterns,
    and rent stabilization counts at the zip code or neighborhood level.
    Use this to compare areas, identify investment hotspots, or understand
    a neighborhood's character before drilling into individual properties.

    At least one of zip_code or neighborhood is required.
    """
    if not zip_code and not neighborhood:
        raise ToolError(
            "At least one of zip_code or neighborhood is required."
        )

    if months < 1 or months > 120:
        raise ToolError("months must be between 1 and 120.")

    safe_neighborhood = escape_like(neighborhood) if neighborhood else None
    safe_building_class = escape_like(building_class) if building_class else None

    result: dict = {
        "search_criteria": {
            "zip_code": zip_code,
            "neighborhood": neighborhood,
            "building_class": building_class,
            "months": months,
        },
        "data_sources": [],
    }

    # ── Property stock (zip code only — profile table has postcode) ──
    if zip_code:
        try:
            stock = await fetch_one(_SQL_PROPERTY_STOCK, zip_code)
            if stock and stock.get("total_properties", 0) > 0:
                result["property_stock"] = stock
            else:
                result["property_stock"] = None
                result["property_stock_note"] = f"No properties found for zip code {zip_code}."
            result["data_sources"].append(data_freshness_note("pluto"))
        except asyncpg.UndefinedTableError:
            result["property_stock"] = None
            result["property_stock_note"] = "Property profile data not loaded."

    # ── Sales summary ────────────────────────────────────────────────
    try:
        if zip_code:
            sales_summary = await fetch_one(_SQL_SALES_SUMMARY, zip_code, months)
        elif safe_neighborhood:
            sales_summary = await fetch_one(
                _SQL_NEIGHBORHOOD_SALES_SUMMARY, safe_neighborhood, months
            )
        else:
            sales_summary = None

        if sales_summary and sales_summary.get("market_sales", 0) > 0:
            sales_summary["median_price_formatted"] = format_currency(
                sales_summary.get("median_price")
            )
            sales_summary["avg_price_formatted"] = format_currency(
                sales_summary.get("avg_price")
            )
            median_ppsf = sales_summary.get("median_ppsf")
            sales_summary["median_ppsf_formatted"] = (
                f"${median_ppsf:,.2f}" if median_ppsf else "N/A"
            )
            result["sales_summary"] = sales_summary

            if sales_summary["market_sales"] < 10:
                result["sales_note"] = (
                    f"Only {sales_summary['market_sales']} market sales in "
                    f"the last {months} months — statistics may not be reliable."
                )
        else:
            result["sales_summary"] = None
            result["sales_note"] = (
                f"No market sales found in the last {months} months."
            )
        result["data_sources"].append(data_freshness_note("rpad"))
    except asyncpg.UndefinedTableError:
        result["sales_summary"] = None
        result["sales_note"] = "Sales data not loaded (Phase B)."

    # ── Quarterly trends ─────────────────────────────────────────────
    if include_quarterly_trends:
        try:
            quarterly = await fetch_all(
                _SQL_SALES_QUARTERLY,
                zip_code,
                months,
                safe_neighborhood,
                safe_building_class,
            )
            for row in quarterly:
                row["median_price_formatted"] = format_currency(
                    row.get("median_price")
                )
                median_ppsf = row.get("median_ppsf")
                row["median_ppsf_formatted"] = (
                    f"${median_ppsf:,.2f}" if median_ppsf else "N/A"
                )
            result["quarterly_trends"] = quarterly
        except asyncpg.UndefinedTableError:
            result["quarterly_trends"] = []

    # ── Violation summary for area ───────────────────────────────────
    if include_violations and zip_code:
        try:
            violation_stats = await fetch_one(_SQL_VIOLATION_AREA, zip_code)
            if violation_stats:
                result["violation_summary"] = violation_stats
            result["data_sources"].append(data_freshness_note("hpd_violations"))
        except asyncpg.UndefinedTableError:
            result["violation_summary"] = None
            result["violation_note"] = "Violation data not loaded."

    # ── Rent stabilization for area ──────────────────────────────────
    if include_rent_stabilization and zip_code:
        try:
            rentstab_stats = await fetch_one(_SQL_RENTSTAB_AREA, zip_code)
            if rentstab_stats and rentstab_stats.get("stabilized_buildings", 0) > 0:
                total_res = rentstab_stats.get("total_residential_units") or 0
                stab_2017 = rentstab_stats.get("total_stabilized_units_2017") or 0
                stab_2007 = rentstab_stats.get("total_stabilized_units_2007") or 0

                rentstab_stats["pct_stabilized"] = (
                    round(stab_2017 / total_res * 100, 1)
                    if total_res > 0 else None
                )
                if stab_2007 > 0 and stab_2017 > 0:
                    rentstab_stats["unit_change_2007_2017"] = stab_2017 - stab_2007
                    rentstab_stats["pct_change_2007_2017"] = round(
                        (stab_2017 - stab_2007) / stab_2007 * 100, 1
                    )

                result["rent_stabilization"] = rentstab_stats
            else:
                result["rent_stabilization"] = None
                result["rent_stabilization_note"] = (
                    "No rent-stabilized buildings found in this zip code."
                )
            result["data_sources"].append(data_freshness_note("rentstab"))
        except asyncpg.UndefinedTableError:
            result["rent_stabilization"] = None

    # ── Tax liens count for area ─────────────────────────────────────
    if zip_code:
        try:
            lien_stats = await fetch_one(_SQL_LIENS_AREA, zip_code)
            if lien_stats:
                result["tax_lien_count"] = lien_stats.get("properties_with_liens", 0)
        except asyncpg.UndefinedTableError:
            pass

    # ── Finalize ─────────────────────────────────────────────────────
    result["data_as_of"] = (
        "; ".join(dict.fromkeys(result.pop("data_sources")))
        if result["data_sources"]
        else "No data sources queried."
    )
    return result
