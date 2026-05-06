"""Tests for the cleanup-cron consolidation in scripts/sync_all.py.

Verifies that sync_all.main() now folds in the post-sync cleanup pass that
used to live in the standalone nyc-property-intel-cron-cleanup Railway
service. See docs/cost-cuts-plan-cleanup-consolidation-2026-05-06.md.

Pure unit tests — every external side effect is mocked. No DB, no
subprocess, no network. Run anywhere.
"""

from __future__ import annotations

import os
import sys

# scripts/ isn't a package; share the same sys.path trick that
# tests/test_cleanup_idle_tokens.py uses.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import sync_all  # noqa: E402


# ── Helpers ───────────────────────────────────────────────────────────


def _patch_run_one_ok(monkeypatch):
    """Replace sync_all.run_one with a stub that returns rc=0 instantly.

    The real run_one spawns `uv run python scripts/sync_delta.py KEY` as a
    subprocess — we don't want that in unit tests.
    """
    def fake_run_one(key: str) -> sync_all.RunResult:
        return sync_all.RunResult(key=key, rc=0, duration_sec=0.0, log_tail="ok")
    monkeypatch.setattr(sync_all, "run_one", fake_run_one)


def _patch_send_alert_noop(monkeypatch):
    """Don't send a real alert email during tests."""
    monkeypatch.setattr(sync_all, "send_alert", lambda *a, **kw: True)


def _patch_sleep_zero(monkeypatch):
    """The 5s inter-dataset stagger would slow tests pointlessly."""
    monkeypatch.setattr(sync_all.time, "sleep", lambda *_: None)


def _run_main_capture(monkeypatch, argv):
    """Run sync_all.main() with sys.argv = argv. Returns the SystemExit code.

    Wraps the cleanup coroutine so we can assert call/skip without hitting
    the DB. Tracks whether asyncio.run was actually called and with what.
    """
    monkeypatch.setattr(sys, "argv", ["sync_all.py", *argv])

    calls = {"cleanup_calls": 0, "cleanup_dry_run_args": []}

    async def fake_cleanup(*, dry_run: bool) -> int:
        calls["cleanup_calls"] += 1
        calls["cleanup_dry_run_args"].append(dry_run)
        return 0

    monkeypatch.setattr(sync_all, "cleanup_idle_tokens", fake_cleanup)

    # asyncio.run is fine to call with our fake coroutine — it's a real coro.
    # Let the real asyncio.run drive it so we exercise the wrapper path.

    code = None
    try:
        sync_all.main()
    except SystemExit as e:
        code = int(e.code) if e.code is not None else 0
    return code, calls


# ── Tests ─────────────────────────────────────────────────────────────


class TestCleanupGating:
    """Cleanup must run on weekly tier-2 only — never tier-1, never --only."""

    def test_tier_1_does_not_run_cleanup(self, monkeypatch):
        _patch_run_one_ok(monkeypatch)
        _patch_send_alert_noop(monkeypatch)
        _patch_sleep_zero(monkeypatch)
        # Choose a key that exists in the registry. Force --tier 1 explicitly.
        code, calls = _run_main_capture(monkeypatch, ["--tier", "1"])
        assert code == 0
        assert calls["cleanup_calls"] == 0, (
            "tier-1 (daily) sync must not run idle-token cleanup — "
            "that would change cadence vs the prior weekly schedule."
        )

    def test_tier_2_runs_cleanup_with_dry_run_false(self, monkeypatch):
        _patch_run_one_ok(monkeypatch)
        _patch_send_alert_noop(monkeypatch)
        _patch_sleep_zero(monkeypatch)
        code, calls = _run_main_capture(monkeypatch, ["--tier", "2"])
        assert code == 0
        assert calls["cleanup_calls"] == 1, "tier-2 must trigger exactly one cleanup pass"
        assert calls["cleanup_dry_run_args"] == [False], (
            "production cleanup must run with dry_run=False — never accidentally "
            "no-op the revoke step on the weekly cron."
        )

    def test_only_flag_does_not_run_cleanup(self, monkeypatch):
        """--only is operator-driven (one-off / triage). Don't sweep tokens."""
        _patch_run_one_ok(monkeypatch)
        _patch_send_alert_noop(monkeypatch)
        _patch_sleep_zero(monkeypatch)
        # Pick any dataset that exists in the registry.
        from sync_delta import DATASETS  # noqa: PLC0415
        a_key = next(iter(DATASETS))
        code, calls = _run_main_capture(monkeypatch, ["--only", a_key])
        assert code == 0
        assert calls["cleanup_calls"] == 0


class TestCleanupKillSwitch:
    """--skip-cleanup flag and SYNC_SKIP_CLEANUP env both bypass the sweep."""

    def test_skip_cleanup_flag_suppresses(self, monkeypatch):
        _patch_run_one_ok(monkeypatch)
        _patch_send_alert_noop(monkeypatch)
        _patch_sleep_zero(monkeypatch)
        code, calls = _run_main_capture(monkeypatch, ["--tier", "2", "--skip-cleanup"])
        assert code == 0
        assert calls["cleanup_calls"] == 0

    def test_env_var_suppresses(self, monkeypatch):
        _patch_run_one_ok(monkeypatch)
        _patch_send_alert_noop(monkeypatch)
        _patch_sleep_zero(monkeypatch)
        monkeypatch.setenv("SYNC_SKIP_CLEANUP", "1")
        code, calls = _run_main_capture(monkeypatch, ["--tier", "2"])
        assert code == 0
        assert calls["cleanup_calls"] == 0

    def test_env_var_other_value_does_not_suppress(self, monkeypatch):
        """Only literal '1' suppresses — keep the contract narrow."""
        _patch_run_one_ok(monkeypatch)
        _patch_send_alert_noop(monkeypatch)
        _patch_sleep_zero(monkeypatch)
        monkeypatch.setenv("SYNC_SKIP_CLEANUP", "true")  # not "1"
        code, calls = _run_main_capture(monkeypatch, ["--tier", "2"])
        assert code == 0
        assert calls["cleanup_calls"] == 1


class TestCleanupCannotChangeExitCode:
    """Cleanup hiccups must never propagate to the parent's exit code.

    The standalone cleanup-cron exited 0 always (see cleanup_idle_tokens.main()).
    The consolidated cron must preserve that contract.
    """

    def test_cleanup_exception_does_not_change_zero_exit(self, monkeypatch):
        _patch_run_one_ok(monkeypatch)
        _patch_send_alert_noop(monkeypatch)
        _patch_sleep_zero(monkeypatch)

        async def boom(*, dry_run: bool) -> int:
            raise RuntimeError("DB unreachable")

        monkeypatch.setattr(sync_all, "cleanup_idle_tokens", boom)
        monkeypatch.setattr(sys, "argv", ["sync_all.py", "--tier", "2"])

        code = None
        try:
            sync_all.main()
        except SystemExit as e:
            code = int(e.code) if e.code is not None else 0

        assert code == 0, (
            "a cleanup crash must not turn a successful sync into a non-zero exit "
            "(would trigger Railway's ON_FAILURE restart policy and crash-loop)."
        )

    def test_cleanup_exception_does_not_mask_sync_failure(self, monkeypatch):
        """If a sync dataset fails (rc=2), final exit is still 2 even if cleanup also blew up."""
        def fake_run_one(key: str) -> sync_all.RunResult:
            return sync_all.RunResult(key=key, rc=2, duration_sec=0.0, log_tail="boom")

        monkeypatch.setattr(sync_all, "run_one", fake_run_one)
        _patch_send_alert_noop(monkeypatch)
        _patch_sleep_zero(monkeypatch)

        async def boom(*, dry_run: bool) -> int:
            raise RuntimeError("cleanup also broken")

        monkeypatch.setattr(sync_all, "cleanup_idle_tokens", boom)
        monkeypatch.setattr(sys, "argv", ["sync_all.py", "--tier", "2"])

        code = None
        try:
            sync_all.main()
        except SystemExit as e:
            code = int(e.code) if e.code is not None else 0

        assert code == 2, "sync failure must still exit 2; cleanup state is independent"
