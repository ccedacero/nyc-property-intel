#!/usr/bin/env python3
"""Cursor-based incremental delta sync for NYC Open Data → Postgres.

Usage:
    DATABASE_URL=postgres://...  SOCRATA_APP_TOKEN=...  \
        uv run python scripts/sync_delta.py hpd_violations [--dry-run] [--reset]

See docs/data-refresh-plan.md for the architecture.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from dataclasses import dataclass

import asyncpg
import httpx

logger = logging.getLogger("sync_delta")

# ── Dataset registry ──────────────────────────────────────────────────
# One entry per dataset. Add new tables here as we onboard them.
@dataclass(frozen=True)
class DatasetCfg:
    key: str               # internal name (matches sync_state.dataset_key)
    socrata_id: str        # Socrata 4x4 ID
    table: str             # Postgres table name
    cursor_col: str        # Column name in OUR table (used for cursor advance)
    pk_cols: tuple[str, ...]  # Columns forming the primary key for UPSERT
    tier: int              # 1 = daily, 2 = weekly, 3 = monthly+
    socrata_cursor_col: str | None = None  # source name if different (e.g. 'received_date' vs 'receiveddate')
    column_map: dict[str, str] | None = None  # source-stripped → target overrides


def _normalize_socrata_keys(row: dict, column_map: dict[str, str] | None = None) -> dict:
    """Strip underscores from Socrata column names; apply explicit overrides last.

    Strip handles the common case (received_date → receiveddate).
    column_map handles cases where local schema diverged (document_amt → docamount).
    """
    out = {}
    for k, v in row.items():
        if k.startswith(":"):
            continue
        stripped = k.replace("_", "")
        target = column_map.get(stripped, stripped) if column_map else stripped
        out[target] = v
    return out


import re
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _is_valid_date_cursor(value) -> bool:
    """Reject junk date values (e.g. 'Y9990120') and absurd future dates that
    would poison the cursor and block all future incremental syncs."""
    if not isinstance(value, str) or not _ISO_DATE_RE.match(value):
        return False
    # Reject anything more than 1 day in the future — clearly bad source data.
    from datetime import date, timedelta
    try:
        d = date.fromisoformat(value[:10])
        return d <= date.today() + timedelta(days=1)
    except ValueError:
        return False

DATASETS: dict[str, DatasetCfg] = {
    "hpd_violations": DatasetCfg(
        key="hpd_violations", socrata_id="wvxf-dwi5", table="hpd_violations",
        cursor_col="novissueddate", pk_cols=("violationid",), tier=1,
    ),
    "hpd_complaints_and_problems": DatasetCfg(
        key="hpd_complaints_and_problems", socrata_id="ygpa-z7cr",
        table="hpd_complaints_and_problems",
        cursor_col="receiveddate", pk_cols=("problemid",), tier=1,
        socrata_cursor_col="received_date",
    ),
    "hpd_litigations": DatasetCfg(
        key="hpd_litigations", socrata_id="59kj-x8nc", table="hpd_litigations",
        cursor_col="caseopendate", pk_cols=("litigationid",), tier=1,
    ),
    "dob_violations": DatasetCfg(
        key="dob_violations", socrata_id="3h2n-5cm9", table="dob_violations",
        cursor_col="issuedate", pk_cols=("isndobbisviol",), tier=1,
        socrata_cursor_col="issue_date",
    ),
    "ecb_violations": DatasetCfg(
        key="ecb_violations", socrata_id="6bgk-3dad", table="ecb_violations",
        cursor_col="issuedate", pk_cols=("ecbviolationnumber",), tier=1,
        socrata_cursor_col="issue_date",
    ),
    "marshal_evictions_all": DatasetCfg(
        key="marshal_evictions_all", socrata_id="6z8x-wfk4",
        table="marshal_evictions_all",
        cursor_col="executeddate", pk_cols=("courtindexnumber",), tier=2,
        socrata_cursor_col="executed_date",
        column_map={
            "evictionpossession": "evictionlegalpossession",
        },
    ),
    "real_property_master": DatasetCfg(
        key="real_property_master", socrata_id="bnx9-e6tj",
        table="real_property_master",
        cursor_col="modifieddate", pk_cols=("documentid",), tier=1,
        socrata_cursor_col="modified_date",
        # Source uses different short names than our local schema.
        column_map={
            "documentamt": "docamount",
            "documentdate": "docdate",
            "percenttrans": "pcttransferred",
            "recordedborough": "borough",
            "reelpg": "reelpage",
            "reelyr": "reelyear",
        },
    ),
    # dobjobs (ic3t-wcy2): local table has 1.1M duplicate rows under (job, doc)
    # because NYCDB loaded historical snapshots. Local 'id' PK is synthetic and
    # not present in source. Phase 2.6: add row_hash column + migrate to it.
    # dob_complaints: same problem. Phase 2.6.
}

# ── Tunables ──────────────────────────────────────────────────────────
PAGE_SIZE = 50_000
INTER_PAGE_SLEEP_SEC = 0.25
RETRY_BACKOFF_SEC = [5, 15, 30, 60, 120]
HTTP_TIMEOUT_SEC = 180
DRIFT_WARN_PCT = 5.0
DRIFT_ERR_PCT = 10.0


# ── Socrata I/O ───────────────────────────────────────────────────────
async def fetch_page(
    client: httpx.AsyncClient,
    socrata_id: str,
    cursor_col: str,
    cursor_value: str | None,
    offset: int = 0,
    column_map: dict[str, str] | None = None,
) -> list[dict]:
    """Fetch one page; retry with exp backoff. Raises on permanent failure.

    Two modes:
      - Backfill (cursor_value is None): paginate by $offset, no $where.
        Source doesn't need cursor_col to be indexed — fast for any column.
      - Incremental (cursor_value set): use $where on cursor_col.
        Requires the source to be indexed on cursor_col for performance.
    """
    url = f"https://data.cityofnewyork.us/resource/{socrata_id}.json"
    params: dict[str, str | int] = {
        "$limit": PAGE_SIZE,
        "$order": f"{cursor_col} ASC",
    }
    if cursor_value:
        # Normalize: PG-formatted timestamps ('2026-04-06 00:00:00') need to
        # become ISO-8601 ('2026-04-06T00:00:00') for SoQL.
        normalized = cursor_value.replace(" ", "T", 1)
        params["$where"] = f"{cursor_col} > '{normalized}'"
    else:
        params["$offset"] = offset

    for attempt, wait in enumerate([0, *RETRY_BACKOFF_SEC]):
        if wait:
            logger.warning("retrying in %ds (attempt %d)", wait, attempt)
            await asyncio.sleep(wait)
        try:
            r = await client.get(url, params=params)
            r.raise_for_status()
            rows = r.json()
            return [_normalize_socrata_keys(row, column_map) for row in rows]
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (429, 503, 504, 502):
                continue  # retry
            raise
        except (httpx.ReadError, httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError):
            continue
    raise RuntimeError(f"giving up after {len(RETRY_BACKOFF_SEC)} retries on {socrata_id}")


async def fetch_expected_rowcount(client: httpx.AsyncClient, socrata_id: str) -> int | None:
    """Pull /api/views/{id}.json metadata for drift comparison."""
    try:
        r = await client.get(f"https://data.cityofnewyork.us/api/views/{socrata_id}.json")
        r.raise_for_status()
        meta = r.json()
        # Socrata returns row count in different fields depending on dataset type
        if "rowsCount" in meta:
            return int(meta["rowsCount"])
        for col in meta.get("columns", []):
            cached = col.get("cachedContents", {})
            if "non_null" in cached:
                return int(cached["non_null"])
    except Exception as e:
        logger.warning("could not fetch expected row count: %s", e)
    return None


# ── DB ops ────────────────────────────────────────────────────────────
async def get_target_columns(conn: asyncpg.Connection, table: str) -> list[tuple[str, str, int | None]]:
    """Return ordered list of (column_name, data_type, max_length) for target table."""
    rows = await conn.fetch(
        """
        SELECT column_name, data_type, character_maximum_length
        FROM information_schema.columns
        WHERE table_name = $1 AND table_schema = 'public'
        ORDER BY ordinal_position
        """,
        table,
    )
    return [(r["column_name"], r["data_type"], r["character_maximum_length"]) for r in rows]


def _coerce(value, pg_type: str, max_len: int | None = None):
    """Convert Socrata's string value to the right Python type for asyncpg COPY.
    Respects character_maximum_length — truncates oversize strings rather than
    aborting the page. Source data sometimes has longer values than schema (e.g.
    ZIP+4 in a CHAR(5) column).
    """
    if value is None or value == "":
        return None
    s = str(value)
    try:
        if pg_type in ("integer", "smallint", "bigint"):
            return int(float(s))
        if pg_type in ("numeric", "double precision", "real"):
            return float(s)
        if pg_type == "boolean":
            return s.upper() in ("Y", "T", "TRUE", "1", "YES")
        if pg_type == "date":
            from datetime import date
            return date.fromisoformat(s.split("T", 1)[0])
        if pg_type in ("timestamp without time zone", "timestamp with time zone"):
            from datetime import datetime
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        # text, varchar, character types
        if max_len is not None and len(s) > max_len:
            return s[:max_len]
        return s
    except (ValueError, TypeError):
        return None


async def upsert_page(
    conn: asyncpg.Connection,
    cfg: DatasetCfg,
    target_cols: list[tuple[str, str, int | None]],
    rows: list[dict],
) -> int:
    """Stage page into a temp table and UPSERT into target. Returns rows affected.

    Only updates target columns that the source row actually provided. This
    preserves locally-derived columns (e.g., NYCDB extensions) that aren't
    in the Socrata API response — without this, ON CONFLICT DO UPDATE would
    NULL them out on every sync.
    """
    if not rows:
        return 0

    # Source sometimes returns duplicate PKs within a single page (e.g., when
    # records are updated multiple times since the last cursor). UPSERT can't
    # affect the same target row twice in one statement — dedupe by PK, keeping
    # the LAST occurrence (which Socrata's $order tends to deliver as the most
    # recent version for stable cursor columns).
    seen: dict[tuple, dict] = {}
    for r in rows:
        pk = tuple(r.get(c) for c in cfg.pk_cols)
        if all(v is not None for v in pk):
            seen[pk] = r
    rows = list(seen.values())
    if not rows:
        return 0

    col_names = [c for c, _, _ in target_cols]

    # Columns the source actually populated (union across all rows in this page).
    source_cols = set()
    for r in rows:
        source_cols.update(r.keys())
    updatable_cols = [c for c in col_names if c in source_cols and c not in cfg.pk_cols]

    projected = [
        tuple(_coerce(row.get(c), t, ml) for c, t, ml in target_cols) for row in rows
    ]

    async with conn.transaction():
        await conn.execute(
            f'CREATE TEMP TABLE _stage (LIKE {cfg.table} INCLUDING DEFAULTS) ON COMMIT DROP'
        )
        await conn.copy_records_to_table("_stage", records=projected, columns=col_names)

        col_list = ", ".join(f'"{c}"' for c in col_names)
        pk_list = ", ".join(f'"{c}"' for c in cfg.pk_cols)
        update_assign = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in updatable_cols)
        on_conflict = (
            f"ON CONFLICT ({pk_list}) DO UPDATE SET {update_assign}"
            if updatable_cols
            else f"ON CONFLICT ({pk_list}) DO NOTHING"
        )

        sql = (
            f'INSERT INTO "{cfg.table}" ({col_list}) '
            f"SELECT {col_list} FROM _stage "
            f"{on_conflict}"
        )
        result = await conn.execute(sql)
        affected = int(result.rsplit(" ", 1)[-1])

    return affected


async def read_state(conn: asyncpg.Connection, key: str) -> dict | None:
    row = await conn.fetchrow("SELECT * FROM sync_state WHERE dataset_key = $1", key)
    return dict(row) if row else None


async def write_state(
    conn: asyncpg.Connection,
    key: str,
    *,
    cursor_value: str | None = None,
    last_error: str | None = None,
    rows_added: int | None = None,
    expected_rows: int | None = None,
    actual_rows: int | None = None,
    success: bool = False,
) -> None:
    sets = ["last_run_at = NOW()"]
    args: list = []
    i = 1

    def add(col: str, val):
        nonlocal i
        sets.append(f"{col} = ${i}")
        args.append(val)
        i += 1

    if cursor_value is not None:
        add("cursor_value", cursor_value)
    if success:
        sets.append("last_success_at = NOW()")
        add("last_error", None)
    elif last_error is not None:
        add("last_error", last_error)
    if rows_added is not None:
        sets.append(f"rows_added_total = rows_added_total + ${i}")
        args.append(rows_added)
        i += 1
    if expected_rows is not None:
        add("expected_rows", expected_rows)
    if actual_rows is not None:
        add("actual_rows", actual_rows)

    args.append(key)
    sql = f"UPDATE sync_state SET {', '.join(sets)} WHERE dataset_key = ${i}"
    await conn.execute(sql, *args)


# ── Main sync loop ────────────────────────────────────────────────────
async def sync_dataset(cfg: DatasetCfg, *, dry_run: bool, reset: bool) -> int:
    """Returns process exit code (0 ok, 1 partial, 2 fatal)."""
    db_url = os.environ["DATABASE_URL"]
    app_token = os.environ.get("SOCRATA_APP_TOKEN", "")

    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2, command_timeout=120)
    headers = {"X-App-Token": app_token} if app_token else {}

    async with httpx.AsyncClient(headers=headers, timeout=HTTP_TIMEOUT_SEC) as client:
        # Acquire a connection only briefly for setup, then release.
        async with pool.acquire() as conn:
            state = await read_state(conn, cfg.key)
            if not state:
                logger.error("no sync_state row for %s — run migration 001/002", cfg.key)
                return 2
            if reset:
                logger.warning("--reset: clearing cursor for full backfill")
                await conn.execute(
                    "UPDATE sync_state SET cursor_value = NULL WHERE dataset_key = $1", cfg.key
                )
                state["cursor_value"] = None
            target_cols = await get_target_columns(conn, cfg.table)

        cursor_value: str | None = state["cursor_value"]
        is_backfill = cursor_value is None
        expected = await fetch_expected_rowcount(client, cfg.socrata_id)
        logger.info(
            "starting sync %s: mode=%s cursor=%s expected_total=%s",
            cfg.key, "backfill" if is_backfill else "incremental", cursor_value, expected,
        )

        total_added = 0
        page_num = 0
        offset = 0
        max_cursor_seen: str | None = cursor_value

        while True:
            page_num += 1
            t0 = time.monotonic()
            # Backfill uses $offset (always fast). Incremental uses $where on cursor_col
            # (requires source-side index on cursor_col, but only over the small delta).
            try:
                rows = await fetch_page(
                    client, cfg.socrata_id,
                    cfg.socrata_cursor_col or cfg.cursor_col,
                    cursor_value=None if is_backfill else max_cursor_seen,
                    offset=offset,
                    column_map=cfg.column_map,
                )
            except Exception as e:
                logger.exception("fatal fetch error on page %d", page_num)
                async with pool.acquire() as conn:
                    await write_state(conn, cfg.key, last_error=str(e)[:500])
                return 2

            if not rows:
                logger.info("end of stream — %d rows added across %d pages",
                            total_added, page_num - 1)
                break

            page_max = max(
                (r[cfg.cursor_col] for r in rows
                 if _is_valid_date_cursor(r.get(cfg.cursor_col))),
                default=max_cursor_seen,
            )

            if dry_run:
                logger.info("[dry-run] page %d: %d rows (max %s=%s)",
                            page_num, len(rows), cfg.cursor_col, page_max)
            else:
                try:
                    async with pool.acquire() as conn:
                        added = await upsert_page(conn, cfg, target_cols, rows)
                        # Persist cursor only in incremental mode. Backfill cursor
                        # is committed at the very end so a crashed backfill resumes
                        # via $offset, not via $where (which would skip rows).
                        if not is_backfill:
                            await write_state(
                                conn, cfg.key, cursor_value=page_max, rows_added=added,
                            )
                        else:
                            await write_state(conn, cfg.key, rows_added=added)
                except Exception as e:
                    logger.exception("fatal upsert error on page %d", page_num)
                    async with pool.acquire() as conn:
                        await write_state(conn, cfg.key, last_error=str(e)[:500])
                    return 2
                total_added += added
                logger.info("page %d: %d rows in %.1fs (max %s=%s)",
                            page_num, added, time.monotonic() - t0, cfg.cursor_col, page_max)

            max_cursor_seen = page_max
            offset += len(rows)

            if len(rows) < PAGE_SIZE:
                logger.info("partial page (%d < %d) — end of stream", len(rows), PAGE_SIZE)
                break

            await asyncio.sleep(INTER_PAGE_SLEEP_SEC)

        # ── final state + drift check ──────────────────────────────────
        async with pool.acquire() as conn:
            actual = await conn.fetchval(f'SELECT COUNT(*) FROM "{cfg.table}"')
            if not dry_run:
                # Promote backfill cursor at end so future runs are incremental.
                final_cursor = max_cursor_seen if is_backfill else None
                await write_state(
                    conn, cfg.key,
                    cursor_value=final_cursor,
                    expected_rows=expected, actual_rows=actual, success=True,
                )

        if expected and expected > 0 and actual < expected:
            missing_pct = (expected - actual) / expected * 100
            logger.info("drift check: actual=%s expected=%s (missing %.2f%%)",
                        actual, expected, missing_pct)
            if missing_pct >= DRIFT_ERR_PCT:
                logger.error("MISSING >= %.0f%% — alert needed", DRIFT_ERR_PCT)
                return 1
            if missing_pct >= DRIFT_WARN_PCT:
                logger.warning("missing >= %.0f%% — warn", DRIFT_WARN_PCT)
        else:
            logger.info("drift check: actual=%s expected=%s (ok)", actual, expected)

    await pool.close()
    return 0


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("dataset", help=f"one of: {', '.join(DATASETS)}")
    p.add_argument("--dry-run", action="store_true", help="fetch but do not write")
    p.add_argument("--reset", action="store_true", help="clear cursor for full backfill")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.dataset not in DATASETS:
        logger.error("unknown dataset: %s", args.dataset)
        sys.exit(2)

    rc = asyncio.run(sync_dataset(DATASETS[args.dataset], dry_run=args.dry_run, reset=args.reset))
    sys.exit(rc)


if __name__ == "__main__":
    main()
