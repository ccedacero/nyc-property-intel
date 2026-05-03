#!/usr/bin/env python3
"""Per-column NULL audit: find columns with anomalously high NULL rates.

The dob_complaints discovery showed _coerce() silently NULLs date columns
when Socrata returns M/D/YYYY or YYYYMMDD instead of ISO. Count-match alone
masks this. This script flags every date / timestamp column with NULL rate
above a threshold so we can catch the same bug on other datasets.

Run:
    RAILWAY_DB=postgresql://... uv run python scripts/column_null_audit.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

import asyncpg

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sync_delta import DATASETS  # noqa: E402

NULL_RATE_FLAG = 0.50  # flag any date/timestamp col over 50% NULL


async def audit_table(conn: asyncpg.Connection, table: str) -> list[dict]:
    cols = await conn.fetch(
        """SELECT column_name, data_type
             FROM information_schema.columns
            WHERE table_name = $1 AND table_schema = 'public'
              AND data_type IN ('date', 'timestamp without time zone',
                                'timestamp with time zone')
            ORDER BY ordinal_position""",
        table,
    )
    if not cols:
        return []
    total = await conn.fetchval(f'SELECT COUNT(*) FROM "{table}"')
    if not total:
        return []
    out = []
    for c in cols:
        n = c["column_name"]
        nulls = await conn.fetchval(f'SELECT COUNT(*) FROM "{table}" WHERE "{n}" IS NULL')
        rate = nulls / total
        out.append({
            "table": table,
            "column": n,
            "data_type": c["data_type"],
            "total": total,
            "nulls": nulls,
            "null_rate": rate,
        })
    return out


async def main() -> None:
    db_url = os.environ.get("RAILWAY_DB") or os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: set RAILWAY_DB or DATABASE_URL", file=sys.stderr)
        sys.exit(1)

    print(f"# Connecting to {db_url.rsplit('@', 1)[-1]}", file=sys.stderr)

    pool = await asyncpg.create_pool(db_url, min_size=2, max_size=4, command_timeout=180)

    all_rows: list[dict] = []
    async with pool.acquire() as conn:
        for cfg in DATASETS.values():
            try:
                rows = await audit_table(conn, cfg.table)
                all_rows.extend(rows)
            except Exception as e:
                print(f"# ERR {cfg.table}: {e}", file=sys.stderr)

    await pool.close()

    # Output
    print(f"{'TABLE':<30} {'COLUMN':<32} {'TYPE':<14} {'NULLS':>10} / {'TOTAL':<10}  {'%':>6}  FLAG")
    flagged = []
    for r in all_rows:
        flag = "🚨" if r["null_rate"] >= NULL_RATE_FLAG else ("⚠️ " if r["null_rate"] >= 0.10 else "  ")
        if r["null_rate"] >= NULL_RATE_FLAG:
            flagged.append(r)
        print(
            f"{r['table']:<30} {r['column']:<32} {r['data_type']:<14} "
            f"{r['nulls']:>10,} / {r['total']:<10,}  {r['null_rate']*100:>5.1f}%  {flag}"
        )

    if flagged:
        print(f"\n# 🚨 {len(flagged)} columns >= {NULL_RATE_FLAG*100:.0f}% NULL — likely _coerce() format mismatch:", file=sys.stderr)
        for r in flagged:
            print(f"  - {r['table']}.{r['column']} ({r['data_type']}): {r['null_rate']*100:.1f}% NULL", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
