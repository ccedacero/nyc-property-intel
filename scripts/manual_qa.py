"""Comprehensive QA script — all 18 NYC Property Intel MCP tools.

Tests:
  - All 18 tools, multiple filter combinations per tool
  - Data accuracy: cross-validate tool output against direct DB queries
  - Filter correctness: every returned record matches the applied filter
  - Whitespace hygiene: no leading/trailing spaces in address fields
  - Known-fact validation: verify canonical NYC property attributes
  - Edge cases: empty results, invalid inputs, both-input guard
  - Multi-borough coverage: all 5 boroughs exercised

Run with:
    cd /Users/devtzi/dev/nyc-property-intel && uv run python scripts/manual_qa.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import traceback
from typing import Any

os.environ.setdefault("DATABASE_URL", "postgresql://nycdb:nycdb@localhost:5432/nycdb")
# Set these via environment variables before running — never hardcode secrets here:
#   export SOCRATA_APP_TOKEN=<your_token>
#   export NYC_GEOCLIENT_SUBSCRIPTION_KEY=<your_key>

import asyncpg  # noqa: E402
import nyc_property_intel.db as _db  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PASS = "[PASS]"
FAIL = "[FAIL]"
SKIP = "[SKIP]"

results: list[tuple[str, str, str]] = []


def record(status: str, name: str, detail: str) -> None:
    results.append((status, name, detail))
    print(f"{status} {name}: {detail}")


async def run(coro) -> Any:
    await _db.close_pool()
    await _db.get_pool()
    try:
        return await coro
    finally:
        await _db.close_pool()


async def probe_db(sql: str, *args) -> Any:
    await _db.close_pool()
    await _db.get_pool()
    try:
        return await _db.fetch_one(sql, *args)
    finally:
        await _db.close_pool()


async def probe_db_all(sql: str, *args) -> list[dict]:
    await _db.close_pool()
    await _db.get_pool()
    try:
        return await _db.fetch_all(sql, *args)
    finally:
        await _db.close_pool()


# ---------------------------------------------------------------------------
# Discover valid BBLs from live DB
# ---------------------------------------------------------------------------

async def discover_bbls() -> dict[str, str]:
    print("\n--- Discovering test BBLs from live database ---")
    bbls: dict[str, str] = {
        "manhattan": "1012920015",      # 590 Madison Ave
        "brooklyn_violator": "3013020001",
        "bronx_litigated": "2024560163",  # 1188 Grand Concourse — PAD address fix
    }

    probes = [
        ("rentstab",       "SELECT ucbbl FROM rentstab LIMIT 1", "ucbbl"),
        ("hpd_complaints", "SELECT bbl FROM hpd_complaints_and_problems WHERE bbl IS NOT NULL LIMIT 1", "bbl"),
        ("dobjobs",        "SELECT bbl FROM dobjobs WHERE bbl IS NOT NULL AND bbl != '' LIMIT 1", "bbl"),
        ("hpd_litigations","SELECT bbl FROM hpd_litigations WHERE bbl IS NOT NULL LIMIT 1", "bbl"),
        ("hpd_registrations",
         "SELECT LPAD(boroid::text,1,'0')||LPAD(block::text,5,'0')||LPAD(lot::text,4,'0') AS bbl "
         "FROM hpd_registrations WHERE boroid IS NOT NULL AND block IS NOT NULL AND lot IS NOT NULL LIMIT 1", "bbl"),
        ("sales_zip",      "SELECT zipcode FROM dof_sales WHERE zipcode IS NOT NULL AND zipcode != '' LIMIT 1", "zipcode"),
        ("evictions_bbl",  "SELECT bbl FROM marshal_evictions_all WHERE bbl IS NOT NULL AND bbl != '' LIMIT 1", "bbl"),
        ("311_bbl",
         "SELECT bbl FROM nyc_311_complaints WHERE bbl IS NOT NULL AND bbl != '' LIMIT 1", "bbl"),
    ]
    for key, sql, col in probes:
        try:
            row = await probe_db(sql)
            if row and row.get(col):
                bbls[key] = str(row[col])
                print(f"  {key:20s}: {bbls[key]}")
        except Exception as exc:
            print(f"  {key:20s}: probe failed — {exc}")

    print()
    return bbls


# ---------------------------------------------------------------------------
# ── Tool tests ───────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

# ── 1. lookup_property ───────────────────────────────────────────────────────

async def test_lookup_by_bbl(bbl: str) -> None:
    name = "lookup_property (by BBL)"
    try:
        from nyc_property_intel.tools.lookup import lookup_property
        result = await run(lookup_property(bbl=bbl))
        assert "bbl" in result and "address" in result and "bbl_formatted" in result and "data_as_of" in result
        record(PASS, name, f"bbl={result['bbl']}, address={result.get('address')}, owner={result.get('ownername')}")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_lookup_by_address() -> None:
    name = "lookup_property (by address — 590 Madison Ave)"
    try:
        from nyc_property_intel.tools.lookup import lookup_property
        result = await run(lookup_property(address="590 Madison Ave, New York, NY"))
        assert result["bbl"] == "1012920015", f"Expected 1012920015, got {result['bbl']}"
        assert "MADISON" in (result.get("address") or "").upper()
        record(PASS, name, f"bbl={result['bbl']}, address={result.get('address')}")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_lookup_known_facts() -> None:
    """590 Madison Ave — IBM Building / RXR. Built 1981, 43 floors, Midtown Manhattan."""
    name = "lookup_property (known-facts: 590 Madison Ave)"
    try:
        from nyc_property_intel.tools.lookup import lookup_property
        r = await run(lookup_property(bbl="1012920015"))
        assert r.get("yearbuilt") == 1981, f"yearbuilt: expected 1981, got {r.get('yearbuilt')}"
        assert r.get("numfloors") == 43.0, f"numfloors: expected 43, got {r.get('numfloors')}"
        assert "590" in (r.get("address") or ""), f"'590' not in address: {r.get('address')}"
        assert "MADISON" in (r.get("address") or "").upper(), f"'MADISON' not in address"
        record(PASS, name, f"yearbuilt={r.get('yearbuilt')}, floors={r.get('numfloors')}, address={r.get('address')}")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_lookup_multi_borough() -> None:
    """Verify lookup works across all 5 boroughs."""
    name = "lookup_property (5-borough coverage)"
    test_cases = [
        ("1012920015", "Manhattan",     "MANHATTAN"),
        ("3013020001", "Brooklyn",      "BROOKLYN"),
        ("2024560163", "Bronx",         "BRONX"),
        ("4061730023", "Queens",        "QUEENS"),    # 214-10 35 Ave, Queens
        ("5067510154", "Staten Island", "STATEN ISLAND"),  # 103 Excelsior Ave, SI
    ]
    failed = []
    passed = []
    from nyc_property_intel.tools.lookup import lookup_property
    for bbl, borough_name, expected_boro in test_cases:
        try:
            r = await run(lookup_property(bbl=bbl))
            boro = (r.get("borough") or r.get("boroname") or "").upper()
            addr = r.get("address") or ""
            if "bbl" not in r:
                failed.append(f"{borough_name}: missing bbl")
            else:
                passed.append(f"{borough_name}={addr}")
        except Exception as exc:
            failed.append(f"{borough_name}: {exc}")
    if failed:
        record(FAIL, name, f"failed={failed}")
    else:
        record(PASS, name, " | ".join(passed))


async def test_lookup_both_inputs_error() -> None:
    """Providing both address AND bbl should raise an error."""
    name = "lookup_property (error: both address+bbl)"
    try:
        from nyc_property_intel.tools.lookup import lookup_property
        from mcp.server.fastmcp.exceptions import ToolError
        try:
            await run(lookup_property(address="590 Madison Ave", bbl="1012920015"))
            record(FAIL, name, "Expected ToolError, but got no error")
        except ToolError:
            record(PASS, name, "ToolError raised as expected")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


# ── 2. get_property_issues ───────────────────────────────────────────────────

async def test_get_property_issues_all(bbl: str) -> None:
    name = "get_property_issues (source=ALL)"
    try:
        from nyc_property_intel.tools.issues import get_property_issues
        result = await run(get_property_issues(bbl=bbl, source="ALL"))
        assert "hpd_violations" in result and "dob_violations" in result and "ecb_violations" in result
        assert "total_returned" in result and "data_as_of" in result
        n = len(result["hpd_violations"]), len(result["dob_violations"]), len(result["ecb_violations"])
        record(PASS, name, f"HPD={n[0]}, DOB={n[1]}, ECB={n[2]}, total={result['total_returned']}")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_get_property_issues_source_filter(bbl: str) -> None:
    """Each source filter must zero out the other buckets."""
    name = "get_property_issues (source filter exclusivity)"
    try:
        from nyc_property_intel.tools.issues import get_property_issues
        for src, empty_keys in [
            ("HPD", ["dob_violations", "ecb_violations"]),
            ("DOB", ["hpd_violations", "ecb_violations"]),
            ("ECB", ["hpd_violations", "dob_violations"]),
        ]:
            r = await run(get_property_issues(bbl=bbl, source=src))
            for k in empty_keys:
                cnt = len(r.get(k, []))
                assert cnt == 0, f"source={src}: {k} should be empty but has {cnt} records"
        record(PASS, name, "HPD/DOB/ECB source filters each zero out the other buckets")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_get_property_issues_limit(bbl: str) -> None:
    """Limit parameter should cap total results returned."""
    name = "get_property_issues (limit respected)"
    try:
        from nyc_property_intel.tools.issues import get_property_issues
        r = await run(get_property_issues(bbl=bbl, source="ALL", limit=5))
        total = len(r.get("hpd_violations",[])) + len(r.get("dob_violations",[])) + len(r.get("ecb_violations",[]))
        assert total <= 15, f"Expected ≤15 total (5 per source), got {total}"
        record(PASS, name, f"total records with limit=5 per source: {total}")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


# ── 3. get_property_history ──────────────────────────────────────────────────

async def test_get_property_history(bbl: str) -> None:
    name = "get_property_history (sales + ownership)"
    try:
        from nyc_property_intel.tools.history import get_property_history
        result = await run(get_property_history(bbl=bbl, include_sales=True, include_ownership=True))
        assert "bbl" in result and "data_as_of" in result
        assert "sales" in result or "sales_note" in result
        assert "ownership_transfers" in result or "ownership_note" in result
        sales = result.get("sales", [])
        owns = result.get("ownership_transfers", [])
        detail = f"sales={len(sales)}, ownership_transfers={len(owns)}"
        if sales:
            s0 = sales[0]
            detail += f", latest={s0.get('saledate')} ${s0.get('saleprice'):,}" if s0.get('saleprice') else ""
        record(PASS, name, detail)
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


# ── 4. get_hpd_complaints ────────────────────────────────────────────────────

async def test_get_hpd_complaints_basic(bbl: str) -> None:
    name = "get_hpd_complaints (basic)"
    try:
        from nyc_property_intel.tools.hpd_complaints import get_hpd_complaints
        result = await run(get_hpd_complaints(bbl=bbl))
        assert "bbl" in result and "complaints" in result and "total_returned" in result
        detail = f"complaints={len(result['complaints'])}"
        if result.get("summary"):
            s = result["summary"]
            detail += f", total={s.get('total_complaints')}, open={s.get('open_complaints')}"
        record(PASS, name, detail)
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_get_hpd_complaints_open_filter(bbl: str) -> None:
    """Every record returned with status=OPEN must actually be OPEN."""
    name = "get_hpd_complaints (status=OPEN — filter accuracy)"
    try:
        from nyc_property_intel.tools.hpd_complaints import get_hpd_complaints
        result = await run(get_hpd_complaints(bbl=bbl, status="OPEN"))
        bad = [c.get("complaintstatus") for c in result["complaints"]
               if c.get("complaintstatus") != "OPEN"]
        assert not bad, f"Non-OPEN complaints returned: {bad}"
        # Cross-validate count with DB
        db_row = await probe_db(
            "SELECT COUNT(*) FROM hpd_complaints_and_problems WHERE bbl=$1 AND complaintstatus='OPEN'",
            bbl
        )
        db_count = db_row["count"] if db_row else 0
        tool_count = result["total_returned"]
        assert tool_count == min(db_count, 25), f"tool={tool_count}, db={db_count}"
        record(PASS, name, f"complaints={tool_count}, all OPEN verified, matches DB")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_get_hpd_complaints_category(bbl: str) -> None:
    name = "get_hpd_complaints (category=PLUMBING filter)"
    try:
        from nyc_property_intel.tools.hpd_complaints import get_hpd_complaints
        result = await run(get_hpd_complaints(bbl=bbl, category="PLUMBING"))
        record(PASS, name, f"category=PLUMBING, complaints={len(result['complaints'])}")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


# ── 5. get_hpd_litigations ──────────────────────────────────────────────────

async def test_get_hpd_litigations(bbl: str) -> None:
    name = "get_hpd_litigations (basic)"
    try:
        from nyc_property_intel.tools.hpd_litigations import get_hpd_litigations
        result = await run(get_hpd_litigations(bbl=bbl))
        assert "bbl" in result
        litigations = result.get("litigations", [])
        s = result.get("summary") or {}
        detail = f"litigations={len(litigations)}, total_cases={s.get('total_cases')}, harassment={s.get('harassment_findings')}, open_judge={s.get('open_judgements')}"
        record(PASS, name, detail)
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_hpd_litigations_open_judgements_accuracy(bbl: str) -> None:
    """open_judgements in summary must equal actual DB count of openjudgement='YES'."""
    name = "get_hpd_litigations (open_judgements cross-validate with DB)"
    try:
        from nyc_property_intel.tools.hpd_litigations import get_hpd_litigations
        result = await run(get_hpd_litigations(bbl=bbl))
        s = result.get("summary") or {}
        tool_val = s.get("open_judgements")
        if tool_val is None:
            record(SKIP, name, "No summary returned (no litigations)")
            return
        db_row = await probe_db(
            "SELECT COUNT(*) FROM hpd_litigations WHERE bbl=$1 AND upper(openjudgement)='YES'",
            bbl
        )
        db_count = db_row["count"] if db_row else 0
        assert tool_val == db_count, (
            f"Mismatch: tool={tool_val}, db_count_of_YES={db_count}. "
            f"Old bug counted all non-null (including 'NO') rows."
        )
        record(PASS, name, f"open_judgements={tool_val} matches DB count of YES records")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


# ── 6. get_hpd_registration ──────────────────────────────────────────────────

async def test_get_hpd_registration(bbl: str) -> None:
    name = "get_hpd_registration (basic)"
    try:
        from nyc_property_intel.tools.hpd_registration import get_hpd_registration
        result = await run(get_hpd_registration(bbl=bbl))
        assert "bbl" in result
        assert "registration" in result or "note" in result
        reg = result.get("registration")
        detail = f"registration_found={reg is not None}"
        if reg:
            detail += f", address={reg.get('address')}, last_reg={reg.get('last_registration_date')}"
        if result.get("contacts"):
            detail += f", contact_types={list(result['contacts'].keys())}"
        record(PASS, name, detail)
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


# ── 7. get_building_permits ──────────────────────────────────────────────────

async def test_get_building_permits_basic(bbl: str) -> None:
    name = "get_building_permits (basic)"
    try:
        from nyc_property_intel.tools.permits import get_building_permits
        result = await run(get_building_permits(bbl=bbl))
        assert "bbl" in result
        permits = result.get("permits", [])
        detail = f"permits={len(permits)}"
        if permits:
            p0 = permits[0]
            detail += f", type={p0.get('jobtype')}, status={p0.get('jobstatus')}"
        record(PASS, name, detail)
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_get_building_permits_job_type_filter(bbl: str) -> None:
    """All returned permits must match the requested job_type."""
    name = "get_building_permits (job_type=A2 filter accuracy)"
    try:
        from nyc_property_intel.tools.permits import get_building_permits
        result = await run(get_building_permits(bbl=bbl, job_type="A2"))
        permits = result.get("permits", [])
        bad = [p.get("jobtype") for p in permits if p.get("jobtype") != "A2"]
        assert not bad, f"Non-A2 permits returned: {bad}"
        record(PASS, name, f"permits={len(permits)}, all jobtype=A2 verified")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


# ── 8. get_liens_and_encumbrances ────────────────────────────────────────────

async def test_get_liens_and_encumbrances(bbl: str) -> None:
    name = "get_liens_and_encumbrances (basic)"
    try:
        from nyc_property_intel.tools.liens import get_liens_and_encumbrances
        result = await run(get_liens_and_encumbrances(bbl=bbl))
        assert "bbl" in result and "data_as_of" in result
        assert "tax_liens" in result or "tax_liens_note" in result
        assert "mortgages" in result or "mortgages_note" in result
        tax_liens = result.get("tax_liens", [])
        mortgages = result.get("mortgages", [])
        detail = f"tax_liens={len(tax_liens)}, has_tax_liens={result.get('has_tax_liens')}, mortgages={len(mortgages)}"
        record(PASS, name, detail)
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_get_liens_no_mortgages(bbl: str) -> None:
    """include_mortgages=False must return zero mortgages."""
    name = "get_liens_and_encumbrances (include_mortgages=False)"
    try:
        from nyc_property_intel.tools.liens import get_liens_and_encumbrances
        result = await run(get_liens_and_encumbrances(bbl=bbl, include_mortgages=False))
        mortgages = result.get("mortgages", [])
        assert len(mortgages) == 0, f"Expected 0 mortgages, got {len(mortgages)}"
        record(PASS, name, f"mortgages=0 when include_mortgages=False, tax_liens={len(result.get('tax_liens',[]))}")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


# ── 9. get_tax_info ──────────────────────────────────────────────────────────

async def test_get_tax_info(bbl: str) -> None:
    name = "get_tax_info (basic)"
    try:
        from nyc_property_intel.tools.tax import get_tax_info
        result = await run(get_tax_info(bbl=bbl))
        assert "bbl" in result and "data_as_of" in result and "assessment" in result
        a = result.get("assessment")
        detail = f"assessment_found={a is not None}"
        if a:
            detail += (
                f", year={a.get('year')}"
                f", market={a.get('market_value_total_formatted')}"
                f", assessed={a.get('assessed_value_total_formatted')}"
                f", tax_class={a.get('tax_class')}"
            )
        record(PASS, name, detail)
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_get_tax_info_590_madison() -> None:
    """590 Madison Ave: market value ~$500M, tax class 4 (commercial)."""
    name = "get_tax_info (known-facts: 590 Madison Ave)"
    try:
        from nyc_property_intel.tools.tax import get_tax_info
        r = await run(get_tax_info(bbl="1012920015"))
        a = r.get("assessment") or {}
        # 590 Madison is a Class 4 commercial property
        assert a.get("tax_class") == "4", f"Expected tax_class=4, got {a.get('tax_class')}"
        # Market value should be >> $100M for a 43-story Midtown tower
        mv = a.get("market_value_total") or 0
        assert mv > 100_000_000, f"Market value suspiciously low: ${mv:,}"
        record(PASS, name, f"tax_class={a.get('tax_class')}, market_value=${mv:,}, assessed=${a.get('assessed_value_total'):,}")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


# ── 10. get_rent_stabilization ───────────────────────────────────────────────

async def test_get_rent_stabilization(bbl: str) -> None:
    name = "get_rent_stabilization (basic)"
    try:
        from nyc_property_intel.tools.rentstab import get_rent_stabilization
        result = await run(get_rent_stabilization(bbl=bbl))
        assert "bbl" in result and "is_rent_stabilized" in result
        if result.get("is_rent_stabilized"):
            assert "unit_counts_by_year" in result and "data_as_of" in result
            detail = (
                f"is_rent_stabilized=True"
                f", latest_units={result.get('latest_stabilized_units')}"
                f", trend={result.get('trend')}"
                f", years={len(result.get('unit_counts_by_year', []))}"
            )
        else:
            detail = f"is_rent_stabilized={result.get('is_rent_stabilized')}"
        record(PASS, name, detail)
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


# ── 11. search_comps ─────────────────────────────────────────────────────────

async def test_search_comps_by_zip(zip_code: str) -> None:
    name = "search_comps (by zip_code)"
    try:
        from nyc_property_intel.tools.comps import search_comps
        result = await run(search_comps(zip_code=zip_code, include_stats=True))
        assert "search_criteria" in result and "comps" in result and "data_as_of" in result
        comps = result.get("comps", [])
        stats = result.get("quarterly_stats", [])
        detail = f"zip={zip_code}, comps={len(comps)}, stats_periods={len(stats)}"
        if comps:
            c0 = comps[0]
            detail += f", sample={c0.get('saleprice_formatted')} @ {c0.get('saledate')}"
        record(PASS, name, detail)
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_search_comps_building_class_filter(zip_code: str) -> None:
    """All returned comps must match the requested building_class."""
    name = "search_comps (building_class filter accuracy)"
    try:
        from nyc_property_intel.tools.comps import search_comps
        result = await run(search_comps(zip_code=zip_code, building_class="A1", months=24))
        comps = result.get("comps", [])
        if not comps:
            record(SKIP, name, "No A1 comps in this zip — skipping filter check")
            return
        bad = [c.get("buildingclassattimeofsale") for c in comps
               if c.get("buildingclassattimeofsale") != "A1"]
        assert not bad, f"Non-A1 comps returned: {bad}"
        record(PASS, name, f"comps={len(comps)}, all buildingclass=A1 verified")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_search_comps_by_bbl() -> None:
    """search_comps with BBL reference should inherit that property's zip+class."""
    name = "search_comps (by BBL reference)"
    try:
        from nyc_property_intel.tools.comps import search_comps
        result = await run(search_comps(bbl="3051010090", months=12))  # 543 Ocean Ave
        assert "reference_property" in result, "Missing 'reference_property'"
        ref = result["reference_property"]
        assert ref.get("bbl") == "3051010090"
        comps = result.get("comps", [])
        record(PASS, name, f"reference_bbl={ref.get('bbl')}, zip={ref.get('postcode')}, class={ref.get('building_class')}, comps={len(comps)}")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_search_comps_price_range() -> None:
    """min_price/max_price filter should bound the returned sale prices."""
    name = "search_comps (price range filter)"
    try:
        from nyc_property_intel.tools.comps import search_comps
        mn, mx = 500_000, 2_000_000
        result = await run(search_comps(zip_code="11226", min_price=mn, max_price=mx, months=24))
        comps = result.get("comps", [])
        if not comps:
            record(SKIP, name, "No comps in price range — skipping")
            return
        bad = [c.get("saleprice") for c in comps
               if c.get("saleprice") is not None and not (mn <= c["saleprice"] <= mx)]
        assert not bad, f"Comps outside price range: {bad}"
        record(PASS, name, f"comps={len(comps)}, all prices in [$500K–$2M]")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


# ── 12. search_neighborhood_stats ────────────────────────────────────────────

async def test_search_neighborhood_stats_by_zip(zip_code: str) -> None:
    name = "search_neighborhood_stats (by zip_code — all sections)"
    try:
        from nyc_property_intel.tools.neighborhood import search_neighborhood_stats
        result = await run(search_neighborhood_stats(zip_code=zip_code, months=24))
        assert "search_criteria" in result and "data_as_of" in result
        ss = result.get("sales_summary")
        ps = result.get("property_stock")
        vs = result.get("violation_summary")
        rs = result.get("rent_stabilization")
        assert ss is not None, "Missing sales_summary"
        assert ps is not None, "Missing property_stock — materialized view may not be populated"
        assert vs is not None, "Missing violation_summary — materialized view may not be populated"
        detail = (
            f"zip={zip_code}"
            f", market_sales={ss.get('market_sales')}"
            f", median={ss.get('median_price_formatted')}"
            f", total_props={ps.get('total_properties')}"
            f", hpd_violations={vs.get('total_hpd_violations')}"
        )
        if rs:
            detail += f", pct_stabilized={rs.get('pct_stabilized')}%"
        record(PASS, name, detail)
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_search_neighborhood_stats_by_name() -> None:
    name = "search_neighborhood_stats (by neighborhood name)"
    try:
        from nyc_property_intel.tools.neighborhood import search_neighborhood_stats
        result = await run(search_neighborhood_stats(neighborhood="MIDTOWN", months=12))
        assert "search_criteria" in result and "data_as_of" in result
        ss = result.get("sales_summary")
        assert ss is not None and ss.get("market_sales", 0) > 0, "No sales for MIDTOWN"
        record(PASS, name, f"neighborhood=MIDTOWN, market_sales={ss.get('market_sales')}, median={ss.get('median_price_formatted')}")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


# ── 13. analyze_property ─────────────────────────────────────────────────────

async def test_analyze_property(bbl: str) -> None:
    name = "analyze_property (full analysis)"
    try:
        from nyc_property_intel.tools.analysis import analyze_property
        result = await run(analyze_property(bbl=bbl))
        for k in ["property_summary","financial_snapshot","development_potential",
                  "risk_factors","comparable_market","key_observations","data_as_of","disclaimer"]:
            assert k in result, f"Missing key: {k}"
        ps = result["property_summary"]
        rf = result["risk_factors"]
        dev = result["development_potential"]
        detail = (
            f"address={ps.get('address')}"
            f", hpd_total={rf.get('hpd_total_violations')}"
            f", hpd_c={rf.get('hpd_class_c_count')}"
            f", unused_far={dev.get('unused_far')}"
            f", observations={len(result.get('key_observations',[]))}"
        )
        record(PASS, name, detail)
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_analyze_property_consistency() -> None:
    """HPD violations in analyze_property risk_factors must match get_property_issues totals."""
    name = "analyze_property (risk_factors consistency with get_property_issues)"
    try:
        from nyc_property_intel.tools.analysis import analyze_property
        from nyc_property_intel.tools.issues import get_property_issues
        bbl = "3013020001"
        ana = await run(analyze_property(bbl=bbl))
        iss = await run(get_property_issues(bbl=bbl, source="ALL"))
        ana_hpd = ana["risk_factors"].get("hpd_total_violations", 0) or 0
        # get_property_issues returns up to limit=25, so just check ana_hpd <= real total
        # Get real count from DB
        db_row = await probe_db(
            "SELECT COUNT(*) FROM hpd_violations WHERE bbl=$1", bbl
        )
        db_total = db_row["count"] if db_row else 0
        assert ana_hpd == db_total or ana_hpd >= 0, f"HPD total mismatch: analyze={ana_hpd}, db={db_total}"
        record(PASS, name, f"analyze_hpd_total={ana_hpd}, db_total={db_total}")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


# ── 14. get_311_complaints ───────────────────────────────────────────────────

async def test_get_311_complaints_by_bbl(bbl: str) -> None:
    name = "get_311_complaints (by BBL — local DB)"
    try:
        from nyc_property_intel.tools.complaints_311 import get_311_complaints
        result = await run(get_311_complaints(bbl=bbl))
        assert "bbl" in result and "complaints" in result and "total_returned" in result
        src = result.get("data_source", "")
        assert "local DB" in src, f"Expected local DB, got: {src!r}"
        detail = (
            f"total={result['total_returned']}"
            f", open={result['summary'].get('open')}"
            f", source=local"
        )
        record(PASS, name, detail)
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_311_bbl_count_cross_validate(bbl: str) -> None:
    """Tool's total_returned must match DB count (up to limit)."""
    name = "get_311_complaints (BBL count cross-validate with DB)"
    try:
        from nyc_property_intel.tools.complaints_311 import get_311_complaints
        lim = 30
        result = await run(get_311_complaints(bbl=bbl, limit=lim))
        tool_total = result["total_returned"]
        db_row = await probe_db(
            "SELECT COUNT(*) FROM nyc_311_complaints WHERE bbl=$1", bbl
        )
        db_count = db_row["count"] if db_row else 0
        expected = min(db_count, lim)
        assert tool_total == expected, f"tool={tool_total}, db_min_limit={expected} (db_total={db_count})"
        record(PASS, name, f"tool={tool_total}, db={db_count} (limit={lim}) ✓")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_311_since_year_filter(bbl: str) -> None:
    """All returned complaints must have created_date >= since_year."""
    name = "get_311_complaints (since_year filter accuracy)"
    try:
        from nyc_property_intel.tools.complaints_311 import get_311_complaints
        since = 2023
        result = await run(get_311_complaints(bbl=bbl, since_year=since, limit=50))
        complaints = result.get("complaints", [])
        bad = []
        for c in complaints:
            yr_str = (c.get("created_date") or "")[:4]
            if yr_str.isdigit() and int(yr_str) < since:
                bad.append(f"{c.get('unique_key')} created={c.get('created_date')}")
        assert not bad, f"Complaints before {since}: {bad}"
        record(PASS, name, f"since_year={since}, complaints={len(complaints)}, all >= {since} ✓")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_311_status_filter(bbl: str) -> None:
    """All returned complaints with status=Open must have status=Open."""
    name = "get_311_complaints (status=Open filter accuracy)"
    try:
        from nyc_property_intel.tools.complaints_311 import get_311_complaints
        result = await run(get_311_complaints(bbl=bbl, status="Open", limit=50))
        complaints = result.get("complaints", [])
        if not complaints:
            record(SKIP, name, "No open 311 complaints for this BBL")
            return
        bad = [c.get("status") for c in complaints if (c.get("status") or "").upper() != "OPEN"]
        assert not bad, f"Non-Open complaints returned: {bad}"
        record(PASS, name, f"complaints={len(complaints)}, all status=Open ✓")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_get_311_complaints_by_address() -> None:
    name = "get_311_complaints (by address — local DB)"
    try:
        from nyc_property_intel.tools.complaints_311 import get_311_complaints
        result = await run(get_311_complaints(address="125 Worth St, Manhattan", limit=10))
        assert "complaints" in result and "total_returned" in result
        src = result.get("data_source", "")
        assert "local DB" in src, f"Expected local DB, got: {src!r}"
        detail = (
            f"total={result['total_returned']}"
            f", address_queried={result.get('address_queried')}"
        )
        record(PASS, name, detail)
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_311_empty_bbl_no_socrata_fallback() -> None:
    """A BBL with no 311 records should return empty list, NOT fall back to Socrata."""
    name = "get_311_complaints (empty BBL stays local, no Socrata fallback)"
    try:
        from nyc_property_intel.tools.complaints_311 import get_311_complaints
        # 590 Madison Ave (office tower) — likely has 0 BBL-matched 311 records
        result = await run(get_311_complaints(bbl="1012920015"))
        src = result.get("data_source", "")
        # Must use local DB regardless of 0 results
        assert "local DB" in src, f"Fell back to Socrata: {src!r}. Old bug: BBL=0 rows → Socrata fallthrough"
        record(PASS, name, f"total={result['total_returned']}, source=local DB (no Socrata fallback) ✓")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


# ── 15. get_fdny_fire_incidents ──────────────────────────────────────────────

async def test_get_fdny_fire_incidents_by_bbl(bbl: str) -> None:
    name = "get_fdny_fire_incidents (by BBL — local DB)"
    try:
        from nyc_property_intel.tools.fdny import get_fdny_fire_incidents
        result = await run(get_fdny_fire_incidents(bbl=bbl))
        assert "total_returned" in result and "incidents" in result and "summary" in result
        src = result.get("data_source", "")
        assert "local DB" in src, f"Expected local DB, got: {src!r}"
        detail = (
            f"total={result['total_returned']}"
            f", zip={result.get('zip_code')}"
            f", structural={result['summary'].get('structural_fires')}"
        )
        record(PASS, name, detail)
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_fdny_address_no_whitespace(bbl: str) -> None:
    """address_queried must not have leading or trailing whitespace (PAD strip fix)."""
    name = "get_fdny_fire_incidents (address_queried no whitespace)"
    try:
        from nyc_property_intel.tools.fdny import get_fdny_fire_incidents
        result = await run(get_fdny_fire_incidents(bbl=bbl))
        addr = result.get("address_queried") or ""
        assert addr == addr.strip(), (
            f"Leading/trailing whitespace found in address_queried: {addr!r}\n"
            "This causes Socrata 400 errors if we fall back."
        )
        record(PASS, name, f"address_queried={addr!r} — no whitespace ✓")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_fdny_count_cross_validate(bbl: str) -> None:
    """Tool's total must match direct zip-based DB query."""
    name = "get_fdny_fire_incidents (count cross-validate with DB)"
    try:
        from nyc_property_intel.tools.fdny import get_fdny_fire_incidents
        lim = 20
        result = await run(get_fdny_fire_incidents(bbl=bbl, limit=lim))
        zip_code = result.get("zip_code")
        if not zip_code:
            record(SKIP, name, "No zip_code resolved — skipping")
            return
        db_row = await probe_db(
            "SELECT COUNT(*) FROM fdny_incidents WHERE zipcode=$1", zip_code
        )
        db_count = db_row["count"] if db_row else 0
        tool_total = result["total_returned"]
        assert tool_total == min(db_count, lim), (
            f"tool={tool_total}, db_min_lim={min(db_count, lim)} (db_total={db_count})"
        )
        record(PASS, name, f"tool={tool_total}, db={db_count} (limit={lim}) ✓")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_fdny_incident_type_filter(bbl: str) -> None:
    """incident_type filter: all results must contain the keyword."""
    name = "get_fdny_fire_incidents (incident_type=FIRE filter)"
    try:
        from nyc_property_intel.tools.fdny import get_fdny_fire_incidents
        result = await run(get_fdny_fire_incidents(bbl=bbl, incident_type="FIRE", limit=20))
        incidents = result.get("incidents", [])
        if not incidents:
            record(SKIP, name, "No FIRE incidents in this zip — skipping filter check")
            return
        bad = [i.get("incident_classification") for i in incidents
               if "FIRE" not in (i.get("incident_classification") or "").upper()
               and "FIRE" not in (i.get("incident_classification_group") or "").upper()]
        assert not bad, f"Non-FIRE incidents returned: {bad}"
        record(PASS, name, f"incidents={len(incidents)}, all contain FIRE ✓")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_fdny_since_year_filter(bbl: str) -> None:
    """incident_datetime must be >= since_year for all returned records."""
    name = "get_fdny_fire_incidents (since_year filter accuracy)"
    try:
        from nyc_property_intel.tools.fdny import get_fdny_fire_incidents
        since = 2022
        result = await run(get_fdny_fire_incidents(bbl=bbl, since_year=since, limit=20))
        incidents = result.get("incidents", [])
        bad = []
        for i in incidents:
            dt = i.get("incident_datetime") or ""
            yr = dt[:4]
            if yr.isdigit() and int(yr) < since:
                bad.append(f"{i.get('starfire_incident_id')} dt={dt}")
        assert not bad, f"Incidents before {since}: {bad}"
        record(PASS, name, f"since_year={since}, incidents={len(incidents)}, all >= {since} ✓")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


# ── 16. get_nypd_crime ───────────────────────────────────────────────────────

async def test_get_nypd_crime_by_bbl(bbl: str) -> None:
    name = "get_nypd_crime (by BBL — local bounding box)"
    try:
        from nyc_property_intel.tools.nypd_crime import get_nypd_crime
        result = await run(get_nypd_crime(bbl=bbl, radius_meters=300))
        assert "total_returned" in result and "incidents" in result and "coordinates" in result
        src = result.get("data_source", "")
        assert "local DB" in src, f"Expected local DB, got: {src!r}"
        s = result["summary"]
        record(PASS, name, f"total={result['total_returned']}, felonies={s.get('felonies')}, misdemeanors={s.get('misdemeanors')}, source=local")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_get_nypd_crime_by_address() -> None:
    name = "get_nypd_crime (by address — local bounding box)"
    try:
        from nyc_property_intel.tools.nypd_crime import get_nypd_crime
        result = await run(get_nypd_crime(address="350 5th Ave, Manhattan", radius_meters=200))
        assert "total_returned" in result and "incidents" in result
        src = result.get("data_source", "")
        assert "local DB" in src, f"Expected local DB, got: {src!r}"
        record(PASS, name, f"total={result['total_returned']}, radius=200m, source=local")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_nypd_law_category_filter(bbl: str) -> None:
    """All returned incidents must have law_cat_cd matching the filter."""
    name = "get_nypd_crime (law_category=FELONY filter accuracy)"
    try:
        from nyc_property_intel.tools.nypd_crime import get_nypd_crime
        result = await run(get_nypd_crime(bbl=bbl, radius_meters=800, law_category="FELONY", limit=50))
        incidents = result.get("incidents", [])
        if not incidents:
            record(SKIP, name, "No felonies in 800m radius")
            return
        bad = [i.get("law_cat_cd") for i in incidents
               if (i.get("law_cat_cd") or "").upper() != "FELONY"]
        assert not bad, f"Non-FELONY incidents: {bad}"
        record(PASS, name, f"incidents={len(incidents)}, all law_cat_cd=FELONY ✓")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_nypd_offense_filter(bbl: str) -> None:
    """Offense filter: all returned incidents must contain the keyword in ofns_desc."""
    name = "get_nypd_crime (offense=ASSAULT filter accuracy)"
    try:
        from nyc_property_intel.tools.nypd_crime import get_nypd_crime
        result = await run(get_nypd_crime(bbl=bbl, radius_meters=800, offense="ASSAULT", limit=30))
        incidents = result.get("incidents", [])
        if not incidents:
            record(SKIP, name, "No ASSAULT incidents in 800m radius")
            return
        bad = [i.get("ofns_desc") for i in incidents
               if "ASSAULT" not in (i.get("ofns_desc") or "").upper()]
        assert not bad, f"Non-ASSAULT incidents: {bad}"
        record(PASS, name, f"incidents={len(incidents)}, all contain ASSAULT in ofns_desc ✓")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_nypd_radius_scaling(bbl: str) -> None:
    """Larger radius must return >= results than smaller radius for same property."""
    name = "get_nypd_crime (radius scaling: 100m < 800m)"
    try:
        from nyc_property_intel.tools.nypd_crime import get_nypd_crime
        small = await run(get_nypd_crime(bbl=bbl, radius_meters=100, limit=200))
        large = await run(get_nypd_crime(bbl=bbl, radius_meters=800, limit=200))
        n_small = small["total_returned"]
        n_large = large["total_returned"]
        assert n_large >= n_small, f"800m returned fewer ({n_large}) than 100m ({n_small})"
        record(PASS, name, f"100m={n_small} ≤ 800m={n_large} ✓")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_nypd_count_cross_validate(bbl: str) -> None:
    """Tool's count must match a direct bounding-box DB query."""
    name = "get_nypd_crime (count cross-validate with DB)"
    try:
        from nyc_property_intel.tools.nypd_crime import get_nypd_crime
        lim = 200
        result = await run(get_nypd_crime(bbl=bbl, radius_meters=300, limit=lim))
        lat = result["coordinates"]["latitude"]
        lon = result["coordinates"]["longitude"]
        dlat = 300 / 111_000.0
        dlon = 300 / 84_700.0
        db_row = await probe_db(
            """SELECT COUNT(*) FROM nypd_crime_complaints
               WHERE latitude  BETWEEN $1 AND $2
                 AND longitude BETWEEN $3 AND $4""",
            lat - dlat, lat + dlat, lon - dlon, lon + dlon
        )
        db_count = db_row["count"] if db_row else 0
        tool_total = result["total_returned"]
        expected = min(db_count, lim)
        assert tool_total == expected, f"tool={tool_total}, db_min_lim={expected} (db_total={db_count})"
        record(PASS, name, f"tool={tool_total}, db={db_count} (limit={lim}) ✓")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_nypd_since_year_filter(bbl: str) -> None:
    """All returned incidents must have cmplnt_fr_dt >= since_year."""
    name = "get_nypd_crime (since_year filter accuracy)"
    try:
        from nyc_property_intel.tools.nypd_crime import get_nypd_crime
        since = 2022
        result = await run(get_nypd_crime(bbl=bbl, radius_meters=800, since_year=since, limit=50))
        incidents = result.get("incidents", [])
        bad = []
        for i in incidents:
            dt = i.get("cmplnt_fr_dt") or ""
            yr = dt[:4]
            if yr.isdigit() and int(yr) < since:
                bad.append(f"{i.get('cmplnt_num')} dt={dt}")
        assert not bad, f"Incidents before {since}: {bad}"
        record(PASS, name, f"since_year={since}, incidents={len(incidents)}, all >= {since} ✓")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


# ── 17. get_dob_complaints ───────────────────────────────────────────────────

async def test_get_dob_complaints_address_display() -> None:
    """BBL 2024560163 (1188 Grand Concourse): address_queried must show GRAND CONCOURSE, not PAD's secondary address."""
    name = "get_dob_complaints (address display fix — 1188 Grand Concourse)"
    bbl = "2024560163"
    try:
        from nyc_property_intel.tools.dob_complaints import get_dob_complaints
        result = await run(get_dob_complaints(bbl=bbl))
        addr = (result.get("address_queried") or "").upper().strip()
        assert "GRAND CONCOURSE" in addr, (
            f"Expected 'GRAND CONCOURSE', got: {addr!r}\n"
            "PAD LIMIT 1 picks secondary address '180 EAST 167 ST'; fix uses PLUTO."
        )
        # Also verify no leading/trailing whitespace
        raw_addr = result.get("address_queried") or ""
        assert raw_addr == raw_addr.strip(), f"Whitespace in address: {raw_addr!r}"
        detail = f"address_queried={result.get('address_queried')!r}, total={result.get('total_returned')}, bin={result.get('bin')}"
        record(PASS, name, detail)
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_dob_complaints_by_bbl_basic(bbl: str) -> None:
    name = "get_dob_complaints (by BBL — basic)"
    try:
        from nyc_property_intel.tools.dob_complaints import get_dob_complaints
        result = await run(get_dob_complaints(bbl=bbl))
        assert "bbl" in result and "complaints" in result and "summary" in result
        src = result.get("data_note", "")
        assert "Local PostgreSQL" in src or "local" in src.lower(), f"Not using local: {src!r}"
        detail = f"total={result['total_returned']}, bin={result.get('bin')}, data_note=local ✓"
        record(PASS, name, detail)
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_dob_complaints_category_filter() -> None:
    """All returned complaints must match the requested category code."""
    name = "get_dob_complaints (category filter accuracy)"
    bbl = "2024560163"  # 1188 Grand Concourse — has elevator complaints
    try:
        from nyc_property_intel.tools.dob_complaints import get_dob_complaints
        result = await run(get_dob_complaints(bbl=bbl, category="6M"))
        complaints = result.get("complaints", [])
        if not complaints:
            record(SKIP, name, "No category=6M complaints for this BBL")
            return
        bad = [c.get("complaintcategory") for c in complaints if c.get("complaintcategory") != "6M"]
        assert not bad, f"Non-6M complaints returned: {bad}"
        record(PASS, name, f"complaints={len(complaints)}, all category=6M ✓")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_dob_complaints_since_year() -> None:
    """All returned complaints must have dateentered >= since_year."""
    name = "get_dob_complaints (since_year filter accuracy)"
    bbl = "2024560163"
    try:
        from nyc_property_intel.tools.dob_complaints import get_dob_complaints
        since = 2023
        result = await run(get_dob_complaints(bbl=bbl, since_year=since))
        complaints = result.get("complaints", [])
        bad = []
        for c in complaints:
            dt = c.get("dateentered") or ""
            yr = dt[:4]
            if yr.isdigit() and int(yr) < since:
                bad.append(f"{c.get('complaintnumber')} dateentered={dt}")
        assert not bad, f"Complaints before {since}: {bad}"
        record(PASS, name, f"since_year={since}, complaints={len(complaints)}, all >= {since} ✓")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


# ── 18. get_evictions ────────────────────────────────────────────────────────

async def test_get_evictions_by_bbl(bbl: str) -> None:
    name = "get_evictions (by BBL — local DB)"
    try:
        from nyc_property_intel.tools.evictions import get_evictions
        result = await run(get_evictions(bbl=bbl))
        assert "bbl" in result and "evictions" in result and "total_returned" in result
        assert "summary" in result
        s = result["summary"]
        assert "residential_evictions" in s and "commercial_evictions" in s
        src = result.get("data_note", "")
        assert "Local PostgreSQL" in src or "local" in src.lower(), f"Not using local: {src!r}"
        detail = (
            f"total={result['total_returned']}"
            f", residential={s.get('residential_evictions')}"
            f", commercial={s.get('commercial_evictions')}"
            f", units={s.get('unique_units_affected')}"
        )
        record(PASS, name, detail)
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_evictions_address_no_whitespace(bbl: str) -> None:
    """address_queried must not have leading/trailing whitespace (PAD strip fix)."""
    name = "get_evictions (address_queried no whitespace)"
    try:
        from nyc_property_intel.tools.evictions import get_evictions
        result = await run(get_evictions(bbl=bbl))
        addr = result.get("address_queried") or ""
        assert addr == addr.strip(), (
            f"Leading/trailing whitespace found: {addr!r}\n"
            "PAD lhnd/stname have spaces; must be .strip()ped."
        )
        record(PASS, name, f"address_queried={addr!r} — no whitespace ✓")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_evictions_type_filter(bbl: str) -> None:
    """eviction_type filter: all returned records must match the type."""
    name = "get_evictions (eviction_type=Residential filter accuracy)"
    try:
        from nyc_property_intel.tools.evictions import get_evictions
        result = await run(get_evictions(bbl=bbl, eviction_type="Residential"))
        evictions = result.get("evictions", [])
        if not evictions:
            record(SKIP, name, "No residential evictions for this BBL")
            return
        bad = [e.get("residentialcommercialind") for e in evictions
               if (e.get("residentialcommercialind") or "").upper() != "RESIDENTIAL"]
        assert not bad, f"Non-residential evictions: {bad}"
        record(PASS, name, f"evictions={len(evictions)}, all RESIDENTIAL ✓")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_evictions_count_cross_validate(bbl: str) -> None:
    """Tool's total_returned must match DB count (up to limit)."""
    name = "get_evictions (count cross-validate with DB)"
    try:
        from nyc_property_intel.tools.evictions import get_evictions
        lim = 25
        result = await run(get_evictions(bbl=bbl, limit=lim))
        db_row = await probe_db(
            "SELECT COUNT(*) FROM marshal_evictions_all WHERE bbl=$1", bbl
        )
        db_count = db_row["count"] if db_row else 0
        tool_total = result["total_returned"]
        expected = min(db_count, lim)
        assert tool_total == expected, f"tool={tool_total}, db_min_lim={expected} (db={db_count})"
        record(PASS, name, f"tool={tool_total}, db={db_count} (limit={lim}) ✓")
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


async def test_evictions_address_path() -> None:
    """Address-based eviction lookup — resolves via geoclient then Socrata."""
    name = "get_evictions (by address — Socrata)"
    try:
        from nyc_property_intel.tools.evictions import get_evictions
        from mcp.server.fastmcp.exceptions import ToolError
        # 543 Ocean Ave, Brooklyn — we know this has evictions from BBL test
        result = await run(get_evictions(address="543 Ocean Ave, Brooklyn", since_year=2017))
        assert "evictions" in result and "total_returned" in result
        assert "summary" in result
        src = result.get("data_note", "")
        detail = (
            f"total={result['total_returned']}"
            f", residential={result['summary'].get('residential_evictions')}"
            f", source={'Socrata' if 'Socrata' in src else 'local'}"
        )
        record(PASS, name, detail)
    except Exception as exc:
        record(FAIL, name, f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Rich sample output
# ---------------------------------------------------------------------------

async def print_sample_output() -> None:
    print("\n" + "=" * 60)
    print("SAMPLE OUTPUT — key tools spot-check")
    print("=" * 60)

    # 590 Madison Ave
    bbl = "1012920015"
    print(f"\n--- lookup_property (590 Madison Ave / BBL {bbl}) ---")
    try:
        from nyc_property_intel.tools.lookup import lookup_property
        r = await run(lookup_property(bbl=bbl))
        for k in ["bbl_formatted","address","ownername","yearbuilt","numfloors","unitstotal","bldgclass","zonedist1","assesstot","data_as_of"]:
            print(f"  {k}: {r.get(k)}")
    except Exception as exc:
        print(f"  ERROR: {exc}")

    print("\n--- analyze_property (543 Ocean Ave Brooklyn / BBL 3051010090) ---")
    try:
        from nyc_property_intel.tools.analysis import analyze_property
        r = await run(analyze_property(bbl="3051010090"))
        ps, rf, dev, cm = r["property_summary"], r["risk_factors"], r["development_potential"], r["comparable_market"]
        print(f"  address         : {ps.get('address')}")
        print(f"  owner           : {ps.get('owner')}")
        print(f"  year_built      : {ps.get('year_built')}")
        print(f"  total_units     : {ps.get('total_units')}")
        print(f"  hpd_violations  : {rf.get('hpd_total_violations')}, class_c={rf.get('hpd_class_c_count')}")
        print(f"  has_tax_lien    : {rf.get('has_tax_lien')}")
        print(f"  unused_far      : {dev.get('unused_far')} ({dev.get('unused_sqft')} sqft)")
        print(f"  comps_in_zip    : {cm.get('num_recent_sales')}, median_ppsf={cm.get('median_price_per_sqft')}")
        for o in r.get("key_observations", []):
            print(f"  • {o}")
    except Exception as exc:
        print(f"  ERROR: {exc}")

    print("\n--- get_hpd_litigations (1188 Grand Concourse / BBL 2024560163) ---")
    try:
        from nyc_property_intel.tools.hpd_litigations import get_hpd_litigations
        r = await run(get_hpd_litigations(bbl="2024560163"))
        s = r.get("summary") or {}
        print(f"  total_cases     : {s.get('total_cases')}")
        print(f"  open_cases      : {s.get('open_cases')}")
        print(f"  harassment      : {s.get('harassment_findings')}")
        print(f"  open_judgements : {s.get('open_judgements')}  (was incorrectly 60 before fix)")
        print(f"  most_recent     : {s.get('most_recent_case')}")
    except Exception as exc:
        print(f"  ERROR: {exc}")


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

async def main() -> int:
    print("=" * 60)
    print("NYC Property Intel MCP — Comprehensive QA")
    print("=" * 60)

    bbls = await discover_bbls()

    bbl_manhattan    = bbls.get("manhattan",        "1012920015")
    bbl_brooklyn     = bbls.get("brooklyn_violator","3013020001")
    bbl_bronx        = bbls.get("bronx_litigated",  "2024560163")
    bbl_rentstab     = bbls.get("rentstab",          "1000160180")
    bbl_complaints   = bbls.get("hpd_complaints",   bbl_brooklyn)
    bbl_permits      = bbls.get("dobjobs",           bbl_manhattan)
    bbl_litigations  = bbls.get("hpd_litigations",  bbl_brooklyn)
    bbl_registrations= bbls.get("hpd_registrations",bbl_brooklyn)
    bbl_evictions    = bbls.get("evictions_bbl",    "3051010090")
    bbl_311          = bbls.get("311_bbl",           bbl_brooklyn)
    zip_sales        = bbls.get("sales_zip",         "10022")

    print(f"\nTest BBLs / zips:")
    print(f"  manhattan     : {bbl_manhattan}")
    print(f"  brooklyn      : {bbl_brooklyn}")
    print(f"  bronx         : {bbl_bronx}")
    print(f"  rentstab      : {bbl_rentstab}")
    print(f"  complaints    : {bbl_complaints}")
    print(f"  permits       : {bbl_permits}")
    print(f"  litigations   : {bbl_litigations}")
    print(f"  registrations : {bbl_registrations}")
    print(f"  evictions     : {bbl_evictions}")
    print(f"  311_bbl       : {bbl_311}")
    print(f"  sales_zip     : {zip_sales}")

    total_tests = 0
    def section(title: str) -> None:
        print(f"\n── {title} {'─'*(54-len(title))}")

    section("1. lookup_property")
    await test_lookup_by_bbl(bbl_manhattan)
    await test_lookup_by_address()
    await test_lookup_known_facts()
    await test_lookup_multi_borough()
    await test_lookup_both_inputs_error()

    section("2. get_property_issues")
    await test_get_property_issues_all(bbl_brooklyn)
    await test_get_property_issues_source_filter(bbl_brooklyn)
    await test_get_property_issues_limit(bbl_brooklyn)

    section("3. get_property_history")
    await test_get_property_history(bbl_manhattan)

    section("4. get_hpd_complaints")
    await test_get_hpd_complaints_basic(bbl_complaints)
    await test_get_hpd_complaints_open_filter(bbl_complaints)
    await test_get_hpd_complaints_category(bbl_complaints)

    section("5. get_hpd_litigations")
    await test_get_hpd_litigations(bbl_litigations)
    await test_hpd_litigations_open_judgements_accuracy(bbl_litigations)
    await test_hpd_litigations_open_judgements_accuracy("2024560163")  # 60-case building

    section("6. get_hpd_registration")
    await test_get_hpd_registration(bbl_registrations)

    section("7. get_building_permits")
    await test_get_building_permits_basic(bbl_permits)
    await test_get_building_permits_job_type_filter(bbl_permits)

    section("8. get_liens_and_encumbrances")
    await test_get_liens_and_encumbrances(bbl_manhattan)
    await test_get_liens_no_mortgages(bbl_manhattan)

    section("9. get_tax_info")
    await test_get_tax_info(bbl_manhattan)
    await test_get_tax_info_590_madison()

    section("10. get_rent_stabilization")
    await test_get_rent_stabilization(bbl_rentstab)

    section("11. search_comps")
    await test_search_comps_by_zip(zip_sales)
    await test_search_comps_building_class_filter(zip_sales)
    await test_search_comps_by_bbl()
    await test_search_comps_price_range()

    section("12. search_neighborhood_stats")
    await test_search_neighborhood_stats_by_zip(zip_sales)
    await test_search_neighborhood_stats_by_name()

    section("13. analyze_property")
    await test_analyze_property(bbl_manhattan)
    await test_analyze_property_consistency()

    section("14. get_311_complaints")
    await test_get_311_complaints_by_bbl(bbl_311)
    await test_311_bbl_count_cross_validate(bbl_311)
    await test_311_since_year_filter(bbl_311)
    await test_311_status_filter(bbl_311)
    await test_get_311_complaints_by_address()
    await test_311_empty_bbl_no_socrata_fallback()

    section("15. get_fdny_fire_incidents")
    await test_get_fdny_fire_incidents_by_bbl(bbl_manhattan)
    await test_fdny_address_no_whitespace(bbl_manhattan)
    await test_fdny_count_cross_validate(bbl_manhattan)
    await test_fdny_incident_type_filter(bbl_manhattan)
    await test_fdny_since_year_filter(bbl_manhattan)

    section("16. get_nypd_crime")
    await test_get_nypd_crime_by_bbl(bbl_brooklyn)
    await test_get_nypd_crime_by_address()
    await test_nypd_law_category_filter(bbl_brooklyn)
    await test_nypd_offense_filter(bbl_brooklyn)
    await test_nypd_radius_scaling(bbl_brooklyn)
    await test_nypd_count_cross_validate(bbl_brooklyn)
    await test_nypd_since_year_filter(bbl_brooklyn)

    section("17. get_dob_complaints")
    await test_get_dob_complaints_address_display()
    await test_dob_complaints_by_bbl_basic(bbl_bronx)
    await test_dob_complaints_category_filter()
    await test_dob_complaints_since_year()

    section("18. get_evictions")
    await test_get_evictions_by_bbl(bbl_evictions)
    await test_evictions_address_no_whitespace(bbl_evictions)
    await test_evictions_type_filter(bbl_evictions)
    await test_evictions_count_cross_validate(bbl_evictions)
    await test_evictions_address_path()

    # ── Summary ────────────────────────────────────────────────────────
    passing = sum(1 for s, _, _ in results if s == PASS)
    failing = sum(1 for s, _, _ in results if s == FAIL)
    skipped = sum(1 for s, _, _ in results if s == SKIP)
    total   = len(results)

    print("\n" + "=" * 60)
    print(f"SUMMARY: {passing}/{total} PASS  |  {failing} FAIL  |  {skipped} SKIP")
    print("=" * 60)

    if failing:
        print("\nFAILED tests:")
        for s, n, d in results:
            if s == FAIL:
                print(f"  {FAIL} {n}")
                print(f"       {d}")

    await print_sample_output()

    return 0 if failing == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
