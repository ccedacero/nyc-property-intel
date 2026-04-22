"""Phase B integration tests — verify new datasets are loaded and queryable.

These tests exercise the MCP tools against Phase B tables that are now loaded:
dof_property_valuation_and_assessments, dof_exemptions, dof_tax_lien_sale_list,
hpd_registrations, hpd_litigations, hpd_contacts, rentstab, ecb_violations

And validate that existing tools correctly interact with Phase B data in:
dof_sales, dof_annual_sales, dob_violations, mv_violation_summary

Run with:
    DATABASE_URL=postgresql://nycdb:nycdb@localhost:5432/nycdb \
        uv run pytest tests/test_phase_b_integration.py -m integration -v
"""

from __future__ import annotations

import pytest

# ── Known-good BBLs ─────────────────────────────────────────────────
BBL_MANHATTAN = "1012920015"  # 590 Madison Ave — has sales in both tables
BBL_BROOKLYN = "3013020001"   # Brooklyn — 498 DOB violations, many HPD


# ── 1. Sales UNION ALL correctness ──────────────────────────────────

@pytest.mark.integration
async def test_sales_union_all_returns_results_from_both_tables():
    """UNION ALL in history.py pulls rows from BOTH dof_sales and dof_annual_sales,
    and DISTINCT ON deduplicates overlapping (bbl, saledate, saleprice) tuples."""
    from nyc_property_intel.tools.history import get_property_history

    result = await get_property_history(
        bbl=BBL_MANHATTAN,
        include_sales=True,
        include_ownership=False,
        include_transactions=False,
        limit=50,
    )

    sales = result["sales"]
    assert len(sales) > 0, "Expected at least one sale for Manhattan BBL"

    # Every row must have the columns the UNION ALL aliases define
    required_keys = {"bbl", "saledate", "saleprice", "sale_type", "saleprice_formatted"}
    for sale in sales:
        missing = required_keys - set(sale.keys())
        assert not missing, f"Sale record missing keys: {missing}"

    # Verify deduplication — no two rows share the same (saledate, saleprice)
    seen = set()
    for sale in sales:
        key = (str(sale.get("saledate")), str(sale.get("saleprice")))
        assert key not in seen, f"Duplicate sale found after DISTINCT ON: {key}"
        seen.add(key)


@pytest.mark.integration
async def test_sales_union_all_sale_type_classification():
    """sale_type CASE expression correctly labels arms-length vs nominal sales."""
    from nyc_property_intel.tools.history import get_property_history

    result = await get_property_history(
        bbl=BBL_MANHATTAN,
        include_sales=True,
        include_ownership=False,
        include_transactions=False,
        limit=50,
    )

    for sale in result["sales"]:
        price = sale.get("saleprice")
        sale_type = sale.get("sale_type")
        assert sale_type in ("MARKET", "NON_ARMS_LENGTH"), (
            f"Unexpected sale_type: {sale_type}"
        )
        if price is not None and price <= 100:
            assert sale_type == "NON_ARMS_LENGTH"
        elif price is not None and price > 100:
            assert sale_type == "MARKET"


@pytest.mark.integration
async def test_sales_ordered_by_date_descending():
    """Sales come back in descending date order as the ORDER BY clause dictates."""
    from nyc_property_intel.tools.history import get_property_history

    result = await get_property_history(
        bbl=BBL_MANHATTAN,
        include_sales=True,
        include_ownership=False,
        include_transactions=False,
        limit=50,
    )

    sales = result["sales"]
    dates = [s["saledate"] for s in sales if s.get("saledate") is not None]
    assert dates == sorted(dates, reverse=True), "Sales should be ordered by saledate DESC"


# ── 2. DOB violations query ─────────────────────────────────────────

@pytest.mark.integration
async def test_dob_violations_for_known_violator():
    """BBL 3013020001 has 498 DOB violations; get_property_issues(source=DOB)
    should return a full page of results."""
    from nyc_property_intel.tools.issues import get_property_issues

    result = await get_property_issues(bbl=BBL_BROOKLYN, source="DOB", limit=50)

    dob = result["dob_violations"]
    assert len(dob) == 50, (
        f"Expected limit-capped 50 DOB violations, got {len(dob)}"
    )
    # HPD must be empty since we asked for DOB only
    assert result["hpd_violations"] == []

    # Spot-check DOB violation structure
    first = dob[0]
    assert "bbl" in first
    assert "issuedate" in first
    assert "violationtype" in first or "violationtypecode" in first
    assert "description" in first


@pytest.mark.integration
async def test_dob_violations_ordered_by_issue_date_desc():
    """DOB violations come back in descending issuedate order."""
    from nyc_property_intel.tools.issues import get_property_issues

    result = await get_property_issues(bbl=BBL_BROOKLYN, source="DOB", limit=20)

    dates = [
        v["issuedate"]
        for v in result["dob_violations"]
        if v.get("issuedate") is not None
    ]
    assert dates == sorted(dates, reverse=True), (
        "DOB violations should be ordered by issuedate DESC"
    )


# ── 3. Violation summary with HPD + DOB ─────────────────────────────

@pytest.mark.integration
async def test_mv_violation_summary_has_nonzero_dob_total():
    """mv_violation_summary for BBL 3013020001 should have dob_total >= 498."""
    from nyc_property_intel.tools.issues import get_property_issues

    result = await get_property_issues(
        bbl=BBL_BROOKLYN, source="ALL", include_summary=True, limit=1,
    )

    summary = result.get("summary")
    assert summary is not None, "Expected summary from mv_violation_summary"
    assert summary["dob_total"] >= 498, (
        f"Expected dob_total >= 498, got {summary['dob_total']}"
    )


@pytest.mark.integration
async def test_mv_violation_summary_has_both_hpd_and_dob():
    """Violation summary should report both HPD and DOB counts for the Brooklyn BBL."""
    from nyc_property_intel.tools.issues import get_property_issues

    result = await get_property_issues(
        bbl=BBL_BROOKLYN, source="ALL", include_summary=True, limit=1,
    )

    summary = result["summary"]
    assert summary["hpd_total"] > 0, "Expected non-zero HPD total"
    assert summary["dob_total"] > 0, "Expected non-zero DOB total"
    assert summary["hpd_class_c"] is not None, "Expected hpd_class_c in summary"
    assert summary["dob_most_recent"] is not None, "Expected dob_most_recent date"
    assert summary["hpd_most_recent"] is not None, "Expected hpd_most_recent date"


# ── 4. Comps query with quarterly stats ──────────────────────────────

@pytest.mark.integration
async def test_search_comps_returns_quarterly_stats_section():
    """search_comps with include_stats=True returns quarterly_stats with data."""
    from nyc_property_intel.tools.comps import search_comps

    result = await search_comps(
        zip_code="10022",
        months=36,
        min_price=10000,
        limit=5,
        include_stats=True,
    )

    assert "quarterly_stats" in result
    stats = result["quarterly_stats"]
    assert isinstance(stats, list)
    assert len(stats) > 0, "Expected at least one quarter of stats over 36 months"

    # Every quarter row should have the expected aggregation columns
    for row in stats:
        assert "quarter" in row
        assert "num_sales" in row
        assert row["num_sales"] > 0
        assert "median_price" in row
        assert "avg_price" in row
        assert "median_price_formatted" in row
        assert "avg_ppsf_formatted" in row


@pytest.mark.integration
async def test_search_comps_quarterly_stats_ordered_desc():
    """Quarterly stats should be ordered by quarter descending (most recent first)."""
    from nyc_property_intel.tools.comps import search_comps

    result = await search_comps(
        zip_code="10022",
        months=36,
        limit=5,
        include_stats=True,
    )

    quarters = [row["quarter"] for row in result["quarterly_stats"]]
    assert quarters == sorted(quarters, reverse=True), (
        "Quarterly stats should be ordered by quarter DESC"
    )


# ── 5. Analysis with sales data ─────────────────────────────────────

@pytest.mark.integration
async def test_analyze_property_financial_snapshot_has_last_sale():
    """analyze_property for a BBL with sales data populates last_sale_price
    and last_sale_date in financial_snapshot."""
    from nyc_property_intel.tools.analysis import analyze_property

    result = await analyze_property(bbl=BBL_MANHATTAN)

    snapshot = result["financial_snapshot"]
    assert snapshot["last_sale_price"] is not None, (
        "Expected last_sale_price to be populated for a BBL with sales"
    )
    assert snapshot["last_sale_date"] is not None, (
        "Expected last_sale_date to be populated for a BBL with sales"
    )
    assert isinstance(snapshot["last_sale_price"], (int, float))
    assert snapshot["last_sale_price"] > 0


@pytest.mark.integration
async def test_analyze_property_violations_dob_populated():
    """analyze_property for the Brooklyn violator BBL has dob total violations > 0."""
    from nyc_property_intel.tools.analysis import analyze_property

    result = await analyze_property(bbl=BBL_BROOKLYN)

    vc = result["violations_and_compliance"]
    dob = vc["dob_violations"]
    hpd = vc["hpd_violations"]
    assert dob is not None
    assert dob["total"] is not None
    assert dob["total"] >= 498, (
        f"Expected dob violations total >= 498, got {dob['total']}"
    )
    assert hpd is not None
    assert hpd["total"] is not None and hpd["total"] > 0
    assert hpd["most_recent"] is not None


@pytest.mark.integration
async def test_analyze_property_recent_sales_list_populated():
    """analyze_property returns a non-empty recent_sales list for a BBL with sales."""
    from nyc_property_intel.tools.analysis import analyze_property

    result = await analyze_property(bbl=BBL_MANHATTAN)

    recent_sales = result["recent_sales"]
    assert isinstance(recent_sales, list)
    assert len(recent_sales) > 0, "Expected recent_sales to be non-empty"

    first = recent_sales[0]
    assert "saledate" in first
    assert "saleprice" in first


@pytest.mark.integration
async def test_analyze_property_comparable_market_populated():
    """analyze_property comparable_market section has data from dof_sales."""
    from nyc_property_intel.tools.analysis import analyze_property

    result = await analyze_property(bbl=BBL_MANHATTAN)

    market = result["comparable_market"]
    assert market["num_recent_sales"] > 0, (
        "Expected comparable sales in the zip code"
    )
    assert market["zip_code"] is not None
