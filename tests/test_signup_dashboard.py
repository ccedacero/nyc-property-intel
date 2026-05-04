"""Unit tests for signup_dashboard classification + helpers.

These tests are pure Python — they don't require a database. The DB-bound
parts (SQL, asyncpg) are intentionally not covered here.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from datetime import UTC

from signup_dashboard import (  # noqa: E402
    DISPOSABLE_DOMAINS,
    INTERNAL_EMAILS,
    _classify,
    _domain_of,
    _is_disposable,
    _is_internal,
    _render_bot_detail,
    _render_chart_data,
    _render_funnel,
)

# ── Helper predicates ────────────────────────────────────────────────

class TestIsInternal:
    def test_explicit_test_emails(self):
        for e in INTERNAL_EMAILS:
            assert _is_internal(e)

    def test_company_domain(self):
        assert _is_internal("anyone@nycpropertyintel.com")
        assert _is_internal("Anyone@NYCPropertyIntel.COM")  # case-insensitive

    def test_external(self):
        assert not _is_internal("real@example.com")
        assert not _is_internal("user@gmail.com")

    def test_none_or_empty(self):
        assert not _is_internal(None)
        assert not _is_internal("")


class TestDomainOf:
    def test_basic(self):
        assert _domain_of("user@example.com") == "example.com"

    def test_uppercase(self):
        assert _domain_of("User@EXAMPLE.com") == "example.com"

    def test_no_at(self):
        assert _domain_of("malformed") == ""

    def test_none(self):
        assert _domain_of(None) == ""


class TestIsDisposable:
    @pytest.mark.parametrize("email", [
        "x@lohinja.com",
        "x@mailinator.com",
        "x@guerrillamail.com",
        "x@10minutemail.com",
    ])
    def test_disposable_hits(self, email):
        assert _is_disposable(email)
        # And the domain must actually be in the curated set.
        assert _domain_of(email) in DISPOSABLE_DOMAINS

    def test_legit_misses(self):
        assert not _is_disposable("ceo@goldmansachs.com")
        assert not _is_disposable("hi@gmail.com")


# ── _classify precedence ─────────────────────────────────────────────

class TestClassify:
    def test_real_requires_3_calls_and_2_days(self):
        cls, reasons = _classify(
            real_calls=5, null_tool_calls=0, distinct_active_days=3,
            is_in_burst=False, is_disposable=False,
        )
        assert cls == "REAL"
        assert reasons == []

    def test_three_calls_one_day_is_light(self):
        # 3 calls but only 1 active day → not REAL, only LIGHT
        cls, _ = _classify(
            real_calls=3, null_tool_calls=0, distinct_active_days=1,
            is_in_burst=False, is_disposable=False,
        )
        assert cls == "LIGHT"

    def test_two_calls_two_days_is_light(self):
        # Spec says LIGHT = 1–2 days of activity with non-NULL tool_name
        cls, _ = _classify(
            real_calls=2, null_tool_calls=0, distinct_active_days=2,
            is_in_burst=False, is_disposable=False,
        )
        assert cls == "LIGHT"

    def test_one_call_is_light(self):
        cls, _ = _classify(
            real_calls=1, null_tool_calls=10, distinct_active_days=1,
            is_in_burst=False, is_disposable=False,
        )
        assert cls == "LIGHT"

    def test_only_null_tool_is_init_only(self):
        cls, reasons = _classify(
            real_calls=0, null_tool_calls=4, distinct_active_days=0,
            is_in_burst=False, is_disposable=False,
        )
        assert cls == "INIT_ONLY"
        assert reasons == []

    def test_zero_no_signals(self):
        cls, reasons = _classify(
            real_calls=0, null_tool_calls=0, distinct_active_days=0,
            is_in_burst=False, is_disposable=False,
        )
        assert cls == "ZERO"
        assert reasons == []

    def test_bot_likely_disposable(self):
        cls, reasons = _classify(
            real_calls=0, null_tool_calls=0, distinct_active_days=0,
            is_in_burst=False, is_disposable=True,
        )
        assert cls == "BOT_LIKELY"
        assert "disposable_domain" in reasons

    def test_bot_likely_burst(self):
        cls, reasons = _classify(
            real_calls=0, null_tool_calls=0, distinct_active_days=0,
            is_in_burst=True, is_disposable=False,
        )
        assert cls == "BOT_LIKELY"
        assert "burst_signup" in reasons

    def test_bot_likely_both_signals(self):
        cls, reasons = _classify(
            real_calls=0, null_tool_calls=0, distinct_active_days=0,
            is_in_burst=True, is_disposable=True,
        )
        assert cls == "BOT_LIKELY"
        assert set(reasons) == {"disposable_domain", "burst_signup"}

    def test_real_user_in_burst_not_flagged(self):
        # If you're REAL, we don't downgrade you for being in a burst window.
        cls, reasons = _classify(
            real_calls=10, null_tool_calls=0, distinct_active_days=4,
            is_in_burst=True, is_disposable=True,
        )
        assert cls == "REAL"
        assert reasons == []

    def test_init_only_with_disposable_stays_init_only(self):
        # Bot signals only escalate ZERO, not INIT_ONLY — handshake means
        # someone wired up Claude Desktop, which is more than a bot does.
        cls, _ = _classify(
            real_calls=0, null_tool_calls=2, distinct_active_days=0,
            is_in_burst=True, is_disposable=True,
        )
        assert cls == "INIT_ONLY"


# ── _render_funnel aggregation ───────────────────────────────────────

def _mk_signup(cls: str, reasons: list[str] | None = None) -> dict:
    return {
        "email": "x@example.com",
        "plan": "trial",
        "source": "cli",
        "created_at": None,
        "real_calls": 0,
        "null_tool_calls": 0,
        "distinct_tools": 0,
        "distinct_active_days": 0,
        "is_in_burst": False,
        "is_disposable": False,
        "is_internal": False,
        "burst_cluster_size": 0,
        "classification": cls,
        "bot_reasons": reasons or [],
    }


class TestRenderFunnel:
    def test_counts_each_bucket(self):
        signups = [
            _mk_signup("REAL"),
            _mk_signup("REAL"),
            _mk_signup("LIGHT"),
            _mk_signup("INIT_ONLY"),
            _mk_signup("ZERO"),
            _mk_signup("BOT_LIKELY", ["disposable_domain"]),
            _mk_signup("BOT_LIKELY", ["burst_signup"]),
            _mk_signup("BOT_LIKELY", ["disposable_domain", "burst_signup"]),
        ]
        f = _render_funnel(signups, days=30)
        assert f["total_signups"] == 8
        assert f["real"] == 2
        assert f["light"] == 1
        assert f["init_only"] == 1
        assert f["zero"] == 1
        assert f["bot_likely"] == 3
        assert f["bot_reasons"]["disposable_domain"] == 2
        assert f["bot_reasons"]["burst_signup"] == 2
        # 2 REAL out of 8 = 25%
        assert f["real_pct"] == 25.0
        # 3 with any real activity (REAL+LIGHT) out of 8 = 37.5%
        assert f["any_activity_pct"] == 37.5

    def test_empty_signups(self):
        f = _render_funnel([], days=30)
        assert f["total_signups"] == 0
        assert f["real_pct"] == 0.0
        assert f["any_activity_pct"] == 0.0


# ── _render_bot_detail aggregation ───────────────────────────────────

class TestRenderBotDetail:
    def test_groups_disposable_domains(self):
        s1 = _mk_signup("BOT_LIKELY", ["disposable_domain"])
        s1["email"] = "a@mailinator.com"
        s1["is_disposable"] = True
        s2 = _mk_signup("BOT_LIKELY", ["disposable_domain"])
        s2["email"] = "b@mailinator.com"
        s2["is_disposable"] = True
        s3 = _mk_signup("ZERO")
        s3["email"] = "c@gmail.com"
        detail = _render_bot_detail([s1, s2, s3])
        assert detail["zero_or_bot_count"] == 3
        assert detail["disposable_domain_hits"] == {"mailinator.com": 2}

    def test_lists_burst_signups(self):
        s = _mk_signup("BOT_LIKELY", ["burst_signup"])
        s["is_in_burst"] = True
        s["burst_cluster_size"] = 4
        detail = _render_bot_detail([s])
        assert len(detail["burst_signups"]) == 1
        assert detail["burst_signups"][0]["cluster_size"] == 4


# ── _render_chart_data fills missing days ────────────────────────────

class TestRenderChartData:
    def test_pads_to_requested_days(self):
        chart = _render_chart_data([], days=14)
        assert len(chart) == 14
        assert all(c["total"] == 0 for c in chart)
        # dates strictly increasing
        dates = [c["date"] for c in chart]
        assert dates == sorted(dates)

    def test_uses_daily_data(self):
        from datetime import datetime
        today = datetime.now(UTC).date().isoformat()
        chart = _render_chart_data([(today, 5, 4)], days=3)
        assert chart[-1]["date"] == today
        assert chart[-1]["total"] == 5
        assert chart[-1]["cli"] == 4
