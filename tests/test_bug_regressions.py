"""Regression tests for confirmed QA bugs — must stay green after fixes.

Each test corresponds to a bug found during the April 2026 MCP stress-test.

Run with:
    DATABASE_URL=postgresql://nycdb:nycdb@localhost:5432/nycdb \
    uv run pytest tests/test_bug_regressions.py -m integration -v

Tables required: hpd_violations, mv_violation_summary,
                 marshal_evictions_all, nyc_311_complaints
"""

from __future__ import annotations

import pytest
from mcp.server.fastmcp.exceptions import ToolError

# Queens rent-stabilized building — has many HPD open violations and evictions.
BBL_QUEENS = "4012900001"


# =============================================================================
# Bug 3 — get_property_issues: empty arrays despite non-zero summary counts
# Root cause: exact-case match on currentstatus (e.g. 'Open' ≠ 'OPEN')
# Fix: upper(currentstatus) = upper($3) + normalize_filter() on inputs
# =============================================================================

@pytest.mark.integration
class TestBug3PropertyIssuesCaseInsensitiveStatus:
    """All case variants of status='Open' must return the same non-empty rows."""

    async def _get_issues(self, status: str) -> dict:
        from nyc_property_intel.tools.issues import get_property_issues
        return await get_property_issues(bbl=BBL_QUEENS, source="HPD", status=status)

    async def test_status_uppercase_returns_rows(self):
        result = await self._get_issues("OPEN")
        assert result["total_returned"] > 0, (
            "status='OPEN' returned 0 rows — Bug 3 regression"
        )
        assert len(result["hpd_violations"]) > 0

    async def test_status_mixedcase_returns_same_rows(self):
        upper = await self._get_issues("OPEN")
        mixed = await self._get_issues("Open")
        lower = await self._get_issues("open")
        assert mixed["total_returned"] == upper["total_returned"], (
            f"status='Open' returned {mixed['total_returned']} rows but "
            f"'OPEN' returned {upper['total_returned']} — Bug 3 regression"
        )
        assert lower["total_returned"] == upper["total_returned"], (
            f"status='open' returned {lower['total_returned']} rows but "
            f"'OPEN' returned {upper['total_returned']} — Bug 3 regression"
        )

    async def test_summary_and_arrays_agree(self):
        result = await self._get_issues("Open")
        summary = result.get("summary") or {}
        hpd_open_in_summary = summary.get("hpd_open") or 0
        hpd_returned = len(result["hpd_violations"])
        # If summary says there are open violations, the array must not be empty.
        if hpd_open_in_summary > 0:
            assert hpd_returned > 0, (
                f"summary reports {hpd_open_in_summary} open HPD violations "
                f"but hpd_violations array is empty — Bug 3 regression"
            )

    async def test_no_source_filter_returns_all_sources(self):
        from nyc_property_intel.tools.issues import get_property_issues
        result = await get_property_issues(bbl=BBL_QUEENS)
        assert "hpd_violations" in result
        assert "dob_violations" in result
        assert "ecb_violations" in result


# =============================================================================
# Bug 2 — get_evictions: DB error with BBL + eviction_type + since_year
# Root cause: executeddate::date type mismatch when column stored as text;
#             normalize_filter() now uppercases eviction_type
# Fix: executeddate::text >= $N (text comparison, safe for date or text columns)
# =============================================================================

@pytest.mark.integration
class TestBug2EvictionsCombinedFilters:
    """BBL + eviction_type + since_year must not raise a DB error."""

    async def test_bbl_with_eviction_type_and_year_no_error(self):
        from nyc_property_intel.tools.evictions import get_evictions
        result = await get_evictions(
            bbl=BBL_QUEENS,
            eviction_type="Residential",
            since_year=2020,
        )
        assert isinstance(result, dict), "get_evictions raised instead of returning dict"
        assert "evictions" in result
        assert "total_returned" in result

    async def test_bbl_with_eviction_type_mixed_case(self):
        from nyc_property_intel.tools.evictions import get_evictions
        result_upper = await get_evictions(bbl=BBL_QUEENS, eviction_type="RESIDENTIAL")
        result_mixed = await get_evictions(bbl=BBL_QUEENS, eviction_type="Residential")
        result_lower = await get_evictions(bbl=BBL_QUEENS, eviction_type="residential")
        assert result_upper["total_returned"] == result_mixed["total_returned"], (
            "eviction_type case variant 'Residential' returned different count from 'RESIDENTIAL'"
        )
        assert result_lower["total_returned"] == result_mixed["total_returned"], (
            "eviction_type case variant 'residential' returned different count from 'Residential'"
        )

    async def test_bbl_only_still_works(self):
        from nyc_property_intel.tools.evictions import get_evictions
        result = await get_evictions(bbl=BBL_QUEENS)
        assert isinstance(result, dict)
        assert "evictions" in result

    async def test_commercial_eviction_type_no_error(self):
        from nyc_property_intel.tools.evictions import get_evictions
        result = await get_evictions(
            bbl=BBL_QUEENS,
            eviction_type="Commercial",
            since_year=2019,
        )
        assert isinstance(result, dict)

    async def test_since_year_alone_no_error(self):
        from nyc_property_intel.tools.evictions import get_evictions
        result = await get_evictions(bbl=BBL_QUEENS, since_year=2021)
        assert isinstance(result, dict)
        assert "evictions" in result


# =============================================================================
# Bug 1 — get_311_complaints: silent failure on address + complaint_type
# Root cause: unindexed LIKE scan on 10M+ rows → timeout → swallowed error
# Fix: resolve address to BBL first (fast indexed path), improve error message
# =============================================================================

@pytest.mark.integration
class TestBug1ComplaintsSilentFailure:
    """Address + complaint_type queries must not return empty error strings."""

    async def test_bbl_path_with_complaint_type_works(self):
        from nyc_property_intel.tools.complaints_311 import get_311_complaints
        result = await get_311_complaints(
            bbl=BBL_QUEENS,
            complaint_type="NOISE",
        )
        assert isinstance(result, dict)
        assert "complaints" in result
        assert "total_returned" in result

    async def test_address_plus_complaint_type_no_silent_error(self):
        """Must return a dict or raise a descriptive ToolError — never a silent empty error."""
        from nyc_property_intel.tools.complaints_311 import get_311_complaints
        try:
            result = await get_311_complaints(
                address="37-06 80th Street, Queens",
                complaint_type="NOISE",
            )
            assert isinstance(result, dict), "Expected dict response"
            assert "complaints" in result
            assert "total_returned" in result
        except ToolError as exc:
            # A ToolError is acceptable — but must have a non-empty, actionable message.
            assert str(exc).strip(), (
                "get_311_complaints raised ToolError with empty message — Bug 1 silent failure"
            )
            assert len(str(exc)) > 10, (
                f"ToolError message too short to be actionable: {exc!r}"
            )

    async def test_bbl_path_no_filter_works(self):
        from nyc_property_intel.tools.complaints_311 import get_311_complaints
        result = await get_311_complaints(bbl=BBL_QUEENS)
        assert isinstance(result, dict)
        assert "complaints" in result

    async def test_bbl_with_since_year_filter_works(self):
        from nyc_property_intel.tools.complaints_311 import get_311_complaints
        result = await get_311_complaints(bbl=BBL_QUEENS, since_year=2022)
        assert isinstance(result, dict)
        assert "total_returned" in result


# =============================================================================
# normalize_filter utility — used by all three bugs
# =============================================================================

class TestNormalizeFilter:
    """normalize_filter must uppercase, strip, and treat empty as None."""

    def test_uppercase(self):
        from nyc_property_intel.utils import normalize_filter
        assert normalize_filter("open") == "OPEN"
        assert normalize_filter("Open") == "OPEN"
        assert normalize_filter("OPEN") == "OPEN"

    def test_strips_whitespace(self):
        from nyc_property_intel.utils import normalize_filter
        assert normalize_filter("  Open  ") == "OPEN"

    def test_none_passthrough(self):
        from nyc_property_intel.utils import normalize_filter
        assert normalize_filter(None) is None

    def test_empty_string_becomes_none(self):
        from nyc_property_intel.utils import normalize_filter
        assert normalize_filter("") is None
        assert normalize_filter("   ") is None
