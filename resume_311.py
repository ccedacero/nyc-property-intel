#!/usr/bin/env python3
"""
Auto-resuming 311 downloader. Counts rows already in file and picks up from there.
Retries indefinitely with long waits when the API goes unresponsive.
"""

import os, csv, time, requests, subprocess
from datetime import datetime

APP_TOKEN  = os.environ["SOCRATA_APP_TOKEN"]
DATASET_ID = "erm2-nwe9"
WHERE      = "created_date >= '2021-01-01T00:00:00.000'"
ORDER      = "created_date"
PAGE_SIZE  = 50_000
TOTAL_EXPECTED = 17_859_512
OUTFILE    = "data/nyc_311.csv"

def log(m):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {m}", flush=True)

def count_local_rows(path):
    if not os.path.exists(path):
        return 0
    try:
        out = subprocess.check_output(["wc", "-l", path]).decode()
        # subtract 1 for header, but if file was appended without header, just count lines
        return max(0, int(out.strip().split()[0]) - 1)
    except:
        return 0

def fetch_page(offset):
    url    = f"https://data.cityofnewyork.us/resource/{DATASET_ID}.json"
    params = {"$limit": PAGE_SIZE, "$offset": offset, "$order": ORDER, "$where": WHERE}
    # Escalating waits — keep trying forever until we get a page
    waits = [15, 30, 60, 120, 240, 300]
    attempt = 0
    while True:
        try:
            r = requests.get(url, params=params,
                             headers={"X-App-Token": APP_TOKEN}, timeout=180)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            wait = waits[min(attempt, len(waits) - 1)]
            log(f"  [!] Attempt {attempt+1} failed at offset {offset:,}: {e}")
            log(f"      Sleeping {wait}s then retrying...")
            time.sleep(wait)
            attempt += 1

def main():
    local_rows = count_local_rows(OUTFILE)
    # Round down to nearest page boundary so we don't corrupt mid-page
    resume_offset = (local_rows // PAGE_SIZE) * PAGE_SIZE
    log(f"File has ~{local_rows:,} rows → resuming from offset {resume_offset:,}")
    log(f"Target: {TOTAL_EXPECTED:,} | Remaining: ~{TOTAL_EXPECTED - resume_offset:,} rows")

    appended = 0
    offset   = resume_offset

    with open(OUTFILE, "a", newline="", encoding="utf-8") as f:
        writer = None
        while offset < TOTAL_EXPECTED + PAGE_SIZE:
            log(f"  Fetching offset={offset:,}  (file total ~{resume_offset + appended:,})...")
            rows = fetch_page(offset)

            if not rows:
                log("  No rows returned — download complete.")
                break

            rows = [{k: v for k, v in row.items() if not k.startswith(":")} for row in rows]

            if writer is None:
                writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()), extrasaction="ignore")

            writer.writerows(rows)
            f.flush()
            appended += len(rows)
            offset   += PAGE_SIZE

            total_so_far = resume_offset + appended
            pct = total_so_far / TOTAL_EXPECTED * 100
            log(f"  Progress: {total_so_far:,} / {TOTAL_EXPECTED:,} ({pct:.1f}%)")

            if len(rows) < PAGE_SIZE:
                log("  Last page — done.")
                break

            time.sleep(1)

    log(f"Finished. Appended {appended:,} rows. Final file: ~{resume_offset + appended:,} rows.")

if __name__ == "__main__":
    main()
