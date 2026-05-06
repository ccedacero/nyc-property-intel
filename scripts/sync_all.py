#!/usr/bin/env python3
"""Run all enabled sync_delta.py datasets in sequence and alert on failure.

Designed for Railway Cron — single entry point that handles a whole tier.

Usage:
    DATABASE_URL=...  SOCRATA_APP_TOKEN=...  RESEND_API_KEY=...  \
        uv run python scripts/sync_all.py [--tier 1] [--only hpd_violations]

Behavior:
  - Iterates dataset registry, filters by --tier (default 1).
  - Runs each via subprocess so a hard crash in one doesn't break the rest.
  - Stagger 5s between datasets to avoid hammering Socrata simultaneously.
  - On any failure or drift warning, emails a summary via Resend.
  - On weekly tier-2 runs, also sweeps idle trial tokens (folded in from
    the standalone cleanup-cron service — see
    docs/cost-cuts-plan-cleanup-consolidation-2026-05-06.md).
    Suppress with --skip-cleanup or SYNC_SKIP_CLEANUP=1.
  - Exits non-zero if any dataset failed (so Railway/cron logs flag it).

Exit codes:
  0 — all datasets OK
  1 — at least one drift warning (still emailed)
  2 — at least one fatal sync failure
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass

# Import the dataset registry from sync_delta — single source of truth
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sync_delta import DATASETS  # noqa: E402
from alerting import send_alert  # noqa: E402
from cleanup_idle_tokens import cleanup_idle_tokens  # noqa: E402

logger = logging.getLogger("sync_all")

INTER_DATASET_STAGGER_SEC = 5


@dataclass
class RunResult:
    key: str
    rc: int           # 0 ok, 1 drift, 2 fatal
    duration_sec: float
    log_tail: str     # last ~30 lines, for the email


def run_one(dataset_key: str) -> RunResult:
    """Run sync_delta.py for one dataset as a subprocess. Capture stderr+stdout."""
    here = os.path.dirname(os.path.abspath(__file__))
    cmd = ["uv", "run", "python", os.path.join(here, "sync_delta.py"), dataset_key]
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=3600,
            env={**os.environ},
        )
        rc = proc.returncode
        # Combine output and keep just the tail — full logs are in Railway
        combined = (proc.stdout + proc.stderr).strip().splitlines()
        tail = "\n".join(combined[-30:])
    except subprocess.TimeoutExpired:
        rc = 2
        tail = "TIMEOUT after 1h"
    except Exception as e:
        rc = 2
        tail = f"subprocess error: {e}"

    return RunResult(
        key=dataset_key, rc=rc,
        duration_sec=time.monotonic() - t0,
        log_tail=tail,
    )


def format_summary(results: list[RunResult]) -> tuple[str, str]:
    """Return (subject, body_plain) for the alert email."""
    ok = [r for r in results if r.rc == 0]
    warned = [r for r in results if r.rc == 1]
    failed = [r for r in results if r.rc not in (0, 1)]

    if failed:
        subject = f"❌ NYC Property Intel sync — {len(failed)} failed, {len(warned)} warned"
    elif warned:
        subject = f"⚠️ NYC Property Intel sync — {len(warned)} drift warnings"
    else:
        subject = f"✅ NYC Property Intel sync — {len(ok)} datasets OK"

    lines = [
        f"{subject}",
        "",
        f"Run at: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}",
        f"Datasets: {len(results)}  ok={len(ok)}  warned={len(warned)}  failed={len(failed)}",
        "",
    ]
    for r in results:
        status = {0: "OK", 1: "WARN", 2: "FAIL"}.get(r.rc, f"rc={r.rc}")
        lines.append(f"  [{status:>4}]  {r.key:30s}  {r.duration_sec:6.1f}s")

    if failed or warned:
        lines.append("")
        lines.append("─── Tails of failed / warned runs ───")
        for r in failed + warned:
            lines.append("")
            lines.append(f"=== {r.key} (rc={r.rc}) ===")
            lines.append(r.log_tail)

    return subject, "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--tier", type=int, default=int(os.environ.get("SYNC_TIER", 1)),
                   help="filter by tier (default: 1, or $SYNC_TIER env var)")
    p.add_argument("--only", help="run a specific dataset only (overrides --tier)")
    p.add_argument(
        "--always-email", action="store_true",
        help="send email even on full success (default: only on warn/fail)",
    )
    p.add_argument(
        "--skip-cleanup", action="store_true",
        help="skip post-sync idle-token cleanup on tier-2 runs "
             "(default: cleanup runs on tier 2 only)",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.only:
        if args.only not in DATASETS:
            logger.error("unknown dataset: %s", args.only)
            sys.exit(2)
        keys = [args.only]
    else:
        keys = [k for k, cfg in DATASETS.items() if cfg.tier == args.tier]
        if not keys:
            logger.warning("no datasets for tier %d", args.tier)
            sys.exit(0)

    logger.info("running %d datasets: %s", len(keys), ", ".join(keys))
    results: list[RunResult] = []
    for i, key in enumerate(keys):
        if i:
            time.sleep(INTER_DATASET_STAGGER_SEC)
        logger.info("─── %s ───", key)
        r = run_one(key)
        logger.info("%s rc=%d in %.1fs", key, r.rc, r.duration_sec)
        results.append(r)

    subject, body = format_summary(results)
    print("\n" + body)

    failed = sum(1 for r in results if r.rc == 2)
    warned = sum(1 for r in results if r.rc == 1)
    if failed or warned or args.always_email:
        send_alert(subject, body)

    # Post-sync idle-token cleanup — folded in from the former
    # nyc-property-intel-cron-cleanup service. Tier-2-only so we preserve the
    # prior weekly cadence; not running on tier-1 keeps daily syncs lean.
    # cleanup_idle_tokens() catches its own exceptions and exits-0-always at
    # the script boundary, but we wrap defensively here too — under no
    # circumstance should a cleanup hiccup change the sync's exit code.
    skip_cleanup = args.skip_cleanup or os.environ.get("SYNC_SKIP_CLEANUP") == "1"
    if not args.only and args.tier == 2 and not skip_cleanup:
        logger.info("running cleanup_idle_tokens (post-sync, tier=2)")
        try:
            asyncio.run(cleanup_idle_tokens(dry_run=False))
        except Exception:
            logger.exception("cleanup_idle_tokens crashed — sync exit code unchanged")
    elif skip_cleanup:
        logger.info("skipping post-sync cleanup (--skip-cleanup or SYNC_SKIP_CLEANUP=1)")

    if failed:
        sys.exit(2)
    # Drift warnings are informational — surfaced via log + email.
    # Exiting non-zero would trigger Railway's ON_FAILURE restart policy
    # and crash-loop the cron when a dataset has a permanent drift gap
    # (e.g. dobjobs: Socrata frozen at 2020-05-21, local has 1.8M of 2.7M rows).
    sys.exit(0)


if __name__ == "__main__":
    main()
