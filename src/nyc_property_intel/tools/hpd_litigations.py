"""HPD litigations tool — buildings sued by HPD for housing violations.

Returns HPD litigation records including case type (heat/services, harassment,
tenant protection), case status, harassment findings, and penalties. This is
the strongest signal of a problem building — HPD only litigates the worst
offenders.
"""

from __future__ import annotations

import logging
from typing import Any

import asyncpg
from mcp.server.fastmcp.exceptions import ToolError

from nyc_property_intel.app import mcp
from nyc_property_intel.db import fetch_all
from nyc_property_intel.utils import data_freshness_note, validate_bbl

logger = logging.getLogger(__name__)

_SQL_LITIGATIONS = """\
SELECT litigationid, casetype, caseopendate, casestatus,
    openjudgement, findingofharassment, findingdate,
    penalty, respondent
FROM hpd_litigations
WHERE bbl = $1
ORDER BY caseopendate DESC NULLS LAST;"""

_SQL_LITIGATION_SUMMARY = """\
SELECT
    COUNT(*) AS total_cases,
    COUNT(*) FILTER (WHERE casestatus = 'OPEN' OR casestatus = 'ACTIVE') AS open_cases,
    COUNT(*) FILTER (WHERE findingofharassment IS NOT NULL
        AND findingofharassment != '' AND findingofharassment != 'NO') AS harassment_findings,
    COUNT(*) FILTER (WHERE openjudgement IS NOT NULL
        AND openjudgement != '') AS open_judgements,
    MAX(caseopendate) AS most_recent_case,
    -- Case type distribution
    (SELECT jsonb_agg(row_to_json(t)) FROM (
        SELECT casetype, COUNT(*) AS cnt
        FROM hpd_litigations WHERE bbl = $1
        GROUP BY casetype ORDER BY cnt DESC
    ) t) AS case_types
FROM hpd_litigations WHERE bbl = $1;"""


@mcp.tool()
async def get_hpd_litigations(bbl: str) -> dict:
    """Get HPD litigation history — cases where HPD sued the building owner.

    HPD only litigates the worst-offending buildings. This is a strong
    red flag for investors. Shows case types (heat/services, harassment,
    tenant protection), harassment findings, open judgements, and penalties.
    A building with HPD litigation history carries significant regulatory risk.
    """
    try:
        validate_bbl(bbl)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc

    result: dict[str, Any] = {"bbl": bbl}

    try:
        # Summary
        summary_rows = await fetch_all(_SQL_LITIGATION_SUMMARY, bbl)
        summary = summary_rows[0] if summary_rows else None

        # Detail records
        litigations = await fetch_all(_SQL_LITIGATIONS, bbl)

        result["summary"] = summary
        result["litigations"] = litigations
        result["has_litigation_history"] = len(litigations) > 0

        if summary and summary.get("harassment_findings", 0) > 0:
            result["harassment_warning"] = (
                "This building has harassment findings by HPD. "
                "This is a serious red flag indicating the landlord was found "
                "to have engaged in tenant harassment."
            )

        result["data_as_of"] = data_freshness_note("hpd_litigations")

    except asyncpg.UndefinedTableError:
        result["litigations"] = []
        result["has_litigation_history"] = None
        result["note"] = "HPD litigations data not loaded."

    return result
