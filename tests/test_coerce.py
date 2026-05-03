"""Regression tests for _coerce and the flexible date parsers.

The original `_coerce` only accepted ISO 8601 — silently NULL'd M/D/YYYY,
YYYYMMDD, and YYYYMMDDHHMMSS values returned by some Socrata datasets
(eabe-havv, ic3t-wcy2, 3h2n-5cm9). Each format observed below is a real
sample taken from the NYC Open Data API on 2026-05-03.
"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from scripts.sync_delta import (
    _coerce,
    _parse_flexible_date,
    _parse_flexible_datetime,
)


class TestParseFlexibleDate:
    @pytest.mark.parametrize("s, expected", [
        ("2014-01-06", date(2014, 1, 6)),
        ("2014-01-06T00:00:00.000", date(2014, 1, 6)),
        ("2014-01-06T00:00:00.000Z", date(2014, 1, 6)),
        ("2014-01-06T12:30:45+00:00", date(2014, 1, 6)),
        ("06/23/2023", date(2023, 6, 23)),
        ("6/3/2023", date(2023, 6, 3)),
        ("06/24/2023 00:00:00", date(2023, 6, 24)),
        ("19881031", date(1988, 10, 31)),
        ("20260503000000", date(2026, 5, 3)),
    ])
    def test_known_formats(self, s, expected):
        assert _parse_flexible_date(s) == expected

    @pytest.mark.parametrize("s", [
        "",
        "   ",
        "0",
        "000000",
        "Y9990120",
        "Y30819",
        "0   0612",
        "0000207",
        "xyz",
        "not-a-date",
        "9999/99/99",
        "13/45/2023",  # invalid month/day
    ])
    def test_garbage_returns_none(self, s):
        assert _parse_flexible_date(s) is None

    def test_us_format_not_eu(self):
        # NYC Socrata is documented as US format. Confirm 04/05/2023 → April 5,
        # not May 4. If this ever flips we want the test to catch it.
        assert _parse_flexible_date("04/05/2023") == date(2023, 4, 5)


class TestParseFlexibleDatetime:
    def test_iso_with_time_naive(self):
        assert _parse_flexible_datetime("2014-01-06T12:30:45.000") == \
            datetime(2014, 1, 6, 12, 30, 45)

    def test_us_datetime_full(self):
        assert _parse_flexible_datetime("06/24/2023 14:15:16") == \
            datetime(2023, 6, 24, 14, 15, 16)

    def test_yyyymmddhhmmss(self):
        assert _parse_flexible_datetime("20260503142530") == \
            datetime(2026, 5, 3, 14, 25, 30)

    def test_garbage(self):
        assert _parse_flexible_datetime("Y9990120") is None
        assert _parse_flexible_datetime("") is None


class TestCoerceDate:
    """End-to-end through _coerce — mimics the actual sync code path."""

    @pytest.mark.parametrize("source, expected", [
        # eabe-havv (dob_complaints)
        ("12/14/2018", date(2018, 12, 14)),
        # eabe-havv.dobrundate (14-digit)
        ("20260503000000", date(2026, 5, 3)),
        # ic3t-wcy2 (dobjobs)
        ("06/23/2023", date(2023, 6, 23)),
        # 3h2n-5cm9 (dob_violations)
        ("19881031", date(1988, 10, 31)),
        ("20240102", date(2024, 1, 2)),
        # ISO datasets (most)
        ("2026-04-30T00:00:00.000", date(2026, 4, 30)),
    ])
    def test_real_socrata_samples(self, source, expected):
        assert _coerce(source, "date") == expected

    @pytest.mark.parametrize("source", ["Y9990120", "Y30819", "0", "0   0612", ""])
    def test_sentinel_returns_none(self, source):
        assert _coerce(source, "date") is None


class TestCoerceTimestamp:
    def test_dobjobs_dobrundate_format(self):
        # ic3t-wcy2.dobrundate: '06/24/2023 00:00:00'
        assert _coerce("06/24/2023 00:00:00", "timestamp without time zone") == \
            datetime(2023, 6, 24, 0, 0, 0)

    def test_iso_with_z(self):
        # nyc_311 etc.
        result = _coerce("2026-05-02T02:25:54.000Z", "timestamp with time zone")
        assert result is not None
        assert result.year == 2026
        assert result.month == 5

    def test_garbage_returns_none(self):
        assert _coerce("Y9990120", "timestamp without time zone") is None


class TestCoerceOtherTypes:
    """Confirm the non-date branches are unchanged."""

    def test_integer(self):
        assert _coerce("42", "integer") == 42
        assert _coerce("42.0", "integer") == 42

    def test_numeric(self):
        assert _coerce("3.14", "numeric") == 3.14

    def test_boolean(self):
        assert _coerce("Y", "boolean") is True
        assert _coerce("N", "boolean") is False
        assert _coerce("True", "boolean") is True

    def test_text_truncation(self):
        assert _coerce("hello world", "character varying", max_len=5) == "hello"

    def test_empty_string_to_none(self):
        assert _coerce("", "date") is None
        assert _coerce(None, "date") is None
