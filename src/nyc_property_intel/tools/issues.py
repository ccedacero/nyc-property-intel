"""Property violations tool — HPD and DOB violation records.

Returns housing (HPD) and building code (DOB) violations for a property,
with optional filtering by source, severity class, status, and date range.
Includes a summary from the materialized view when available.
"""

from __future__ import annotations

import datetime
import logging
from typing import Any

import asyncpg
from mcp.server.fastmcp.exceptions import ToolError

from nyc_property_intel.app import mcp
from nyc_property_intel.db import fetch_all, fetch_one
from nyc_property_intel.utils import data_freshness_note, validate_bbl

logger = logging.getLogger(__name__)

_VALID_SOURCES = {"HPD", "DOB", "ALL"}

_SQL_SUMMARY = """\
SELECT bbl, hpd_total, hpd_class_a, hpd_class_b, hpd_class_c, hpd_open,
    hpd_most_recent, dob_total, dob_no_disposition, dob_has_disposition,
    dob_most_recent
FROM mv_violation_summary WHERE bbl = $1;"""

_SQL_HPD = """\
SELECT violationid, class, inspectiondate, approveddate, currentstatus,
    violationstatus, novdescription, novissueddate, apartment, story, rentimpairing
FROM hpd_violations
WHERE bbl = $1
  AND ($2::text IS NULL OR class = $2)
  AND ($3::text IS NULL OR currentstatus = $3)
  AND ($4::date IS NULL OR inspectiondate >= $4)
ORDER BY inspectiondate DESC
LIMIT $5;"""

_SQL_DOB = """\
SELECT isndobbisviol, bbl, issuedate, violationtypecode, violationtype,
    violationcategory, description, dispositiondate, dispositioncomments,
    penalityapplied, violationnumber
FROM dob_violations
WHERE bbl = $1
  AND ($2::date IS NULL OR issuedate >= $2)
ORDER BY issuedate DESC
LIMIT $3;"""


@mcp.tool()
async def get_property_issues(
    bbl: str,
    source: str = "ALL",
    status: str | None = None,
    severity: str | None = None,
    since_date: str | None = None,
    limit: int = 50,
    include_summary: bool = True,
) -> dict:
    """Get HPD housing violations and DOB building code violations for a property. HPD Class C violations are immediately hazardous. Returns both summary counts and violation details. Use this to assess a building's regulatory risk profile."""
    # ── Validate inputs ──────────────────────────────────────────────
    try:
        validate_bbl(bbl)
    except ValueError as exc:
        raise ToolError(str(exc))

    source_upper = source.upper()
    if source_upper not in _VALID_SOURCES:
        raise ToolError(
            f"Invalid source: {source!r}. Must be one of: HPD, DOB, ALL."
        )

    # Parse since_date if provided.
    since: datetime.date | None = None
    if since_date is not None:
        try:
            since = datetime.date.fromisoformat(since_date)
        except ValueError:
            raise ToolError(
                f"Invalid date format: {since_date!r}. "
                "Please use ISO 8601 format: YYYY-MM-DD."
            )

    # ── Summary from materialized view ───────────────────────────────
    summary: dict[str, Any] | None = None
    if include_summary:
        try:
            summary = await fetch_one(_SQL_SUMMARY, bbl)
        except asyncpg.UndefinedTableError:
            logger.info("mv_violation_summary not available, skipping summary")
            summary = None

    # ── HPD violations ───────────────────────────────────────────────
    hpd_violations: list[dict[str, Any]] = []
    if source_upper in ("HPD", "ALL"):
        hpd_violations = await fetch_all(
            _SQL_HPD,
            bbl,
            severity,   # $2 — class filter (A/B/C)
            status,     # $3 — currentstatus filter
            since,      # $4 — date filter
            limit,      # $5 — row limit
        )

    # ── DOB violations ───────────────────────────────────────────────
    dob_violations: list[dict[str, Any]] = []
    if source_upper in ("DOB", "ALL"):
        dob_violations = await fetch_all(
            _SQL_DOB,
            bbl,
            since,      # $2 — date filter
            limit,      # $3 — row limit
        )

    # ── Build response ───────────────────────────────────────────────
    total_returned = len(hpd_violations) + len(dob_violations)

    freshness_parts: list[str] = []
    if source_upper in ("HPD", "ALL"):
        freshness_parts.append(data_freshness_note("hpd_violations"))
    if source_upper in ("DOB", "ALL"):
        freshness_parts.append(data_freshness_note("dob_violations"))

    return {
        "bbl": bbl,
        "summary": summary,
        "hpd_violations": hpd_violations,
        "dob_violations": dob_violations,
        "total_returned": total_returned,
        "data_as_of": " | ".join(freshness_parts),
    }
