"""One-off manual test: send a real "watch this building" alert email to verify
end-to-end variable interpolation (feature 1.9).

Sends ONLY to the email passed as an argument. Does NOT write to the
watched_buildings table and does NOT touch any real watch — it just exercises
snapshot_counts → diff_increases → _send_watch_email with live data.

Usage (run with the backend service env so Loops creds are present):
    railway run -s nyc-property-intel uv run \
        python scripts/trigger_test_watch_alert.py <email> <bbl> ["address"]
"""

import asyncio
import sys

from nyc_property_intel.db import close_pool, get_pool
from nyc_property_intel.watch import (
    _latest_report_url,
    _send_watch_email,
    diff_increases,
    snapshot_counts,
)


async def main() -> None:
    if len(sys.argv) < 3:
        print("usage: trigger_test_watch_alert.py <email> <bbl> [address]")
        sys.exit(1)
    email = sys.argv[1]
    bbl = sys.argv[2]
    address = sys.argv[3] if len(sys.argv) > 3 else None

    pool = await get_pool()
    cur = await snapshot_counts(pool, bbl)
    # Diff against zero so every current signal reads as "new" — a rich,
    # multi-variable {{changes}} string to confirm interpolation.
    changes = diff_increases({k: 0 for k in cur}, cur)
    if not changes:
        changes = ["1 new open HPD violation"]  # guarantee a non-empty test
    report_url = await _latest_report_url(pool, bbl)

    print(f"bbl={bbl}")
    print(f"snapshot={cur}")
    print(f"changes={changes!r}")
    print(f"report_url={report_url}")
    print(f"sending to {email} ...")

    # Use a real watch row id if one exists for this email+bbl so the email's
    # unsubscribe link is live; otherwise the payload falls back gracefully.
    watch_id = await pool.fetchval(
        "SELECT id FROM watched_buildings WHERE email = $1 AND bbl = $2",
        email.lower(), bbl,
    )
    print(f"watch_id={watch_id}")

    sent = await _send_watch_email(email, address, bbl, changes, report_url, watch_id=watch_id)
    print(f"sent={sent}")
    await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
