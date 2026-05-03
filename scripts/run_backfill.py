#!/usr/bin/env python3
"""One-off backfill runner for the nyc-property-intel-backfill Railway service.

Reads BACKFILL_DATASETS env var (comma-separated), runs sync_delta.py --reset
for each in sequence. Bypasses sync_all.py's 1h subprocess timeout so multi-hour
backfills (FDNY, NYPD) can complete on Railway without timing out.

Always exits 0 so Railway's ON_FAILURE restart policy doesn't loop the service
on a partial failure — the per-dataset rc is logged in the summary instead.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill")


def main() -> int:
    raw = os.environ.get("BACKFILL_DATASETS", "").strip()
    if not raw:
        logger.error("BACKFILL_DATASETS env var is empty — set comma-separated dataset keys")
        return 0

    datasets = [d.strip() for d in raw.split(",") if d.strip()]
    logger.info("backfill plan (%d datasets): %s", len(datasets), datasets)

    overall_t0 = time.monotonic()
    results: list[tuple[str, int, float]] = []

    for i, key in enumerate(datasets, 1):
        logger.info("─── [%d/%d] %s --reset ───", i, len(datasets), key)
        t0 = time.monotonic()
        proc = subprocess.run(
            ["uv", "run", "python", "scripts/sync_delta.py", key, "--reset"],
            check=False,
        )
        dt = time.monotonic() - t0
        logger.info("[%d/%d] %s rc=%d in %.1fs", i, len(datasets), key, proc.returncode, dt)
        results.append((key, proc.returncode, dt))

    total = time.monotonic() - overall_t0
    logger.info("─── summary (total %.1fs / %.1f min) ───", total, total / 60)
    for key, rc, dt in results:
        status = {0: "OK", 1: "WARN"}.get(rc, f"rc={rc}")
        logger.info("  [%-4s]  %-30s  %6.1fs", status, key, dt)

    return 0


if __name__ == "__main__":
    sys.exit(main())
