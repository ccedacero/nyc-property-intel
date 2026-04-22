"""Compound analysis tool — comprehensive due diligence summary.

Aggregates data from multiple sources (property profile, violations, complaints,
litigations, sales history, tax assessment, evictions, permits, 311, mortgages,
and comparable sales) into a single investment-grade property report.
"""

from __future__ import annotations

import asyncio
import logging
import statistics
from typing import Any

import asyncpg
from mcp.server.fastmcp.exceptions import ToolError

from nyc_property_intel.app import mcp
from nyc_property_intel.db import fetch_all, fetch_one
from nyc_property_intel.utils import parse_bbl, validate_bbl

logger = logging.getLogger(__name__)

# ── SQL queries ──────────────────────────────────────────────────────

_SQL_PROFILE = """\
SELECT bbl, address, borough, block, lot, ownername, bldgclass, landuse,
    zonedist1, zonedist2, overlay1, spdist1,
    numbldgs, numfloors, unitsres, unitstotal,
    lotarea, bldgarea, comarea, resarea, officearea, retailarea,
    yearbuilt, yearalter1, yearalter2, condono,
    builtfar, residfar, commfar, facilfar,
    assessland, assesstot, exempttot,
    histdist, landmark, latitude, longitude, postcode
FROM mv_property_profile WHERE bbl = $1;"""

_SQL_PROFILE_FALLBACK = """\
SELECT bbl, address, borough, block, lot, ownername, bldgclass, landuse,
    zonedist1, zonedist2, overlay1, spdist1,
    numbldgs, numfloors, unitsres, unitstotal,
    lotarea, bldgarea, comarea, resarea, officearea, retailarea,
    yearbuilt, yearalter1, yearalter2, condono,
    builtfar, residfar, commfar, facilfar,
    assessland, assesstot, exempttot,
    histdist, landmark, latitude, longitude, postcode
FROM pluto_latest WHERE bbl = $1;"""

_SQL_VIOLATION_SUMMARY = """\
SELECT bbl, hpd_total, hpd_class_a, hpd_class_b, hpd_class_c, hpd_open,
    hpd_most_recent, dob_total, dob_no_disposition, dob_has_disposition,
    dob_most_recent
FROM mv_violation_summary WHERE bbl = $1;"""

_SQL_RECENT_SALES = """\
SELECT saledate, saleprice, address, apartmentnumber, buildingclasscategory,
    buildingclassattimeofsale, residentialunits, commercialunits, totalunits,
    landsquarefeet, grosssquarefeet
FROM dof_sales
WHERE bbl = $1
ORDER BY saledate DESC
LIMIT 5;"""

_SQL_COMP_SALES = """\
SELECT bbl, address, saledate, saleprice, grosssquarefeet,
    buildingclasscategory, totalunits
FROM dof_sales
WHERE zipcode = $1
  AND saledate >= (CURRENT_DATE - INTERVAL '12 months')
  AND saleprice > 10000
  AND grosssquarefeet > 0
ORDER BY saledate DESC
LIMIT 10;"""

_SQL_OWNERSHIP = """\
SELECT bbl, documentid, doctype, doc_type_description, docdate, docamount,
    owner_name, address1, city, state, zip, recordedfiled
FROM mv_current_ownership WHERE bbl = $1;"""

_SQL_TAX_LIENS = """\
SELECT bbl, cycle, reportdate, waterdebtonly
FROM dof_tax_lien_sale_list WHERE bbl = $1
ORDER BY reportdate DESC NULLS LAST LIMIT 1;"""

_SQL_RENTSTAB = """\
SELECT ucbbl, uc2017, uc2016, uc2015, uc2014, est2017, unitsres
FROM rentstab WHERE ucbbl = $1;"""

_SQL_EXEMPTIONS = """\
SELECT e.exmpcode, e.exname, e.curexmptot,
    c.description AS code_description
FROM dof_exemptions e
LEFT JOIN dof_exemption_classification_codes c
    ON e.exmpcode = c.exemptcode
WHERE e.bbl = $1
ORDER BY e.curexmptot DESC NULLS LAST LIMIT 5;"""

# ── New summary SQL queries ──────────────────────────────────────────

_SQL_HPD_COMPLAINTS_SUMMARY = """\
SELECT
    COUNT(DISTINCT complaintid) AS total_complaints,
    COUNT(*) FILTER (WHERE complaintstatus = 'OPEN') AS open_complaints,
    MAX(receiveddate) AS most_recent
FROM hpd_complaints_and_problems
WHERE bbl = $1 AND problemduplicateflag IS NOT TRUE;"""

_SQL_HPD_LITIGATIONS_SUMMARY = """\
SELECT
    COUNT(*) AS total_cases,
    COUNT(*) FILTER (WHERE casestatus IN ('OPEN', 'ACTIVE')) AS open_cases,
    COUNT(*) FILTER (
        WHERE findingofharassment IS NOT NULL
          AND findingofharassment NOT IN ('', 'NO')
    ) AS harassment_findings,
    MAX(caseopendate) AS most_recent_case
FROM hpd_litigations WHERE bbl = $1;"""

_SQL_HPD_REGISTRATION = """\
SELECT registrationid, lastregistrationdate, registrationenddate
FROM hpd_registrations
WHERE boroid = $1::smallint AND block = $2::int AND lot = $3::smallint
ORDER BY lastregistrationdate DESC NULLS LAST
LIMIT 1;"""

_SQL_HPD_REGISTRATION_AGENT = """\
SELECT type, firstname, middleinitial, lastname, corporationname
FROM hpd_contacts
WHERE registrationid = $1 AND type IN ('ManagingAgent', 'Agent')
LIMIT 1;"""

_SQL_EVICTIONS_SUMMARY = """\
SELECT
    COUNT(*) AS total_evictions,
    COUNT(*) FILTER (WHERE upper(residentialcommercialind) = 'RESIDENTIAL') AS residential_evictions,
    COUNT(*) FILTER (WHERE upper(residentialcommercialind) = 'COMMERCIAL') AS commercial_evictions,
    MAX(executeddate::text) AS most_recent
FROM marshal_evictions_all WHERE bbl = $1;"""

_SQL_PERMITS_SUMMARY = """\
SELECT
    COUNT(*) AS total_filings,
    MAX(prefilingdate) AS most_recent_filing,
    COUNT(*) FILTER (WHERE jobtype = 'NB') AS new_buildings,
    COUNT(*) FILTER (WHERE jobtype IN ('A1', 'A2')) AS alterations,
    COUNT(*) FILTER (WHERE jobtype = 'DM') AS demolitions
FROM dobjobs WHERE bbl = $1;"""

_SQL_311_SUMMARY = """\
SELECT
    COUNT(*) AS total_complaints,
    COUNT(*) FILTER (WHERE upper(status) = 'OPEN') AS open_complaints,
    MAX(created_date) AS most_recent
FROM nyc_311_complaints WHERE bbl = $1;"""

_SQL_MORTGAGES_SUMMARY = """\
SELECT
    COUNT(*) AS total_recorded,
    COUNT(*) FILTER (WHERE m.doctype IN ('MTGE', 'AGMT', 'SMTG')) AS active_mortgages,
    COUNT(*) FILTER (WHERE m.doctype = 'SAT') AS satisfactions,
    MAX(m.docdate) AS most_recent_date,
    SUM(CASE WHEN m.doctype IN ('MTGE', 'AGMT', 'SMTG')
        THEN m.docamount ELSE 0 END) AS total_mortgage_amount
FROM real_property_legals l
JOIN real_property_master m ON l.documentid = m.documentid
WHERE l.borough = $1 AND l.block = $2::int AND l.lot = $3::int
  AND m.doctype IN ('MTGE', 'AGMT', 'ASST', 'SAT', 'SMTG', 'AL&R', 'AALR');"""


# ── Existing sub-query runners ────────────────────────────────────────

async def _fetch_profile(bbl: str) -> dict[str, Any] | None:
    try:
        row = await fetch_one(_SQL_PROFILE, bbl)
        if row is not None:
            return row
    except asyncpg.UndefinedTableError:
        logger.info("mv_property_profile not available, trying pluto_latest")
    return await fetch_one(_SQL_PROFILE_FALLBACK, bbl)


async def _fetch_violation_summary(bbl: str) -> dict[str, Any] | None:
    try:
        return await fetch_one(_SQL_VIOLATION_SUMMARY, bbl)
    except asyncpg.UndefinedTableError:
        logger.info("mv_violation_summary not available")
        return None


async def _fetch_recent_sales(bbl: str) -> list[dict[str, Any]]:
    try:
        return await fetch_all(_SQL_RECENT_SALES, bbl)
    except asyncpg.UndefinedTableError:
        logger.info("dof_sales table not available (Phase A)")
        return []


async def _fetch_comp_sales(zip_code: str) -> list[dict[str, Any]]:
    try:
        return await fetch_all(_SQL_COMP_SALES, zip_code)
    except asyncpg.UndefinedTableError:
        logger.info("dof_sales table not available for comps (Phase A)")
        return []


async def _fetch_ownership(bbl: str) -> dict[str, Any] | None:
    try:
        return await fetch_one(_SQL_OWNERSHIP, bbl)
    except asyncpg.UndefinedTableError:
        logger.info("mv_current_ownership not available (Phase B)")
        return None


async def _fetch_tax_lien(bbl: str) -> dict[str, Any] | None:
    try:
        return await fetch_one(_SQL_TAX_LIENS, bbl)
    except asyncpg.UndefinedTableError:
        return None


async def _fetch_rentstab(bbl: str) -> dict[str, Any] | None:
    try:
        return await fetch_one(_SQL_RENTSTAB, bbl)
    except asyncpg.UndefinedTableError:
        return None


async def _fetch_exemptions(bbl: str) -> list[dict[str, Any]]:
    try:
        return await fetch_all(_SQL_EXEMPTIONS, bbl)
    except asyncpg.UndefinedTableError:
        return []


# ── New sub-query runners ─────────────────────────────────────────────

async def _fetch_hpd_complaints_summary(bbl: str) -> dict[str, Any] | None:
    try:
        return await fetch_one(_SQL_HPD_COMPLAINTS_SUMMARY, bbl)
    except asyncpg.UndefinedTableError:
        logger.info("hpd_complaints_and_problems not available")
        return None


async def _fetch_hpd_litigations_summary(bbl: str) -> dict[str, Any] | None:
    try:
        return await fetch_one(_SQL_HPD_LITIGATIONS_SUMMARY, bbl)
    except asyncpg.UndefinedTableError:
        logger.info("hpd_litigations not available")
        return None


async def _fetch_hpd_registration_summary(
    borough: int, block: int, lot: int
) -> dict[str, Any] | None:
    try:
        reg = await fetch_one(_SQL_HPD_REGISTRATION, borough, block, lot)
        if reg is None:
            return {"registered": False}
        reg_id = reg["registrationid"]
        agent_row = await fetch_one(_SQL_HPD_REGISTRATION_AGENT, reg_id)
        managing_agent: str | None = None
        if agent_row:
            corp = (agent_row.get("corporationname") or "").strip()
            name_parts = [
                agent_row.get("firstname"),
                agent_row.get("middleinitial"),
                agent_row.get("lastname"),
            ]
            person = " ".join(p for p in name_parts if p)
            managing_agent = corp or person or None
        return {
            "registered": True,
            "last_registration_date": reg.get("lastregistrationdate"),
            "registration_end_date": reg.get("registrationenddate"),
            "managing_agent": managing_agent,
        }
    except asyncpg.UndefinedTableError:
        logger.info("hpd_registrations not available")
        return None


async def _fetch_evictions_summary(bbl: str) -> dict[str, Any] | None:
    try:
        return await fetch_one(_SQL_EVICTIONS_SUMMARY, bbl)
    except asyncpg.UndefinedTableError:
        logger.info("marshal_evictions_all not available")
        return None


async def _fetch_permits_summary(bbl: str) -> dict[str, Any] | None:
    try:
        return await fetch_one(_SQL_PERMITS_SUMMARY, bbl)
    except asyncpg.UndefinedTableError:
        logger.info("dobjobs not available")
        return None


async def _fetch_311_summary(bbl: str) -> dict[str, Any] | None:
    try:
        return await fetch_one(_SQL_311_SUMMARY, bbl)
    except asyncpg.UndefinedTableError:
        logger.info("nyc_311_complaints not available")
        return None


async def _fetch_mortgages_summary(
    borough: int, block: int, lot: int
) -> dict[str, Any] | None:
    try:
        return await fetch_one(_SQL_MORTGAGES_SUMMARY, borough, block, lot)
    except asyncpg.UndefinedTableError:
        logger.info("ACRIS tables not available (Phase C)")
        return None


# ── Helper functions ─────────────────────────────────────────────────

def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _build_property_summary(
    profile: dict[str, Any],
    bbl_info: dict[str, str],
) -> dict[str, Any]:
    return {
        "bbl": profile.get("bbl"),
        "bbl_formatted": bbl_info["bbl_formatted"],
        "address": profile.get("address"),
        "borough": bbl_info["borough_name"],
        "owner": profile.get("ownername"),
        "building_class": profile.get("bldgclass"),
        "zoning_district": profile.get("zonedist1"),
        "year_built": profile.get("yearbuilt"),
        "num_floors": profile.get("numfloors"),
        "total_units": profile.get("unitstotal"),
        "residential_units": profile.get("unitsres"),
        "lot_area_sqft": profile.get("lotarea"),
        "building_area_sqft": profile.get("bldgarea"),
        "landmark_district": profile.get("histdist") or profile.get("landmark") or None,
        "coordinates": {
            "latitude": _safe_float(profile.get("latitude")),
            "longitude": _safe_float(profile.get("longitude")),
        },
    }


def _build_financial_snapshot(
    profile: dict[str, Any],
    recent_sales: list[dict[str, Any]],
    exemptions: list[dict[str, Any]],
) -> dict[str, Any]:
    last_sale_price = None
    last_sale_date = None
    if recent_sales:
        last_sale_price = _safe_float(recent_sales[0].get("saleprice"))
        last_sale_date = recent_sales[0].get("saledate")

    exemption_list = [
        {
            "code": e.get("exmpcode"),
            "name": (e.get("exname") or e.get("code_description") or "").strip(),
            "value": e.get("curexmptot"),
        }
        for e in exemptions
    ] if exemptions else []

    return {
        "assessed_land": _safe_float(profile.get("assessland")),
        "assessed_total": _safe_float(profile.get("assesstot")),
        "exempt_total": _safe_float(profile.get("exempttot")),
        "last_sale_price": last_sale_price,
        "last_sale_date": last_sale_date,
        "tax_exemptions": exemption_list if exemption_list else None,
    }


def _build_development_potential(profile: dict[str, Any]) -> dict[str, Any]:
    built_far = _safe_float(profile.get("builtfar"))
    lot_area = _safe_float(profile.get("lotarea"))

    far_candidates = [
        _safe_float(profile.get("residfar")),
        _safe_float(profile.get("commfar")),
        _safe_float(profile.get("facilfar")),
    ]
    valid_fars = [f for f in far_candidates if f is not None and f > 0]
    max_allowed_far = max(valid_fars) if valid_fars else None

    unused_far: float | None = None
    unused_sqft: float | None = None
    is_maxed_out: bool | None = None

    if built_far is not None and max_allowed_far is not None:
        unused_far = round(max_allowed_far - built_far, 2)
        if lot_area is not None and lot_area > 0:
            unused_sqft = round(unused_far * lot_area)
        is_maxed_out = unused_far <= 0.1

    return {
        "current_far": built_far,
        "max_allowed_far": max_allowed_far,
        "unused_far": unused_far,
        "unused_sqft": unused_sqft,
        "is_maxed_out": is_maxed_out,
    }


def _build_violations_and_compliance(
    violations: dict[str, Any] | None,
    hpd_complaints: dict[str, Any] | None,
    hpd_litigations: dict[str, Any] | None,
    permits: dict[str, Any] | None,
) -> dict[str, Any]:
    hpd_viol: dict[str, Any] | None = None
    dob_viol: dict[str, Any] | None = None

    if violations is not None:
        hpd_most_recent = violations.get("hpd_most_recent")
        dob_most_recent = violations.get("dob_most_recent")

        hpd_viol = {
            "total": violations.get("hpd_total"),
            "class_a": violations.get("hpd_class_a"),
            "class_b": violations.get("hpd_class_b"),
            "class_c": violations.get("hpd_class_c"),
            "open": violations.get("hpd_open"),
            "most_recent": hpd_most_recent,
        }
        dob_viol = {
            "total": violations.get("dob_total"),
            "open_or_no_disposition": violations.get("dob_no_disposition"),
            "resolved": violations.get("dob_has_disposition"),
            "most_recent": dob_most_recent,
        }

    hpd_comp: dict[str, Any] | None = None
    if hpd_complaints is not None:
        total = int(hpd_complaints.get("total_complaints") or 0)
        if total > 0 or hpd_complaints.get("most_recent"):
            hpd_comp = {
                "total": total,
                "open": int(hpd_complaints.get("open_complaints") or 0),
                "most_recent": hpd_complaints.get("most_recent"),
            }

    hpd_lit: dict[str, Any] | None = None
    if hpd_litigations is not None:
        total_cases = int(hpd_litigations.get("total_cases") or 0)
        if total_cases > 0:
            hpd_lit = {
                "total_cases": total_cases,
                "open_cases": int(hpd_litigations.get("open_cases") or 0),
                "harassment_findings": int(
                    hpd_litigations.get("harassment_findings") or 0
                ),
                "most_recent_case": hpd_litigations.get("most_recent_case"),
            }

    bldg_permits: dict[str, Any] | None = None
    if permits is not None:
        total_filings = int(permits.get("total_filings") or 0)
        if total_filings > 0:
            bldg_permits = {
                "total_filings": total_filings,
                "new_buildings": int(permits.get("new_buildings") or 0),
                "alterations": int(permits.get("alterations") or 0),
                "demolitions": int(permits.get("demolitions") or 0),
                "most_recent_filing": permits.get("most_recent_filing"),
            }

    return {
        "hpd_violations": hpd_viol,
        "dob_violations": dob_viol,
        "hpd_complaints": hpd_comp,
        "hpd_litigations": hpd_lit,
        "building_permits": bldg_permits,
    }


def _build_ownership_and_legal(
    ownership: dict[str, Any] | None,
    hpd_registration: dict[str, Any] | None,
    tax_lien: dict[str, Any] | None,
    mortgages: dict[str, Any] | None,
) -> dict[str, Any]:
    tax_liens_section: dict[str, Any] = {
        "has_tax_liens": tax_lien is not None,
    }
    if tax_lien:
        tax_liens_section["cycle"] = tax_lien.get("cycle")
        tax_liens_section["water_debt_only"] = tax_lien.get("waterdebtonly")

    mortgages_section: dict[str, Any] | None = None
    if mortgages is not None:
        total_recorded = int(mortgages.get("total_recorded") or 0)
        if total_recorded > 0:
            mortgages_section = {
                "total_recorded": total_recorded,
                "active_mortgages": int(mortgages.get("active_mortgages") or 0),
                "satisfactions": int(mortgages.get("satisfactions") or 0),
                "most_recent_date": mortgages.get("most_recent_date"),
                "total_mortgage_amount": _safe_float(
                    mortgages.get("total_mortgage_amount")
                ),
            }

    return {
        "deed_owner": ownership,
        "hpd_registration": hpd_registration,
        "tax_liens": tax_liens_section,
        "mortgages": mortgages_section,
    }


def _build_tenant_and_operations(
    rentstab_row: dict[str, Any] | None,
    evictions: dict[str, Any] | None,
    complaints_311: dict[str, Any] | None,
) -> dict[str, Any]:
    rent_stabilization: dict[str, Any] | None = None
    if rentstab_row:
        latest_count = (
            rentstab_row.get("uc2017")
            or rentstab_row.get("uc2016")
            or rentstab_row.get("uc2015")
        )
        rent_stabilization = {
            "is_rent_stabilized": True,
            "latest_stabilized_units": latest_count,
            "total_residential_units": rentstab_row.get("unitsres"),
            "is_estimated": bool(rentstab_row.get("est2017")),
        }

    evictions_section: dict[str, Any] | None = None
    if evictions is not None:
        total = int(evictions.get("total_evictions") or 0)
        if total > 0:
            evictions_section = {
                "total": total,
                "residential": int(evictions.get("residential_evictions") or 0),
                "commercial": int(evictions.get("commercial_evictions") or 0),
                "most_recent": evictions.get("most_recent"),
            }

    complaints_section: dict[str, Any] | None = None
    if complaints_311 is not None:
        total = int(complaints_311.get("total_complaints") or 0)
        if total > 0:
            complaints_section = {
                "total": total,
                "open": int(complaints_311.get("open_complaints") or 0),
                "most_recent": complaints_311.get("most_recent"),
            }

    return {
        "rent_stabilization": rent_stabilization,
        "evictions": evictions_section,
        "complaints_311": complaints_section,
    }


def _build_comparable_market(
    zip_code: str | None,
    comp_sales: list[dict[str, Any]],
) -> dict[str, Any]:
    price_per_sqft_values: list[float] = []
    for sale in comp_sales:
        price = _safe_float(sale.get("saleprice"))
        sqft = _safe_float(sale.get("grosssquarefeet"))
        if price is not None and sqft is not None and sqft > 0:
            price_per_sqft_values.append(price / sqft)

    ppsf_sample_size = len(price_per_sqft_values)
    # Require at least 5 comps with usable sqft to avoid a misleading median.
    median_ppsf = (
        round(statistics.median(price_per_sqft_values), 2)
        if ppsf_sample_size >= 5
        else None
    )

    result: dict[str, Any] = {
        "zip_code": zip_code,
        "num_recent_sales": len(comp_sales),
        "median_price_per_sqft": median_ppsf,
        "ppsf_sample_size": ppsf_sample_size,
    }
    if median_ppsf is None and comp_sales:
        result["ppsf_note"] = (
            f"Suppressed — only {ppsf_sample_size} of {len(comp_sales)} "
            "comps have usable sqft data (minimum 5 required for reliable median)."
        )
    return result


def _generate_observations(
    violations_section: dict[str, Any],
    financial: dict[str, Any],
    dev: dict[str, Any],
    ownership_legal: dict[str, Any],
    tenant_ops: dict[str, Any],
) -> list[str]:
    observations: list[str] = []

    # HPD violations
    hpd_viol = violations_section.get("hpd_violations") or {}
    hpd_class_c = hpd_viol.get("class_c")
    if hpd_class_c is not None and hpd_class_c > 0:
        observations.append(
            f"Building has {hpd_class_c} Class C (immediately hazardous) HPD violations"
        )
    hpd_open = hpd_viol.get("open")
    if hpd_open is not None and hpd_open > 10:
        observations.append(
            f"Building has {hpd_open} open HPD violations — elevated regulatory risk"
        )

    # HPD litigations (strongest red flag)
    lit = violations_section.get("hpd_litigations")
    if lit:
        if lit.get("harassment_findings", 0) > 0:
            observations.append(
                f"HPD found harassment at this building ({lit['harassment_findings']} finding(s)) "
                "— serious red flag for tenant rights violations"
            )
        elif lit.get("open_cases", 0) > 0:
            observations.append(
                f"Building has {lit['open_cases']} active HPD litigation case(s) — "
                "indicates severe compliance issues"
            )
        elif lit.get("total_cases", 0) > 0:
            observations.append(
                f"Building has a history of HPD litigation ({lit['total_cases']} case(s))"
            )

    # Sales flags
    last_sale_price = financial.get("last_sale_price")
    if last_sale_price is not None and last_sale_price <= 100:
        observations.append(
            f"Last recorded sale was ${last_sale_price:,.0f} — "
            "likely a non-arm's-length transfer (LLC restructuring, inheritance)"
        )

    # FAR / development
    unused_far = dev.get("unused_far")
    unused_sqft = dev.get("unused_sqft")
    if unused_far is not None and unused_far > 0.5:
        sqft_note = f" ({unused_sqft:,.0f} buildable sqft)" if unused_sqft else ""
        observations.append(
            f"Property has {unused_far:.1f} unused FAR{sqft_note} — "
            "potential development upside"
        )
    elif unused_far is not None and unused_far <= 0.1:
        observations.append("FAR is essentially maxed out — limited development potential")

    # Assessed vs sale price gap
    assessed_total = financial.get("assessed_total")
    if (
        assessed_total is not None
        and last_sale_price is not None
        and last_sale_price > assessed_total * 1.5
    ):
        observations.append("Last sale price significantly exceeds assessed value")

    # Tax liens
    tax_liens = ownership_legal.get("tax_liens") or {}
    if tax_liens.get("has_tax_liens"):
        observations.append(
            "Property appeared on DOF tax lien sale list — indicates delinquent taxes or charges"
        )

    # Rent stabilization
    rent_stab = (tenant_ops.get("rent_stabilization") or {})
    rs_units = rent_stab.get("latest_stabilized_units")
    if rs_units:
        observations.append(f"Building has {rs_units} rent-stabilized units")

    # Evictions
    evictions = tenant_ops.get("evictions")
    if evictions and evictions.get("total", 0) >= 3:
        observations.append(
            f"{evictions['total']} marshal evictions executed at this building — "
            "possible tenant instability or displacement activity"
        )

    # High 311 volume
    complaints_311 = tenant_ops.get("complaints_311")
    if complaints_311 and complaints_311.get("open", 0) >= 5:
        observations.append(
            f"{complaints_311['open']} open 311 complaints — active tenant-reported issues"
        )

    # Mortgages
    mortgages = ownership_legal.get("mortgages")
    if mortgages and mortgages.get("active_mortgages", 0) >= 3:
        observations.append(
            f"Property has {mortgages['active_mortgages']} recorded mortgage instruments — "
            "review debt profile before underwriting"
        )

    return observations


# ── Main tool ────────────────────────────────────────────────────────

@mcp.tool()
async def analyze_property(bbl: str) -> dict:
    """Generate a comprehensive due diligence summary for a NYC property.

    Combines data from 14 sources concurrently: property profile, HPD/DOB
    violations, HPD complaints, HPD litigations, HPD registration, evictions,
    building permits, 311 complaints, sales history, tax assessment, tax liens,
    ACRIS mortgages, rent stabilization, and comparable sales. Use this when
    the user wants a complete picture of a property for investment analysis.
    """
    try:
        validate_bbl(bbl)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc

    bbl_info = parse_bbl(bbl)
    borough_int = int(bbl_info["borough"])
    block_int = int(bbl_info["block"])
    lot_int = int(bbl_info["lot"])

    # ── Run all sub-queries concurrently ──────────────────────────────
    try:
        results = await asyncio.wait_for(
            asyncio.gather(
                _fetch_profile(bbl),                                       # 0
                _fetch_violation_summary(bbl),                             # 1
                _fetch_recent_sales(bbl),                                  # 2
                _fetch_ownership(bbl),                                     # 3
                _fetch_tax_lien(bbl),                                      # 4
                _fetch_rentstab(bbl),                                      # 5
                _fetch_exemptions(bbl),                                    # 6
                _fetch_hpd_complaints_summary(bbl),                        # 7
                _fetch_hpd_litigations_summary(bbl),                       # 8
                _fetch_hpd_registration_summary(borough_int, block_int, lot_int),  # 9
                _fetch_evictions_summary(bbl),                             # 10
                _fetch_permits_summary(bbl),                               # 11
                _fetch_311_summary(bbl),                                   # 12
                _fetch_mortgages_summary(borough_int, block_int, lot_int), # 13
                return_exceptions=True,
            ),
            timeout=45,
        )
    except TimeoutError as exc:
        raise ToolError(
            "Analysis timed out after 45 seconds. The database may be under "
            "heavy load. Please try again."
        ) from exc

    # Unpack results. Re-raise ToolError (DB connection failures) immediately.
    # Replace other exceptions with None / [] for graceful degradation.
    def _unpack_required(result: Any, label: str) -> Any:
        if isinstance(result, ToolError):
            raise result
        if isinstance(result, BaseException):
            logger.error("%s sub-query failed", label, exc_info=result)
            return None
        return result

    def _unpack_optional(result: Any, label: str, default: Any = None) -> Any:
        if isinstance(result, BaseException):
            if not isinstance(result, asyncpg.UndefinedTableError):
                logger.warning("%s sub-query failed: %s", label, result)
            return default
        return result

    profile: dict[str, Any] | None = _unpack_required(results[0], "profile")
    violations = _unpack_optional(results[1], "violations")
    recent_sales: list = _unpack_optional(results[2], "recent_sales", default=[])
    ownership = _unpack_optional(results[3], "ownership")
    tax_lien = _unpack_optional(results[4], "tax_lien")
    rentstab_row = _unpack_optional(results[5], "rentstab")
    exemptions: list = _unpack_optional(results[6], "exemptions", default=[])
    hpd_complaints_sum = _unpack_optional(results[7], "hpd_complaints")
    hpd_litigations_sum = _unpack_optional(results[8], "hpd_litigations")
    hpd_registration_sum = _unpack_optional(results[9], "hpd_registration")
    evictions_sum = _unpack_optional(results[10], "evictions")
    permits_sum = _unpack_optional(results[11], "permits")
    complaints_311_sum = _unpack_optional(results[12], "311_complaints")
    mortgages_sum = _unpack_optional(results[13], "mortgages")

    if profile is None:
        raise ToolError(
            f"No property found for BBL {bbl}. "
            "This BBL may not exist or may not yet be in the PLUTO dataset. "
            "Double-check the BBL and try again."
        )

    # Comp sales require the zip code from the profile (separate step).
    zip_code = profile.get("postcode")
    comp_sales: list[dict[str, Any]] = []
    if zip_code:
        try:
            comp_sales = await asyncio.wait_for(
                _fetch_comp_sales(str(zip_code)),
                timeout=15,
            )
        except TimeoutError:
            logger.error("Comp sales query timed out")
        except (ToolError, asyncpg.PostgresError) as exc:
            logger.error("Comp sales query failed: %s", exc)
        except Exception as exc:
            logger.exception("Unexpected error in comp sales query: %s", exc)

    # ── Build standardized report sections ───────────────────────────
    property_summary = _build_property_summary(profile, bbl_info)
    financial_snapshot = _build_financial_snapshot(profile, recent_sales, exemptions)
    development_potential = _build_development_potential(profile)
    violations_and_compliance = _build_violations_and_compliance(
        violations, hpd_complaints_sum, hpd_litigations_sum, permits_sum
    )
    ownership_and_legal = _build_ownership_and_legal(
        ownership, hpd_registration_sum, tax_lien, mortgages_sum
    )
    tenant_and_operations = _build_tenant_and_operations(
        rentstab_row, evictions_sum, complaints_311_sum
    )
    comparable_market = _build_comparable_market(zip_code, comp_sales)

    key_observations = _generate_observations(
        violations_and_compliance,
        financial_snapshot,
        development_potential,
        ownership_and_legal,
        tenant_and_operations,
    )

    # ── Track data gaps ──────────────────────────────────────────────
    data_gaps: list[str] = []
    if violations is None:
        data_gaps.append(
            "Violation summary unavailable — mv_violation_summary may need to be created"
        )
    if not recent_sales:
        data_gaps.append("No DOF sales records found for this property")
    if ownership is None:
        data_gaps.append("Deed ownership unavailable — ACRIS tables may not be loaded (Phase C)")
    if hpd_complaints_sum is None:
        data_gaps.append("HPD complaints unavailable — hpd_complaints_and_problems not loaded")
    if hpd_litigations_sum is None:
        data_gaps.append("HPD litigations unavailable — hpd_litigations not loaded")
    if evictions_sum is None:
        data_gaps.append("Evictions unavailable — marshal_evictions_all not loaded")
    if permits_sum is None:
        data_gaps.append("Building permits unavailable — dobjobs not loaded")
    if complaints_311_sum is None:
        data_gaps.append("311 complaints unavailable — nyc_311_complaints not loaded")
    if mortgages_sum is None:
        data_gaps.append("Mortgage records unavailable — ACRIS tables may not be loaded (Phase C)")
    if not comp_sales:
        data_gaps.append("No comparable sales found in this zip code within the last 12 months")

    return {
        "property_summary": property_summary,
        "financial_snapshot": financial_snapshot,
        "development_potential": development_potential,
        "violations_and_compliance": violations_and_compliance,
        "ownership_and_legal": ownership_and_legal,
        "tenant_and_operations": tenant_and_operations,
        "comparable_market": comparable_market,
        "recent_sales": recent_sales,
        "key_observations": key_observations,
        "data_gaps": data_gaps if data_gaps else None,
        "data_as_of": (
            "Data sourced from NYC public records. "
            "PLUTO updated quarterly, HPD/DOB updated daily, "
            "DOF sales updated monthly, ACRIS updated daily."
        ),
        "disclaimer": (
            "This is an informational summary from public records, "
            "not an appraisal or investment recommendation."
        ),
    }
