"""Property tax tool — assessments, market values, and exemptions.

Returns DOF property valuation data and active tax exemptions (421a, J-51,
STAR, etc.) for a given BBL.
"""

from __future__ import annotations

import logging

import asyncpg
from mcp.server.fastmcp.exceptions import ToolError

from nyc_property_intel.app import mcp
from nyc_property_intel.db import fetch_all, fetch_one
from nyc_property_intel.utils import data_freshness_note, format_currency, validate_bbl

logger = logging.getLogger(__name__)

_TAX_CLASS_DESCRIPTIONS: dict[str, str] = {
    "1": "1-3 family residential",
    "2": "Residential (4+ units, co-ops, condos)",
    "2A": "Rental (4-6 units)",
    "2B": "Rental (7-10 units)",
    "2C": "Co-op/condo (2-10 units)",
    "3": "Utility property",
    "4": "Commercial/industrial",
}

_SQL_ASSESSMENT = """\
SELECT bbl, year, pytaxclass,
    pymktland, pymkttot, pyactland, pyacttot, pyactextot, pytxbtot,
    cbnmktland, cbnmkttot, cbnactland, cbnacttot, cbnactextot, cbntxbtot,
    fintaxclass
FROM dof_property_valuation_and_assessments
WHERE bbl = $1 AND pymkttot > 0
ORDER BY year DESC
LIMIT 1;"""

_SQL_EXEMPTIONS = """\
SELECT DISTINCT ON (e.exmpcode)
    e.exmpcode, e.exname, e.curexmptot, e.year,
    c.description AS code_description
FROM dof_exemptions e
LEFT JOIN dof_exemption_classification_codes c
    ON e.exmpcode = c.exemptcode
WHERE e.bbl = $1
ORDER BY e.exmpcode, e.curexmptot DESC NULLS LAST;"""


@mcp.tool()
async def get_tax_info(bbl: str) -> dict:
    """Get property tax assessment, market value, and exemption details.

    Shows assessed and market values (land and total), tax class, taxable
    value, and any active tax exemptions like 421a, J-51, or STAR.
    """
    try:
        validate_bbl(bbl)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc

    result: dict = {"bbl": bbl, "data_sources": []}

    # Assessment
    try:
        assessment = await fetch_one(_SQL_ASSESSMENT, bbl)
        if assessment:
            tax_class = assessment.get("pytaxclass") or assessment.get("fintaxclass")
            tax_class_str = str(tax_class).strip() if tax_class else None
            result["assessment"] = {
                "year": assessment.get("year"),
                "tax_class": tax_class_str,
                "tax_class_description": (
                    _TAX_CLASS_DESCRIPTIONS.get(tax_class_str, "Unknown")
                    if tax_class_str
                    else None
                ),
                "market_value_land": assessment.get("pymktland"),
                "market_value_total": assessment.get("pymkttot"),
                "assessed_value_land": assessment.get("pyactland"),
                "assessed_value_total": assessment.get("pyacttot"),
                "exempt_value": assessment.get("pyactextot"),
                "taxable_value": assessment.get("pytxbtot"),
                "market_value_land_formatted": format_currency(assessment.get("pymktland")),
                "market_value_total_formatted": format_currency(assessment.get("pymkttot")),
                "assessed_value_total_formatted": format_currency(assessment.get("pyacttot")),
                "taxable_value_formatted": format_currency(assessment.get("pytxbtot")),
            }
            # Include tentative values if available
            cbn_total = assessment.get("cbnmkttot")
            if cbn_total and cbn_total > 0:
                result["tentative_values"] = {
                    "market_value_land": assessment.get("cbnmktland"),
                    "market_value_total": cbn_total,
                    "assessed_value_total": assessment.get("cbnacttot"),
                    "market_value_total_formatted": format_currency(cbn_total),
                }
        else:
            result["assessment"] = None
            result["assessment_note"] = "No assessment records found for this BBL."
        result["data_sources"].append(data_freshness_note("rpad"))
    except asyncpg.UndefinedTableError:
        result["assessment"] = None
        result["assessment_note"] = (
            "Assessment data table not loaded. "
            "Available after Phase B data ingestion."
        )

    # Exemptions
    try:
        exemptions = await fetch_all(_SQL_EXEMPTIONS, bbl)
        result["exemptions"] = [
            {
                "code": e.get("exmpcode"),
                "name": (e.get("exname") or e.get("code_description") or "").strip(),
                "exempt_value": e.get("curexmptot"),
                "exempt_value_formatted": format_currency(e.get("curexmptot")),
                "year": e.get("year"),
            }
            for e in exemptions
        ]
        result["has_exemptions"] = len(exemptions) > 0
        result["total_exempt_value"] = sum(
            e.get("curexmptot") or 0 for e in exemptions
        )
        result["total_exempt_value_formatted"] = format_currency(
            result["total_exempt_value"]
        )
        result["data_sources"].append(data_freshness_note("rpad"))
    except asyncpg.UndefinedTableError:
        result["exemptions"] = []
        result["has_exemptions"] = None
        result["exemptions_note"] = (
            "Exemptions data table not loaded. "
            "Available after Phase B data ingestion."
        )

    result["data_as_of"] = "; ".join(dict.fromkeys(result.pop("data_sources")))
    return result
