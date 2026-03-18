"""Integration tests for NYC Property Intel MCP tools.

Run against a live local PostgreSQL database with nycdb schema loaded.
These tests verify that actual SQL queries work against the real tables
and materialized views.

Run with:
    DATABASE_URL=postgresql://nycdb:nycdb@localhost:5432/nycdb uv run pytest tests/test_tools_integration.py -m integration -v

Tables required: pluto_latest, pad_adr, hpd_violations, dof_sales,
                 dof_annual_sales, dob_violations
Materialized views: mv_property_profile, mv_violation_summary
"""

from __future__ import annotations

import pytest

# ── Known-good BBLs in the loaded data ────────────────────────────────
BBL_MANHATTAN = "1012920015"  # 590 Madison Ave — has sales data
BBL_BROOKLYN = "3013020001"   # Brooklyn — has many HPD + DOB violations


# ── lookup_property ───────────────────────────────────────────────────

@pytest.mark.integration
async def test_lookup_property_by_bbl():
    """lookup_property returns a property dict with essential fields when given a valid BBL."""
    from nyc_property_intel.tools.lookup import lookup_property

    result = await lookup_property(bbl=BBL_MANHATTAN)

    assert isinstance(result, dict)
    assert result["bbl"] == BBL_MANHATTAN or str(result["bbl"]) == BBL_MANHATTAN
    assert result.get("address") is not None
    assert result.get("bldgclass") is not None
    assert result.get("yearbuilt") is not None
    assert "bbl_formatted" in result
    assert "data_as_of" in result


@pytest.mark.integration
async def test_lookup_property_not_found():
    """lookup_property raises ToolError for a BBL that doesn't exist."""
    from mcp.server.fastmcp.exceptions import ToolError
    from nyc_property_intel.tools.lookup import lookup_property

    with pytest.raises(ToolError, match="No property found"):
        await lookup_property(bbl="1000000000")


# ── get_property_issues ──────────────────────────────────────────────

@pytest.mark.integration
async def test_get_property_issues_hpd_violations():
    """get_property_issues returns HPD violations for a known violator BBL."""
    from nyc_property_intel.tools.issues import get_property_issues

    result = await get_property_issues(bbl=BBL_BROOKLYN, source="HPD", limit=10)

    assert isinstance(result, dict)
    assert result["bbl"] == BBL_BROOKLYN
    assert isinstance(result["hpd_violations"], list)
    assert len(result["hpd_violations"]) > 0, "Expected HPD violations for this BBL"
    # DOB should be empty since we asked for HPD only
    assert result["dob_violations"] == []

    # Spot-check HPD violation structure
    first = result["hpd_violations"][0]
    assert "violationid" in first
    assert "class" in first
    assert "inspectiondate" in first


@pytest.mark.integration
async def test_get_property_issues_dob_violations():
    """get_property_issues returns DOB violations when source=DOB."""
    from nyc_property_intel.tools.issues import get_property_issues

    result = await get_property_issues(bbl=BBL_BROOKLYN, source="DOB", limit=10)

    assert isinstance(result, dict)
    assert isinstance(result["dob_violations"], list)
    assert len(result["dob_violations"]) > 0, "Expected DOB violations for this BBL"
    # HPD should be empty since we asked for DOB only
    assert result["hpd_violations"] == []


@pytest.mark.integration
async def test_get_property_issues_summary_includes_both_counts():
    """get_property_issues summary has both HPD and DOB total counts."""
    from nyc_property_intel.tools.issues import get_property_issues

    result = await get_property_issues(
        bbl=BBL_BROOKLYN, source="ALL", include_summary=True, limit=5
    )

    summary = result.get("summary")
    assert summary is not None, "Expected a summary from mv_violation_summary"
    assert "hpd_total" in summary
    assert "dob_total" in summary
    assert isinstance(summary["hpd_total"], (int, float))
    assert isinstance(summary["dob_total"], (int, float))


# ── get_property_history ─────────────────────────────────────────────

@pytest.mark.integration
async def test_get_property_history_returns_sales():
    """get_property_history returns sales for a BBL with known sales."""
    from nyc_property_intel.tools.history import get_property_history

    result = await get_property_history(
        bbl=BBL_MANHATTAN,
        include_sales=True,
        include_ownership=False,
        include_transactions=False,
        limit=10,
    )

    assert isinstance(result, dict)
    assert result["bbl"] == BBL_MANHATTAN
    assert "sales" in result
    assert isinstance(result["sales"], list)
    assert len(result["sales"]) > 0, "Expected sales records for this BBL"

    # Spot-check sale record structure
    first = result["sales"][0]
    assert "saledate" in first
    assert "saleprice" in first
    assert "saleprice_formatted" in first
    assert "sale_type" in first  # MARKET or NON_ARMS_LENGTH from CASE expression


@pytest.mark.integration
async def test_get_property_history_union_all_deduplication():
    """get_property_history UNION ALL combines dof_sales + dof_annual_sales with dedup."""
    from nyc_property_intel.tools.history import get_property_history

    result = await get_property_history(
        bbl=BBL_MANHATTAN,
        include_sales=True,
        include_ownership=False,
        include_transactions=False,
        limit=50,
    )

    sales = result["sales"]
    # Verify dedup: no two sales should share the exact same (saledate, saleprice)
    seen = set()
    for sale in sales:
        key = (sale.get("saledate"), sale.get("saleprice"))
        assert key not in seen, f"Duplicate sale found: {key}"
        seen.add(key)


# ── search_comps ──────────────────────────────────────────────────────

@pytest.mark.integration
async def test_search_comps_by_zip_code():
    """search_comps returns comparable sales for a Manhattan zip code."""
    from nyc_property_intel.tools.comps import search_comps

    result = await search_comps(
        zip_code="10022",  # Midtown East — 590 Madison Ave area
        months=36,         # Wide window to ensure results
        min_price=10000,
        limit=10,
        include_stats=False,
    )

    assert isinstance(result, dict)
    assert "comps" in result
    assert isinstance(result["comps"], list)
    assert result["num_comps_found"] > 0, "Expected comps in this zip code"

    # Spot-check comp record
    first = result["comps"][0]
    assert "bbl" in first
    assert "saleprice" in first
    assert "saleprice_formatted" in first
    assert "price_per_sqft_formatted" in first
    assert "saledate" in first


@pytest.mark.integration
async def test_search_comps_with_stats():
    """search_comps includes quarterly market statistics when requested."""
    from nyc_property_intel.tools.comps import search_comps

    result = await search_comps(
        zip_code="10022",
        months=36,
        limit=5,
        include_stats=True,
    )

    assert "quarterly_stats" in result
    assert isinstance(result["quarterly_stats"], list)
    # With 36-month window, we should have at least one quarter of data
    assert len(result["quarterly_stats"]) > 0, "Expected quarterly stats"

    first_quarter = result["quarterly_stats"][0]
    assert "quarter" in first_quarter
    assert "num_sales" in first_quarter
    assert "median_price" in first_quarter


@pytest.mark.integration
async def test_search_comps_with_reference_bbl():
    """search_comps uses reference BBL to auto-fill zip code."""
    from nyc_property_intel.tools.comps import search_comps

    result = await search_comps(
        bbl=BBL_MANHATTAN,
        months=36,
        limit=5,
        include_stats=False,
    )

    assert isinstance(result, dict)
    assert "reference_property" in result
    assert result["reference_property"]["bbl"] == BBL_MANHATTAN
    assert result["reference_property"]["postcode"] is not None


# ── analyze_property ─────────────────────────────────────────────────

@pytest.mark.integration
async def test_analyze_property_returns_all_sections():
    """analyze_property returns a complete analysis with all expected sections."""
    from nyc_property_intel.tools.analysis import analyze_property

    result = await analyze_property(bbl=BBL_MANHATTAN)

    assert isinstance(result, dict)

    # All top-level sections must be present
    expected_sections = [
        "property_summary",
        "financial_snapshot",
        "development_potential",
        "risk_factors",
        "comparable_market",
        "recent_sales",
        "key_observations",
        "data_as_of",
        "disclaimer",
    ]
    for section in expected_sections:
        assert section in result, f"Missing section: {section}"


@pytest.mark.integration
async def test_analyze_property_summary_fields():
    """analyze_property property_summary has essential fields populated."""
    from nyc_property_intel.tools.analysis import analyze_property

    result = await analyze_property(bbl=BBL_MANHATTAN)
    summary = result["property_summary"]

    assert summary["bbl"] is not None
    assert summary["bbl_formatted"] is not None
    assert summary["address"] is not None
    assert summary["building_class"] is not None
    assert summary["year_built"] is not None
    assert summary["borough"] == "Manhattan"


@pytest.mark.integration
async def test_analyze_property_key_observations_is_list():
    """analyze_property key_observations is a list of strings."""
    from nyc_property_intel.tools.analysis import analyze_property

    result = await analyze_property(bbl=BBL_MANHATTAN)

    observations = result["key_observations"]
    assert isinstance(observations, list)
    for obs in observations:
        assert isinstance(obs, str)


@pytest.mark.integration
async def test_analyze_property_risk_factors_for_violator():
    """analyze_property shows violation counts for a property with known violations."""
    from nyc_property_intel.tools.analysis import analyze_property

    result = await analyze_property(bbl=BBL_BROOKLYN)

    risk = result["risk_factors"]
    # This BBL has many violations — counts should be non-None and > 0
    assert risk["hpd_total_violations"] is not None
    assert risk["hpd_total_violations"] > 0
    assert risk["dob_total_violations"] is not None
    assert risk["dob_total_violations"] > 0
