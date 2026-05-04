#!/usr/bin/env python3
"""Auto-revoke idle trial tokens that never made a real product call.

Designed for Railway Cron — runs weekly on a dedicated cron service.

Background: We saw 25 external signups in 16 days, of which 23 made zero
real API calls. This script keeps the user table clean by auto-revoking
trial tokens that have been sitting idle for > 7 days with no real usage.

Idle definition:
  - plan = 'trial'
  - revoked_at IS NULL
  - created_at < NOW() - INTERVAL '7 days'
  - No row in mcp_usage_log with tool_name IS NOT NULL.
    (NULL tool_name rows are MCP `initialize`/`list_tools` handshakes —
     they don't count as real product usage.)

Internal accounts are excluded — anything ending in @nycpropertyintel.com
plus an explicit allowlist for personal test accounts.

Usage:
    DATABASE_URL=postgres://... uv run python scripts/cleanup_idle_tokens.py
    DATABASE_URL=postgres://... uv run python scripts/cleanup_idle_tokens.py --dry-run

Exit code: always 0 (don't crash-loop the cron on transient DB errors —
log and move on; alerting handles failure visibility).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

import asyncpg

logger = logging.getLogger("cleanup_idle_tokens")

# ── Tunables ──────────────────────────────────────────────────────────
IDLE_DAYS = 7
REVOKE_NOTE = "auto-revoked: 7d idle no real calls"

# ── Internal accounts — never auto-revoke even if idle ────────────────
# Hard-coded domain check: anything @nycpropertyintel.com is staff/QA.
INTERNAL_DOMAIN = "@nycpropertyintel.com"

# Explicit allowlist for personal test accounts on outside domains.
INTERNAL_EMAIL_ALLOWLIST: frozenset[str] = frozenset({
    "qa@nycpropertyintel.com",
    "qa+verify-50@nycpropertyintel.com",
    "dev-internal@nycpropertyintel.com",
    "cristiancedacero@gmail.com",
    "devtzitest@gmail.com",
    "launchhero.test@gmail.com",
})


def is_internal_email(email: str) -> bool:
    """Return True if email is on the never-auto-revoke allowlist.

    The check is case-insensitive; emails are normalised to lowercase.
    Domain check covers all @nycpropertyintel.com addresses (incl. plus-tags
    we haven't enumerated). The allowlist covers personal accounts on outside
    domains used for live-fire testing.
    """
    if not email:
        return False
    e = email.strip().lower()
    if e.endswith(INTERNAL_DOMAIN):
        return True
    return e in INTERNAL_EMAIL_ALLOWLIST


# ── Core query ────────────────────────────────────────────────────────
# Find idle trial tokens. A token is "idle" if:
#   - plan = 'trial'
#   - revoked_at IS NULL
#   - created_at < NOW() - INTERVAL '<IDLE_DAYS> days'
#   - No row in mcp_usage_log with tool_name IS NOT NULL for this token_hash.
#
# We use NOT EXISTS on the usage table — token_hash is indexed
# (mcp_usage_log_token_date), so this stays fast even as the log grows.
#
# Returned columns include the days-idle calc so we can log it cleanly.
SELECT_IDLE_TOKENS_SQL = """
    SELECT
        t.token_hash,
        t.token_prefix,
        t.customer_email,
        t.created_at,
        t.notes,
        EXTRACT(DAY FROM (NOW() - t.created_at))::int AS days_idle
    FROM mcp_tokens t
    WHERE t.plan = 'trial'
      AND t.revoked_at IS NULL
      AND t.created_at < NOW() - make_interval(days => $1)
      AND NOT EXISTS (
          SELECT 1
          FROM mcp_usage_log u
          WHERE u.token_hash = t.token_hash
            AND u.tool_name IS NOT NULL
      )
    ORDER BY t.created_at ASC
"""


# Append the auto-revoke note to existing notes (preserves operator notes).
# Using COALESCE + concatenation so we don't smash an existing notes string.
REVOKE_TOKEN_SQL = """
    UPDATE mcp_tokens
    SET revoked_at = NOW(),
        notes = CASE
            WHEN notes IS NULL OR notes = '' THEN $2
            ELSE notes || E'\n' || $2
        END
    WHERE token_hash = $1
      AND revoked_at IS NULL
"""


# ── Main ──────────────────────────────────────────────────────────────
async def cleanup_idle_tokens(*, dry_run: bool) -> int:
    """Find idle trial tokens, revoke them (unless dry-run). Returns count revoked."""
    db_url = os.environ.get("RAILWAY_DB") or os.environ.get("DATABASE_URL")
    if not db_url:
        logger.error("neither RAILWAY_DB nor DATABASE_URL is set — nothing to do")
        return 0

    try:
        pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2, command_timeout=30)
    except Exception as e:
        logger.error("could not connect to DB: %s", e)
        return 0

    revoked = 0
    skipped_internal = 0
    try:
        async with pool.acquire() as conn:
            try:
                rows = await conn.fetch(SELECT_IDLE_TOKENS_SQL, IDLE_DAYS)
            except Exception as e:
                logger.exception("query for idle tokens failed: %s", e)
                return 0

            logger.info(
                "found %d idle trial token(s) older than %d days with no real calls",
                len(rows), IDLE_DAYS,
            )

            for r in rows:
                email = r["customer_email"] or ""
                days = r["days_idle"]
                prefix = r["token_prefix"]

                if is_internal_email(email):
                    skipped_internal += 1
                    logger.info(
                        "SKIP internal: %s (prefix=%s, idle=%dd)",
                        email, prefix, days,
                    )
                    continue

                if dry_run:
                    logger.info(
                        "[dry-run] WOULD REVOKE: %s (prefix=%s, idle=%dd)",
                        email, prefix, days,
                    )
                    revoked += 1
                    continue

                try:
                    result = await conn.execute(
                        REVOKE_TOKEN_SQL, r["token_hash"], REVOKE_NOTE,
                    )
                    affected = int(result.rsplit(" ", 1)[-1]) if result else 0
                    if affected:
                        revoked += 1
                        logger.info(
                            "REVOKED: %s (prefix=%s, idle=%dd)",
                            email, prefix, days,
                        )
                    else:
                        # Race: someone else revoked between SELECT and UPDATE.
                        logger.info(
                            "no-op: %s already revoked between query and update",
                            email,
                        )
                except Exception as e:
                    # One bad row shouldn't kill the whole pass.
                    logger.exception(
                        "failed to revoke %s (prefix=%s): %s", email, prefix, e,
                    )
                    continue
    finally:
        await pool.close()

    logger.info(
        "summary: revoked=%d skipped_internal=%d dry_run=%s",
        revoked, skipped_internal, dry_run,
    )
    return revoked


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--dry-run", action="store_true",
        help="print what would be revoked without modifying the DB",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    try:
        asyncio.run(cleanup_idle_tokens(dry_run=args.dry_run))
    except Exception as e:
        # Belt-and-suspenders: top-level catch so the cron exits 0 no matter what.
        # Transient DB errors shouldn't trigger Railway's restart policy and
        # crash-loop the cron service.
        logger.exception("unexpected error in cleanup pass: %s", e)

    sys.exit(0)


if __name__ == "__main__":
    main()
