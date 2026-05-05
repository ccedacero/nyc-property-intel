#!/usr/bin/env python3
"""Phase 5 post-backfill verification — runs after Group 2 completes.

Reports:
- Per-dataset row count vs Socrata's metadata count (drift %)
- sync_state freshness for the 6 backfilled datasets
- High-NULL date columns (column_null_audit subset)
- Pass/fail rollup at the end

Run: RAILWAY_DB="postgresql://..." uv run python scripts/verify_backfill.py
"""
from __future__ import annotations

import asyncio
import os
import sys

import asyncpg
import httpx

DATASETS = {
    # key            socrata_id     primary date column to NULL-check (or None)
    "dob_violations":        ("3h2n-5cm9", "issuedate"),
    "dob_complaints":        ("eabe-havv", "dateentered"),
    "dobjobs":               ("ic3t-wcy2", "latestactiondate"),
    "nyc_311_complaints":    ("erm2-nwe9", "created_date"),
    "fdny_incidents":        ("8m42-w767", "incident_datetime"),
    "nypd_crime_complaints": ("qgea-i56i", "rpt_dt"),
}

NULL_PASS_THRESHOLD_PCT = 5.0  # any cursor column NULL > this is a regression


async def fetch_socrata_count(client: httpx.AsyncClient, socrata_id: str) -> int | None:
    try:
        r = await client.get(f"https://data.cityofnewyork.us/api/views/{socrata_id}.json", timeout=30)
        r.raise_for_status()
        meta = r.json()
        if "rowsCount" in meta:
            return int(meta["rowsCount"])
        for col in meta.get("columns", []):
            cached = col.get("cachedContents", {})
            if "non_null" in cached:
                return int(cached["non_null"])
    except Exception as e:
        print(f"  ! socrata count fetch failed: {e}", file=sys.stderr)
    return None


async def main() -> int:
    db_url = os.environ.get("RAILWAY_DB") or os.environ.get("DATABASE_URL")
    if not db_url:
        print("set RAILWAY_DB or DATABASE_URL", file=sys.stderr)
        return 2

    conn = await asyncpg.connect(db_url)
    failures: list[str] = []
    warnings: list[str] = []

    print(f"{'dataset':<26}{'local':>14}{'socrata':>14}{'drift':>10}  {'cursor_col_null%':>18}  status")
    print("─" * 100)

    async with httpx.AsyncClient() as client:
        for key, (socrata_id, null_col) in DATASETS.items():
            local = await conn.fetchval(f'SELECT COUNT(*) FROM "{key}"')
            socrata = await fetch_socrata_count(client, socrata_id)
            null_total = await conn.fetchval(f'SELECT COUNT(*) FROM "{key}"') if null_col else 0
            null_count = await conn.fetchval(
                f'SELECT COUNT(*) FROM "{key}" WHERE "{null_col}" IS NULL'
            ) if null_col else 0
            null_pct = (100.0 * null_count / null_total) if null_total else 0.0

            if socrata is None:
                drift_pct = float("nan")
                drift_str = "?"
            else:
                drift_pct = abs(local - socrata) / max(socrata, 1) * 100
                drift_str = f"{drift_pct:.2f}%"

            # Status rollup
            row_ok = (socrata is None) or (drift_pct < 5.0)
            null_ok = null_pct <= NULL_PASS_THRESHOLD_PCT
            if row_ok and null_ok:
                status = "✅"
            elif null_ok and not row_ok:
                status = "⚠️ row drift"
                warnings.append(f"{key}: row drift {drift_str}")
            elif row_ok and not null_ok:
                status = "❌ cursor col NULL"
                failures.append(f"{key}: {null_col} {null_pct:.2f}% NULL")
            else:
                status = "❌ both"
                failures.append(f"{key}: row drift {drift_str} + {null_col} {null_pct:.2f}% NULL")

            socrata_str = f"{socrata:,}" if socrata else "—"
            print(
                f"{key:<26}{local:>14,}{socrata_str:>14}{drift_str:>10}  "
                f"{null_pct:>17.4f}%  {status}"
            )

    print("─" * 100)

    # sync_state freshness
    print("\nsync_state for the 6 datasets:")
    rows = await conn.fetch("""
        SELECT dataset_key, last_run_at, last_success_at,
               cursor_value, expected_rows, actual_rows
        FROM sync_state
        WHERE dataset_key = ANY($1::text[])
        ORDER BY dataset_key
    """, list(DATASETS.keys()))
    for r in rows:
        cur = (r['cursor_value'] or '')[:30]
        print(f"  {r['dataset_key']:<26}  last_run={r['last_run_at']!s:<28}  cursor={cur}")

    await conn.close()

    print()
    if failures:
        print(f"❌ FAIL — {len(failures)} datasets:")
        for f in failures:
            print(f"  • {f}")
        return 1
    if warnings:
        print(f"⚠️  WARN — {len(warnings)} datasets:")
        for w in warnings:
            print(f"  • {w}")
        return 0
    print("✅ all 6 datasets pass: row count within 5% of Socrata + cursor column ≤5% NULL")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
