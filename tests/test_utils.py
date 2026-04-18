"""Unit tests for utils.py and geoclient.py pure-logic functions.

No database or network connections required.
"""

from __future__ import annotations

import pytest

from nyc_property_intel.utils import (
    BOROUGH_CODE_TO_NAME,
    BOROUGH_NAME_TO_CODE,
    borough_code_to_name,
    borough_name_to_code,
    format_currency,
    parse_bbl,
    validate_bbl,
)
from nyc_property_intel.geoclient import parse_address, normalize_street_name, _ZIP_TO_BOROUGH


# ── validate_bbl ─────────────────────────────────────────────────────


class TestValidateBbl:
    """Tests for validate_bbl."""

    def test_valid_manhattan(self, empire_state_bbl):
        borough, block, lot = validate_bbl(empire_state_bbl)
        assert borough == "1"
        assert block == "00835"
        assert lot == "0001"

    def test_valid_brooklyn(self, brooklyn_brownstone_bbl):
        borough, block, lot = validate_bbl(brooklyn_brownstone_bbl)
        assert borough == "3"
        assert block == "01162"
        assert lot == "0044"

    def test_valid_queens(self, queens_multifamily_bbl):
        borough, block, lot = validate_bbl(queens_multifamily_bbl)
        assert borough == "4"
        assert block == "00492"
        assert lot == "0033"

    def test_valid_bronx(self):
        borough, block, lot = validate_bbl("2023450010")
        assert borough == "2"
        assert block == "02345"
        assert lot == "0010"

    def test_valid_staten_island(self):
        borough, block, lot = validate_bbl("5001230056")
        assert borough == "5"
        assert block == "00123"
        assert lot == "0056"

    def test_strips_whitespace(self):
        borough, block, lot = validate_bbl("  1008350001  ")
        assert borough == "1"

    def test_strips_hyphens(self):
        borough, block, lot = validate_bbl("1-00835-0001")
        assert borough == "1"
        assert block == "00835"
        assert lot == "0001"

    def test_too_short_raises(self):
        with pytest.raises(ValueError, match="Invalid BBL"):
            validate_bbl("100835")

    def test_too_long_raises(self):
        with pytest.raises(ValueError, match="Invalid BBL"):
            validate_bbl("10083500011")

    def test_non_numeric_raises(self):
        with pytest.raises(ValueError, match="Invalid BBL"):
            validate_bbl("1008abcdef")

    def test_borough_zero_raises(self):
        with pytest.raises(ValueError, match="Invalid BBL"):
            validate_bbl("0008350001")

    def test_borough_six_raises(self):
        with pytest.raises(ValueError, match="Invalid BBL"):
            validate_bbl("6008350001")

    def test_borough_nine_raises(self):
        with pytest.raises(ValueError, match="Invalid BBL"):
            validate_bbl("9008350001")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="Invalid BBL"):
            validate_bbl("")

    def test_all_zeros_raises(self):
        with pytest.raises(ValueError, match="Invalid BBL"):
            validate_bbl("0000000000")


# ── parse_bbl ────────────────────────────────────────────────────────


class TestParseBbl:
    """Tests for parse_bbl."""

    def test_returns_dict_with_all_keys(self, empire_state_bbl):
        result = parse_bbl(empire_state_bbl)
        assert set(result.keys()) == {
            "borough", "block", "lot", "borough_name", "bbl_formatted",
        }

    def test_borough_name_populated(self, empire_state_bbl):
        result = parse_bbl(empire_state_bbl)
        assert result["borough_name"] == "Manhattan"

    def test_bbl_formatted(self, empire_state_bbl):
        result = parse_bbl(empire_state_bbl)
        assert result["bbl_formatted"] == "1-00835-0001"

    def test_brooklyn_borough_name(self, brooklyn_brownstone_bbl):
        result = parse_bbl(brooklyn_brownstone_bbl)
        assert result["borough_name"] == "Brooklyn"

    def test_queens_borough_name(self, queens_multifamily_bbl):
        result = parse_bbl(queens_multifamily_bbl)
        assert result["borough_name"] == "Queens"

    def test_invalid_bbl_raises(self):
        with pytest.raises(ValueError):
            parse_bbl("bad")


# ── BOROUGH_NAME_TO_CODE ─────────────────────────────────────────────


class TestBoroughNameToCode:
    """Tests for the BOROUGH_NAME_TO_CODE mapping and borough_name_to_code()."""

    @pytest.mark.parametrize("name,expected", [
        ("manhattan", "1"),
        ("new york", "1"),
        ("new york county", "1"),
        ("ny", "1"),
        ("mn", "1"),
        ("bronx", "2"),
        ("bronx county", "2"),
        ("bx", "2"),
        ("brooklyn", "3"),
        ("kings", "3"),
        ("kings county", "3"),
        ("bk", "3"),
        ("queens", "4"),
        ("queens county", "4"),
        ("qn", "4"),
        ("staten island", "5"),
        ("richmond", "5"),
        ("richmond county", "5"),
        ("si", "5"),
    ])
    def test_alias_maps_correctly(self, name, expected):
        assert BOROUGH_NAME_TO_CODE[name] == expected

    def test_function_is_case_insensitive(self):
        assert borough_name_to_code("BROOKLYN") == "3"
        assert borough_name_to_code("Brooklyn") == "3"
        assert borough_name_to_code("brooklyn") == "3"

    def test_function_strips_whitespace(self):
        assert borough_name_to_code("  Queens  ") == "4"

    def test_unrecognized_borough_raises(self):
        with pytest.raises(ValueError, match="Unrecognized borough"):
            borough_name_to_code("Narnia")


# ── borough_code_to_name ─────────────────────────────────────────────


class TestBoroughCodeToName:
    """Tests for borough_code_to_name."""

    @pytest.mark.parametrize("code,expected", [
        ("1", "Manhattan"),
        ("2", "Bronx"),
        ("3", "Brooklyn"),
        ("4", "Queens"),
        ("5", "Staten Island"),
    ])
    def test_valid_codes(self, code, expected):
        assert borough_code_to_name(code) == expected

    def test_invalid_code_zero(self):
        with pytest.raises(ValueError, match="Invalid borough code"):
            borough_code_to_name("0")

    def test_invalid_code_six(self):
        with pytest.raises(ValueError, match="Invalid borough code"):
            borough_code_to_name("6")

    def test_invalid_code_empty(self):
        with pytest.raises(ValueError, match="Invalid borough code"):
            borough_code_to_name("")

    def test_invalid_code_non_numeric(self):
        with pytest.raises(ValueError, match="Invalid borough code"):
            borough_code_to_name("abc")


# ── format_currency ──────────────────────────────────────────────────


class TestFormatCurrency:
    """Tests for format_currency."""

    def test_none_returns_na(self):
        assert format_currency(None) == "N/A"

    def test_zero(self):
        assert format_currency(0) == "$0"

    def test_integer(self):
        assert format_currency(1250000) == "$1,250,000"

    def test_large_number(self):
        assert format_currency(999_999_999) == "$999,999,999"

    def test_float_whole_number(self):
        assert format_currency(500.0) == "$500"

    def test_float_with_cents(self):
        assert format_currency(1234.56) == "$1,234.56"

    def test_negative_integer(self):
        assert format_currency(-5000) == "-$5,000"

    def test_negative_float(self):
        assert format_currency(-1234.56) == "-$1,234.56"

    def test_small_amount(self):
        assert format_currency(1) == "$1"


# ── parse_address ────────────────────────────────────────────────────


class TestParseAddress:
    """Tests for parse_address from geoclient.py."""

    def test_standard_address_with_borough_and_zip(self):
        result = parse_address("123 Main St, Brooklyn, NY 11201")
        assert result["house_number"] == "123"
        assert result["street"] == "Main St"
        assert result["borough_code"] == "3"
        assert result["borough_name"] == "Brooklyn"

    def test_queens_hyphenated_house_number(self):
        result = parse_address("37-10 30th Ave, Queens")
        assert result["house_number"] == "37-10"
        assert result["street"] == "30th Ave"
        assert result["borough_code"] == "4"
        assert result["borough_name"] == "Queens"

    def test_zip_only_borough_detection_manhattan(self):
        result = parse_address("123 Main St 10001")
        assert result["borough_code"] == "1"
        assert result["borough_name"] == "Manhattan"

    def test_zip_only_borough_detection_bronx(self):
        result = parse_address("456 Grand Concourse 10451")
        assert result["borough_code"] == "2"
        assert result["borough_name"] == "Bronx"

    def test_zip_only_borough_detection_staten_island(self):
        result = parse_address("789 Victory Blvd 10301")
        assert result["borough_code"] == "5"
        assert result["borough_name"] == "Staten Island"

    def test_missing_borough_and_zip_raises(self):
        from mcp.server.fastmcp.exceptions import ToolError

        with pytest.raises(ToolError, match="Could not determine borough"):
            parse_address("123 Main St")

    def test_unparseable_address_raises(self):
        from mcp.server.fastmcp.exceptions import ToolError

        with pytest.raises(ToolError, match="Could not parse address"):
            parse_address("not a real address at all!!!")

    def test_address_with_state_no_comma(self):
        result = parse_address("350 5th Ave, Manhattan, NY 10118")
        assert result["house_number"] == "350"
        assert result["borough_code"] == "1"

    def test_borough_alias_in_address(self):
        result = parse_address("100 Broadway, New York, NY 10005")
        assert result["borough_code"] == "1"
        assert result["borough_name"] == "Manhattan"


# ── _ZIP_TO_BOROUGH ──────────────────────────────────────────────────


class TestZipToBorough:
    """Spot-check the _ZIP_TO_BOROUGH mapping."""

    @pytest.mark.parametrize("zip_code,expected_code,expected_name", [
        (10001, "1", "Manhattan"),
        (10022, "1", "Manhattan"),
        (10282, "1", "Manhattan"),
        (10451, "2", "Bronx"),
        (10475, "2", "Bronx"),
        (11201, "3", "Brooklyn"),
        (11256, "3", "Brooklyn"),
        (11004, "4", "Queens"),
        (11101, "4", "Queens"),
        (11375, "4", "Queens"),
        (11697, "4", "Queens"),
        (10301, "5", "Staten Island"),
        (10314, "5", "Staten Island"),
    ])
    def test_zip_maps_to_correct_borough(self, zip_code, expected_code, expected_name):
        matched_code = None
        for zip_range, code in _ZIP_TO_BOROUGH.items():
            if zip_code in zip_range:
                matched_code = code
                break
        assert matched_code == expected_code, (
            f"Zip {zip_code} should map to borough {expected_code} ({expected_name})"
        )

    def test_non_nyc_zip_not_matched(self):
        """A zip outside NYC should not match any borough."""
        for zip_range, code in _ZIP_TO_BOROUGH.items():
            assert 90210 not in zip_range


# ── normalize_street_name ────────────────────────────────────────────


class TestNormalizeStreetName:
    """Tests for normalize_street_name from geoclient.py."""

    @pytest.mark.parametrize("input_str,expected", [
        # Ordinal expansion
        ("5th Ave",          "Fifth Avenue"),
        ("7th Ave",          "Seventh Avenue"),
        ("1st Pl",           "First Place"),
        ("30th St",          "Thirtieth Street"),
        ("42nd St",          "Forty-Second Street"),
        ("W 34th St",        "W Thirty-Fourth Street"),
        # Suffix-only expansion (no ordinal)
        ("Main St",          "Main Street"),
        ("Queens Blvd",      "Queens Boulevard"),
        ("Atlantic Ave",     "Atlantic Avenue"),
        ("Ocean Pkwy",       "Ocean Parkway"),
        # Already canonical — should pass through unchanged
        ("Fifth Avenue",     "Fifth Avenue"),
        ("Grand Concourse",  "Grand Concourse"),
        # 100+ ordinals: pass through as-is (GeoClient handles them)
        ("E 110th St",       "E 110th Street"),
        ("W 125th St",       "W 125th Street"),
        # Queens hyphenated address street component
        ("30th Ave",         "Thirtieth Avenue"),
    ])
    def test_normalization(self, input_str: str, expected: str) -> None:
        assert normalize_street_name(input_str) == expected

    @pytest.mark.parametrize("address,expected_street", [
        ("350 5th Ave, Manhattan, NY 10118",  "Fifth Avenue"),
        ("37-10 30th Ave, Queens",            "Thirtieth Avenue"),
        ("100 Gold Street, Manhattan",        "Gold Street"),
    ])
    def test_parse_then_normalize(self, address: str, expected_street: str) -> None:
        parsed = parse_address(address)
        assert normalize_street_name(parsed["street"]) == expected_street
