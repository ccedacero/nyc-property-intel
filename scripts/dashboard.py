#!/usr/bin/env python3
"""Live dashboard — usage, errors, sync health, signups.

Run anytime against the production DB:
    RAILWAY_DB=... uv run python scripts/dashboard.py

Defaults to last 24h. Pass --hours 168 for last week.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone

import asyncpg


async def run(hours: int) -> None:
    db_url = os.environ.get("RAILWAY_DB") or os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: set RAILWAY_DB or DATABASE_URL", file=sys.stderr)
        sys.exit(1)

    conn = await asyncpg.connect(db_url)
    now = datetime.now(timezone.utc)

    print("\n" + "=" * 78)
    print(f"NYC PROPERTY INTEL — LIVE DASHBOARD  (last {hours}h)")
    print(f"Run at: {now.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 78)

    # ── 1. Tool calls by tool ─────────────────────────────────────────
    print("\n┌─ Tool calls (top 15) ──────────────────────────────────")
    rows = await conn.fetch(f"""
        SELECT tool_name,
               COUNT(*) AS calls,
               COUNT(*) FILTER (WHERE status_code >= 400) AS errors,
               ROUND(AVG(duration_ms)) AS avg_ms,
               PERCENTILE_DISC(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95_ms
          FROM mcp_usage_log
         WHERE called_at > NOW() - INTERVAL '{hours} hours'
         GROUP BY tool_name
         ORDER BY calls DESC
         LIMIT 15
    """)
    if rows:
        print(f"  {'TOOL':<32} {'CALLS':>6} {'ERR':>5} {'AVG ms':>7} {'P95 ms':>7}")
        for r in rows:
            print(f"  {r['tool_name']:<32} {r['calls']:>6} {r['errors']:>5} {r['avg_ms'] or 0:>7} {r['p95_ms'] or 0:>7}")
    else:
        print("  (no calls in window)")

    # ── 2. Errors / non-200s ──────────────────────────────────────────
    print("\n┌─ Recent errors (status >= 400) ────────────────────────")
    rows = await conn.fetch(f"""
        SELECT tool_name, status_code, called_at, duration_ms
          FROM mcp_usage_log
         WHERE called_at > NOW() - INTERVAL '{hours} hours'
           AND status_code >= 400
         ORDER BY called_at DESC LIMIT 10
    """)
    if rows:
        print(f"  {'WHEN':<22} {'TOOL':<28} {'STATUS':>6} {'DURATION'}")
        for r in rows:
            t = r['called_at'].astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
            print(f"  {t:<22} {r['tool_name']:<28} {r['status_code']:>6} {r['duration_ms']}ms")
    else:
        print("  ✓ no errors")

    # ── 3. Active customers ───────────────────────────────────────────
    print("\n┌─ Top customers ────────────────────────────────────────")
    rows = await conn.fetch(f"""
        SELECT t.customer_email, t.plan,
               COUNT(*) AS calls,
               COUNT(DISTINCT u.tool_name) AS unique_tools,
               COUNT(*) FILTER (WHERE u.status_code >= 400) AS errors
          FROM mcp_usage_log u
          JOIN mcp_tokens t USING (token_hash)
         WHERE u.called_at > NOW() - INTERVAL '{hours} hours'
         GROUP BY t.customer_email, t.plan
         ORDER BY calls DESC LIMIT 10
    """)
    if rows:
        print(f"  {'EMAIL':<42} {'PLAN':<6} {'CALLS':>6} {'TOOLS':>6} {'ERR':>5}")
        for r in rows:
            print(f"  {(r['customer_email'] or 'UNKNOWN')[:42]:<42} {r['plan']:<6} {r['calls']:>6} {r['unique_tools']:>6} {r['errors']:>5}")
    else:
        print("  (no usage)")

    # ── 4. Signups / token issuance ───────────────────────────────────
    print("\n┌─ New signups & magic links ────────────────────────────")
    new_tokens = await conn.fetchval(f"""
        SELECT COUNT(*) FROM mcp_tokens
         WHERE created_at > NOW() - INTERVAL '{hours} hours'
    """)
    new_links = await conn.fetchval(f"""
        SELECT COUNT(*) FROM web_magic_links
         WHERE created_at > NOW() - INTERVAL '{hours} hours'
    """)
    used_links = await conn.fetchval(f"""
        SELECT COUNT(*) FROM web_magic_links
         WHERE created_at > NOW() - INTERVAL '{hours} hours'
           AND used_at IS NOT NULL
    """)
    activation_rate = (used_links / new_links * 100) if new_links else 0
    print(f"  New tokens issued:       {new_tokens}")
    print(f"  Magic links created:     {new_links}")
    print(f"  Magic links activated:   {used_links}  ({activation_rate:.0f}% activation)")

    # ── 5. Sync state ────────────────────────────────────────────────
    print("\n┌─ Cron sync freshness ──────────────────────────────────")
    rows = await conn.fetch("""
        SELECT dataset_key,
               last_success_at,
               last_error,
               EXTRACT(EPOCH FROM (NOW() - last_success_at)) / 3600 AS age_h
          FROM sync_state ORDER BY dataset_key
    """)
    print(f"  {'DATASET':<32} {'AGE':<10} {'STATUS'}")
    for r in rows:
        if r['last_success_at']:
            age_h = r['age_h']
            age = f"{age_h:.1f}h" if age_h < 48 else f"{age_h/24:.1f}d"
        else:
            age = "NEVER"
        flag = "❌" if r['last_error'] else ("⚠️ " if r['last_success_at'] is None or (r['age_h'] or 0) > 48 else "✓ ")
        err = (r['last_error'] or "")[:40]
        print(f"  {flag} {r['dataset_key']:<30} {age:<10} {err}")

    # ── 6. Auth failures ──────────────────────────────────────────────
    rows = await conn.fetch(f"""
        SELECT status_code, COUNT(*) AS cnt
          FROM mcp_usage_log
         WHERE called_at > NOW() - INTERVAL '{hours} hours'
           AND status_code IN (401, 403, 429)
         GROUP BY status_code ORDER BY status_code
    """)
    if rows:
        print("\n┌─ Auth & rate-limit blocks ─────────────────────────────")
        for r in rows:
            label = {401: "401 unauthorized (no/invalid token)",
                     403: "403 forbidden (revoked/expired)",
                     429: "429 rate-limited (quota hit)"}.get(r['status_code'], str(r['status_code']))
            print(f"  {label:<48} {r['cnt']}")

    print("\n" + "=" * 78 + "\n")
    await conn.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--hours", type=int, default=24, help="Look-back window (default 24)")
    args = p.parse_args()
    asyncio.run(run(args.hours))
