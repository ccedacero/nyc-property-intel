"""Regression tests for the resilient-backfill changes.

Covers:
  1. RETRY_BACKOFF_SEC schedule (extended to survive multi-minute Socrata 5xx
     outages — the old 5..120s budget was ~230s total, too short).
  2. run_backfill's BACKFILL_RESET=0 omits --reset from the subprocess call,
     so a crashed multi-hour backfill can resume from the persisted cursor
     instead of restarting at $offset=0.

See docs/data-refresh-plan.md and the 2026-05-03 nypd_crime_complaints
incidents (two crashed backfills) for context.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest import mock

import pytest

# Make scripts/ importable without installing it as a package.
REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


class TestRetryBackoffSchedule:
    """The retry budget must be long enough to survive a sustained Socrata
    5xx outage (observed up to 5-10 minutes during long-running backfills).
    Old budget [5,15,30,60,120] = ~230s — too short."""

    def test_schedule_is_extended(self):
        from scripts.sync_delta import RETRY_BACKOFF_SEC

        assert RETRY_BACKOFF_SEC == [10, 30, 60, 120, 300, 600, 1200]

    def test_total_budget_at_least_30_minutes(self):
        from scripts.sync_delta import RETRY_BACKOFF_SEC

        # Total wait across all retries should comfortably exceed 30 minutes
        # so a single 5-10 min outage doesn't terminate a multi-hour run.
        assert sum(RETRY_BACKOFF_SEC) >= 30 * 60

    def test_schedule_is_monotonic(self):
        """Backoff must be non-decreasing — exponential-ish ramp."""
        from scripts.sync_delta import RETRY_BACKOFF_SEC

        for a, b in zip(RETRY_BACKOFF_SEC, RETRY_BACKOFF_SEC[1:]):
            assert a <= b


class TestBackfillResetEnv:
    """BACKFILL_RESET=0 → resume mode (omit --reset). Default → legacy behavior."""

    def _run_backfill(self, monkeypatch, *, datasets: str, reset_env: str | None):
        monkeypatch.setenv("BACKFILL_DATASETS", datasets)
        if reset_env is None:
            monkeypatch.delenv("BACKFILL_RESET", raising=False)
        else:
            monkeypatch.setenv("BACKFILL_RESET", reset_env)

        # Re-import to pick up the patched env on each call.
        if "run_backfill" in sys.modules:
            del sys.modules["run_backfill"]
        run_backfill = importlib.import_module("run_backfill")

        fake_proc = mock.Mock(returncode=0)
        with mock.patch.object(
            run_backfill.subprocess, "run", return_value=fake_proc
        ) as run_mock:
            rc = run_backfill.main()
        return rc, run_mock

    def test_default_includes_reset(self, monkeypatch):
        rc, run_mock = self._run_backfill(
            monkeypatch, datasets="nypd_crime_complaints", reset_env=None,
        )
        assert rc == 0
        run_mock.assert_called_once()
        cmd = run_mock.call_args[0][0]
        assert "--reset" in cmd
        assert cmd[-2:] == ["nypd_crime_complaints", "--reset"]

    def test_reset_one_includes_reset(self, monkeypatch):
        _, run_mock = self._run_backfill(
            monkeypatch, datasets="nypd_crime_complaints", reset_env="1",
        )
        cmd = run_mock.call_args[0][0]
        assert "--reset" in cmd

    def test_reset_zero_omits_reset(self, monkeypatch):
        rc, run_mock = self._run_backfill(
            monkeypatch, datasets="nypd_crime_complaints", reset_env="0",
        )
        assert rc == 0
        run_mock.assert_called_once()
        cmd = run_mock.call_args[0][0]
        assert "--reset" not in cmd, f"expected no --reset, got: {cmd}"
        # Must still invoke the underlying script with the dataset key.
        assert cmd[-1] == "nypd_crime_complaints"

    def test_reset_zero_runs_each_dataset(self, monkeypatch):
        _, run_mock = self._run_backfill(
            monkeypatch,
            datasets="nypd_crime_complaints, fdny_incidents",
            reset_env="0",
        )
        assert run_mock.call_count == 2
        for call in run_mock.call_args_list:
            cmd = call[0][0]
            assert "--reset" not in cmd

    def test_empty_datasets_returns_zero(self, monkeypatch):
        # Service must always exit 0 to avoid Railway restart loops.
        monkeypatch.setenv("BACKFILL_DATASETS", "")
        if "run_backfill" in sys.modules:
            del sys.modules["run_backfill"]
        run_backfill = importlib.import_module("run_backfill")
        with mock.patch.object(run_backfill.subprocess, "run") as run_mock:
            rc = run_backfill.main()
        assert rc == 0
        run_mock.assert_not_called()
