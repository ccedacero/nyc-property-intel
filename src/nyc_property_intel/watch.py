"""Feature 1.9 — "watch this building" email alerts (the retention engine).

A user who views a report subscribes with (email, BBL). On each daily tier-1
sync, ``process_watches`` recomputes the building's open-risk snapshot and, if a
signal has *increased* since the last alert, sends one Loops transactional email
and advances the marker. Frequency-capped at one email per building per week.

The snapshot counts mirror the definitions the tools/MV expose, so "2 new open
HPD violations" in an alert matches what the user sees when they re-run the
report:
  - hpd_open    : hpd_violations.violationstatus = 'Open'
  - dob_open    : dob_violations.dispositiondate IS NULL   (MV's open proxy)
  - ecb_active  : ecb_violations.ecbviolationstatus = 'ACTIVE'
  - litigations : hpd_litigations row count (new case = signal)
"""

from __future__ import annotations

import json
import logging
import secrets

import httpx

from nyc_property_intel.config import settings
from nyc_property_intel.db import get_pool

logger = logging.getLogger(__name__)

_LOOPS_API_BASE = "https://app.loops.so/api/v1"
_SITE_BASE = "https://nycpropertyintel.com"

# Human labels for each tracked signal (singular; pluralized at format time).
_SIGNAL_LABELS = {
    "hpd_open": "open HPD violation",
    "dob_open": "open DOB violation",
    "ecb_active": "active ECB violation",
    "litigations": "HPD litigation case",
}
_SIGNAL_KEYS = tuple(_SIGNAL_LABELS)

# One query, one BBL → all four counts. Mirrors the tool/MV count definitions.
_SNAPSHOT_SQL = """
SELECT
  (SELECT COUNT(*) FROM hpd_violations
     WHERE bbl = $1 AND violationstatus = 'Open')                       AS hpd_open,
  (SELECT COUNT(*) FROM dob_violations
     WHERE bbl = $1 AND dispositiondate IS NULL)                        AS dob_open,
  (SELECT COUNT(*) FROM ecb_violations
     WHERE bbl = $1 AND upper(ecbviolationstatus) = 'ACTIVE')           AS ecb_active,
  (SELECT COUNT(*) FROM hpd_litigations WHERE bbl = $1)                 AS litigations
"""

_watch_table_ready = False


async def _ensure_watch_table(pool) -> None:
    """Idempotently provision watched_buildings once per process.

    Mirrors chat._ensure_reports_table: the mounted MCP sub-app's lifespan does
    not run under Starlette, so we can't rely on startup DDL.
    """
    global _watch_table_ready
    if _watch_table_ready:
        return
    await pool.execute(
        """
        CREATE TABLE IF NOT EXISTS watched_buildings (
            id               TEXT PRIMARY KEY,
            email            TEXT NOT NULL,
            bbl              TEXT NOT NULL,
            address          TEXT,
            baseline         JSONB NOT NULL,
            last_seen        JSONB NOT NULL,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_notified_at TIMESTAMPTZ,
            active           BOOLEAN NOT NULL DEFAULT TRUE
        )
        """
    )
    await pool.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_watched_buildings_email_bbl "
        "ON watched_buildings (email, bbl)"
    )
    await pool.execute(
        "CREATE INDEX IF NOT EXISTS idx_watched_buildings_active "
        "ON watched_buildings (bbl) WHERE active"
    )
    _watch_table_ready = True


async def snapshot_counts(pool, bbl: str) -> dict:
    """Current open-risk counts for a building. Missing building → all zeros."""
    row = await pool.fetchrow(_SNAPSHOT_SQL, bbl)
    if row is None:
        return {k: 0 for k in _SIGNAL_KEYS}
    return {k: int(row[k] or 0) for k in _SIGNAL_KEYS}


def diff_increases(prev: dict, cur: dict) -> list[str]:
    """Human-readable phrases for signals that INCREASED. Empty list = no alert.

    Decreases (closures) and flat counts never produce an alert.
    """
    out: list[str] = []
    for key, label in _SIGNAL_LABELS.items():
        delta = int(cur.get(key, 0)) - int(prev.get(key, 0))
        if delta > 0:
            out.append(f"{delta} new {label}" + ("s" if delta != 1 else ""))
    return out


async def register_watch(email: str, bbl: str, address: str | None) -> str:
    """Subscribe (email, BBL). Baseline = current snapshot, so the user is only
    alerted on changes that happen *after* they start watching. Idempotent:
    re-subscribing reactivates and refreshes the address without re-baselining.
    """
    email = email.strip().lower()
    pool = await get_pool()
    await _ensure_watch_table(pool)
    snap_json = json.dumps(await snapshot_counts(pool, bbl))
    wid = secrets.token_urlsafe(8)
    row = await pool.fetchrow(
        """
        INSERT INTO watched_buildings (id, email, bbl, address, baseline, last_seen)
        VALUES ($1, $2, $3, $4, $5::jsonb, $5::jsonb)
        ON CONFLICT (email, bbl) DO UPDATE
            SET active = TRUE,
                address = COALESCE(EXCLUDED.address, watched_buildings.address)
        RETURNING id
        """,
        wid,
        email,
        bbl,
        (address or None) and address[:160],
        snap_json,
    )
    return row["id"]


async def _send_watch_email(
    email: str, address: str | None, bbl: str, changes: list[str], report_url: str | None
) -> bool:
    """Send one Loops transactional alert. Returns True on a successful send."""
    if not settings.loops_api_key or not settings.loops_watch_transactional_id:
        logger.warning(
            "Loops watch email not configured (api_key/transactional_id) — "
            "NOT sending to %s. Changes: %s",
            email,
            changes,
        )
        return False
    payload = {
        "transactionalId": settings.loops_watch_transactional_id,
        "email": email,
        "dataVariables": {
            "address": address or f"BBL {bbl}",
            "changes": "; ".join(changes),
            "reportUrl": report_url or f"{_SITE_BASE}/chat",
        },
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{_LOOPS_API_BASE}/transactional",
                headers={
                    "Authorization": f"Bearer {settings.loops_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
    except Exception as exc:
        logger.error("watch email to %s raised: %s", email, exc)
        return False
    if not resp.is_success:
        logger.error("watch email to %s failed: %s %s", email, resp.status_code, resp.text)
        return False
    logger.info("watch alert sent to %s (%s)", email, "; ".join(changes))
    return True


# Frequency cap: at most one alert per watch per this many days.
_NOTIFY_COOLDOWN_DAYS = 7


async def process_watches(dry_run: bool = False) -> dict:
    """Diff every active watch and alert on increases. Defensive: never raises.

    Designed to run from the daily tier-1 sync, after the MV refresh, so the
    snapshot reads fresh data. Returns a stats dict for the cron log.
    """
    stats = {"checked": 0, "alerted": 0, "rebaselined": 0, "errors": 0}
    try:
        pool = await get_pool()
        await _ensure_watch_table(pool)
        rows = await pool.fetch(
            "SELECT id, email, bbl, address, last_seen, last_notified_at "
            "FROM watched_buildings WHERE active"
        )
    except Exception:
        logger.exception("process_watches: failed to load watches")
        stats["errors"] += 1
        return stats

    for row in rows:
        stats["checked"] += 1
        try:
            prev = row["last_seen"]
            if isinstance(prev, str):  # asyncpg returns jsonb as str unless a codec is set
                prev = json.loads(prev)
            cur = await snapshot_counts(pool, row["bbl"])
            changes = diff_increases(prev, cur)

            # Respect the per-watch cooldown.
            cooled_down = (
                row["last_notified_at"] is None
                or (await _past_cooldown(pool, row["last_notified_at"]))
            )

            if changes and cooled_down:
                report_url = await _latest_report_url(pool, row["bbl"])
                sent = True if dry_run else await _send_watch_email(
                    row["email"], row["address"], row["bbl"], changes, report_url
                )
                if sent:
                    stats["alerted"] += 1
                    if not dry_run:
                        await pool.execute(
                            "UPDATE watched_buildings "
                            "SET last_seen = $1::jsonb, last_notified_at = NOW() "
                            "WHERE id = $2",
                            json.dumps(cur),
                            row["id"],
                        )
                # If the send failed, leave last_seen untouched so the pending
                # change is retried on the next run.
            elif not changes:
                # Flat or decreased (closures) → re-baseline downward so a future
                # re-increase is detected, but don't reset the notify clock.
                if cur != prev and not dry_run:
                    await pool.execute(
                        "UPDATE watched_buildings SET last_seen = $1::jsonb WHERE id = $2",
                        json.dumps(cur),
                        row["id"],
                    )
                    stats["rebaselined"] += 1
            # changes-but-not-cooled-down: leave last_seen so the delta stays
            # pending and alerts once the cooldown expires.
        except Exception:
            logger.exception("process_watches: error on watch %s", row["id"])
            stats["errors"] += 1

    logger.info("process_watches done: %s", stats)
    return stats


async def _past_cooldown(pool, last_notified_at) -> bool:
    """True if last_notified_at is older than the cooldown window."""
    return bool(
        await pool.fetchval(
            "SELECT $1::timestamptz < NOW() - make_interval(days => $2)",
            last_notified_at,
            _NOTIFY_COOLDOWN_DAYS,
        )
    )


async def _latest_report_url(pool, bbl: str) -> str | None:
    """Deep-link the alert to the most recent shared report for this building,
    if one exists, so the email lands on a real page rather than the chat root.
    """
    try:
        rid = await pool.fetchval(
            "SELECT id FROM shared_reports WHERE bbl = $1 ORDER BY created_at DESC LIMIT 1",
            bbl,
        )
    except Exception:
        return None
    return f"{_SITE_BASE}/r/{rid}" if rid else None
