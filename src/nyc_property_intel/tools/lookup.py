"""Property lookup tool — the entry point for all property queries.

Resolves an address or BBL to a full property profile from PLUTO data,
including building characteristics, zoning, assessed values, and owner info.
"""

from __future__ import annotations

import logging

import asyncpg
from mcp.server.fastmcp.exceptions import ToolError

from nyc_property_intel.app import mcp
from nyc_property_intel.db import fetch_one
from nyc_property_intel.geoclient import resolve_address_to_bbl
from nyc_property_intel.utils import data_freshness_note, parse_bbl, validate_bbl

logger = logging.getLogger(__name__)

_PROFILE_COLUMNS = """\
bbl, address, borough, block, lot, ownername, bldgclass, landuse,
    zonedist1, zonedist2, overlay1, spdist1,
    numbldgs, numfloors, unitsres, unitstotal,
    lotarea, bldgarea, comarea, resarea, officearea, retailarea,
    yearbuilt, yearalter1, yearalter2, condono,
    builtfar, residfar, commfar, facilfar,
    assessland, assesstot, exempttot,
    histdist, landmark, latitude, longitude, postcode"""

_SQL_PRIMARY = f"""\
SELECT {_PROFILE_COLUMNS}
FROM mv_property_profile WHERE bbl = $1;"""

_SQL_FALLBACK = f"""\
SELECT {_PROFILE_COLUMNS}
FROM pluto_latest WHERE bbl = $1;"""


@mcp.tool()
async def lookup_property(
    address: str | None = None,
    bbl: str | None = None,
    borough: str | None = None,
) -> dict:
    """Look up a NYC property by address or BBL.

    Returns the full property profile including building details, zoning,
    assessed value, owner, and lot characteristics. This is always the first
    tool to call — you need a BBL before using other tools.
    """
    # ── Resolve BBL ──────────────────────────────────────────────────
    if address is None and bbl is None:
        raise ToolError(
            "Please provide either an address or a BBL. "
            "Example address: \"123 Main St, Brooklyn, NY 11201\". "
            "Example BBL: \"3012340001\"."
        )
    if address is not None and bbl is not None:
        raise ToolError("Provide either address or bbl, not both.")

    if bbl is not None:
        try:
            validate_bbl(bbl)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
    else:
        # address is not None — resolve it.
        # Append borough to the address string if provided and not already
        # present, so the geoclient parser can use it for disambiguation.
        resolve_input = address
        if borough:
            addr_lower = address.lower()
            boro_lower = borough.lower()
            if boro_lower not in addr_lower:
                resolve_input = f"{address}, {borough}"
        bbl = await resolve_address_to_bbl(resolve_input)

    # ── Query property profile ───────────────────────────────────────
    row: dict | None = None
    source_table = "pluto"

    try:
        row = await fetch_one(_SQL_PRIMARY, bbl)
        if row is not None:
            source_table = "pluto"
    except asyncpg.UndefinedTableError:
        logger.info("mv_property_profile not found, falling back to pluto_latest")
        row = None

    if row is None:
        try:
            row = await fetch_one(_SQL_FALLBACK, bbl)
        except asyncpg.UndefinedTableError:
            logger.info("pluto_latest table not found either")
            row = None

    if row is None:
        raise ToolError(
            f"No property found for BBL {bbl}. "
            "This BBL may not exist or may not yet be in the PLUTO dataset. "
            "Double-check the address or BBL and try again."
        )

    # ── Enrich the result ────────────────────────────────────────────
    bbl_info = parse_bbl(str(row["bbl"]))
    row["bbl_formatted"] = bbl_info["bbl_formatted"]

    # Condo detection: lot >= 1000 in Manhattan, lot >= 7501 in outer boroughs,
    # or condono field is present. The lot-number heuristic matches PLUTO's
    # convention where condo unit lots start at 1001 (Manhattan) / 7501 (outer).
    lot_int = int(bbl_info["lot"])
    is_manhattan = bbl_info["borough"] == "1"
    condono = row.get("condono")
    row["is_condo"] = (
        (is_manhattan and lot_int >= 1000)
        or (not is_manhattan and lot_int >= 7501)
        or (condono is not None and condono != "")
    )

    row["data_as_of"] = data_freshness_note(source_table)

    return row
