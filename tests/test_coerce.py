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
    DATASETS,
    _coerce,
    _is_valid_date_cursor,
    _normalize_cursor_date,
    _normalize_socrata_keys,
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
        "13/45/2023",  # would-be M/D/YYYY but month=13 — strptime rejects
        "12/32/2023",  # M=12 valid, but day=32 invalid
        "20231301",    # would-be YYYYMMDD but month=13
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


class TestCursorHelpers:
    """The cursor-advancement path at sync_delta.py:851-855 reads
    cfg.cursor_col from each Socrata row and feeds it through these helpers.
    Before the fix, M/D/YYYY values from dob_complaints and dobjobs were all
    rejected, leaving the cursor stuck and burning API quota."""

    @pytest.mark.parametrize("source, normalized", [
        # M/D/YYYY (dob_complaints, dobjobs) — were rejected before
        ("12/14/2018", "2018-12-14"),
        ("06/23/2023", "2023-06-23"),
        ("06/24/2023 00:00:00", "2023-06-24"),
        # YYYYMMDD (dob_violations) — was already accepted
        ("19881031", "1988-10-31"),
        # 14-digit (eabe-havv.dobrundate)
        ("20260503000000", "2026-05-03"),
        # ISO (most datasets)
        ("2026-04-30T00:00:00.000", "2026-04-30"),
    ])
    def test_normalize_to_iso(self, source, normalized):
        assert _is_valid_date_cursor(source) is True
        assert _normalize_cursor_date(source) == normalized

    @pytest.mark.parametrize("source", [
        "Y9990120",   # 3h2n-5cm9 sentinel — would poison cursor
        "0",
        "0   0612",
        "9999-12-31", # absurd-future protection
        "garbage",
        None,
        42,           # non-string
    ])
    def test_rejects_garbage_and_future(self, source):
        assert _is_valid_date_cursor(source) is False


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


class TestNormalizeSocrataKeys:
    """Regression tests for _normalize_socrata_keys.

    Strip-only mode (no column_map) handles the common case (e.g.
    `received_date` → `receiveddate`). When the local schema diverges from
    the source's short names, an explicit column_map must remap or the
    field is silently dropped at upsert time. See hpd_litigations bug:
    `boroid` and `casejudgement` were 100% NULL because no map was supplied.
    """

    def test_strip_underscores(self):
        out = _normalize_socrata_keys({"received_date": "2024-01-01", "unique_key": "abc"})
        assert out == {"receiveddate": "2024-01-01", "uniquekey": "abc"}

    def test_drops_socrata_system_fields(self):
        out = _normalize_socrata_keys({":id": "row-x", ":updated_at": "2024", "foo": "bar"})
        assert out == {"foo": "bar"}

    def test_column_map_overrides_stripped_name(self):
        out = _normalize_socrata_keys(
            {"boroid": "1", "casejudgement": "YES"},
            column_map={"boroid": "boro", "casejudgement": "openjudgement"},
        )
        assert out == {"boro": "1", "openjudgement": "YES"}

    def test_column_map_handles_combined_strip_then_remap(self):
        # nyc_311_complaints style: strip first → "addresstype", then remap → "address_type"
        out = _normalize_socrata_keys(
            {"address_type": "ADDRESS"},
            column_map={"addresstype": "address_type"},
        )
        assert out == {"address_type": "ADDRESS"}

    def test_hpd_litigations_config_remaps_known_mismatches(self):
        # Guards the dataset registry: regressing the column_map would make
        # boro / openjudgement go silently NULL again on every sync.
        cfg = DATASETS["hpd_litigations"]
        assert cfg.column_map is not None
        assert cfg.column_map.get("boroid") == "boro"
        assert cfg.column_map.get("casejudgement") == "openjudgement"
        # Round-trip: a real Socrata payload should land on the local column names.
        sample_source = {
            "litigationid": "460074",
            "boroid": "3",
            "casejudgement": "YES",
            "findingdate": "01/02/2025 00:00:00",
            "findingofharassment": "After Inquest",
        }
        out = _normalize_socrata_keys(sample_source, cfg.column_map)
        assert out["boro"] == "3"
        assert out["openjudgement"] == "YES"
        assert out["findingdate"] == "01/02/2025 00:00:00"
        assert out["findingofharassment"] == "After Inquest"
        # And the source-side keys should be GONE from the normalized dict
        # (otherwise upsert_page would still see them and they'd be silently
        # dropped at the column-projection step but might confuse other code).
        assert "boroid" not in out
        assert "casejudgement" not in out
