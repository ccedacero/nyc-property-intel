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


# =============================================================================
# Bug 4 — GeoClient BBL normalization
# Root cause: GeoClient returns BBLs with hyphens or fewer than 10 digits,
#             causing the len(bbl) == 10 guard to drop the result silently.
# Fix: normalize_geoclient_bbl() strips hyphens and zero-pads to 10 digits.
# =============================================================================

class TestNormalizeGeoClientBbl:
    """normalize_geoclient_bbl must produce a 10-digit string from any valid input."""

    def _normalize(self, raw: str):
        from nyc_property_intel.geoclient import normalize_geoclient_bbl
        return normalize_geoclient_bbl(raw)

    def test_hyphenated_bbl_normalized(self):
        assert self._normalize("1-00835-0001") == "1008350001"

    def test_already_10_digits_passthrough(self):
        assert self._normalize("1008350001") == "1008350001"

    def test_short_bbl_zero_padded(self):
        # GeoClient may omit leading zeros on block/lot portions.
        assert self._normalize("100835001") == "0100835001"

    def test_all_zeros_edge_case(self):
        assert self._normalize("0000000000") == "0000000000"

    def test_non_digit_after_strip_returns_none(self):
        assert self._normalize("1-ABC-0001") is None

    def test_empty_string_returns_none(self):
        assert self._normalize("") is None

    def test_too_long_returns_none(self):
        assert self._normalize("12345678901") is None  # 11 digits

    def test_whitespace_stripped(self):
        assert self._normalize("  1008350001  ") == "1008350001"


# =============================================================================
# PLUTO data-gap error message — lookup_property
# When a valid BBL is not in PLUTO, the error must explain the data gap and
# direct users to other tools instead of saying "BBL may not exist."
# =============================================================================

class TestLookupPropertyPlutoDataGapMessage:
    """lookup_property raises a descriptive ToolError for BBLs absent from PLUTO."""

    async def test_missing_bbl_mentions_pluto_gap(self):
        from unittest.mock import AsyncMock, patch
        from mcp.server.fastmcp.exceptions import ToolError
        from nyc_property_intel.tools.lookup import lookup_property

        with patch("nyc_property_intel.tools.lookup.fetch_one", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = None  # both primary and fallback miss
            with pytest.raises(ToolError) as exc_info:
                await lookup_property(bbl="1008350001")

        msg = str(exc_info.value)
        assert "PLUTO" in msg, "Error must mention PLUTO dataset"
        assert "data gap" in msg.lower() or "known" in msg.lower(), (
            "Error must characterize the issue as a known data gap"
        )
        assert "other" in msg.lower() or "tool" in msg.lower(), (
            "Error must direct users to other tools"
        )

# =============================================================================
# Bug 5 — PAD fallback fails for Queens hyphenated house numbers
# Root cause 1: normalize_street_name("80th St") → "Eightieth Street" was
#               passed to PAD, which stores numeric ordinals ("80 STREET").
# Root cause 2: hyphen stripped from house number ("37-06" → "3706") before
#               range comparison. Text comparison "37-06" >= "3706" is False
#               because '-' (ASCII 45) < '0' (ASCII 48), so hhnd >= clause fails.
# Fix: _pad_street_name() strips ordinal suffixes for PAD; range query uses
#      original hyphenated form; resolve_address_to_bbl passes raw street to PAD.
# =============================================================================

class TestPadStreetName:
    """_pad_street_name strips ordinal suffixes and expands abbreviations for PAD."""

    def _pad(self, s: str) -> str:
        from nyc_property_intel.geoclient import _pad_street_name
        return _pad_street_name(s)

    def test_ordinal_th_stripped(self):
        assert self._pad("80th Street") == "80 STREET"

    def test_ordinal_st_stripped(self):
        assert self._pad("1st Avenue") == "1 AVENUE"

    def test_ordinal_nd_stripped(self):
        assert self._pad("2nd Street") == "2 STREET"

    def test_ordinal_rd_stripped(self):
        assert self._pad("3rd Place") == "3 PLACE"

    def test_abbreviation_expanded(self):
        assert self._pad("80th St") == "80 STREET"
        assert self._pad("5th Ave") == "5 AVENUE"

    def test_already_numeric_passthrough(self):
        assert self._pad("80 STREET") == "80 STREET"

    def test_spelled_out_unchanged(self):
        # Spelled-out names that GeoClient accepted pass through intact
        result = self._pad("Eightieth Street")
        assert "80" not in result  # cannot reverse-map to a number


@pytest.mark.integration
class TestQueensHyphenatedAddressPADFallback:
    """37-06 80th St (Queens) must resolve to BBL 4012900001 via PAD fallback.

    GeoClient is disabled in these tests (mocked to raise) so we exercise the
    PAD code path in isolation.
    """

    @pytest.fixture(autouse=True)
    def disable_geoclient(self, monkeypatch):
        from mcp.server.fastmcp.exceptions import ToolError
        import nyc_property_intel.geoclient as gc
        async def _fail(*a, **kw):
            raise ToolError("GeoClient disabled in test")
        monkeypatch.setattr(gc, "_call_geoclient", _fail)
        # Clear address cache so each test runs the full resolution path.
        gc._address_cache.clear()

    async def test_queens_hyphenated_resolves_to_correct_bbl(self):
        from nyc_property_intel.geoclient import resolve_address_to_bbl
        bbl = await resolve_address_to_bbl("37-06 80th St, Queens")
        assert bbl == "4012900001", (
            f"Expected BBL 4012900001 for '37-06 80th St, Queens', got {bbl!r}"
        )

    async def test_queens_hyphenated_full_address(self):
        from nyc_property_intel.geoclient import resolve_address_to_bbl
        bbl = await resolve_address_to_bbl("37-06 80th Street, Queens, NY")
        assert bbl == "4012900001"

    async def test_queens_hyphenated_lookup_property(self):
        from nyc_property_intel.tools.lookup import lookup_property
        result = await lookup_property(address="37-06 80th St", borough="queens")
        assert result["bbl"] == "4012900001"
        assert result["address"] == "37-06 80 STREET"


    async def test_missing_bbl_message_contains_bbl_formatted(self):
        from unittest.mock import AsyncMock, patch
        from mcp.server.fastmcp.exceptions import ToolError
        from nyc_property_intel.tools.lookup import lookup_property

        with patch("nyc_property_intel.tools.lookup.fetch_one", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = None
            with pytest.raises(ToolError) as exc_info:
                await lookup_property(bbl="1008350001")

        msg = str(exc_info.value)
        # BBL should appear in display format (borough-block-lot) or raw.
        assert "1008350001" in msg or "1-00835-0001" in msg, (
            "Error must include the BBL so the user knows which property failed"
        )
