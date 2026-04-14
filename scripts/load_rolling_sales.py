"""
Load DOF Rolling Sales Excel files into dof_sales table.

Downloads the 5 borough rolling-sales workbooks from the NYC DOF website,
parses them (skipping 4 header/note rows), computes a BBL from
borough+block+lot, and inserts records newer than the current max saledate
(so re-runs are safe and idempotent without needing a unique constraint).

Usage:
    uv run python scripts/load_rolling_sales.py
    DATABASE_URL=postgresql://... uv run python scripts/load_rolling_sales.py
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import os
import tempfile
from pathlib import Path

import asyncpg
import httpx
import openpyxl

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://nycdb:nycdb@localhost:5432/nycdb"
)

DOF_BASE = "https://www.nyc.gov/assets/finance/downloads/pdf/rolling_sales"
BOROUGH_FILES = {
    "1": "rollingsales_manhattan.xlsx",
    "2": "rollingsales_bronx.xlsx",
    "3": "rollingsales_brooklyn.xlsx",
    "4": "rollingsales_queens.xlsx",
    "5": "rollingsales_statenisland.xlsx",
}

# DOF rolling-sales Excel layout:
# Rows 0-3: notes/title  |  Row 4: column headers  |  Row 5+: data
DATA_START_IDX = 5

INSERT_SQL = """
INSERT INTO dof_sales (
    borough, neighborhood, buildingclasscategory, taxclassasoffinalroll,
    block, lot, easement, buildingclassasoffinalroll,
    address, apartmentnumber, zipcode,
    residentialunits, commercialunits, totalunits,
    landsquarefeet, grosssquarefeet, yearbuilt,
    taxclassattimeofsale, buildingclassattimeofsale,
    saleprice, saledate, bbl
) VALUES (
    $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,
    $11,$12,$13,$14,$15,$16,$17,$18,$19,
    $20,$21,$22
)
"""


def compute_bbl(borough: str | int, block: int, lot: int) -> str:
    return f"{int(borough)}{int(block):05d}{int(lot):04d}"


def safe_int(val) -> int | None:
    if val is None or val == "":
        return None
    try:
        return int(float(str(val).replace(",", "")))
    except (ValueError, TypeError):
        return None


def safe_str(val, max_len: int | None = None) -> str | None:
    if val is None:
        return None
    s = str(val).strip()
    s = s[:max_len] if max_len else s
    return s or None


def parse_date(raw) -> datetime.date | None:
    if isinstance(raw, datetime.datetime):
        return raw.date()
    if isinstance(raw, datetime.date):
        return raw
    if isinstance(raw, str):
        for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
            try:
                return datetime.datetime.strptime(raw.strip(), fmt).date()
            except ValueError:
                pass
    return None


def parse_workbook(path: Path, borough_num: str, cutoff: datetime.date) -> list[tuple]:
    """
    Parse a DOF rolling-sales workbook.
    Returns a list of row tuples ready for asyncpg executemany,
    filtered to only records with saledate > cutoff.
    """
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    records: list[tuple] = []
    skipped_blank = 0
    skipped_old = 0

    for row in ws.iter_rows(min_row=DATA_START_IDX + 1, values_only=True):
        if all(c is None for c in row):
            skipped_blank += 1
            continue

        block = safe_int(row[4])
        lot = safe_int(row[5])
        if block is None or lot is None:
            skipped_blank += 1
            continue

        sale_date = parse_date(row[20])
        if sale_date is None or sale_date <= cutoff:
            skipped_old += 1
            continue

        sale_price = safe_int(row[19])
        bbl = compute_bbl(borough_num, block, lot)

        records.append((
            safe_str(row[0], 1),        # borough
            safe_str(row[1]),           # neighborhood
            safe_str(row[2]),           # buildingclasscategory
            safe_str(row[3]),           # taxclassasoffinalroll
            str(block).zfill(5),        # block
            str(lot).zfill(4),          # lot
            safe_str(row[6]),           # easement
            safe_str(row[7]),           # buildingclassasoffinalroll
            safe_str(row[8]),           # address
            safe_str(row[9]),           # apartmentnumber
            safe_str(row[10], 5),       # zipcode
            safe_int(row[11]),          # residentialunits
            safe_int(row[12]),          # commercialunits
            safe_int(row[13]),          # totalunits
            safe_int(row[14]),          # landsquarefeet
            safe_int(row[15]),          # grosssquarefeet
            safe_int(row[16]),          # yearbuilt
            safe_str(row[17]),          # taxclassattimeofsale
            safe_str(row[18]),          # buildingclassattimeofsale
            sale_price,                 # saleprice
            sale_date,                  # saledate
            bbl,                        # bbl
        ))

    log.info(
        "  Parsed %d new records (skipped %d old, %d blank/bad)",
        len(records), skipped_old, skipped_blank,
    )
    return records


async def main() -> None:
    conn = await asyncpg.connect(DATABASE_URL)

    before_count = await conn.fetchval("SELECT COUNT(*) FROM dof_sales")
    cutoff: datetime.date = await conn.fetchval("SELECT MAX(saledate) FROM dof_sales")
    log.info("Current: %d rows, max saledate = %s", before_count, cutoff)
    log.info("Will insert only records with saledate > %s", cutoff)

    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        for borough_num, filename in BOROUGH_FILES.items():
            url = f"{DOF_BASE}/{filename}"
            log.info("Downloading %s ...", filename)

            resp = await client.get(url)
            resp.raise_for_status()
            log.info("  %d KB received", len(resp.content) // 1024)

            with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
                tmp.write(resp.content)
                tmp_path = Path(tmp.name)

            try:
                records = parse_workbook(tmp_path, borough_num, cutoff)
                if not records:
                    log.info("  No new records for borough %s — skipping.", borough_num)
                    continue

                log.info("  Inserting %d records for borough %s ...", len(records), borough_num)
                await conn.executemany(INSERT_SQL, records)
                log.info("  ✅ Borough %s done.", borough_num)
            finally:
                tmp_path.unlink(missing_ok=True)

    after_count = await conn.fetchval("SELECT COUNT(*) FROM dof_sales")
    max_after: datetime.date = await conn.fetchval("SELECT MAX(saledate) FROM dof_sales")

    log.info("=" * 50)
    log.info(
        "Before: %d rows | After: %d rows | Net new: +%d",
        before_count, after_count, after_count - before_count,
    )
    log.info("Max saledate: %s → %s", cutoff, max_after)
    log.info("Done ✅")

    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
