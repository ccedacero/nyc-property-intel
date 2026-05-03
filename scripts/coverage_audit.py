#!/usr/bin/env python3
"""Per-dataset coverage audit: local DB vs Socrata source-of-truth.

Run against Railway prod DB:
    RAILWAY_DB=postgresql://... uv run python scripts/coverage_audit.py

Outputs JSON-lines on stdout (one row per dataset) and a Markdown summary
to docs/data-coverage-audit-{YYYY-MM-DD}.md.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import date, datetime, timezone

import asyncpg
import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sync_delta import DATASETS, DatasetCfg  # noqa: E402

_SEM = asyncio.Semaphore(6)


async def _socrata(client, cfg: DatasetCfg, q: dict) -> list | None:
    url = f"https://data.cityofnewyork.us/resource/{cfg.socrata_id}.json"
    try:
        async with _SEM:
            r = await client.get(url, params=q, timeout=60)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        return [{"_err": f"{type(e).__name__}: {e!s:.80}"}]


async def _socrata_meta(client, cfg: DatasetCfg) -> dict | None:
    try:
        async with _SEM:
            r = await client.get(
                f"https://data.cityofnewyork.us/api/views/{cfg.socrata_id}.json",
                timeout=60,
            )
            r.raise_for_status()
            return r.json()
    except Exception as e:
        return {"_err": f"{type(e).__name__}: {e!s:.80}"}


async def audit_one(pool: asyncpg.Pool, client: httpx.AsyncClient, cfg: DatasetCfg) -> dict:
    src_col = cfg.socrata_cursor_col or cfg.cursor_col

    async with pool.acquire() as conn:
        try:
            local_count = await conn.fetchval(f'SELECT COUNT(*) FROM "{cfg.table}"')
        except Exception as e:
            local_count = None
            local_count_err = f"{type(e).__name__}: {e!s:.80}"
        else:
            local_count_err = None

        try:
            local_min = await conn.fetchval(f'SELECT MIN("{cfg.cursor_col}") FROM "{cfg.table}"')
            local_max = await conn.fetchval(f'SELECT MAX("{cfg.cursor_col}") FROM "{cfg.table}"')
            local_null_cursor = await conn.fetchval(
                f'SELECT COUNT(*) FROM "{cfg.table}" WHERE "{cfg.cursor_col}" IS NULL'
            )
        except Exception:
            local_min = local_max = None
            local_null_cursor = None

        state = await conn.fetchrow(
            """SELECT last_run_at, last_success_at, cursor_value, actual_rows, last_error,
                      rows_added_total, expected_rows
                 FROM sync_state WHERE dataset_key = $1""",
            cfg.key,
        )

    soc_count_rows = await _socrata(client, cfg, {"$select": "count(*)"})
    soc_count = None
    if soc_count_rows and isinstance(soc_count_rows, list) and soc_count_rows:
        v = soc_count_rows[0].get("count") or soc_count_rows[0].get("count_1")
        try:
            soc_count = int(v) if v is not None else None
        except (TypeError, ValueError):
            soc_count = None

    soc_minmax = await _socrata(
        client, cfg,
        {"$select": f"min({src_col}) as min_v, max({src_col}) as max_v"},
    )
    soc_min = soc_max = None
    if soc_minmax and isinstance(soc_minmax, list) and soc_minmax and "_err" not in soc_minmax[0]:
        soc_min = soc_minmax[0].get("min_v")
        soc_max = soc_minmax[0].get("max_v")

    meta = await _socrata_meta(client, cfg)
    meta_rows = None
    if meta and "_err" not in meta:
        if "rowsCount" in meta:
            meta_rows = meta["rowsCount"]
        else:
            for col in meta.get("columns") or []:
                cached = col.get("cachedContents") or {}
                if "non_null" in cached:
                    try:
                        meta_rows = int(cached["non_null"])
                        break
                    except (TypeError, ValueError):
                        pass
        flags = meta.get("flags") or []
        meta_archived = bool(meta.get("archived")) or "archived" in flags
        meta_deprecated = bool(meta.get("deprecated"))
    else:
        meta_archived = meta_deprecated = None

    return {
        "key": cfg.key,
        "tier": cfg.tier,
        "table": cfg.table,
        "cursor_col": cfg.cursor_col,
        "socrata_id": cfg.socrata_id,
        "sync_mode": cfg.sync_mode,
        # local DB
        "local_count": local_count,
        "local_count_err": local_count_err,
        "local_min": str(local_min) if local_min is not None else None,
        "local_max": str(local_max) if local_max is not None else None,
        "local_null_cursor": local_null_cursor,
        # sync_state
        "state_cursor": (state["cursor_value"] if state else None),
        "state_rows_added_total": (state["rows_added_total"] if state else None),
        "state_expected_rows": (state["expected_rows"] if state else None),
        "state_last_run_at": state["last_run_at"].isoformat() if state and state["last_run_at"] else None,
        "state_last_success_at": state["last_success_at"].isoformat() if state and state["last_success_at"] else None,
        "state_last_error": (state["last_error"] if state else None),
        # Socrata source-of-truth
        "soc_count": soc_count,
        "soc_min": soc_min,
        "soc_max": soc_max,
        "soc_meta_rows": meta_rows,
        "soc_archived": meta_archived,
        "soc_deprecated": meta_deprecated,
    }


def classify(r: dict) -> tuple[str, str]:
    """Return (status_code, one-line explanation)."""
    if r["local_count"] is None:
        return ("ERROR", f"local query failed: {r['local_count_err']}")

    soc_count = r["soc_count"]
    local = r["local_count"]
    if soc_count is None:
        return ("UNKNOWN", "Socrata count(*) failed; cannot compare")

    diff_pct = (local - soc_count) / soc_count * 100 if soc_count > 0 else 0.0

    # Local AHEAD of Socrata (e.g. dobjobs has post-frozen-date augmentation,
    # or the source dropped rows since last sync)
    if local > soc_count:
        return ("LOCAL_AHEAD", f"local has {local-soc_count:,} more rows ({diff_pct:+.1f}%) — likely source-side deletion or NYCDB augmentation")

    # Within tolerance — sync is healthy
    if abs(diff_pct) < 1.0:
        return ("ALIGNED", f"local within 1% of Socrata ({diff_pct:+.2f}%)")
    if abs(diff_pct) < 5.0:
        return ("MINOR_DRIFT", f"{diff_pct:+.2f}% — likely lag between syncs")

    # Significant deficit
    if r["state_last_run_at"] is None:
        return ("NEVER_SYNCED", f"missing {soc_count-local:,} rows ({diff_pct:+.1f}%); sync_state.last_run_at is NULL — cron never ran")

    # Frozen source check: is local cursor far ahead of Socrata's max?
    try:
        l_max_d = (r["local_max"] or "")[:10]
        s_max_d = (r["soc_max"] or "")[:10]
        if l_max_d and s_max_d:
            ld = date.fromisoformat(l_max_d) if "-" in l_max_d else None
            sd = date.fromisoformat(s_max_d.replace("/", "-")) if s_max_d and "-" in s_max_d else None
            if ld and sd and ld > sd and (ld - sd).days > 365:
                return ("FROZEN_SOURCE",
                        f"local max {l_max_d} > Socrata max {s_max_d} ({(ld-sd).days}d ahead) — source frozen, missing {soc_count-local:,} historical rows")
    except (ValueError, TypeError):
        pass

    return ("DEFICIT", f"missing {soc_count-local:,} rows ({diff_pct:+.1f}%)")


async def main() -> None:
    db_url = os.environ.get("RAILWAY_DB") or os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: set RAILWAY_DB or DATABASE_URL", file=sys.stderr)
        sys.exit(1)

    pool = await asyncpg.create_pool(db_url, min_size=2, max_size=8, command_timeout=120)
    async with httpx.AsyncClient(timeout=60) as client:
        results = await asyncio.gather(
            *[audit_one(pool, client, cfg) for cfg in DATASETS.values()],
            return_exceptions=False,
        )
    await pool.close()

    # JSON lines to stdout
    for r in results:
        status, explanation = classify(r)
        r["status"] = status
        r["explanation"] = explanation
        print(json.dumps(r, default=str))

    # Markdown report
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs")
    os.makedirs(out_dir, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    md_path = os.path.join(out_dir, f"data-coverage-audit-{today}.md")

    by_status: dict[str, list] = {}
    for r in results:
        by_status.setdefault(r["status"], []).append(r)

    lines: list[str] = []
    lines.append(f"# Data coverage audit — {today}")
    lines.append("")
    lines.append(f"Comparison of local Postgres tables to Socrata source-of-truth at {datetime.now(timezone.utc).isoformat()}.")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"| Status | Count | Datasets |")
    lines.append(f"|---|---:|---|")
    order = ["ALIGNED", "MINOR_DRIFT", "LOCAL_AHEAD", "FROZEN_SOURCE", "DEFICIT", "NEVER_SYNCED", "UNKNOWN", "ERROR"]
    for status in order:
        rows = by_status.get(status, [])
        if rows:
            keys = ", ".join(sorted(r["key"] for r in rows))
            lines.append(f"| **{status}** | {len(rows)} | {keys} |")
    lines.append("")

    lines.append("## Per-dataset detail")
    lines.append("")
    lines.append("| Dataset | Tier | Local rows | Socrata rows | Diff % | Local max | Socrata max | Status |")
    lines.append("|---|---:|---:|---:|---:|---|---|---|")
    for r in sorted(results, key=lambda x: (x["tier"], x["key"])):
        local = r["local_count"] or 0
        soc = r["soc_count"] or 0
        diff = ((local - soc) / soc * 100) if soc else 0.0
        diff_s = f"{diff:+.1f}%" if soc else "n/a"
        lines.append(
            f"| `{r['key']}` | {r['tier']} | {local:,} | {soc:,} | {diff_s} | "
            f"{r['local_max'] or '—'} | {r['soc_max'] or '—'} | {r['status']} |"
        )
    lines.append("")

    for status in order:
        rows = by_status.get(status, [])
        if not rows:
            continue
        lines.append(f"## {status}")
        lines.append("")
        for r in sorted(rows, key=lambda x: x["key"]):
            lines.append(f"### `{r['key']}` (tier {r['tier']})")
            lines.append("")
            lines.append(f"- **Local**: {r['local_count']:,} rows; cursor range {r['local_min']} → {r['local_max']}; {r['local_null_cursor'] or 0:,} NULL cursor")
            lines.append(f"- **Socrata**: {r['soc_count'] or 'unknown'} rows (count_*); range {r['soc_min']} → {r['soc_max']}; metadata says {r['soc_meta_rows']}")
            lines.append(f"- **State**: cursor={r['state_cursor']}, last_success={r['state_last_success_at']}, rows_added_total={r['state_rows_added_total']}")
            if r['state_last_error']:
                lines.append(f"- **Last error**: `{r['state_last_error']}`")
            lines.append(f"- **Diagnosis**: {r['explanation']}")
            lines.append("")

    with open(md_path, "w") as f:
        f.write("\n".join(lines))
    print(f"\n# wrote {md_path}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
