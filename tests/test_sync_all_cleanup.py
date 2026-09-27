"""Tests for the cleanup-cron consolidation in scripts/sync_all.py.

Verifies that sync_all.main() folds in the post-sync cleanup pass that used to
live in the standalone nyc-property-intel-cron-cleanup Railway service. See
docs/cost-cuts-plan-cleanup-consolidation-2026-05-06.md.

Pure unit tests — every external side effect is mocked. No DB, no subprocess,
no network. Run anywhere.

Tier membership is MOCKED (sync_all.DATASETS is patched to a fixed fake
registry) so these tests never break when the live dataset config is re-tiered
— which is exactly what silently broke them in 2026-09 (all tier-2 datasets
were moved to tier-3, so `--tier 2` early-exited before cleanup). The empty-
tier-2 case now has its own explicit regression test below.
"""

from __future__ import annotations

import os
import sys

# scripts/ isn't a package; share the same sys.path trick that
# tests/test_cleanup_idle_tokens.py uses.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import sync_all  # noqa: E402


# ── Helpers ───────────────────────────────────────────────────────────


class _FakeCfg:
    """Minimal stand-in for sync_delta.DatasetCfg — only .tier is read here."""

    def __init__(self, tier: int) -> None:
        self.tier = tier


# One dataset per tier, so tier selection is deterministic regardless of the
# live registry. Tests that need an EMPTY tier-2 patch this explicitly.
_FAKE_DATASETS = {
    "fake_t1": _FakeCfg(1),
    "fake_t2": _FakeCfg(2),
    "fake_t3": _FakeCfg(3),
}
_FAKE_DATASETS_NO_TIER2 = {
    "fake_t1": _FakeCfg(1),
    "fake_t3": _FakeCfg(3),
}


def _patch_common(monkeypatch, *, datasets=None):
    """Stub every external side effect and pin the dataset registry.

    Includes the post-sync DB steps (MV refresh + watch processing) so these
    tests never touch a database — falling through an empty tier-2 to the
    cleanup pass also reaches those steps.
    """
    def fake_run_one(key: str) -> sync_all.RunResult:
        return sync_all.RunResult(key=key, rc=0, duration_sec=0.0, log_tail="ok")
    monkeypatch.setattr(sync_all, "run_one", fake_run_one)
    monkeypatch.setattr(sync_all, "send_alert", lambda *a, **kw: True)
    monkeypatch.setattr(sync_all.time, "sleep", lambda *_: None)
    monkeypatch.setattr(sync_all, "DATASETS", datasets if datasets is not None else _FAKE_DATASETS)

    async def _noop_async(*_a, **_k):
        return {}
    # Post-sync DB steps — stub so no test hits a real database.
    monkeypatch.setattr(sync_all, "_refresh_materialized_views", _noop_async)
    import nyc_property_intel.watch as _watch  # noqa: PLC0415
    monkeypatch.setattr(_watch, "process_watches", _noop_async)


def _run_main_capture(monkeypatch, argv, *, datasets=None):
    """Run sync_all.main() with sys.argv = argv. Returns (exit_code, calls)."""
    _patch_common(monkeypatch, datasets=datasets)
    monkeypatch.setattr(sys, "argv", ["sync_all.py", *argv])

    calls = {"cleanup_calls": 0, "cleanup_dry_run_args": []}

    async def fake_cleanup(*, dry_run: bool) -> int:
        calls["cleanup_calls"] += 1
        calls["cleanup_dry_run_args"].append(dry_run)
        return 0

    monkeypatch.setattr(sync_all, "cleanup_idle_tokens", fake_cleanup)

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
        code, calls = _run_main_capture(monkeypatch, ["--tier", "1"])
        assert code == 0
        assert calls["cleanup_calls"] == 0, (
            "tier-1 (daily) sync must not run idle-token cleanup — "
            "that would change cadence vs the prior weekly schedule."
        )

    def test_tier_2_runs_cleanup_with_dry_run_false(self, monkeypatch):
        code, calls = _run_main_capture(monkeypatch, ["--tier", "2"])
        assert code == 0
        assert calls["cleanup_calls"] == 1, "tier-2 must trigger exactly one cleanup pass"
        assert calls["cleanup_dry_run_args"] == [False], (
            "production cleanup must run with dry_run=False — never accidentally "
            "no-op the revoke step on the weekly cron."
        )

    def test_empty_tier_2_still_runs_cleanup(self, monkeypatch):
        """Regression (2026-09): after all tier-2 datasets were re-tiered to
        tier-3, the weekly `--tier 2` cron early-exited on the empty sync set
        BEFORE the cleanup pass — silently killing idle-token cleanup in prod.
        An empty tier-2 must still run the cleanup pass."""
        code, calls = _run_main_capture(monkeypatch, ["--tier", "2"], datasets=_FAKE_DATASETS_NO_TIER2)
        assert code == 0
        assert calls["cleanup_calls"] == 1, (
            "an empty tier-2 (its datasets re-tiered away) must STILL run the "
            "weekly idle-token cleanup — the cron's whole remaining job."
        )

    def test_only_flag_does_not_run_cleanup(self, monkeypatch):
        """--only is operator-driven (one-off / triage). Don't sweep tokens."""
        code, calls = _run_main_capture(monkeypatch, ["--only", "fake_t1"])
        assert code == 0
        assert calls["cleanup_calls"] == 0


class TestCleanupKillSwitch:
    """--skip-cleanup flag and SYNC_SKIP_CLEANUP env both bypass the sweep."""

    def test_skip_cleanup_flag_suppresses(self, monkeypatch):
        code, calls = _run_main_capture(monkeypatch, ["--tier", "2", "--skip-cleanup"])
        assert code == 0
        assert calls["cleanup_calls"] == 0

    def test_env_var_suppresses(self, monkeypatch):
        monkeypatch.setenv("SYNC_SKIP_CLEANUP", "1")
        code, calls = _run_main_capture(monkeypatch, ["--tier", "2"])
        assert code == 0
        assert calls["cleanup_calls"] == 0

    def test_env_var_other_value_does_not_suppress(self, monkeypatch):
        """Only literal '1' suppresses — keep the contract narrow."""
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
        _patch_common(monkeypatch)

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
        _patch_common(monkeypatch)

        def fake_run_one(key: str) -> sync_all.RunResult:
            return sync_all.RunResult(key=key, rc=2, duration_sec=0.0, log_tail="boom")

        monkeypatch.setattr(sync_all, "run_one", fake_run_one)

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
