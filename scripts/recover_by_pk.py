#!/usr/bin/env python3
"""One-off recovery: backfill rows missing due to Socrata $offset pagination ties.

Some Socrata datasets have many rows sharing the same cursor-column value
(e.g. hpd_registrations has 141K rows with `lastregistrationdate` in 2025).
Standard `$order=cursor&$offset=N` pagination silently drops chunks when
page boundaries fall mid-tie. The result is a stable ~5% gap that even a
`--reset` backfill can't close.

This script works around it for any dataset with a single-column unique PK:
  1. Pull ALL primary-key values from Socrata via `$order=<pk>&$select=<pk>`
     (paginated; the PK is monotonic + tie-free, so $offset enumerates cleanly).
  2. Diff against local table.
  3. Fetch full rows for missing PKs in chunks via `$where=<pk> IN (...)`.
  4. UPSERT into local table.

Usage:
    RAILWAY_DB=... uv run python scripts/recover_by_pk.py <dataset_key>

Only datasets with single-column PKs are supported. hpd_registrations
(`registrationid`) is the canonical target.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

import asyncpg
import httpx

from sync_delta import (  # type: ignore[import-not-found]
    DATASETS,
    _coerce,
    _normalize_socrata_keys,
    get_target_columns,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("recover_by_pk")

PAGE_SIZE = 50_000
ID_CHUNK = 200          # IDs per $where IN (...) call
HTTP_TIMEOUT = 60


async def fetch_all_pks(client: httpx.AsyncClient, socrata_id: str, pk: str) -> set[str]:
    """Enumerate all PK values from Socrata using PK-ordered pagination."""
    pks: set[str] = set()
    offset = 0
    while True:
        url = f"https://data.cityofnewyork.us/resource/{socrata_id}.json"
        params = {"$select": pk, "$order": pk, "$offset": offset, "$limit": PAGE_SIZE}
        r = await client.get(url, params=params, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        rows = r.json()
        if not rows:
            break
        for row in rows:
            v = row.get(pk)
            if v is not None:
                pks.add(str(v))
        logger.info("fetched %d PKs (cumulative %d)", len(rows), len(pks))
        if len(rows) < PAGE_SIZE:
            break
        offset += len(rows)
    return pks


async def fetch_rows_by_id(
    client: httpx.AsyncClient, socrata_id: str, pk: str, ids: list[str]
) -> list[dict]:
    """Fetch full rows for a list of PK values via $where IN (...)."""
    rows: list[dict] = []
    for i in range(0, len(ids), ID_CHUNK):
        chunk = ids[i : i + ID_CHUNK]
        # Quote each ID — Socrata expects single quotes for string PKs.
        in_clause = ",".join(f"'{x}'" for x in chunk)
        url = f"https://data.cityofnewyork.us/resource/{socrata_id}.json"
        params = {"$where": f"{pk} IN ({in_clause})", "$limit": ID_CHUNK}
        r = await client.get(url, params=params, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        rows.extend(r.json())
        logger.info("fetched chunk %d/%d (%d rows so far)",
                    i // ID_CHUNK + 1, (len(ids) + ID_CHUNK - 1) // ID_CHUNK, len(rows))
    return rows


async def main(dataset_key: str) -> int:
    if dataset_key not in DATASETS:
        logger.error("unknown dataset: %s", dataset_key)
        return 2
    cfg = DATASETS[dataset_key]
    if len(cfg.pk_cols) != 1:
        logger.error("recovery requires a single-column PK; %s has %s",
                     dataset_key, cfg.pk_cols)
        return 2

    db_url = os.environ.get("RAILWAY_DB") or os.environ.get("DATABASE_URL")
    if not db_url:
        logger.error("set RAILWAY_DB or DATABASE_URL")
        return 2

    pk = cfg.pk_cols[0]
    table = cfg.table
    socrata_pk = pk  # Same name in source unless mapped (none of our targets need this)

    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2, command_timeout=600)
    async with httpx.AsyncClient() as client:
        logger.info("step 1: enumerate all Socrata PKs for %s", cfg.socrata_id)
        socrata_pks = await fetch_all_pks(client, cfg.socrata_id, socrata_pk)
        logger.info("Socrata enumerated PKs: %d", len(socrata_pks))

        logger.info("step 2: pull local PKs")
        async with pool.acquire() as conn:
            local_rows = await conn.fetch(f'SELECT "{pk}" FROM {table}')
        local_pks = {str(r[pk]) for r in local_rows if r[pk] is not None}
        logger.info("local PKs: %d", len(local_pks))

        missing = sorted(socrata_pks - local_pks)
        logger.info("missing PKs to recover: %d", len(missing))

        if not missing:
            logger.info("nothing to recover — local already has every Socrata PK")
            await pool.close()
            return 0

        logger.info("step 3: fetch full rows for missing PKs")
        raw_rows = await fetch_rows_by_id(client, cfg.socrata_id, socrata_pk, missing)
        logger.info("fetched %d full rows", len(raw_rows))

        if not raw_rows:
            logger.warning("Socrata returned 0 rows for missing PKs — possibly a $where issue")
            await pool.close()
            return 1

        logger.info("step 4: UPSERT into %s", table)
        async with pool.acquire() as conn:
            target_cols = await get_target_columns(conn, table)
            col_names = [c for c, _, _ in target_cols]

            # Normalize each row to local schema, then coerce by pg_type
            projected = []
            for r in raw_rows:
                norm = _normalize_socrata_keys(r, cfg.column_map)
                projected.append(
                    tuple(_coerce(norm.get(c), t, ml) for c, t, ml in target_cols)
                )

            # Drop rows where PK coerces to None
            pk_idx = col_names.index(pk)
            kept = [t for t in projected if t[pk_idx] is not None]
            if len(kept) < len(projected):
                logger.warning("dropped %d rows with NULL PK after coercion",
                               len(projected) - len(kept))

            async with conn.transaction():
                await conn.execute(
                    f'CREATE TEMP TABLE _stage (LIKE {table} INCLUDING DEFAULTS) ON COMMIT DROP'
                )
                await conn.copy_records_to_table("_stage", records=kept, columns=col_names)

                col_list = ", ".join(f'"{c}"' for c in col_names)
                update_assign = ", ".join(
                    f'"{c}" = EXCLUDED."{c}"' for c in col_names if c != pk
                )
                await conn.execute(
                    f'INSERT INTO {table} ({col_list}) '
                    f'SELECT {col_list} FROM _stage '
                    f'ON CONFLICT ("{pk}") DO UPDATE SET {update_assign}'
                )
            logger.info("UPSERT complete: %d rows applied", len(kept))

    await pool.close()
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    sys.exit(asyncio.run(main(sys.argv[1])))
