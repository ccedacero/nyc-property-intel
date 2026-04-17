"""Building permits tool — DOB job filings and permit history.

Returns DOB job applications (new buildings, alterations, demolitions)
from the legacy BIS system for a given BBL.
"""

from __future__ import annotations

import logging

import asyncpg
from mcp.server.fastmcp.exceptions import ToolError

from nyc_property_intel.app import mcp
from nyc_property_intel.db import fetch_all
from nyc_property_intel.utils import data_freshness_note, validate_bbl

logger = logging.getLogger(__name__)

_JOB_TYPE_DESCRIPTIONS: dict[str, str] = {
    "NB": "New Building",
    "A1": "Alteration Type 1 (major)",
    "A2": "Alteration Type 2 (minor)",
    "A3": "Alteration Type 3 (cosmetic)",
    "DM": "Demolition",
    "SG": "Sign",
}

_SQL_PERMITS = """\
SELECT DISTINCT ON (job, doc)
    job, doc, borough, house, streetname, block, lot, bin,
    jobtype, jobstatus, jobstatusdescrp, latestactiondate,
    buildingtype, prefilingdate, fullypaid, fullypermitted,
    initialcost, totalestfee, existingzoningsqft, proposedzoningsqft,
    existingdwellingunits, proposeddwellingunits,
    existingnoofstories, proposednoofstories, existingheight, proposedheight,
    jobdescription, ownersbusinessname, ownersphone,
    applicantsfirstname, applicantslastname
FROM dobjobs
WHERE bbl = $1
  AND ($2::text IS NULL OR jobtype = $2)
ORDER BY job, doc, prefilingdate DESC NULLS LAST, latestactiondate DESC NULLS LAST
LIMIT $3;"""


@mcp.tool()
async def get_building_permits(
    bbl: str,
    job_type: str | None = None,
    limit: int = 20,
) -> dict:
    """Get DOB building permit filings and job applications for a property.

    Shows new building, alteration, and demolition applications with costs,
    status, proposed changes (stories, units, height), and applicant info.
    Use this to understand planned or completed construction activity.
    """
    try:
        validate_bbl(bbl)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc

    if limit < 1 or limit > 100:
        raise ToolError("limit must be between 1 and 100.")

    if job_type and job_type.upper() not in _JOB_TYPE_DESCRIPTIONS:
        raise ToolError(
            f"Invalid job_type: {job_type!r}. "
            f"Valid types: {', '.join(_JOB_TYPE_DESCRIPTIONS.keys())}."
        )

    job_type_param = job_type.upper() if job_type else None

    try:
        permits = await fetch_all(_SQL_PERMITS, bbl, job_type_param, limit)
    except asyncpg.UndefinedTableError:
        return {
            "bbl": bbl,
            "permits": [],
            "note": "DOB jobs data not loaded. Available after Phase C data ingestion.",
        }

    for p in permits:
        jt = p.get("jobtype")
        p["jobtype_description"] = _JOB_TYPE_DESCRIPTIONS.get(jt, jt)

    return {
        "bbl": bbl,
        "permits": permits,
        "total_returned": len(permits),
        "data_as_of": data_freshness_note("dob_permits"),
    }
