"""Compound analysis tool — comprehensive due diligence summary.

Aggregates data from multiple sources (property profile, violations,
sales history, tax assessment, comparable sales) into a single
investment-grade property report.
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
SELECT exmpcode, exname, curexmptot
FROM dof_exemptions
WHERE bbl = $1
ORDER BY curexmptot DESC NULLS LAST LIMIT 5;"""


# ── Sub-query runners ────────────────────────────────────────────────

async def _fetch_profile(bbl: str) -> dict[str, Any] | None:
    """Fetch property profile, falling back to pluto_latest."""
    try:
        row = await fetch_one(_SQL_PROFILE, bbl)
        if row is not None:
            return row
    except asyncpg.UndefinedTableError:
        logger.info("mv_property_profile not available, trying pluto_latest")

    return await fetch_one(_SQL_PROFILE_FALLBACK, bbl)


async def _fetch_violation_summary(bbl: str) -> dict[str, Any] | None:
    """Fetch violation summary from materialized view."""
    try:
        return await fetch_one(_SQL_VIOLATION_SUMMARY, bbl)
    except asyncpg.UndefinedTableError:
        logger.info("mv_violation_summary not available")
        return None


async def _fetch_recent_sales(bbl: str) -> list[dict[str, Any]]:
    """Fetch recent sales for this specific property."""
    try:
        return await fetch_all(_SQL_RECENT_SALES, bbl)
    except asyncpg.UndefinedTableError:
        logger.info("dof_sales table not available (Phase A)")
        return []


async def _fetch_comp_sales(zip_code: str) -> list[dict[str, Any]]:
    """Fetch comparable sales in the same zip code (last 12 months)."""
    try:
        return await fetch_all(_SQL_COMP_SALES, zip_code)
    except asyncpg.UndefinedTableError:
        logger.info("dof_sales table not available for comps (Phase A)")
        return []


async def _fetch_ownership(bbl: str) -> dict[str, Any] | None:
    """Fetch current ownership from materialized view."""
    try:
        return await fetch_one(_SQL_OWNERSHIP, bbl)
    except asyncpg.UndefinedTableError:
        logger.info("mv_current_ownership not available (Phase B)")
        return None


async def _fetch_tax_lien(bbl: str) -> dict[str, Any] | None:
    """Fetch most recent tax lien record."""
    try:
        return await fetch_one(_SQL_TAX_LIENS, bbl)
    except asyncpg.UndefinedTableError:
        return None


async def _fetch_rentstab(bbl: str) -> dict[str, Any] | None:
    """Fetch rent stabilization data."""
    try:
        return await fetch_one(_SQL_RENTSTAB, bbl)
    except asyncpg.UndefinedTableError:
        return None


async def _fetch_exemptions(bbl: str) -> list[dict[str, Any]]:
    """Fetch top tax exemptions."""
    try:
        return await fetch_all(_SQL_EXEMPTIONS, bbl)
    except asyncpg.UndefinedTableError:
        return []


# ── Helper functions ─────────────────────────────────────────────────

def _safe_float(value: Any) -> float | None:
    """Coerce a value to float, returning None on failure."""
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
    """Build the property_summary section from the profile row."""
    return {
        "bbl": profile.get("bbl"),
        "bbl_formatted": bbl_info["bbl_formatted"],
        "address": profile.get("address"),
        "borough": bbl_info["borough_name"],
        "owner": profile.get("ownername"),
        "building_class": profile.get("bldgclass"),
        "year_built": profile.get("yearbuilt"),
        "num_floors": profile.get("numfloors"),
        "total_units": profile.get("unitstotal"),
        "residential_units": profile.get("unitsres"),
        "lot_area_sqft": profile.get("lotarea"),
        "building_area_sqft": profile.get("bldgarea"),
    }


def _build_financial_snapshot(
    profile: dict[str, Any],
    recent_sales: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the financial_snapshot section."""
    last_sale_price = None
    last_sale_date = None
    if recent_sales:
        last_sale_price = _safe_float(recent_sales[0].get("saleprice"))
        last_sale_date = recent_sales[0].get("saledate")

    return {
        "assessed_land": _safe_float(profile.get("assessland")),
        "assessed_total": _safe_float(profile.get("assesstot")),
        "exempt_total": _safe_float(profile.get("exempttot")),
        "last_sale_price": last_sale_price,
        "last_sale_date": last_sale_date,
    }


def _build_development_potential(profile: dict[str, Any]) -> dict[str, Any]:
    """Build the development_potential section from FAR data."""
    built_far = _safe_float(profile.get("builtfar"))
    lot_area = _safe_float(profile.get("lotarea"))

    # Max allowed FAR is the highest of the permitted FARs.
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


def _build_risk_factors(
    violations: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the risk_factors section from violation summary."""
    if violations is None:
        return {
            "hpd_total_violations": None,
            "hpd_class_c_count": None,
            "hpd_open_violations": None,
            "dob_total_violations": None,
            "most_recent_violation": None,
            "has_tax_lien": None,  # Phase C
        }

    hpd_most_recent = violations.get("hpd_most_recent")
    dob_most_recent = violations.get("dob_most_recent")

    # Pick the more recent violation date from either source.
    most_recent = None
    if hpd_most_recent and dob_most_recent:
        most_recent = max(hpd_most_recent, dob_most_recent)
    else:
        most_recent = hpd_most_recent or dob_most_recent

    return {
        "hpd_total_violations": violations.get("hpd_total"),
        "hpd_class_c_count": violations.get("hpd_class_c"),
        "hpd_open_violations": violations.get("hpd_open"),
        "dob_total_violations": violations.get("dob_total"),
        "most_recent_violation": most_recent,
        "has_tax_lien": None,  # Phase C
    }


def _build_comparable_market(
    zip_code: str | None,
    comp_sales: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the comparable_market section from comp sales data."""
    price_per_sqft_values: list[float] = []
    for sale in comp_sales:
        price = _safe_float(sale.get("saleprice"))
        sqft = _safe_float(sale.get("grosssquarefeet"))
        if price is not None and sqft is not None and sqft > 0:
            price_per_sqft_values.append(price / sqft)

    median_ppsf = (
        round(statistics.median(price_per_sqft_values), 2)
        if price_per_sqft_values
        else None
    )

    return {
        "zip_code": zip_code,
        "num_recent_sales": len(comp_sales),
        "median_price_per_sqft": median_ppsf,
    }


def _generate_observations(
    risk: dict[str, Any],
    financial: dict[str, Any],
    dev: dict[str, Any],
) -> list[str]:
    """Generate key observations as human-readable bullet points."""
    observations: list[str] = []

    hpd_class_c = risk.get("hpd_class_c_count")
    if hpd_class_c is not None and hpd_class_c > 0:
        observations.append(
            f"Building has {hpd_class_c} Class C (immediately hazardous) HPD violations"
        )

    hpd_open = risk.get("hpd_open_violations")
    if hpd_open is not None and hpd_open > 10:
        observations.append(
            f"Building has {hpd_open} open HPD violations — elevated regulatory risk"
        )

    last_sale_price = financial.get("last_sale_price")
    if last_sale_price is not None and last_sale_price <= 100:
        observations.append(
            f"Last recorded sale was ${last_sale_price:,.0f} — "
            "likely a non-arm's-length transfer (LLC restructuring, inheritance)"
        )

    unused_far = dev.get("unused_far")
    unused_sqft = dev.get("unused_sqft")
    if unused_far is not None and unused_far > 0.5:
        sqft_note = f" ({unused_sqft:,.0f} buildable sqft)" if unused_sqft else ""
        observations.append(
            f"Property has {unused_far:.1f} unused FAR{sqft_note} — "
            "potential development upside"
        )
    elif unused_far is not None and unused_far <= 0.1:
        observations.append(
            "FAR is essentially maxed out — limited development potential"
        )

    assessed_total = financial.get("assessed_total")
    if (
        assessed_total is not None
        and last_sale_price is not None
        and last_sale_price > assessed_total * 1.5
    ):
        observations.append(
            "Last sale price significantly exceeds assessed value"
        )

    return observations


# ── Main tool ────────────────────────────────────────────────────────

@mcp.tool()
async def analyze_property(bbl: str) -> dict:
    """Generate a comprehensive due diligence summary for a NYC property.

    Combines data from multiple sources: property profile, violations, sales
    history, tax assessment, and comparable sales. This is the power tool —
    use it when the user wants a complete picture of a property for investment
    analysis.
    """
    # ── Validate BBL ──────────────────────────────────────────────────
    try:
        validate_bbl(bbl)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc

    bbl_info = parse_bbl(bbl)

    # ── Run all sub-queries concurrently ──────────────────────────────
    try:
        results = await asyncio.wait_for(
            asyncio.gather(
                _fetch_profile(bbl),
                _fetch_violation_summary(bbl),
                _fetch_recent_sales(bbl),
                _fetch_ownership(bbl),
                _fetch_tax_lien(bbl),
                _fetch_rentstab(bbl),
                _fetch_exemptions(bbl),
                return_exceptions=True,
            ),
            timeout=45,
        )
    except TimeoutError as exc:
        raise ToolError(
            "Analysis timed out after 45 seconds. The database may be under "
            "heavy load. Please try again."
        ) from exc

    # Unpack results. Re-raise ToolError (DB connection failures) immediately
    # since they indicate infrastructure failure, not missing data. Replace
    # other exceptions (e.g. UndefinedTableError for unloaded datasets) with
    # None / empty list so the analysis degrades gracefully.
    (profile_result, violations_result, sales_result, ownership_result,
     tax_lien_result, rentstab_result, exemptions_result) = results

    profile: dict[str, Any] | None = None
    if isinstance(profile_result, ToolError):
        raise profile_result
    elif isinstance(profile_result, BaseException):
        logger.error("Profile sub-query failed", exc_info=profile_result)
    else:
        profile = profile_result

    violations: dict[str, Any] | None = None
    if isinstance(violations_result, ToolError):
        raise violations_result
    elif isinstance(violations_result, BaseException):
        logger.error("Violations sub-query failed", exc_info=violations_result)
    else:
        violations = violations_result

    recent_sales: list[dict[str, Any]] = []
    if isinstance(sales_result, ToolError):
        raise sales_result
    elif isinstance(sales_result, BaseException):
        logger.error("Sales sub-query failed", exc_info=sales_result)
    else:
        recent_sales = sales_result  # type: ignore[assignment]

    ownership: dict[str, Any] | None = None
    if isinstance(ownership_result, ToolError):
        raise ownership_result
    elif isinstance(ownership_result, BaseException):
        logger.error("Ownership sub-query failed", exc_info=ownership_result)
    else:
        ownership = ownership_result

    tax_lien: dict[str, Any] | None = None
    if not isinstance(tax_lien_result, BaseException):
        tax_lien = tax_lien_result

    rentstab_row: dict[str, Any] | None = None
    if not isinstance(rentstab_result, BaseException):
        rentstab_row = rentstab_result

    exemptions: list[dict[str, Any]] = []
    if not isinstance(exemptions_result, BaseException):
        exemptions = exemptions_result  # type: ignore[assignment]

    # Profile is required — can't build an analysis without it.
    if profile is None:
        raise ToolError(
            f"No property found for BBL {bbl}. "
            "This BBL may not exist or may not yet be in the PLUTO dataset. "
            "Double-check the BBL and try again."
        )

    # Fetch comps using the property's zip code (separate step because
    # we need the zip code from the profile).
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

    # ── Build analysis sections ───────────────────────────────────────
    property_summary = _build_property_summary(profile, bbl_info)
    financial_snapshot = _build_financial_snapshot(profile, recent_sales)
    development_potential = _build_development_potential(profile)
    risk_factors = _build_risk_factors(violations)
    risk_factors["has_tax_lien"] = tax_lien is not None
    if tax_lien:
        risk_factors["tax_lien_cycle"] = tax_lien.get("cycle")
        risk_factors["tax_lien_water_debt_only"] = tax_lien.get("waterdebtonly")
    comparable_market = _build_comparable_market(zip_code, comp_sales)

    # Rent stabilization
    rent_stabilization: dict[str, Any] | None = None
    if rentstab_row:
        latest_count = rentstab_row.get("uc2017") or rentstab_row.get("uc2016") or rentstab_row.get("uc2015")
        rent_stabilization = {
            "is_rent_stabilized": True,
            "latest_stabilized_units": latest_count,
            "total_residential_units": rentstab_row.get("unitsres"),
            "is_estimated": bool(rentstab_row.get("est2017")),
        }

    # Exemptions
    exemption_list = [
        {"code": e.get("exmpcode"), "name": (e.get("exname") or "").strip(), "value": e.get("curexmptot")}
        for e in exemptions
    ] if exemptions else []

    key_observations = _generate_observations(
        risk_factors,
        financial_snapshot,
        development_potential,
    )

    # ── Track data gaps ─────────────────────────────────────────────
    data_gaps: list[str] = []
    if violations is None:
        data_gaps.append(
            "Violation summary unavailable — mv_violation_summary may need to be created"
        )
    if not recent_sales:
        data_gaps.append("No DOF sales records found for this property")
    if ownership is None:
        data_gaps.append("Ownership data unavailable — ACRIS tables may not be loaded (Phase C)")
    if not comp_sales:
        data_gaps.append("No comparable sales found in this zip code within the last 12 months")

    # Add rent stab / tax lien observations
    if tax_lien:
        key_observations.append(
            "Property appeared on DOF tax lien sale list — indicates delinquent taxes or charges"
        )
    if rent_stabilization:
        units = rent_stabilization.get("latest_stabilized_units")
        if units:
            key_observations.append(
                f"Building has {units} rent-stabilized units"
            )

    return {
        "property_summary": property_summary,
        "financial_snapshot": financial_snapshot,
        "development_potential": development_potential,
        "risk_factors": risk_factors,
        "comparable_market": comparable_market,
        "recent_sales": recent_sales,
        "ownership": ownership,
        "rent_stabilization": rent_stabilization,
        "tax_exemptions": exemption_list if exemption_list else None,
        "key_observations": key_observations,
        "data_gaps": data_gaps if data_gaps else None,
        "data_as_of": (
            "Data sourced from NYC public records. "
            "PLUTO updated quarterly, HPD/DOB updated daily, "
            "DOF sales updated monthly."
        ),
        "disclaimer": (
            "This is an informational summary from public records, "
            "not an appraisal or investment recommendation."
        ),
    }
