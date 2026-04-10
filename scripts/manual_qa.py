"""Manual QA script for all 13 NYC Property Intel MCP tools.

Run with:
    cd /Users/devtzi/dev/nyc-property-intel && uv run python scripts/manual_qa.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import traceback
from typing import Any

# Must be set before importing project modules
os.environ.setdefault("DATABASE_URL", "postgresql://nycdb:nycdb@localhost:5432/nycdb")

import asyncpg  # noqa: E402 — needed for direct DB probes

import nyc_property_intel.db as _db  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PASS = "[PASS]"
FAIL = "[FAIL]"
SKIP = "[SKIP]"

results: list[tuple[str, str, str]] = []  # (status, name, detail)


def record(status: str, name: str, detail: str) -> None:
    results.append((status, name, detail))
    print(f"{status} {name}: {detail}")


async def run(coro) -> Any:
    """Reinitialise the pool, run coro, then close pool."""
    await _db.close_pool()
    await _db.get_pool()
    try:
        return await coro
    finally:
        await _db.close_pool()


async def probe_db(sql: str, *args) -> Any:
    """Run a raw SQL probe, managing pool lifecycle."""
    await _db.close_pool()
    await _db.get_pool()
    try:
        return await _db.fetch_one(sql, *args)
    finally:
        await _db.close_pool()


async def probe_db_all(sql: str, *args) -> list[dict]:
    """Run a raw SQL probe returning all rows."""
    await _db.close_pool()
    await _db.get_pool()
    try:
        return await _db.fetch_all(sql, *args)
    finally:
        await _db.close_pool()


# ---------------------------------------------------------------------------
# Discover valid BBLs from the live DB
# ---------------------------------------------------------------------------

async def discover_bbls() -> dict[str, str]:
    """Query the DB to find BBLs that will produce interesting test results."""
    print("\n--- Discovering test BBLs from live database ---")
    bbls: dict[str, str] = {
        "manhattan": "1012920015",       # 590 Madison Ave — always try this one
        "brooklyn_violator": "3013020001",
    }

    # Rent stab BBL
    try:
        row = await probe_db("SELECT ucbbl FROM rentstab LIMIT 1")
        if row and row.get("ucbbl"):
            bbls["rentstab"] = str(row["ucbbl"])
            print(f"  rentstab BBL   : {bbls['rentstab']}")
    except Exception as exc:
        print(f"  rentstab probe failed: {exc}")

    # HPD complaints BBL
    try:
        row = await probe_db("SELECT bbl FROM hpd_complaints_and_problems WHERE bbl IS NOT NULL LIMIT 1")
        if row and row.get("bbl"):
            bbls["hpd_complaints"] = str(row["bbl"])
            print(f"  hpd_complaints BBL: {bbls['hpd_complaints']}")
    except Exception as exc:
        print(f"  hpd_complaints probe failed: {exc}")

    # DOB jobs BBL
    try:
        row = await probe_db(
            "SELECT bbl FROM dobjobs WHERE bbl IS NOT NULL AND bbl != '' LIMIT 1"
        )
        if row and row.get("bbl"):
            bbls["dobjobs"] = str(row["bbl"])
            print(f"  dobjobs BBL    : {bbls['dobjobs']}")
    except Exception as exc:
        print(f"  dobjobs probe failed: {exc}")

    # HPD litigations BBL
    try:
        row = await probe_db("SELECT bbl FROM hpd_litigations WHERE bbl IS NOT NULL LIMIT 1")
        if row and row.get("bbl"):
            bbls["hpd_litigations"] = str(row["bbl"])
            print(f"  hpd_litigations BBL: {bbls['hpd_litigations']}")
    except Exception as exc:
        print(f"  hpd_litigations probe failed: {exc}")

    # HPD registrations BBL (via borough/block/lot)
    try:
        row = await probe_db(
            "SELECT LPAD(boroid::text,1,'0') || LPAD(block::text,5,'0') || LPAD(lot::text,4,'0') AS bbl "
            "FROM hpd_registrations WHERE boroid IS NOT NULL AND block IS NOT NULL AND lot IS NOT NULL LIMIT 1"
        )
        if row and row.get("bbl"):
            bbls["hpd_registrations"] = str(row["bbl"])
            print(f"  hpd_registrations BBL: {bbls['hpd_registrations']}")
    except Exception as exc:
        print(f"  hpd_registrations probe failed: {exc}")

    # DOF sales zip code
    try:
        row = await probe_db("SELECT zipcode FROM dof_sales WHERE zipcode IS NOT NULL AND zipcode != '' LIMIT 1")
        if row and row.get("zipcode"):
            bbls["sales_zip"] = str(row["zipcode"])
            print(f"  dof_sales zip  : {bbls['sales_zip']}")
    except Exception as exc:
        print(f"  dof_sales zip probe failed: {exc}")

    print()
    return bbls


# ---------------------------------------------------------------------------
# Individual tests
# ---------------------------------------------------------------------------

async def test_lookup_by_bbl(bbl: str) -> None:
    name = "lookup_property (by BBL)"
    try:
        from nyc_property_intel.tools.lookup import lookup_property  # noqa: F401
        result = await run(lookup_property(bbl=bbl))
        assert "bbl" in result, "Missing 'bbl' key"
        assert "data_as_of" in result, "Missing 'data_as_of'"
        assert "address" in result, "Missing 'address'"
        assert "bbl_formatted" in result, "Missing 'bbl_formatted'"
        record(PASS, name, f"bbl={result['bbl']}, address={result.get('address')}, owner={result.get('ownername')}")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_lookup_by_address() -> None:
    name = "lookup_property (by address)"
    try:
        from nyc_property_intel.tools.lookup import lookup_property
        # Use an address that maps to a known good property
        result = await run(lookup_property(address="590 Madison Ave, New York, NY"))
        assert "bbl" in result, "Missing 'bbl' key"
        assert "data_as_of" in result, "Missing 'data_as_of'"
        record(PASS, name, f"bbl={result['bbl']}, address={result.get('address')}")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_get_property_issues_all(bbl: str) -> None:
    name = "get_property_issues (source=ALL)"
    try:
        from nyc_property_intel.tools.issues import get_property_issues
        result = await run(get_property_issues(bbl=bbl, source="ALL"))
        assert "bbl" in result, "Missing 'bbl'"
        assert "hpd_violations" in result, "Missing 'hpd_violations'"
        assert "dob_violations" in result, "Missing 'dob_violations'"
        assert "ecb_violations" in result, "Missing 'ecb_violations'"
        assert "total_returned" in result, "Missing 'total_returned'"
        assert "data_as_of" in result, "Missing 'data_as_of'"
        n_hpd = len(result["hpd_violations"])
        n_dob = len(result["dob_violations"])
        n_ecb = len(result["ecb_violations"])
        record(PASS, name, f"HPD={n_hpd}, DOB={n_dob}, ECB={n_ecb}, total={result['total_returned']}")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_get_property_issues_hpd(bbl: str) -> None:
    name = "get_property_issues (source=HPD)"
    try:
        from nyc_property_intel.tools.issues import get_property_issues
        result = await run(get_property_issues(bbl=bbl, source="HPD"))
        assert "hpd_violations" in result, "Missing 'hpd_violations'"
        assert "dob_violations" in result, "Missing 'dob_violations'"
        assert len(result["dob_violations"]) == 0, "DOB violations returned despite source=HPD"
        assert "data_as_of" in result, "Missing 'data_as_of'"
        record(PASS, name, f"HPD violations returned: {len(result['hpd_violations'])}, DOB correctly empty")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_get_property_issues_dob(bbl: str) -> None:
    name = "get_property_issues (source=DOB)"
    try:
        from nyc_property_intel.tools.issues import get_property_issues
        result = await run(get_property_issues(bbl=bbl, source="DOB"))
        assert "dob_violations" in result, "Missing 'dob_violations'"
        assert len(result["hpd_violations"]) == 0, "HPD violations returned despite source=DOB"
        assert "data_as_of" in result, "Missing 'data_as_of'"
        record(PASS, name, f"DOB violations returned: {len(result['dob_violations'])}, HPD correctly empty")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_get_property_issues_ecb(bbl: str) -> None:
    name = "get_property_issues (source=ECB)"
    try:
        from nyc_property_intel.tools.issues import get_property_issues
        result = await run(get_property_issues(bbl=bbl, source="ECB"))
        assert "ecb_violations" in result, "Missing 'ecb_violations'"
        assert len(result["hpd_violations"]) == 0, "HPD returned despite source=ECB"
        assert "data_as_of" in result, "Missing 'data_as_of'"
        record(PASS, name, f"ECB violations returned: {len(result['ecb_violations'])}, HPD correctly empty")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_get_property_history(bbl: str) -> None:
    name = "get_property_history (sales + ownership)"
    try:
        from nyc_property_intel.tools.history import get_property_history
        result = await run(get_property_history(bbl=bbl, include_sales=True, include_ownership=True))
        assert "bbl" in result, "Missing 'bbl'"
        assert "data_as_of" in result, "Missing 'data_as_of'"
        assert "data_sources" in result, "Missing 'data_sources'"
        # sales and ownership_transfers must be present (may be empty if tables missing)
        assert "sales" in result or "sales_note" in result, "No sales section at all"
        assert "ownership_transfers" in result or "ownership_note" in result, "No ownership section at all"
        sales = result.get("sales", [])
        owns = result.get("ownership_transfers", [])
        detail = f"sales={len(sales)}, ownership_transfers={len(owns)}"
        if sales:
            s0 = sales[0]
            detail += f", latest_sale={s0.get('saledate')} ${s0.get('saleprice'):,}" if s0.get('saleprice') else ""
        record(PASS, name, detail)
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_get_hpd_complaints_basic(bbl: str) -> None:
    name = "get_hpd_complaints (basic)"
    try:
        from nyc_property_intel.tools.hpd_complaints import get_hpd_complaints
        result = await run(get_hpd_complaints(bbl=bbl))
        assert "bbl" in result, "Missing 'bbl'"
        assert "complaints" in result, "Missing 'complaints'"
        assert "total_returned" in result, "Missing 'total_returned'"
        # data_as_of may be missing if table absent
        detail = f"complaints={len(result['complaints'])}"
        if result.get("summary"):
            s = result["summary"]
            detail += f", total_complaints={s.get('total_complaints')}, open={s.get('open_complaints')}"
        record(PASS, name, detail)
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_get_hpd_complaints_status(bbl: str) -> None:
    name = "get_hpd_complaints (status=OPEN)"
    try:
        from nyc_property_intel.tools.hpd_complaints import get_hpd_complaints
        result = await run(get_hpd_complaints(bbl=bbl, status="OPEN"))
        assert "complaints" in result, "Missing 'complaints'"
        # All returned complaints should have status OPEN
        for c in result["complaints"]:
            assert c.get("complaintstatus") == "OPEN", \
                f"Expected OPEN, got {c.get('complaintstatus')!r}"
        record(PASS, name, f"complaints={len(result['complaints'])}, all status=OPEN verified")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_get_hpd_complaints_category(bbl: str) -> None:
    name = "get_hpd_complaints (category filter)"
    try:
        from nyc_property_intel.tools.hpd_complaints import get_hpd_complaints
        result = await run(get_hpd_complaints(bbl=bbl, category="PLUMBING"))
        assert "complaints" in result, "Missing 'complaints'"
        record(PASS, name, f"category=PLUMBING, complaints returned={len(result['complaints'])}")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_get_hpd_litigations(bbl: str) -> None:
    name = "get_hpd_litigations (basic)"
    try:
        from nyc_property_intel.tools.hpd_litigations import get_hpd_litigations
        result = await run(get_hpd_litigations(bbl=bbl))
        assert "bbl" in result, "Missing 'bbl'"
        assert "litigations" in result or "note" in result, "Missing 'litigations' and 'note'"
        assert "has_litigation_history" in result, "Missing 'has_litigation_history'"
        litigations = result.get("litigations", [])
        detail = f"litigations={len(litigations)}, has_history={result.get('has_litigation_history')}"
        if result.get("summary"):
            s = result["summary"]
            detail += f", total_cases={s.get('total_cases')}, harassment={s.get('harassment_findings')}"
        record(PASS, name, detail)
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_get_hpd_registration(bbl: str) -> None:
    name = "get_hpd_registration (basic)"
    try:
        from nyc_property_intel.tools.hpd_registration import get_hpd_registration
        result = await run(get_hpd_registration(bbl=bbl))
        assert "bbl" in result, "Missing 'bbl'"
        assert "registration" in result or "note" in result, "Missing 'registration' and 'note'"
        reg = result.get("registration")
        detail = f"registration_found={reg is not None}"
        if reg:
            detail += f", address={reg.get('address')}, last_reg={reg.get('last_registration_date')}"
        if result.get("contacts"):
            detail += f", contact_types={list(result['contacts'].keys())}"
        record(PASS, name, detail)
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_get_building_permits_basic(bbl: str) -> None:
    name = "get_building_permits (basic)"
    try:
        from nyc_property_intel.tools.permits import get_building_permits
        result = await run(get_building_permits(bbl=bbl))
        assert "bbl" in result, "Missing 'bbl'"
        assert "permits" in result or "note" in result, "Missing 'permits' and 'note'"
        permits = result.get("permits", [])
        detail = f"permits={len(permits)}"
        if permits:
            p0 = permits[0]
            detail += f", latest_type={p0.get('jobtype')} ({p0.get('jobtype_description')}), status={p0.get('jobstatus')}"
        if "data_as_of" in result:
            detail += f", has data_as_of=yes"
        record(PASS, name, detail)
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_get_building_permits_job_type(bbl: str) -> None:
    name = "get_building_permits (job_type=A2)"
    try:
        from nyc_property_intel.tools.permits import get_building_permits
        result = await run(get_building_permits(bbl=bbl, job_type="A2"))
        assert "permits" in result or "note" in result, "Missing 'permits' and 'note'"
        permits = result.get("permits", [])
        for p in permits:
            assert p.get("jobtype") == "A2", f"Expected A2 got {p.get('jobtype')!r}"
        record(PASS, name, f"job_type=A2, permits={len(permits)}, all types verified")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_get_liens_and_encumbrances(bbl: str) -> None:
    name = "get_liens_and_encumbrances (tax_liens + mortgages)"
    try:
        from nyc_property_intel.tools.liens import get_liens_and_encumbrances
        result = await run(get_liens_and_encumbrances(bbl=bbl))
        assert "bbl" in result, "Missing 'bbl'"
        assert "data_as_of" in result, "Missing 'data_as_of'"
        assert "tax_liens" in result or "tax_liens_note" in result, "Missing tax_liens section"
        assert "mortgages" in result or "mortgages_note" in result, "Missing mortgages section"
        tax_liens = result.get("tax_liens", [])
        mortgages = result.get("mortgages", [])
        detail = f"tax_liens={len(tax_liens)}, has_tax_liens={result.get('has_tax_liens')}, mortgages={len(mortgages)}"
        if mortgages:
            m0 = mortgages[0]
            detail += f", latest_mortgage={m0.get('docamount_formatted')}"
        record(PASS, name, detail)
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_get_tax_info(bbl: str) -> None:
    name = "get_tax_info (basic)"
    try:
        from nyc_property_intel.tools.tax import get_tax_info
        result = await run(get_tax_info(bbl=bbl))
        assert "bbl" in result, "Missing 'bbl'"
        assert "data_as_of" in result, "Missing 'data_as_of'"
        assert "assessment" in result, "Missing 'assessment'"
        assert "exemptions" in result or "exemptions_note" in result, "Missing exemptions section"
        a = result.get("assessment")
        detail = f"assessment_found={a is not None}"
        if a:
            detail += (
                f", year={a.get('year')}"
                f", market_value={a.get('market_value_total_formatted')}"
                f", assessed={a.get('assessed_value_total_formatted')}"
                f", taxable={a.get('taxable_value_formatted')}"
                f", tax_class={a.get('tax_class')}"
            )
        exemptions = result.get("exemptions", [])
        detail += f", exemptions={len(exemptions)}"
        record(PASS, name, detail)
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_get_rent_stabilization(bbl: str) -> None:
    name = "get_rent_stabilization (basic)"
    try:
        from nyc_property_intel.tools.rentstab import get_rent_stabilization
        result = await run(get_rent_stabilization(bbl=bbl))
        assert "bbl" in result, "Missing 'bbl'"
        assert "is_rent_stabilized" in result, "Missing 'is_rent_stabilized'"
        if result.get("is_rent_stabilized"):
            assert "unit_counts_by_year" in result, "Missing 'unit_counts_by_year'"
            assert "data_as_of" in result, "Missing 'data_as_of'"
            detail = (
                f"is_rent_stabilized=True"
                f", latest_units={result.get('latest_stabilized_units')}"
                f", latest_year={result.get('latest_year')}"
                f", trend={result.get('trend')}"
                f", years_with_data={len(result.get('unit_counts_by_year', []))}"
            )
        else:
            detail = f"is_rent_stabilized={result.get('is_rent_stabilized')}, note={result.get('note')}"
        record(PASS, name, detail)
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_search_comps(zip_code: str) -> None:
    name = "search_comps (by zip_code with stats)"
    try:
        from nyc_property_intel.tools.comps import search_comps
        result = await run(search_comps(zip_code=zip_code, include_stats=True))
        assert "search_criteria" in result, "Missing 'search_criteria'"
        assert "comps" in result or "comps_note" in result, "Missing 'comps' and 'comps_note'"
        assert "data_as_of" in result, "Missing 'data_as_of'"
        comps = result.get("comps", [])
        stats = result.get("quarterly_stats", [])
        detail = f"zip={zip_code}, comps={len(comps)}, quarterly_stats_periods={len(stats)}"
        if comps:
            c0 = comps[0]
            detail += f", sample_sale={c0.get('saleprice_formatted')} @ {c0.get('saledate')}"
            detail += f", ppsf={c0.get('price_per_sqft_formatted')}"
        record(PASS, name, detail)
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_search_neighborhood_stats_by_zip(zip_code: str) -> None:
    name = "search_neighborhood_stats (by zip_code)"
    try:
        from nyc_property_intel.tools.neighborhood import search_neighborhood_stats
        result = await run(search_neighborhood_stats(zip_code=zip_code, months=24))
        assert "search_criteria" in result, "Missing 'search_criteria'"
        assert "data_as_of" in result, "Missing 'data_as_of'"
        detail = f"zip={zip_code}"
        ss = result.get("sales_summary")
        if ss:
            detail += (
                f", market_sales={ss.get('market_sales')}"
                f", median={ss.get('median_price_formatted')}"
                f", avg_ppsf={ss.get('avg_ppsf_formatted')}"
            )
        ps = result.get("property_stock")
        if ps:
            detail += f", total_properties={ps.get('total_properties')}"
        vs = result.get("violation_summary")
        if vs:
            detail += f", total_hpd={vs.get('total_hpd_violations')}"
        record(PASS, name, detail)
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_search_neighborhood_stats_by_name() -> None:
    name = "search_neighborhood_stats (by neighborhood name)"
    try:
        from nyc_property_intel.tools.neighborhood import search_neighborhood_stats
        result = await run(search_neighborhood_stats(neighborhood="MIDTOWN", months=12))
        assert "search_criteria" in result, "Missing 'search_criteria'"
        assert "data_as_of" in result, "Missing 'data_as_of'"
        ss = result.get("sales_summary")
        detail = f"neighborhood=MIDTOWN, sales_summary_found={ss is not None}"
        if ss:
            detail += f", market_sales={ss.get('market_sales')}, median={ss.get('median_price_formatted')}"
        record(PASS, name, detail)
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_analyze_property(bbl: str) -> None:
    name = "analyze_property (full analysis)"
    try:
        from nyc_property_intel.tools.analysis import analyze_property
        result = await run(analyze_property(bbl=bbl))
        required_keys = [
            "property_summary", "financial_snapshot", "development_potential",
            "risk_factors", "comparable_market", "key_observations",
            "data_as_of", "disclaimer",
        ]
        missing = [k for k in required_keys if k not in result]
        if missing:
            raise AssertionError(f"Missing keys: {missing}")
        ps = result["property_summary"]
        fs = result["financial_snapshot"]
        dev = result["development_potential"]
        rf = result["risk_factors"]
        cm = result["comparable_market"]
        detail = (
            f"address={ps.get('address')}"
            f", owner={ps.get('owner')}"
            f", year_built={ps.get('year_built')}"
            f", assessed_total={fs.get('assessed_total')}"
            f", unused_far={dev.get('unused_far')}"
            f", hpd_total={rf.get('hpd_total_violations')}"
            f", hpd_class_c={rf.get('hpd_class_c_count')}"
            f", comps={cm.get('num_recent_sales')}"
            f", median_ppsf={cm.get('median_price_per_sqft')}"
        )
        obs = result.get("key_observations", [])
        if obs:
            detail += f", observations={len(obs)}"
        data_gaps = result.get("data_gaps")
        if data_gaps:
            detail += f", data_gaps={len(data_gaps)}"
        record(PASS, name, detail)
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Pretty-print sample output for interesting tools
# ---------------------------------------------------------------------------

async def print_sample_output() -> None:
    print("\n" + "=" * 60)
    print("SAMPLE OUTPUT — 3 interesting tools")
    print("=" * 60)

    bbl = "1012920015"  # 590 Madison Ave

    # 1. lookup_property
    print("\n--- lookup_property (590 Madison Ave) ---")
    try:
        from nyc_property_intel.tools.lookup import lookup_property
        r = await run(lookup_property(bbl=bbl))
        for k in ["bbl_formatted", "address", "ownername", "yearbuilt", "numfloors",
                   "unitstotal", "bldgclass", "zonedist1", "assesstot", "data_as_of"]:
            print(f"  {k}: {r.get(k)}")
    except Exception as exc:
        print(f"  ERROR: {exc}")

    # 2. get_tax_info
    print("\n--- get_tax_info (590 Madison Ave) ---")
    try:
        from nyc_property_intel.tools.tax import get_tax_info
        r = await run(get_tax_info(bbl=bbl))
        a = r.get("assessment")
        if a:
            for k in ["year", "tax_class", "tax_class_description",
                      "market_value_total_formatted", "assessed_value_total_formatted",
                      "taxable_value_formatted"]:
                print(f"  {k}: {a.get(k)}")
        exemptions = r.get("exemptions", [])
        print(f"  exemptions: {len(exemptions)}")
        for e in exemptions[:3]:
            print(f"    - code={e.get('code')} name={e.get('name')} value={e.get('exempt_value_formatted')}")
    except Exception as exc:
        print(f"  ERROR: {exc}")

    # 3. analyze_property
    print("\n--- analyze_property (590 Madison Ave) ---")
    try:
        from nyc_property_intel.tools.analysis import analyze_property
        r = await run(analyze_property(bbl=bbl))
        ps = r.get("property_summary", {})
        rf = r.get("risk_factors", {})
        dev = r.get("development_potential", {})
        cm = r.get("comparable_market", {})
        print(f"  address: {ps.get('address')}")
        print(f"  owner: {ps.get('owner')}")
        print(f"  year_built: {ps.get('year_built')}")
        print(f"  hpd_total_violations: {rf.get('hpd_total_violations')}")
        print(f"  hpd_class_c: {rf.get('hpd_class_c_count')}")
        print(f"  has_tax_lien: {rf.get('has_tax_lien')}")
        print(f"  unused_far: {dev.get('unused_far')}")
        print(f"  unused_sqft: {dev.get('unused_sqft')}")
        print(f"  is_maxed_out: {dev.get('is_maxed_out')}")
        print(f"  comps_in_zip: {cm.get('num_recent_sales')}")
        print(f"  median_ppsf: {cm.get('median_price_per_sqft')}")
        obs = r.get("key_observations", [])
        print(f"  key_observations ({len(obs)}):")
        for o in obs:
            print(f"    • {o}")
        gaps = r.get("data_gaps")
        if gaps:
            print(f"  data_gaps ({len(gaps)}):")
            for g in gaps:
                print(f"    • {g}")
    except Exception as exc:
        print(f"  ERROR: {exc}")


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

async def main() -> int:
    print("=" * 60)
    print("NYC Property Intel MCP — Manual QA")
    print("=" * 60)

    bbls = await discover_bbls()

    # Pick best BBL for each role (fallback to known-good defaults)
    bbl_manhattan = bbls.get("manhattan", "1012920015")
    bbl_brooklyn = bbls.get("brooklyn_violator", "3013020001")
    bbl_rentstab = bbls.get("rentstab", "3013020001")
    bbl_complaints = bbls.get("hpd_complaints", bbl_brooklyn)
    bbl_permits = bbls.get("dobjobs", bbl_manhattan)
    bbl_litigations = bbls.get("hpd_litigations", bbl_brooklyn)
    bbl_registrations = bbls.get("hpd_registrations", bbl_brooklyn)
    zip_sales = bbls.get("sales_zip", "10022")

    print(f"\nTest BBLs:")
    print(f"  manhattan     : {bbl_manhattan}")
    print(f"  brooklyn      : {bbl_brooklyn}")
    print(f"  rentstab      : {bbl_rentstab}")
    print(f"  complaints    : {bbl_complaints}")
    print(f"  permits       : {bbl_permits}")
    print(f"  litigations   : {bbl_litigations}")
    print(f"  registrations : {bbl_registrations}")
    print(f"  sales_zip     : {zip_sales}")

    print("\n--- Running 13 tools / 20 test cases ---\n")

    # 1. lookup_property
    await test_lookup_by_bbl(bbl_manhattan)
    await test_lookup_by_address()

    # 2. get_property_issues
    await test_get_property_issues_all(bbl_brooklyn)
    await test_get_property_issues_hpd(bbl_brooklyn)
    await test_get_property_issues_dob(bbl_brooklyn)
    await test_get_property_issues_ecb(bbl_brooklyn)

    # 3. get_property_history
    await test_get_property_history(bbl_manhattan)

    # 4. get_hpd_complaints
    await test_get_hpd_complaints_basic(bbl_complaints)
    await test_get_hpd_complaints_status(bbl_complaints)
    await test_get_hpd_complaints_category(bbl_complaints)

    # 5. get_hpd_litigations
    await test_get_hpd_litigations(bbl_litigations)

    # 6. get_hpd_registration
    await test_get_hpd_registration(bbl_registrations)

    # 7. get_building_permits
    await test_get_building_permits_basic(bbl_permits)
    await test_get_building_permits_job_type(bbl_permits)

    # 8. get_liens_and_encumbrances
    await test_get_liens_and_encumbrances(bbl_manhattan)

    # 9. get_tax_info
    await test_get_tax_info(bbl_manhattan)

    # 10. get_rent_stabilization
    await test_get_rent_stabilization(bbl_rentstab)

    # 11. search_comps
    await test_search_comps(zip_sales)

    # 12. search_neighborhood_stats
    await test_search_neighborhood_stats_by_zip(zip_sales)
    await test_search_neighborhood_stats_by_name()

    # 13. analyze_property
    await test_analyze_property(bbl_manhattan)

    # ── Summary ────────────────────────────────────────────────────────
    passing = sum(1 for s, _, _ in results if s == PASS)
    failing = sum(1 for s, _, _ in results if s == FAIL)
    skipped = sum(1 for s, _, _ in results if s == SKIP)
    total = len(results)

    print("\n" + "=" * 60)
    print(f"SUMMARY: {passing}/{total} tests passing  "
          f"({failing} failing, {skipped} skipped)")
    print("=" * 60)

    if failing:
        print("\nFAILED tests:")
        for s, n, d in results:
            if s == FAIL:
                print(f"  {FAIL} {n}: {d}")

    # Print rich sample output
    await print_sample_output()

    return 0 if failing == 0 else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
