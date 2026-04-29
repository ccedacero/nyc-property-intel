"""Liens and encumbrances tool — tax liens and ACRIS mortgage records.

Returns DOF tax lien sale list entries and ACRIS mortgage/lien documents
for a given BBL.
"""

from __future__ import annotations

from typing import Any

import logging

import asyncpg
from mcp.server.fastmcp.exceptions import ToolError

from nyc_property_intel.app import mcp
from nyc_property_intel.db import fetch_all
from nyc_property_intel.utils import (
    data_freshness_note,
    format_currency,
    validate_bbl,
)

logger = logging.getLogger(__name__)

_SQL_TAX_LIENS = """\
SELECT bbl, cycle, borough, block, lot, taxclasscode, buildingclass,
    housenumber, streetname, zipcode, waterdebtonly, reportdate
FROM dof_tax_lien_sale_list
WHERE bbl = $1
ORDER BY reportdate DESC NULLS LAST;"""

_SQL_MORTGAGES = """\
SELECT m.documentid, m.doctype, m.docdate, m.docamount, m.recordedfiled,
    (
        SELECT jsonb_agg(jsonb_build_object(
            'name', p.name, 'party_type',
            CASE p.partytype WHEN 1 THEN 'borrower' WHEN 2 THEN 'lender' ELSE 'other' END
        ) ORDER BY p.partytype, p.name)
        FROM real_property_parties p
        WHERE p.documentid = m.documentid
    ) AS parties
FROM real_property_legals l
JOIN real_property_master m ON l.documentid = m.documentid
WHERE l.borough = $1 AND l.block = $2::int AND l.lot = $3::int
  AND m.doctype IN ('MTGE', 'AGMT', 'ASST', 'SAT', 'SMTG', 'AL&R', 'AALR')
ORDER BY m.docdate DESC
LIMIT $4;"""


@mcp.tool()
async def get_liens_and_encumbrances(
    bbl: str,
    include_tax_liens: bool = True,
    include_mortgages: bool = True,
    limit: int = 20,
) -> dict[str, Any]:
    """Get tax liens and mortgage/encumbrance records for a property.

    Shows DOF tax lien sale list entries and ACRIS mortgage documents
    including lender names, amounts, and satisfaction records. Use this
    to assess a property's debt profile and lien exposure.
    """
    try:
        borough_str, block_str, lot_str = validate_bbl(bbl)
        borough, block, lot = int(borough_str), int(block_str), int(lot_str)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc

    if limit < 1 or limit > 100:
        raise ToolError("limit must be between 1 and 100.")

    result: dict = {"bbl": bbl, "data_sources": []}

    # Tax liens
    if include_tax_liens:
        try:
            liens = await fetch_all(_SQL_TAX_LIENS, bbl)
            result["tax_liens"] = liens
            result["has_tax_liens"] = len(liens) > 0
            if liens:
                result["tax_lien_note"] = (
                    "Property appeared on DOF tax lien sale list. "
                    "This indicates delinquent property taxes, water/sewer charges, "
                    "or other municipal charges."
                )
            result["data_sources"].append(data_freshness_note("dof_tax_liens"))
        except asyncpg.UndefinedTableError:
            result["tax_liens"] = []
            result["has_tax_liens"] = None
            result["tax_liens_note"] = (
                "Tax lien data not loaded. Available after Phase B data ingestion."
            )

    # Mortgages from ACRIS
    if include_mortgages:
        try:
            mortgages = await fetch_all(
                _SQL_MORTGAGES, borough, block, lot, limit
            )
            for m in mortgages:
                m["docamount_formatted"] = format_currency(m.get("docamount"))
            result["mortgages"] = mortgages
            result["data_sources"].append(data_freshness_note("acris_legals"))
        except asyncpg.UndefinedTableError:
            result["mortgages"] = []
            result["mortgages_note"] = (
                "ACRIS mortgage data not loaded. Available after Phase C data ingestion."
            )

    result["data_as_of"] = (
        "; ".join(dict.fromkeys(result.pop("data_sources")))
        if result["data_sources"]
        else "No data sources queried."
    )
    return result
