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


# Most-recent deed grantee for a BBL (M16 fix — condo billing-lot owner
# fallback). Used when PLUTO's `ownername` is null/empty, which is the
# default for condo billing lots (1 Wall, One57, 432 Park, Empire State).
# partytype = 2 in ACRIS = Grantee (buyer / current owner on the deed).
_SQL_DEED_GRANTEE = """\
SELECT p.name AS deed_owner, m.docdate
FROM real_property_legals l
JOIN real_property_master m ON l.documentid = m.documentid
JOIN real_property_parties p ON p.documentid = m.documentid
WHERE l.borough = $1 AND l.block = $2::int AND l.lot = $3::int
  AND m.doctype IN ('DEED', 'DEDL', 'DEDC', 'RPTT', 'CTOR', 'CORRD')
  AND p.partytype = 2
ORDER BY m.docdate DESC NULLS LAST
LIMIT 1;
"""


async def _fetch_deed_owner(bbl_info: dict[str, str]) -> dict[str, Any] | None:
    """Return the most recent ACRIS deed grantee for the BBL, or None."""
    try:
        return await fetch_one(
            _SQL_DEED_GRANTEE,
            bbl_info["borough"],
            int(bbl_info["block"]),
            int(bbl_info["lot"]),
        )
    except asyncpg.UndefinedTableError:
        logger.info("ACRIS deed tables not available, skipping owner fallback")
        return None
    except (asyncpg.PostgresError, ValueError) as exc:
        logger.warning("ACRIS deed-owner fallback failed: %s", exc)
        return None

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

# PLUTO writes literal placeholder strings (not NULL) when DOF has no billing
# entity recorded. Treat all of these as "owner missing" so M16's ACRIS deed
# fallback fires for condo billing lots (1 Wall, 432 Park, One57, etc.).
_PLUTO_OWNER_PLACEHOLDERS = frozenset({
    "UNAVAILABLE OWNER",
    "OWNER UNAVAILABLE",
    "NOT AVAILABLE",
    "N/A",
    "NA",
})


def _is_owner_missing(raw_owner: Any) -> bool:
    if raw_owner is None:
        return True
    s = str(raw_owner).strip()
    if not s:
        return True
    return s.upper() in _PLUTO_OWNER_PLACEHOLDERS


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

        # Surface house-number drift as a structured warning rather than
        # refusing the match. Earlier M5 raised ToolError when drift > 10,
        # but that REGRESSED famous-landmark lookups whose PLUTO
        # street-number is "vanity-different" from the colloquial address:
        #   - Empire State Building: "350 5th Ave" → PLUTO "338 5 AVENUE"
        #     (drift=12; same BBL 1008350041, owner ESRT EMPIRE STATE BUILDING)
        # Fix: trust the GeoClient-returned BBL, surface drift via a
        # warning field. Real wrong-substitutions (e.g. "4521 Broadway" →
        # "4523 Broadway", different owner) still produce a visible
        # warning + the ownername field reveals the mismatch.
        if address.strip().upper() != pluto_address.strip().upper():
            row["address_note"] = (
                f"The address you searched ({address!r}) was resolved to BBL "
                f"{bbl_info['bbl_formatted']}, which PLUTO stores as "
                f"{pluto_address!r}."
            )
            # Threshold tuned low (>2) so wrong-substitutions like
            # "4521 Broadway" → "4523 Broadway" (different building, different
            # owner) fire the warning. Landmark drift cases (Empire State
            # 350 → 338, drift=12) also fire, with copy that explains the
            # landmark pattern. Below-threshold (drift 0-2) is treated as
            # closest-match noise.
            if hn_drift is not None and hn_drift > 2:
                row["address_warning"] = (
                    f"Street-number differs by {hn_drift} from what you "
                    f"typed. For famous landmarks (e.g. Empire State Building "
                    f"= 350 5th Ave colloquially, but PLUTO stores as 338 5 "
                    f"AVENUE), this is normal. For other addresses, please "
                    f"verify the owner name matches the property you intended."
                )
            else:
                row["address_note"] += " Closest match found — please verify this is the correct property."

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

    # M16 fix: PLUTO often has no real `ownername` for condo billing lots
    # (1 Wall, One57, 432 Park, Empire State, etc.) — either NULL or a
    # literal placeholder like "UNAVAILABLE OWNER". The system prompt tells
    # the LLM to "lead with the owner", so without a fallback every luxury-
    # building lookup leads with that placeholder — a credibility hit.
    # Fall back to the most recent ACRIS deed grantee for the billing lot.
    # If that ALSO finds nothing (typical: deeds are filed against unit lots
    # 1001+ in Manhattan / 7501+ outer, not the billing lot), surface an
    # honest "condo billing lot" message rather than the raw placeholder.
    raw_owner = row.get("ownername")
    owner_missing = _is_owner_missing(raw_owner)
    row["owner_source"] = "pluto"
    if owner_missing and row["is_condo"]:
        deed = await _fetch_deed_owner(bbl_info)
        if deed and deed.get("deed_owner"):
            row["ownername"] = deed["deed_owner"]
            row["owner_source"] = "acris_deed"
            row["owner_deed_date"] = deed.get("docdate")
        else:
            row["ownername"] = "Condominium — individual unit owners"
            row["owner_source"] = "condo_aggregate_placeholder"
            row["owner_note"] = (
                "This BBL is the condo master/billing lot. Ownership is held "
                "by individual unit owners on separate unit lots (typically "
                "lot ≥ 1001 in Manhattan, ≥ 7501 in outer boroughs). Use a "
                "specific unit's BBL to look up unit-level ownership."
            )
    elif owner_missing:
        # Non-condo with placeholder ownername — surface a soft hint.
        row["ownername"] = None
        row["owner_source"] = "pluto_placeholder"
        row["owner_note"] = (
            "PLUTO has no owner of record for this BBL. This usually means "
            "DOF has not assigned a billing entity (e.g. recently created "
            "lot, government parcel)."
        )

    row["data_as_of"] = data_freshness_note(source_table)

    return row
