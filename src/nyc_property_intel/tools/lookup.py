"""Property lookup tool — the entry point for all property queries.

Resolves an address or BBL to a full property profile from PLUTO data,
including building characteristics, zoning, assessed values, and owner info.
"""

from __future__ import annotations

import re
from typing import Any

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

# Pulls the leading numeric house number out of a free-form address string.
# Handles hyphenated Queens style (e.g. "40-22 24th St") by taking the first
# segment. Returns None if no leading number is found (apartment numbers,
# named buildings, etc. — in which case we skip the drift check).
_HOUSE_NUMBER_RE = re.compile(r"^\s*(\d+)")


def _parse_house_number(address: str | None) -> int | None:
    if not address:
        return None
    m = _HOUSE_NUMBER_RE.match(address)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


@mcp.tool()
async def lookup_property(
    address: str | None = None,
    bbl: str | None = None,
    borough: str | None = None,
) -> dict[str, Any]:
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
        # Pass borough as a separate hint so the geocoder can use it when the
        # address has no embedded borough/zip. Never append it to the address
        # string: "123 Main St, Jamaica, NY 11435, Queens" confuses the regex
        # because the state/zip appear before the borough name.
        bbl = await resolve_address_to_bbl(address, borough_hint=borough)

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
        # Format the BBL for display (B-BBBBB-LLLL) if it parses cleanly.
        try:
            bbl_display = parse_bbl(str(bbl))["bbl_formatted"]
        except Exception:
            bbl_display = bbl
        raise ToolError(
            f"BBL {bbl_display} is a valid lot identifier but was not found in "
            "the PLUTO dataset (NYC DCP's primary property database). "
            "This is a known data gap for certain property types:\n"
            "  • Condo billing lots — the master lot record is aggregated and "
            "individual unit lots often have no PLUTO row\n"
            "  • Recently built, demolished, or reassigned lots — PLUTO lags "
            "the NYC DTM (Digital Tax Map) by one annual release cycle\n"
            "  • Large coops (tax class 2C) are sometimes excluded\n\n"
            "You can still query violations, permits, sales history, and "
            "complaints for this BBL using the other available tools."
        )

    # ── Enrich the result ────────────────────────────────────────────
    bbl_info = parse_bbl(str(row["bbl"]))
    row["bbl_formatted"] = bbl_info["bbl_formatted"]

    # Surface any mismatch between the user-supplied address and the PLUTO
    # address. The previous version said "Both refer to the same property"
    # unconditionally, which was actively misleading — e.g. "100 Bay Street,
    # Staten Island" resolved to "40 BAY STREET LANDING" (a different building
    # 60 house numbers away). Now we (a) reject matches with a big house-
    # number gap and (b) downgrade the note to a verification prompt instead
    # of asserting equivalence (M5 fix).
    pluto_address = row.get("address")
    if address is not None and pluto_address is not None:
        row["address_queried"] = address
        row["address_pluto"] = pluto_address

        queried_hn = _parse_house_number(address)
        pluto_hn = _parse_house_number(pluto_address)
        hn_drift = (
            abs(queried_hn - pluto_hn)
            if queried_hn is not None and pluto_hn is not None
            else None
        )

        # Refuse the match when the house number drift is unambiguously large.
        # 10 is roughly one block. Bigger gaps mean the geocoder snapped to a
        # different building. Letting that through with a misleading
        # "same property" note destroys credibility — better to ask the user
        # to clarify.
        if hn_drift is not None and hn_drift > 10:
            raise ToolError(
                f"Could not find an exact match for {address!r}. "
                f"The closest PLUTO record is {pluto_address!r} (house number "
                f"differs by {hn_drift}). These are likely different properties. "
                f"Please verify the address — e.g. include the borough, ZIP, "
                f"or a more specific street name."
            )

        if address.strip().upper() != pluto_address.strip().upper():
            row["address_note"] = (
                f"The address you searched ({address!r}) was resolved to BBL "
                f"{bbl_info['bbl_formatted']}, which PLUTO stores as "
                f"{pluto_address!r}. Closest match found — please verify "
                f"this is the correct property."
            )

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
