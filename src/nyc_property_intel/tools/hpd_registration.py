"""HPD registration tool — building registrations and contact info.

Returns HPD registration details including managing agent, owner,
and officer contact information for a given BBL.
"""

from __future__ import annotations

import logging

import asyncpg
from mcp.server.fastmcp.exceptions import ToolError

from nyc_property_intel.app import mcp
from nyc_property_intel.db import fetch_all, fetch_one
from nyc_property_intel.utils import data_freshness_note, validate_bbl

logger = logging.getLogger(__name__)

_SQL_REGISTRATION = """\
SELECT registrationid, buildingid, boroid, housenumber, streetname,
    zip, block, lot, bin, communityboard,
    lastregistrationdate, registrationenddate
FROM hpd_registrations
WHERE boroid = $1::smallint AND block = $2::int AND lot = $3::smallint
ORDER BY lastregistrationdate DESC NULLS LAST
LIMIT 1;"""

_SQL_CONTACTS = """\
SELECT type, contactdescription, corporationname,
    title, firstname, middleinitial, lastname,
    businesshousenumber, businessstreetname, businessapartment,
    businesscity, businessstate, businesszip
FROM hpd_contacts
WHERE registrationid = $1
ORDER BY type, lastname;"""


@mcp.tool()
async def get_hpd_registration(bbl: str) -> dict:
    """Get HPD building registration and contact info.

    Shows the managing agent, corporate owner, head officer, and site
    manager for a registered NYC building. Required for buildings with
    3+ residential units. Use this to find who manages or owns a building.
    """
    try:
        borough_str, block_str, lot_str = validate_bbl(bbl)
        borough, block, lot = int(borough_str), int(block_str), int(lot_str)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc

    result: dict = {"bbl": bbl}

    try:
        registration = await fetch_one(_SQL_REGISTRATION, borough, block, lot)
    except asyncpg.UndefinedTableError:
        return {
            "bbl": bbl,
            "registration": None,
            "note": "HPD registration data not loaded. Available after Phase A data ingestion.",
        }

    if not registration:
        return {
            "bbl": bbl,
            "registration": None,
            "note": "No HPD registration found. Building may have fewer than 3 residential units or registration has lapsed.",
            "data_as_of": data_freshness_note("hpd_registrations"),
        }

    reg_id = registration["registrationid"]
    result["registration"] = {
        "registration_id": reg_id,
        "building_id": registration.get("buildingid"),
        "address": f"{registration.get('housenumber', '')} {registration.get('streetname', '')}".strip(),
        "zip": registration.get("zip"),
        "last_registration_date": registration.get("lastregistrationdate"),
        "registration_end_date": registration.get("registrationenddate"),
    }

    # Fetch contacts for this registration
    try:
        contacts = await fetch_all(_SQL_CONTACTS, reg_id)
    except asyncpg.UndefinedTableError:
        contacts = []

    grouped: dict[str, list] = {}
    for c in contacts:
        contact_type = c.get("type") or "Unknown"
        name_parts = [c.get("firstname"), c.get("middleinitial"), c.get("lastname")]
        full_name = " ".join(p for p in name_parts if p)

        addr_parts = [c.get("businesshousenumber"), c.get("businessstreetname")]
        address = " ".join(p for p in addr_parts if p)
        if c.get("businessapartment"):
            address += f" Apt {c['businessapartment']}"
        city_parts = [c.get("businesscity"), c.get("businessstate"), c.get("businesszip")]
        city_line = ", ".join(p for p in city_parts if p)

        entry = {
            "description": c.get("contactdescription"),
            "corporation_name": c.get("corporationname"),
            "name": full_name or None,
            "title": c.get("title"),
            "address": address or None,
            "city_state_zip": city_line or None,
        }
        grouped.setdefault(contact_type, []).append(entry)

    result["contacts"] = grouped
    result["data_as_of"] = data_freshness_note("hpd_registrations")
    return result
