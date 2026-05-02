#!/usr/bin/env python3
"""Live dashboard — usage, errors, sync health, signups.

Run anytime against the production DB:
    RAILWAY_DB=... uv run python scripts/dashboard.py

Defaults to last 24h. Pass --hours 168 for last week.
Pass --offline to skip Socrata freshness checks.
Pass --exact   to use COUNT(*) instead of pg_class.reltuples for row counts.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone

import asyncpg
import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sync_delta import DATASETS, DatasetCfg  # noqa: E402

_SOCRATA_SEM = asyncio.Semaphore(8)  # cap concurrent Socrata calls


def _to_iso_date(val: str) -> str:
    """Normalize whatever Socrata returns to YYYY-MM-DD (best effort)."""
    from datetime import datetime as _dt
    s = str(val).split("T")[0]
    if "/" in s:
        try:
            return _dt.strptime(s, "%m/%d/%Y").date().isoformat()
        except ValueError:
            return s
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s  # already ISO or sentinel (e.g. Y9990120)


async def _socrata_max_cursor(client: httpx.AsyncClient, cfg: DatasetCfg) -> str | None:
    col = cfg.socrata_cursor_col or cfg.cursor_col
    url = f"https://data.cityofnewyork.us/resource/{cfg.socrata_id}.json"
    try:
        async with _SOCRATA_SEM:
            r = await client.get(url, params={"$select": f"max({col})"}, timeout=30)
            r.raise_for_status()
            data = r.json()
        if data and isinstance(data, list):
            val = data[0].get(f"max_{col}")
            return _to_iso_date(val) if val else None
    except Exception:
        pass
    return None


async def _gather_status(
    pool: asyncpg.Pool,
    client: httpx.AsyncClient | None,
    cfg: DatasetCfg,
    *,
    exact: bool,
) -> dict:
    async with pool.acquire() as conn:
        state = await conn.fetchrow(
            """SELECT last_run_at, last_success_at, cursor_value, actual_rows, last_error
                 FROM sync_state WHERE dataset_key = $1""",
            cfg.key,
        )

        try:
            if exact:
                local_rows = await conn.fetchval(f'SELECT COUNT(*) FROM "{cfg.table}"')
            else:
                local_rows = await conn.fetchval(
                    "SELECT reltuples::bigint FROM pg_class WHERE relname = $1", cfg.table
                )
        except Exception:
            local_rows = None

        try:
            raw = await conn.fetchval(f'SELECT MAX("{cfg.cursor_col}") FROM "{cfg.table}"')
            local_max_cursor = str(raw).split("T")[0] if raw is not None else None
        except Exception:
            local_max_cursor = None

    socrata_max = await _socrata_max_cursor(client, cfg) if client else None

    return {
        "key": cfg.key,
        "tier": cfg.tier,
        "last_run_at": state["last_run_at"] if state else None,
        "last_success_at": state["last_success_at"] if state else None,
        "last_error": (state["last_error"] or "") if state else "",
        "stored_cursor": (state["cursor_value"] or "") if state else "",
        "local_rows": local_rows,
        "local_max_cursor": local_max_cursor,
        "socrata_max": socrata_max,
    }


def _age(ts: datetime | None) -> str:
    if ts is None:
        return "NEVER RUN"
    delta = datetime.now(timezone.utc) - ts.astimezone(timezone.utc)
    h = delta.total_seconds() / 3600
    if h < 1:
        return f"{int(delta.total_seconds() / 60)}m ago"
    if h < 48:
        return f"{h:.1f}h ago"
    return f"{h / 24:.1f}d ago"


def _drift_flag(s: dict) -> str:
    if s["last_error"]:
        return f"❌ {s['last_error'][:35]}"
    if s["last_run_at"] is None:
        return "⏸ NEVER RUN"
    local = s["local_max_cursor"]
    soc = s["socrata_max"]
    if local and soc:
        # ISO dates/timestamps sort lexicographically — trim to date for comparison
        local_d = local[:10]
        soc_d = soc[:10]
        if local_d >= soc_d:
            return "✓"
        from datetime import date
        try:
            lag = (date.fromisoformat(soc_d) - date.fromisoformat(local_d)).days
            if lag >= 7:
                return f"⚠ {lag}d behind"
            return f"~ {lag}d behind"
        except ValueError:
            return f"~ local={local_d} soc={soc_d}"
    if local and not soc:
        return "✓ (Socrata n/a)"
    return "?"


async def run(hours: int, *, offline: bool, exact: bool) -> None:
    db_url = os.environ.get("RAILWAY_DB") or os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: set RAILWAY_DB or DATABASE_URL", file=sys.stderr)
        sys.exit(1)

    pool = await asyncpg.create_pool(db_url, min_size=2, max_size=10, command_timeout=60)
    conn = await pool.acquire()
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
            print(f"  {(r['tool_name'] or '(null)'):<32} {r['calls']:>6} {r['errors']:>5} {r['avg_ms'] or 0:>7} {r['p95_ms'] or 0:>7}")
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

    # ── 5. Data freshness & sync health ──────────────────────────────
    socrata_label = "(offline)" if offline else "(live Socrata check)"
    print(f"\n┌─ Data freshness & sync health {socrata_label} {'─' * 10}")
    print(f"  {'DATASET':<28} {'T':>2}  {'LOCAL ROWS':>13}  {'LOCAL CURSOR':<13}  {'LAST RUN':<13}  {'SOCRATA THROUGH':<16}  DRIFT")

    await pool.release(conn)  # release the dedicated conn before concurrent gather

    http_client = None if offline else httpx.AsyncClient(timeout=30)
    try:
        tasks = [
            _gather_status(pool, http_client, cfg, exact=exact)
            for cfg in DATASETS.values()
        ]
        statuses = await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        if http_client:
            await http_client.aclose()

    for s in statuses:
        if isinstance(s, Exception):
            print(f"  !! error gathering status: {s}")
            continue
        rows_fmt = f"{s['local_rows']:>13,}" if s['local_rows'] is not None else f"{'?':>13}"
        cursor_fmt = (s['local_max_cursor'] or "?")[:13]
        last_run = _age(s['last_run_at'])[:13]
        socrata = (s['socrata_max'] or "?")[:16]
        drift = _drift_flag(s)
        print(f"  {s['key']:<28} {s['tier']:>2}  {rows_fmt}  {cursor_fmt:<13}  {last_run:<13}  {socrata:<16}  {drift}")

    # ── 6. Auth failures ──────────────────────────────────────────────
    async with pool.acquire() as conn:
        auth_rows = await conn.fetch(f"""
            SELECT status_code, COUNT(*) AS cnt
              FROM mcp_usage_log
             WHERE called_at > NOW() - INTERVAL '{hours} hours'
               AND status_code IN (401, 403, 429)
             GROUP BY status_code ORDER BY status_code
        """)
    if auth_rows:
        print("\n┌─ Auth & rate-limit blocks ─────────────────────────────")
        for r in auth_rows:
            label = {401: "401 unauthorized (no/invalid token)",
                     403: "403 forbidden (revoked/expired)",
                     429: "429 rate-limited (quota hit)"}.get(r['status_code'], str(r['status_code']))
            print(f"  {label:<48} {r['cnt']}")

    print("\n" + "=" * 78 + "\n")
    await pool.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--hours", type=int, default=24, help="Look-back window (default 24)")
    p.add_argument("--offline", action="store_true", help="Skip Socrata freshness checks")
    p.add_argument("--exact", action="store_true", help="Use COUNT(*) instead of reltuples for row counts")
    args = p.parse_args()
    asyncio.run(run(args.hours, offline=args.offline, exact=args.exact))
