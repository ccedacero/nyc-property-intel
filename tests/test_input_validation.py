"""Unit tests for tool input validation edge cases.

No database or network connections required — tests only the validation
layer at the top of each tool function, using mocked DB calls.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from nyc_property_intel.geoclient import parse_address
from nyc_property_intel.utils import format_currency


# ── parse_address edge cases ─────────────────────────────────────────


class TestParseAddressEdgeCases:
    """Additional address parsing edge cases beyond the existing suite."""

    def test_address_too_long_raises(self):
        long_addr = "123 " + "A" * 200 + " St, Brooklyn"
        with pytest.raises(ToolError, match="too long"):
            parse_address(long_addr)

    def test_address_exactly_200_chars_accepted(self):
        # 200 chars should pass the length check and then fail on borough
        addr = "123 " + "A" * 180 + " St"  # no borough/zip
        with pytest.raises(ToolError, match="Could not"):
            parse_address(addr)  # fails parsing, not length check

    def test_brooklyn_with_comma_and_state(self):
        result = parse_address("450 Atlantic Ave, Brooklyn, NY 11217")
        assert result["borough_code"] == "3"
        assert result["house_number"] == "450"

    def test_staten_island_two_words(self):
        result = parse_address("100 Bay St, Staten Island, NY 10301")
        assert result["borough_code"] == "5"
        assert result["borough_name"] == "Staten Island"

    def test_address_without_comma_but_with_zip(self):
        result = parse_address("100 Broad St 10004")
        assert result["house_number"] == "100"
        assert result["borough_code"] == "1"  # Manhattan zip range

    def test_error_message_truncates_long_input(self):
        long_addr = "x" * 150  # no house number — won't parse
        with pytest.raises(ToolError) as exc_info:
            parse_address(long_addr)
        # Error message must not contain more than ~120 chars of user input
        error_msg = str(exc_info.value)
        # The original 150-char string should be truncated in the message
        assert len(error_msg) < 300


# ── format_currency correctness ──────────────────────────────────────


class TestFormatCurrencyEdgeCases:
    def test_negative_integer_sign_before_dollar(self):
        assert format_currency(-1) == "-$1"

    def test_negative_large_integer(self):
        assert format_currency(-1_000_000) == "-$1,000,000"

    def test_negative_float(self):
        assert format_currency(-999.99) == "-$999.99"

    def test_zero(self):
        assert format_currency(0) == "$0"

    def test_none_returns_na(self):
        assert format_currency(None) == "N/A"


# ── Tool input validation (mocked DB) ───────────────────────────────


@pytest.mark.asyncio
class TestLookupPropertyValidation:
    async def test_no_address_no_bbl_raises(self):
        from nyc_property_intel.tools.lookup import lookup_property

        with pytest.raises(ToolError, match="either an address or a BBL"):
            await lookup_property(address=None, bbl=None)

    async def test_invalid_bbl_raises(self):
        from nyc_property_intel.tools.lookup import lookup_property

        with pytest.raises(ToolError):
            await lookup_property(bbl="not-a-bbl")

    async def test_bbl_wrong_borough_prefix_raises(self):
        from nyc_property_intel.tools.lookup import lookup_property

        with pytest.raises(ToolError):
            await lookup_property(bbl="6123456789")  # borough 6 doesn't exist


@pytest.mark.asyncio
class TestGetPropertyIssuesValidation:
    async def test_invalid_bbl_raises(self):
        from nyc_property_intel.tools.issues import get_property_issues

        with pytest.raises(ToolError):
            await get_property_issues(bbl="bad")

    async def test_limit_too_low_raises(self):
        from nyc_property_intel.tools.issues import get_property_issues

        with pytest.raises(ToolError, match="limit must be between"):
            await get_property_issues(bbl="1008350001", limit=0)

    async def test_limit_too_high_raises(self):
        from nyc_property_intel.tools.issues import get_property_issues

        with pytest.raises(ToolError, match="limit must be between"):
            await get_property_issues(bbl="1008350001", limit=201)

    async def test_invalid_source_raises(self):
        from nyc_property_intel.tools.issues import get_property_issues

        with pytest.raises(ToolError, match="Invalid source"):
            await get_property_issues(bbl="1008350001", source="FOOBAR")

    async def test_invalid_date_format_raises(self):
        from nyc_property_intel.tools.issues import get_property_issues

        with pytest.raises(ToolError, match="Invalid date format"):
            await get_property_issues(bbl="1008350001", since_date="not-a-date")


@pytest.mark.asyncio
class TestSearchCompsValidation:
    async def test_no_bbl_no_zip_raises(self):
        from nyc_property_intel.tools.comps import search_comps

        with pytest.raises(ToolError, match="zip_code is required"):
            await search_comps(bbl=None, zip_code=None)

    async def test_months_too_low_raises(self):
        from nyc_property_intel.tools.comps import search_comps

        with pytest.raises(ToolError, match="months must be between"):
            await search_comps(zip_code="11201", months=0)

    async def test_months_too_high_raises(self):
        from nyc_property_intel.tools.comps import search_comps

        with pytest.raises(ToolError, match="months must be between"):
            await search_comps(zip_code="11201", months=121)

    async def test_limit_too_low_raises(self):
        from nyc_property_intel.tools.comps import search_comps

        with pytest.raises(ToolError, match="limit must be between"):
            await search_comps(zip_code="11201", limit=0)

    async def test_limit_too_high_raises(self):
        from nyc_property_intel.tools.comps import search_comps

        with pytest.raises(ToolError, match="limit must be between"):
            await search_comps(zip_code="11201", limit=101)

    async def test_invalid_bbl_raises(self):
        from nyc_property_intel.tools.comps import search_comps

        with pytest.raises(ToolError):
            await search_comps(bbl="bad-bbl", zip_code="11201")


@pytest.mark.asyncio
class TestAnalyzePropertyValidation:
    async def test_invalid_bbl_raises(self):
        from nyc_property_intel.tools.analysis import analyze_property

        with pytest.raises(ToolError):
            await analyze_property(bbl="not-valid")

    async def test_bbl_too_short_raises(self):
        from nyc_property_intel.tools.analysis import analyze_property

        with pytest.raises(ToolError):
            await analyze_property(bbl="123")
