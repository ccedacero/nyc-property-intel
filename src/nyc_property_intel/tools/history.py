"""Property history tool — sales, ownership transfers, and ACRIS transactions.

Provides a unified view of a property's transaction history by combining
DOF rolling sales data with ACRIS deed and document records.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

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

# ── SQL queries ──────────────────────────────────────────────────────

_SQL_SALES = """\
SELECT DISTINCT ON (bbl, saledate, saleprice)
    bbl, saledate, saleprice, address, neighborhood,
    buildingclassattimeofsale, buildingclasscategory,
    taxclassattimeofsale, residentialunits, commercialunits,
    totalunits, landsquarefeet, grosssquarefeet, yearbuilt,
    CASE WHEN saleprice IS NOT NULL AND saleprice <= 100
        THEN 'NON_ARMS_LENGTH' ELSE 'MARKET' END AS sale_type
FROM (
    SELECT * FROM dof_sales WHERE bbl = $1
    UNION ALL
    SELECT * FROM dof_annual_sales WHERE bbl = $1
) combined
ORDER BY bbl, saledate DESC, saleprice DESC
LIMIT $2;
"""

_SQL_OWNERSHIP = """\
SELECT m.documentid, m.doctype, dcc.doctypedescription AS doc_type_description,
    m.docdate, m.docamount, m.recordedfiled,
    sellers.names AS seller_names, buyers.names AS buyer_names
FROM acris_real_property_legals l
JOIN acris_real_property_master m ON l.documentid = m.documentid
JOIN acris_document_control_codes dcc ON m.doctype = dcc.doctype
LEFT JOIN LATERAL (
    SELECT array_agg(p.name ORDER BY p.name) AS names
    FROM acris_real_property_parties p
    WHERE p.documentid = m.documentid AND p.partytype = 1
) sellers ON true
LEFT JOIN LATERAL (
    SELECT array_agg(p.name ORDER BY p.name) AS names
    FROM acris_real_property_parties p
    WHERE p.documentid = m.documentid AND p.partytype = 2
) buyers ON true
WHERE l.borough = $1 AND l.block = $2::int AND l.lot = $3::int
  AND m.doctype IN ('DEED', 'DEDL', 'DEDC', 'RPTT', 'CTOR', 'CORRD')
ORDER BY m.docdate DESC
LIMIT $4;
"""

_SQL_TRANSACTIONS = """\
SELECT m.documentid, m.doctype, dcc.doctypedescription AS doc_type_description,
    m.docdate, m.docamount, m.recordedfiled,
    (
        SELECT jsonb_agg(jsonb_build_object(
            'name', p.name, 'party_type',
            CASE p.partytype WHEN 1 THEN 'seller' WHEN 2 THEN 'buyer' ELSE 'other' END
        ) ORDER BY p.partytype, p.name)
        FROM acris_real_property_parties p
        WHERE p.documentid = m.documentid
    ) AS parties
FROM acris_real_property_legals l
JOIN acris_real_property_master m ON l.documentid = m.documentid
JOIN acris_document_control_codes dcc ON m.doctype = dcc.doctype
WHERE l.borough = $1 AND l.block = $2::int AND l.lot = $3::int
  AND ($4::text IS NULL OR m.doctype = $4)
  AND ($5::date IS NULL OR m.docdate >= $5)
  AND ($6::date IS NULL OR m.docdate <= $6)
ORDER BY m.docdate DESC
LIMIT $7;
"""


def _parse_date(value: str | None) -> date | None:
    """Parse a date string in YYYY-MM-DD format, or return None."""
    if value is None:
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        raise ToolError(
            f"Invalid date format: {value!r}. Use YYYY-MM-DD (e.g. '2020-01-15')."
        )


@mcp.tool()
async def get_property_history(
    bbl: str,
    include_sales: bool = True,
    include_ownership: bool = True,
    include_transactions: bool = False,
    doc_type_filter: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 15,
) -> dict:
    """Get the history of a NYC property including sales from DOF records and ownership transfers from ACRIS deed records. Shows sale prices, dates, buyer/seller names, and document types. Use this to understand a property's transaction history and price trajectory."""

    # ── Validate inputs ───────────────────────────────────────────────
    try:
        borough, block, lot = validate_bbl(bbl)
    except ValueError as exc:
        raise ToolError(str(exc))

    parsed_start = _parse_date(start_date)
    parsed_end = _parse_date(end_date)

    if limit < 1 or limit > 100:
        raise ToolError("limit must be between 1 and 100.")

    result: dict = {
        "bbl": bbl,
        "data_sources": [],
    }

    # ── Sales section ─────────────────────────────────────────────────
    if include_sales:
        try:
            sales = await fetch_all(_SQL_SALES, bbl, limit)
            for sale in sales:
                sale["saleprice_formatted"] = format_currency(sale.get("saleprice"))
                if sale.get("sale_type") == "NON_ARMS_LENGTH":
                    sale["note"] = (
                        "Sale price <= $100 indicates a non-arm's-length "
                        "transaction (e.g., LLC transfer, estate transfer, "
                        "nominal consideration)."
                    )
            result["sales"] = sales
            result["data_sources"].append(data_freshness_note("rpad"))
        except asyncpg.UndefinedTableError:
            logger.info("Sales tables (dof_sales / dof_annual_sales) not loaded yet")
            result["sales"] = []
            result["sales_note"] = (
                "Sales data tables are not yet loaded. "
                "This data will be available after Phase B data ingestion."
            )

    # ── Ownership transfers section ───────────────────────────────────
    if include_ownership:
        try:
            ownership = await fetch_all(
                _SQL_OWNERSHIP, borough, block, lot, limit
            )
            for record in ownership:
                record["docamount_formatted"] = format_currency(
                    record.get("docamount")
                )
            result["ownership_transfers"] = ownership
            result["data_sources"].append(data_freshness_note("acris_legals"))
        except asyncpg.UndefinedTableError:
            logger.info("ACRIS tables not loaded yet — skipping ownership section")
            result["ownership_transfers"] = []
            result["ownership_note"] = (
                "ACRIS ownership data tables are not yet loaded. "
                "This data will be available after Phase C data ingestion."
            )

    # ── Transactions section (all doc types, with filters) ────────────
    if include_transactions:
        try:
            transactions = await fetch_all(
                _SQL_TRANSACTIONS,
                borough,
                block,
                lot,
                doc_type_filter,
                parsed_start,
                parsed_end,
                limit,
            )
            for record in transactions:
                record["docamount_formatted"] = format_currency(
                    record.get("docamount")
                )
            result["transactions"] = transactions
            result["data_sources"].append(data_freshness_note("acris_legals"))
        except asyncpg.UndefinedTableError:
            logger.info("ACRIS tables not loaded yet — skipping transactions section")
            result["transactions"] = []
            result["transactions_note"] = (
                "ACRIS transaction data tables are not yet loaded. "
                "This data will be available after Phase C data ingestion."
            )

    # Deduplicate data sources
    result["data_sources"] = list(dict.fromkeys(result["data_sources"]))
    result["data_as_of"] = "; ".join(result["data_sources"]) if result["data_sources"] else "No data sources queried."

    return result
