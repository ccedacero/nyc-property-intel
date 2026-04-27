#!/usr/bin/env python3
"""QA report: compare Railway DB row counts and sync state against live Socrata APIs.

Usage:
    DATABASE_URL=... SOCRATA_APP_TOKEN=... uv run python scripts/qa_db.py

Output:
    Per-dataset status table with DB count, Socrata count, drift %, cursor age,
    last sync time, and any recorded errors. Flags issues clearly.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone

import asyncpg
import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sync_delta import DATASETS, DatasetCfg

SOCRATA_BASE = "https://data.cityofnewyork.us"
DRIFT_WARN_PCT = 5.0
DRIFT_ERR_PCT = 10.0


async def get_socrata_count(client: httpx.AsyncClient, cfg: DatasetCfg) -> int | None:
    """Fetch row count from Socrata — tries $count endpoint first, falls back to metadata."""
    # Method 1: SoQL aggregate query
    try:
        r = await client.get(
            f"{SOCRATA_BASE}/resource/{cfg.socrata_id}.json",
            params={"$select": "count(*)", "$limit": 1},
            timeout=30,
        )
        if r.is_success:
            data = r.json()
            if data and "count_star_" in data[0]:
                return int(data[0]["count_star_"])
    except Exception:
        pass

    # Method 2: metadata endpoint
    try:
        r = await client.get(
            f"{SOCRATA_BASE}/api/views/{cfg.socrata_id}.json",
            timeout=30,
        )
        if r.is_success:
            meta = r.json()
            if "rowsUpdatedAt" in meta:
                pass  # not what we want
            if "viewLastModified" in meta:
                pass
            for col in meta.get("columns", []):
                cc = col.get("cachedContents", {})
                if "non_null" in cc:
                    return int(cc["non_null"])
    except Exception as e:
        print(f"  [WARN] Socrata count failed for {cfg.key}: {e}")
    return None


async def get_socrata_max_cursor(client: httpx.AsyncClient, cfg: DatasetCfg) -> str | None:
    """Fetch the maximum cursor value from Socrata (to check if DB cursor is current)."""
    cursor_col = cfg.socrata_cursor_col or cfg.cursor_col
    try:
        r = await client.get(
            f"{SOCRATA_BASE}/resource/{cfg.socrata_id}.json",
            params={
                "$select": f"max({cursor_col}) as max_cursor",
                "$limit": 1,
            },
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        if data and "max_cursor" in data[0]:
            return data[0]["max_cursor"]
    except Exception as e:
        print(f"  [WARN] Socrata max cursor failed for {cfg.key}: {e}")
    return None


async def run_qa() -> None:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    app_token = os.environ.get("SOCRATA_APP_TOKEN", "")
    headers = {"X-App-Token": app_token} if app_token else {}

    print("\n" + "=" * 90)
    print("NYC PROPERTY INTEL — DATABASE QA REPORT")
    print(f"Run at: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 90)

    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=3, command_timeout=60)

    async with httpx.AsyncClient(headers=headers, timeout=60) as client:
        # Pull all sync_state rows
        async with pool.acquire() as conn:
            state_rows = await conn.fetch("SELECT * FROM sync_state ORDER BY dataset_key")
            state_map = {r["dataset_key"]: dict(r) for r in state_rows}

        issues: list[str] = []
        results: list[dict] = []

        tasks = {
            key: (
                asyncio.create_task(get_socrata_count(client, cfg)),
                asyncio.create_task(get_socrata_max_cursor(client, cfg)),
            )
            for key, cfg in DATASETS.items()
        }

        for key, cfg in DATASETS.items():
            socrata_count_task, socrata_cursor_task = tasks[key]
            socrata_count = await socrata_count_task
            socrata_max_cursor = await socrata_cursor_task

            state = state_map.get(key)

            # DB actual row count
            async with pool.acquire() as conn:
                try:
                    db_count = await conn.fetchval(f'SELECT COUNT(*) FROM "{cfg.table}"')
                except Exception as e:
                    db_count = None
                    issues.append(f"[{key}] DB table query failed: {e}")

            # Drift calculation
            drift_pct: float | None = None
            drift_flag = ""
            if socrata_count and db_count is not None and socrata_count > 0:
                drift_pct = (socrata_count - db_count) / socrata_count * 100
                if drift_pct >= DRIFT_ERR_PCT:
                    drift_flag = " ❌ CRITICAL"
                    issues.append(f"[{key}] Missing {drift_pct:.1f}% of rows vs Socrata ({db_count:,} / {socrata_count:,})")
                elif drift_pct >= DRIFT_WARN_PCT:
                    drift_flag = " ⚠️  WARN"
                    issues.append(f"[{key}] Drift {drift_pct:.1f}% vs Socrata ({db_count:,} / {socrata_count:,})")
                elif drift_pct < -1.0:
                    drift_flag = " ℹ️  DB>API"  # DB has more rows than Socrata reports

            # Cursor lag: compare DB cursor to Socrata max
            cursor_lag = ""
            db_cursor = state["cursor_value"] if state else None
            if db_cursor and socrata_max_cursor:
                try:
                    db_dt = datetime.fromisoformat(str(db_cursor).replace(" ", "T").split("T")[0])
                    raw = str(socrata_max_cursor).strip()
                    # Normalise YYYYMMDD → YYYY-MM-DD
                    if len(raw) == 8 and raw.isdigit():
                        raw = f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
                    api_dt = datetime.fromisoformat(raw.split("T")[0])
                    today = datetime.now(timezone.utc).replace(tzinfo=None)
                    # Ignore bogus future dates from source (e.g. Y9990120, 2030-xx-xx)
                    if api_dt > today + __import__("datetime").timedelta(days=90):
                        cursor_lag = f" [API cursor bogus: {socrata_max_cursor[:12]}]"
                    else:
                        lag_days = (api_dt - db_dt).days
                        if lag_days > 7:
                            cursor_lag = f" [{lag_days}d behind]"
                            issues.append(f"[{key}] Cursor is {lag_days} days behind Socrata latest ({str(db_cursor)[:10]} vs {raw[:10]})")
                        elif lag_days > 1:
                            cursor_lag = f" [{lag_days}d behind]"
                except Exception:
                    pass

            # Last sync age
            last_success = state.get("last_success_at") if state else None
            if last_success:
                now = datetime.now(timezone.utc)
                if last_success.tzinfo is None:
                    last_success = last_success.replace(tzinfo=timezone.utc)
                age_h = (now - last_success).total_seconds() / 3600
                sync_age = f"{age_h:.1f}h ago"
                if age_h > 48 and cfg.tier == 1:
                    issues.append(f"[{key}] Last success was {age_h:.0f}h ago (tier-1 should sync daily)")
            else:
                sync_age = "NEVER"
                issues.append(f"[{key}] No successful sync recorded")

            last_error = state.get("last_error") if state else "no state row"

            results.append({
                "key": key,
                "tier": cfg.tier,
                "db_count": db_count,
                "socrata_count": socrata_count,
                "drift_pct": drift_pct,
                "drift_flag": drift_flag,
                "db_cursor": str(db_cursor)[:10] if db_cursor else "NULL",
                "api_cursor": str(socrata_max_cursor)[:10] if socrata_max_cursor else "?",
                "cursor_lag": cursor_lag,
                "sync_age": sync_age,
                "last_error": last_error,
            })

        # ── Print table ──────────────────────────────────────────────────
        print(f"\n{'DATASET':<35} {'TIER':<5} {'DB ROWS':>12} {'API ROWS':>12} {'DRIFT':>7}  {'DB CURSOR':<12} {'API CURSOR':<12} {'LAST SYNC'}")
        print("-" * 120)
        for r in results:
            db_str = f"{r['db_count']:,}" if r["db_count"] is not None else "ERROR"
            api_str = f"{r['socrata_count']:,}" if r["socrata_count"] is not None else "?"
            drift_str = f"{r['drift_pct']:+.1f}%" if r["drift_pct"] is not None else "?"
            print(
                f"{r['key']:<35} {r['tier']:<5} {db_str:>12} {api_str:>12} {drift_str:>7}"
                f"  {r['db_cursor']:<12} {r['api_cursor']:<12} {r['sync_age']}{r['drift_flag']}{r['cursor_lag']}"
            )
            if r["last_error"]:
                print(f"  {'':>35} last_error: {str(r['last_error'])[:80]}")

        # ── Issues summary ───────────────────────────────────────────────
        print("\n" + "=" * 90)
        if issues:
            print(f"ISSUES FOUND ({len(issues)}):")
            for i, msg in enumerate(issues, 1):
                print(f"  {i}. {msg}")
        else:
            print("ALL CLEAR — no drift, errors, or stale cursors detected.")
        print("=" * 90 + "\n")

    await pool.close()


if __name__ == "__main__":
    asyncio.run(run_qa())
