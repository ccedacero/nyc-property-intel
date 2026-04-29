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
from nyc_property_intel.utils import data_freshness_note, normalize_filter, validate_bbl

logger = logging.getLogger(__name__)

_VALID_SOURCES = {"HPD", "DOB", "ECB", "ALL"}

_SQL_SUMMARY = """\
SELECT bbl, hpd_total, hpd_class_a, hpd_class_b, hpd_class_c, hpd_open,
    hpd_most_recent, dob_total, dob_no_disposition, dob_has_disposition,
    dob_most_recent
FROM mv_violation_summary WHERE bbl = $1;"""

# ECB stats are not in mv_violation_summary — query directly. Cheap because
# (bbl) is indexed.
_SQL_ECB_SUMMARY = """\
SELECT
    COUNT(*) AS ecb_total,
    COUNT(*) FILTER (WHERE upper(ecbviolationstatus) = 'ACTIVE') AS ecb_active,
    COALESCE(SUM(balancedue), 0)::numeric AS ecb_balance_due_total,
    MAX(issuedate) AS ecb_most_recent
FROM ecb_violations WHERE bbl = $1;"""

_SQL_HPD = """\
SELECT violationid, class, inspectiondate, approveddate, currentstatus,
    violationstatus, novdescription, novissueddate, apartment, story, rentimpairing
FROM hpd_violations
WHERE bbl = $1
  AND ($2::text IS NULL OR upper(class) = upper($2))
  AND ($3::text IS NULL OR upper(violationstatus) = upper($3))
  AND ($4::date IS NULL OR inspectiondate >= $4)
ORDER BY inspectiondate DESC
LIMIT $5;"""

_SQL_DOB = """\
SELECT isndobbisviol, bbl, issuedate, violationtypecode, violationtype,
    violationcategory, description, dispositiondate, dispositioncomments,
    violationnumber
FROM dob_violations
WHERE bbl = $1
  AND ($2::date IS NULL OR issuedate >= $2)
ORDER BY issuedate DESC
LIMIT $3;"""

_SQL_ECB = """\
SELECT ecbviolationnumber, ecbviolationstatus, dobviolationnumber,
    issuedate, serveddate, hearingdate, severity, violationtype,
    violationdescription, respondentname,
    penalityimposed, amountpaid, balancedue,
    sectionlawdescription1
FROM ecb_violations
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
    limit: int = 25,
    include_summary: bool = True,
) -> dict[str, Any]:
    """Get HPD housing violations, DOB building code violations, and ECB/OATH violations for a property.

    HPD Class C violations are immediately hazardous. ECB violations include
    penalties and balances due. Returns both summary counts and violation
    details. Use this to assess a building's regulatory risk profile.

    Note on historical depth: our local DB retains all historical HPD
    violations and complaints, while NYC's live Socrata API rolls older
    records out of its public feed. As a result, the totals reported here
    may exceed what data.cityofnewyork.us shows for the same BBL — the
    extra rows are real, just no longer surfaced by NYC Open Data.
    """
    # ── Validate inputs ──────────────────────────────────────────────
    try:
        validate_bbl(bbl)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc

    if limit < 1 or limit > 200:
        raise ToolError("limit must be between 1 and 200.")

    source_upper = source.upper()
    if source_upper not in _VALID_SOURCES:
        raise ToolError(
            f"Invalid source: {source!r}. Must be one of: HPD, DOB, ECB, ALL."
        )

    # Normalize optional filters — DB stores uppercase values ('OPEN', 'CLOSE', 'A', 'B', 'C').
    # Accepting mixed-case from callers prevents silent empty-result bugs.
    normalized_status = normalize_filter(status)
    normalized_severity = normalize_filter(severity)

    # Parse since_date if provided.
    since: datetime.date | None = None
    if since_date is not None:
        try:
            since = datetime.date.fromisoformat(since_date)
        except ValueError as exc:
            raise ToolError(
                f"Invalid date format: {since_date!r}. "
                "Please use ISO 8601 format: YYYY-MM-DD."
            ) from exc

    # ── Summary from materialized view ───────────────────────────────
    # Always return a fully-populated summary dict so callers can rely on
    # numeric fields without null-checks. Clean buildings (no row in the
    # materialized view) get zeroed counts.
    summary: dict[str, Any] | None = None
    if include_summary:
        try:
            summary = await fetch_one(_SQL_SUMMARY, bbl)
        except asyncpg.UndefinedTableError:
            logger.info("mv_violation_summary not available, skipping summary")
            summary = None

        if summary is None:
            summary = {
                "bbl": bbl,
                "hpd_total": 0, "hpd_class_a": 0, "hpd_class_b": 0,
                "hpd_class_c": 0, "hpd_open": 0, "hpd_most_recent": None,
                "dob_total": 0, "dob_no_disposition": 0,
                "dob_has_disposition": 0, "dob_most_recent": None,
            }

        # Augment with ECB stats — not in the materialized view.
        if source_upper in ("ECB", "ALL"):
            try:
                ecb_stats = await fetch_one(_SQL_ECB_SUMMARY, bbl)
                bal = (ecb_stats or {}).get("ecb_balance_due_total")
                summary["ecb_total"] = int((ecb_stats or {}).get("ecb_total") or 0)
                summary["ecb_active"] = int((ecb_stats or {}).get("ecb_active") or 0)
                summary["ecb_balance_due_total"] = float(bal) if bal is not None else 0.0
                summary["ecb_most_recent"] = (ecb_stats or {}).get("ecb_most_recent")
            except asyncpg.UndefinedTableError:
                logger.info("ecb_violations table not loaded, skipping ECB summary")

    # ── HPD violations ───────────────────────────────────────────────
    hpd_violations: list[dict[str, Any]] = []
    if source_upper in ("HPD", "ALL"):
        try:
            hpd_violations = await fetch_all(
                _SQL_HPD,
                bbl,
                normalized_severity,  # $2 — class filter (A/B/C), uppercased
                normalized_status,    # $3 — currentstatus filter, uppercased
                since,                # $4 — date filter
                limit,                # $5 — row limit
            )
        except asyncpg.UndefinedTableError:
            logger.info("hpd_violations table not loaded, skipping HPD section")

    # ── DOB violations ───────────────────────────────────────────────
    dob_violations: list[dict[str, Any]] = []
    if source_upper in ("DOB", "ALL"):
        try:
            dob_violations = await fetch_all(
                _SQL_DOB,
                bbl,
                since,      # $2 — date filter
                limit,      # $3 — row limit
            )
        except asyncpg.UndefinedTableError:
            logger.info("dob_violations table not loaded, skipping DOB section")

    # ── ECB violations ──────────────────────────────────────────────
    ecb_violations: list[dict[str, Any]] = []
    if source_upper in ("ECB", "ALL"):
        try:
            ecb_violations = await fetch_all(
                _SQL_ECB,
                bbl,
                since,      # $2 — date filter
                limit,      # $3 — row limit
            )
        except asyncpg.UndefinedTableError:
            logger.info("ecb_violations table not loaded, skipping ECB section")

    # ── Cross-validation guard ───────────────────────────────────────
    # Warn if summary says open violations exist but filter returned nothing —
    # catches future regressions where filter normalization silently drops data.
    if (
        summary
        and normalized_status == "OPEN"
        and source_upper in ("HPD", "ALL")
        and (summary.get("hpd_open") or 0) > 0
        and len(hpd_violations) == 0
    ):
        logger.warning(
            "get_property_issues: summary reports %d open HPD violations for BBL %s "
            "but query returned 0 rows (status=%r, severity=%r, since=%r) — "
            "possible filter mismatch or stale materialized view",
            summary["hpd_open"], bbl, normalized_status, normalized_severity, since,
        )

    # ── Build response ───────────────────────────────────────────────
    total_returned = len(hpd_violations) + len(dob_violations) + len(ecb_violations)

    freshness_parts: list[str] = []
    if source_upper in ("HPD", "ALL"):
        freshness_parts.append(data_freshness_note("hpd_violations"))
    if source_upper in ("DOB", "ALL"):
        freshness_parts.append(data_freshness_note("dob_violations"))
    if source_upper in ("ECB", "ALL"):
        freshness_parts.append(data_freshness_note("ecb_violations"))

    return {
        "bbl": bbl,
        "summary": summary,
        "hpd_violations": hpd_violations,
        "dob_violations": dob_violations,
        "ecb_violations": ecb_violations,
        "total_returned": total_returned,
        "data_as_of": " | ".join(freshness_parts),
    }
