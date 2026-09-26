#!/usr/bin/env python3
"""Weekly QA gate — asserts the invariants that matter for production health.

Run by .github/workflows/qa.yml on a weekly schedule (and on-demand). Exits
non-zero on any problem so the workflow fails and GitHub emails the repo owner.

What it checks, and WHY each was chosen:
  1. /healthz — status ok, db ok, and stale_datasets == []. The app already
     encodes the correct per-dataset staleness thresholds, so we defer to it
     for freshness instead of re-implementing (and contradicting) that logic.
  2. hpd_litigations future-dated rows — NYC source data-entry errors put a
     handful of rows decades in the future; they inflate "most recent case"
     in summaries. A couple re-materialize on each sync (known, cosmetic), so
     this FAILS only if the count grows past a threshold (a real regression).
  3. Harassment-finding domain — the false-harassment guardrail (fixed 2026-06)
     counts a finding ONLY when findingofharassment IN ('After Inquest',
     'After Trial'). If NYC ever introduces a NEW finding value, the allowlist
     would silently undercount it — so any unknown value is surfaced for review.

Usage:
    DATABASE_URL=postgresql://... uv run python scripts/weekly_qa_check.py
    # optional: HEALTHZ_URL=https://.../healthz  (defaults to prod)
"""
from __future__ import annotations

import asyncio
import os
import sys

import asyncpg
import httpx

HEALTHZ_URL = os.environ.get(
    "HEALTHZ_URL",
    "https://nyc-property-intel-production.up.railway.app/healthz",
)

# hpd_litigations future-dated rows are source noise (a few re-appear each
# sync). Fail only if they climb past this — a signal the source or our purge
# changed materially, not the steady-state handful.
FUTURE_ROW_FAIL_THRESHOLD = 10

# Values the harassment guardrail's allowlist knows about. Anything else is a
# new NYC value that should be reviewed against hpd_litigations.py / analysis.py.
KNOWN_HARASSMENT_FINDINGS = {"After Inquest", "After Trial", "No Harassment"}


async def check_healthz(problems: list[str]) -> None:
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(HEALTHZ_URL)
        r.raise_for_status()
        data = r.json()
    except Exception as exc:  # noqa: BLE001 — any failure is a critical signal
        problems.append(f"CRITICAL: /healthz unreachable or invalid: {exc}")
        return

    if data.get("status") != "ok":
        problems.append(f"CRITICAL: /healthz status = {data.get('status')!r} (expected 'ok')")
    if data.get("db") != "ok":
        problems.append(f"CRITICAL: /healthz db = {data.get('db')!r} (expected 'ok')")
    stale = data.get("stale_datasets") or []
    if stale:
        problems.append(f"CRITICAL: stale datasets reported by /healthz: {stale}")
    else:
        print("  ok  /healthz — status ok, db ok, no stale datasets")


async def check_future_dated_litigations(pool: asyncpg.Pool, problems: list[str]) -> None:
    n = await pool.fetchval(
        "SELECT COUNT(*) FROM hpd_litigations "
        "WHERE caseopendate > NOW() + INTERVAL '1 year'"
    )
    if n > FUTURE_ROW_FAIL_THRESHOLD:
        problems.append(
            f"future-dated hpd_litigations rows = {n} (> {FUTURE_ROW_FAIL_THRESHOLD}); "
            "the source-error purge or ingestion may have regressed"
        )
    else:
        print(f"  ok  future-dated hpd_litigations rows = {n} (<= {FUTURE_ROW_FAIL_THRESHOLD}, known source noise)")


async def check_harassment_domain(pool: asyncpg.Pool, problems: list[str]) -> None:
    rows = await pool.fetch(
        "SELECT DISTINCT findingofharassment FROM hpd_litigations "
        "WHERE findingofharassment IS NOT NULL"
    )
    values = {r["findingofharassment"] for r in rows}
    unknown = values - KNOWN_HARASSMENT_FINDINGS
    if unknown:
        problems.append(
            f"unknown findingofharassment value(s) {sorted(unknown)} — the guardrail "
            "allowlist ('After Inquest'/'After Trial') may now undercount; review "
            "hpd_litigations.py and analysis.py"
        )
    else:
        print(f"  ok  harassment-finding domain within allowlist ({sorted(values)})")


async def main() -> int:
    problems: list[str] = []

    print("== /healthz ==")
    await check_healthz(problems)

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        problems.append("CRITICAL: DATABASE_URL not set — DB integrity checks skipped")
    else:
        print("== data integrity ==")
        pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2)
        try:
            await check_future_dated_litigations(pool, problems)
            await check_harassment_domain(pool, problems)
        finally:
            await pool.close()

    print()
    if problems:
        print(f"WEEKLY QA FAILED — {len(problems)} issue(s):")
        for p in problems:
            print(f"  ✗ {p}")
        return 1
    print("WEEKLY QA PASSED — all invariants hold.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
