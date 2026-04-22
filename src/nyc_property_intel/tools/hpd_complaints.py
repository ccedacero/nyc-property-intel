"""HPD complaints tool — tenant-reported housing complaints and problems.

Returns HPD complaint records showing what tenants are actively reporting:
plumbing, heat/hot water, pests, paint, etc. Complaints are leading
indicators — they show problems *before* they become formal violations.
"""

from __future__ import annotations

import datetime
import json
import logging
from typing import Any

import asyncpg
from mcp.server.fastmcp.exceptions import ToolError

from nyc_property_intel.app import mcp
from nyc_property_intel.db import fetch_all
from nyc_property_intel.utils import data_freshness_note, escape_like, normalize_filter, validate_bbl

logger = logging.getLogger(__name__)

_SQL_COMPLAINTS = """\
SELECT complaintid, receiveddate, complaintstatus, complaintstatusdate,
    apartment, unittype, spacetype, type, majorcategory, minorcategory,
    problemcode, problemstatus, problemstatusdate, statusdescription
FROM hpd_complaints_and_problems
WHERE bbl = $1
  AND ($2::text IS NULL OR upper(complaintstatus) = upper($2))
  AND ($3::text IS NULL OR majorcategory ILIKE '%' || $3 || '%')
  AND ($4::date IS NULL OR receiveddate >= $4)
  AND problemduplicateflag IS NOT TRUE
ORDER BY receiveddate DESC
LIMIT $5;"""

_SQL_COMPLAINT_SUMMARY = """\
SELECT
    COUNT(DISTINCT complaintid) AS total_complaints,
    COUNT(*) AS total_problems,
    COUNT(*) FILTER (WHERE complaintstatus = 'OPEN') AS open_complaints,
    COUNT(*) FILTER (WHERE complaintstatus = 'CLOSE') AS closed_complaints,
    MAX(receiveddate) AS most_recent,
    -- Top 5 complaint categories
    (SELECT jsonb_agg(row_to_json(t)) FROM (
        SELECT majorcategory, COUNT(*) AS cnt
        FROM hpd_complaints_and_problems
        WHERE bbl = $1 AND problemduplicateflag IS NOT TRUE
        GROUP BY majorcategory ORDER BY cnt DESC LIMIT 5
    ) t) AS top_categories
FROM hpd_complaints_and_problems
WHERE bbl = $1 AND problemduplicateflag IS NOT TRUE;"""


@mcp.tool()
async def get_hpd_complaints(
    bbl: str,
    status: str | None = None,
    category: str | None = None,
    since_date: str | None = None,
    limit: int = 25,
    include_summary: bool = True,
) -> dict:
    """Get HPD tenant complaints and reported problems for a property.

    Complaints are leading indicators of building distress — they show what
    tenants are reporting before formal violations are issued. Categories
    include PLUMBING, PAINT/PLASTER, HEAT/HOT WATER, PEST CONTROL, etc.
    Use this alongside violations to assess a building's condition.
    """
    try:
        validate_bbl(bbl)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc

    if limit < 1 or limit > 200:
        raise ToolError("limit must be between 1 and 200.")

    since: datetime.date | None = None
    if since_date is not None:
        try:
            since = datetime.date.fromisoformat(since_date)
        except ValueError as exc:
            raise ToolError(
                f"Invalid date format: {since_date!r}. Use YYYY-MM-DD."
            ) from exc

    safe_category = escape_like(category) if category else None
    normalized_status = normalize_filter(status)
    result: dict[str, Any] = {"bbl": bbl}

    try:
        # Summary
        if include_summary:
            summary_rows = await fetch_all(_SQL_COMPLAINT_SUMMARY, bbl)
            if summary_rows:
                summary = dict(summary_rows[0])
                # asyncpg returns jsonb_agg subqueries as a JSON string in some
                # fetch paths — parse it back to a native list when needed.
                raw = summary.get("top_categories")
                if isinstance(raw, str):
                    try:
                        summary["top_categories"] = json.loads(raw)
                    except json.JSONDecodeError:
                        pass
                result["summary"] = summary
            else:
                result["summary"] = None

        # Detail records
        complaints = await fetch_all(
            _SQL_COMPLAINTS, bbl, normalized_status, safe_category, since, limit
        )
        result["complaints"] = complaints
        result["total_returned"] = len(complaints)
        result["data_as_of"] = data_freshness_note("hpd_complaints")

    except asyncpg.UndefinedTableError:
        result["complaints"] = []
        result["total_returned"] = 0
        result["note"] = "HPD complaints data not loaded."

    return result
