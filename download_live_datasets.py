#!/usr/bin/env python3
"""
Downloads 311, FDNY, and NYPD datasets via SoQL pagination.
Designed to run under caffeinate in tmux for max reliability.
"""

import os
import csv
import time
import requests
import sys
from datetime import datetime

APP_TOKEN = "REDACTED"
DATA_DIR = "data"
PAGE_SIZE = 50_000
MAX_RETRIES = 5
RETRY_BACKOFF = [5, 15, 30, 60, 120]

DATASETS = [
    {
        "filename": "nypd_crime.csv",
        "dataset_id": "5uac-w243",
        "where": None,          # small, download all ~579K
        "order": "cmplnt_num",
        "est_rows": 580_000,
    },
    {
        "filename": "fdny_incidents.csv",
        "dataset_id": "8m42-w767",
        "where": "incident_datetime >= '2018-01-01T00:00:00.000'",
        "order": "incident_datetime",
        "est_rows": 3_500_000,  # rough estimate for 2018+
    },
    {
        "filename": "nyc_311.csv",
        "dataset_id": "erm2-nwe9",
        "where": "created_date >= '2021-01-01T00:00:00.000'",
        "order": "created_date",
        "est_rows": 5_500_000,  # rough estimate for 2021+
    },
]

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

def fetch_page(dataset_id, where, order, offset, retries=MAX_RETRIES):
    url = f"https://data.cityofnewyork.us/resource/{dataset_id}.json"
    params = {
        "$limit": PAGE_SIZE,
        "$offset": offset,
        "$order": order,
    }
    if where:
        params["$where"] = where

    headers = {"X-App-Token": APP_TOKEN}

    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=120)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            wait = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
            log(f"  [!] Page at offset {offset} failed (attempt {attempt+1}/{retries}): {e}")
            if attempt < retries - 1:
                log(f"      Retrying in {wait}s...")
                time.sleep(wait)
            else:
                log(f"  [!] GIVING UP on offset {offset} after {retries} attempts.")
                return None

def download_dataset(ds):
    filename = ds["filename"]
    dataset_id = ds["dataset_id"]
    where = ds.get("where")
    order = ds["order"]
    est_rows = ds["est_rows"]

    filepath = os.path.join(DATA_DIR, filename)
    tmp_path = filepath + ".tmp"

    log(f"=== Starting: {filename} ===")
    log(f"    Dataset: {dataset_id}")
    if where:
        log(f"    Filter:  {where}")
    log(f"    Est rows: ~{est_rows:,}")

    total_written = 0
    headers_written = False
    offset = 0

    try:
        with open(tmp_path, "w", newline="", encoding="utf-8") as f:
            writer = None

            while True:
                log(f"  Fetching page: offset={offset:,} ({total_written:,} rows so far)...")
                rows = fetch_page(dataset_id, where, order, offset)

                if rows is None:
                    log(f"  [!] Failed page — stopping at {total_written:,} rows.")
                    break

                if not rows:
                    log(f"  Done — no more rows.")
                    break

                # Strip Socrata computed columns (start with ':')
                rows = [{k: v for k, v in row.items() if not k.startswith(":")} for row in rows]

                if not headers_written:
                    fieldnames = list(rows[0].keys())
                    writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                    writer.writeheader()
                    headers_written = True

                writer.writerows(rows)
                total_written += len(rows)
                offset += PAGE_SIZE

                pct = (total_written / est_rows * 100) if est_rows else 0
                log(f"  Progress: {total_written:,} rows ({pct:.1f}% est)")

                if len(rows) < PAGE_SIZE:
                    log(f"  Last page — download complete.")
                    break

                # Brief pause to be kind to the API
                time.sleep(0.5)

    except KeyboardInterrupt:
        log(f"  [!] Interrupted. Partial file at {tmp_path}")
        sys.exit(1)

    # Rename tmp to final only on success
    if total_written > 0:
        if os.path.exists(filepath):
            os.remove(filepath)
        os.rename(tmp_path, filepath)
        log(f"  SUCCESS: {filename} — {total_written:,} rows written to {filepath}")
    else:
        log(f"  WARN: 0 rows written. Keeping tmp file for inspection: {tmp_path}")

    return total_written

def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    log("Starting NYC live dataset downloads...")
    log(f"Page size: {PAGE_SIZE:,} rows/request\n")

    results = {}
    for ds in DATASETS:
        count = download_dataset(ds)
        results[ds["filename"]] = count
        log("")

    log("=== Summary ===")
    for fname, count in results.items():
        log(f"  {fname}: {count:,} rows")

if __name__ == "__main__":
    main()
