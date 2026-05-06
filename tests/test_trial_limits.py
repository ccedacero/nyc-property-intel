"""Unit tests for trial-tier query limit configuration.

Locks in the product spec:
- Trial users get 10 queries/day TOTAL (resets at midnight UTC).
- Of those 10, at most 5 may be analyze_property calls.
- The other ~5 are available for non-analyze tools / chat queries.
- Pro = 500/day, Team = 2000/day (unchanged by this change).

These are pure value assertions — no DB or network required — so they
run as part of the regular unit-test suite and fail loudly if anyone
edits the constants without intending to.
"""

from __future__ import annotations


def test_trial_plan_daily_cap_is_ten():
    """auth.PLAN_LIMITS['trial'] must be the daily cap from the product spec (10)."""
    from nyc_property_intel.auth import PLAN_LIMITS

    assert PLAN_LIMITS["trial"] == 10, (
        f"Trial daily cap drifted from spec — got {PLAN_LIMITS['trial']}, expected 10. "
        "Coordinate with the activation email template (Loops dashboard) before changing."
    )


def test_pro_and_team_limits_unchanged():
    """Pro/Team caps must remain at the launch-pricing values."""
    from nyc_property_intel.auth import PLAN_LIMITS

    assert PLAN_LIMITS["pro"] == 500
    assert PLAN_LIMITS["team"] == 2000


def test_trial_days_unchanged():
    """TRIAL_DAYS stays at 30 — only the per-day cap is being tightened."""
    from nyc_property_intel.auth import TRIAL_DAYS

    assert TRIAL_DAYS == 30


def test_chat_path_daily_limit_matches_plan_limit():
    """The chat-path daily cap (settings.chat_daily_query_limit) must match
    auth.PLAN_LIMITS['trial']. If they drift, web-chat trial users would see
    a different cap than CLI/MCP trial users — confusing and a bug surface.
    """
    from nyc_property_intel.auth import PLAN_LIMITS
    from nyc_property_intel.config import settings

    assert settings.chat_daily_query_limit == PLAN_LIMITS["trial"], (
        f"chat_daily_query_limit ({settings.chat_daily_query_limit}) is out of sync with "
        f"PLAN_LIMITS['trial'] ({PLAN_LIMITS['trial']}). Both must be updated together."
    )


def test_analyze_subcap_does_not_exceed_total():
    """analyze_property sub-cap must be <= total daily cap. Otherwise it's a
    no-op and signals the spec was misread."""
    from nyc_property_intel.config import settings

    assert settings.chat_analyze_trial_limit <= settings.chat_daily_query_limit
    # Per the spec: 5 of 10. Lock the exact value.
    assert settings.chat_analyze_trial_limit == 5
