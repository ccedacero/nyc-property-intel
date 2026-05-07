"""Unit tests for scripts/posthog_ip_backfill.py.

Pure-Python tests — no DB, no network. The PostHog HTTP fetch and the
asyncpg DB read are intentionally not covered here; they're exercised
manually when the script runs against live credentials.
"""
from __future__ import annotations

import io
import os
import sys
from datetime import UTC, datetime, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from posthog_ip_backfill import (  # noqa: E402
    asn_hint,
    classify,
    emit_csv_rows,
    join_events_to_signups,
    summarize,
)


# ── ASN-hint heuristic ────────────────────────────────────────────────


class TestAsnHint:
    def test_aws_3_x_is_datacenter(self):
        # 3.230.0.1 — AWS us-east-1 (the example called out in the spec)
        assert asn_hint("3.230.0.1") == "datacenter"

    def test_aws_52_x_is_datacenter(self):
        assert asn_hint("52.4.5.6") == "datacenter"

    def test_aws_54_x_is_datacenter(self):
        assert asn_hint("54.224.10.20") == "datacenter"

    def test_residential_comcast(self):
        # Comcast/Xfinity residential range, e.g. 73.42.x.x — not in
        # any of our datacenter CIDRs.
        assert asn_hint("73.42.10.20") == "residential"

    def test_residential_verizon(self):
        # Verizon FiOS — 71.x is residential.
        assert asn_hint("71.123.45.67") == "residential"

    def test_invalid_ip_returns_empty(self):
        assert asn_hint("not-an-ip") == ""

    def test_none_returns_empty(self):
        assert asn_hint(None) == ""
        assert asn_hint("") == ""

    def test_ipv6_defaults_to_residential(self):
        # We don't carry IPv6 datacenter ranges; conservative default.
        assert asn_hint("2001:db8::1") == "residential"

    def test_gcp_34_x_is_datacenter(self):
        assert asn_hint("34.128.5.5") == "datacenter"

    def test_azure_20_x_is_datacenter(self):
        assert asn_hint("20.50.1.1") == "datacenter"


# ── Classification ────────────────────────────────────────────────────


class TestClassify:
    def test_real_requires_3_calls_and_2_days(self):
        assert classify(
            real_calls=5, null_tool_calls=0, distinct_active_days=3,
            is_in_burst=False, is_disposable=False,
        ) == "REAL"

    def test_real_threshold_just_under_falls_to_light(self):
        # 3 calls but only 1 active day -> LIGHT, not REAL
        assert classify(
            real_calls=3, null_tool_calls=0, distinct_active_days=1,
            is_in_burst=False, is_disposable=False,
        ) == "LIGHT"

    def test_light_for_any_real_call(self):
        assert classify(
            real_calls=1, null_tool_calls=0, distinct_active_days=1,
            is_in_burst=False, is_disposable=False,
        ) == "LIGHT"

    def test_zero_when_no_signals(self):
        assert classify(
            real_calls=0, null_tool_calls=0, distinct_active_days=0,
            is_in_burst=False, is_disposable=False,
        ) == "ZERO"

    def test_bot_likely_for_disposable(self):
        assert classify(
            real_calls=0, null_tool_calls=0, distinct_active_days=0,
            is_in_burst=False, is_disposable=True,
        ) == "BOT_LIKELY"

    def test_bot_likely_for_burst(self):
        assert classify(
            real_calls=0, null_tool_calls=0, distinct_active_days=0,
            is_in_burst=True, is_disposable=False,
        ) == "BOT_LIKELY"


# ── Join logic ────────────────────────────────────────────────────────


def _ts(secs_offset: int = 0) -> datetime:
    return datetime(2026, 5, 5, 12, 0, 0, tzinfo=UTC) + timedelta(seconds=secs_offset)


def _signup(email: str, secs: int = 0, **extra) -> dict:
    base = {
        "token_hash": f"hash_{email}",
        "email": email,
        "source": "cli",
        "created_at": _ts(secs),
        "real_calls": 0,
        "null_tool_calls": 0,
        "distinct_active_days": 0,
        "burst_cluster_size": 0,
    }
    base.update(extra)
    return base


def _event(email: str | None, secs: int, ip: str = "1.2.3.4",
           country: str = "US") -> dict:
    return {
        "timestamp": _ts(secs),
        "ip": ip,
        "country": country,
        "city": "New York",
        "continent": "NA",
        "email": email,
        "distinct_id": "did_" + (email or f"anon_{secs}"),
    }


class TestJoin:
    def test_exact_email_match_preferred(self):
        # An exact-email event should always beat a same-time anonymous
        # event, even if the anonymous one is closer.
        signups = [_signup("alice@example.com", secs=0)]
        events = [
            _event("alice@example.com", secs=20, ip="73.42.10.20"),
            _event(None, secs=2, ip="3.230.0.1"),  # closer in time
        ]
        joined = join_events_to_signups(
            posthog_events=events, db_signups=signups,
        )
        assert len(joined) == 1
        row = joined[0]
        assert row["match_kind"] == "exact"
        assert row["ip"] == "73.42.10.20"
        assert row["asn_hint"] == "residential"

    def test_heuristic_match_within_window(self):
        signups = [_signup("bob@example.com", secs=0)]
        # Anonymous event 5s away — inside the 10s window.
        events = [_event(None, secs=5, ip="3.230.0.1")]
        joined = join_events_to_signups(
            posthog_events=events, db_signups=signups,
        )
        assert joined[0]["match_kind"] == "heuristic"
        assert joined[0]["ip"] == "3.230.0.1"
        assert joined[0]["asn_hint"] == "datacenter"

    def test_heuristic_match_outside_window_falls_through(self):
        signups = [_signup("carol@example.com", secs=0)]
        # Anonymous event 30s away — outside the 10s window.
        events = [_event(None, secs=30, ip="3.230.0.1")]
        joined = join_events_to_signups(
            posthog_events=events, db_signups=signups,
        )
        assert joined[0]["match_kind"] == "none"
        assert joined[0]["ip"] is None
        assert joined[0]["asn_hint"] == ""

    def test_anonymous_event_consumed_only_once(self):
        # Two close-by signups; only one anonymous event. The first
        # signup (most recent in DB order) consumes the event; the
        # second has no match.
        signups = [
            _signup("first@example.com", secs=0),
            _signup("second@example.com", secs=3),
        ]
        events = [_event(None, secs=1, ip="3.230.0.1")]
        joined = join_events_to_signups(
            posthog_events=events, db_signups=signups,
        )
        # In iteration order, signups[0] gets matched first.
        matches = [r["match_kind"] for r in joined]
        assert matches.count("heuristic") == 1
        assert matches.count("none") == 1


# ── CSV emit ──────────────────────────────────────────────────────────


class TestEmitCsv:
    def test_csv_has_required_columns(self):
        signups = [_signup("alice@example.com", secs=0,
                           real_calls=5, distinct_active_days=3)]
        events = [_event("alice@example.com", secs=2, ip="73.42.10.20",
                         country="US")]
        joined = join_events_to_signups(
            posthog_events=events, db_signups=signups,
        )
        buf = io.StringIO()
        emit_csv_rows(joined, buf)
        output = buf.getvalue()
        # Header
        assert "email,signup_at,ip,country,city,continent,asn_hint" in output
        assert "made_real_call,classification,match_kind" in output
        # Data row
        assert "alice@example.com" in output
        assert "73.42.10.20" in output
        assert "residential" in output
        assert "REAL" in output
        assert "true" in output  # made_real_call

    def test_csv_marks_zero_no_match(self):
        signups = [_signup("nobody@example.com", secs=0)]
        joined = join_events_to_signups(
            posthog_events=[], db_signups=signups,
        )
        buf = io.StringIO()
        emit_csv_rows(joined, buf)
        output = buf.getvalue()
        assert "ZERO" in output
        assert "none" in output  # match_kind
        assert "false" in output


# ── Summary ───────────────────────────────────────────────────────────


class TestSummary:
    def test_burst_cluster_detection(self):
        # Three signups from the same /24 within the same hour.
        signups = [
            _signup(f"bot{i}@x.com", secs=i)
            for i in range(3)
        ]
        events = [
            _event(f"bot{i}@x.com", secs=i, ip=f"3.230.0.{10 + i}")
            for i in range(3)
        ]
        joined = join_events_to_signups(
            posthog_events=events, db_signups=signups,
        )
        s = summarize(joined)
        assert s["total_signups"] == 3
        assert s["matched_to_posthog"] == 3
        # All three are in the same /24, same hour.
        assert len(s["burst_clusters_24_hour"]) == 1
        assert s["burst_clusters_24_hour"][0]["count"] == 3
        # All three are AWS prefixes.
        assert s["asn_hint_breakdown"].get("datacenter") == 3
