#!/usr/bin/env python3
"""Admin CLI — manage NYC Property Intel MCP customer tokens.

Commands:
  migrate          Create auth tables in the database (safe to re-run)
  create           Issue a new token for a customer
  revoke           Revoke all active tokens for a customer email
  list             List all tokens with status and usage
  stats            Show daily usage breakdown

Usage:
  uv run python scripts/manage_tokens.py migrate
  uv run python scripts/manage_tokens.py create --email user@example.com --plan pro
  uv run python scripts/manage_tokens.py create --email user@example.com --plan trial
  uv run python scripts/manage_tokens.py revoke --email user@example.com
  uv run python scripts/manage_tokens.py list
  uv run python scripts/manage_tokens.py stats
  uv run python scripts/manage_tokens.py stats --email user@example.com

Environment variables:
  DATABASE_URL   PostgreSQL connection string (defaults to local nycdb)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

import asyncpg

# ── Import token utilities from the main package ─────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from nyc_property_intel.auth import PLAN_LIMITS, TRIAL_DAYS, generate_token, hash_token

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://nycdb:nycdb@localhost:5432/nycdb"
)

_RAILWAY_PUBLIC_URL = "https://nyc-property-intel-production.up.railway.app/mcp"


# ── DB helpers ────────────────────────────────────────────────────────

async def get_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=3, command_timeout=10)


# ── Commands ──────────────────────────────────────────────────────────

async def cmd_migrate(pool: asyncpg.Pool) -> None:
    """Create auth tables (idempotent — safe to re-run)."""
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS mcp_tokens (
            token_hash      TEXT        PRIMARY KEY,
            token_prefix    TEXT        NOT NULL,
            customer_email  TEXT        NOT NULL,
            plan            TEXT        NOT NULL DEFAULT 'trial',
            daily_limit     INTEGER     NOT NULL DEFAULT 50,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            expires_at      TIMESTAMPTZ,
            revoked_at      TIMESTAMPTZ,
            notes           TEXT
        );

        CREATE TABLE IF NOT EXISTS mcp_daily_usage (
            token_hash  TEXT    NOT NULL REFERENCES mcp_tokens(token_hash),
            date        DATE    NOT NULL DEFAULT CURRENT_DATE,
            call_count  INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (token_hash, date)
        );

        CREATE TABLE IF NOT EXISTS mcp_usage_log (
            id          BIGSERIAL   PRIMARY KEY,
            token_hash  TEXT        NOT NULL REFERENCES mcp_tokens(token_hash),
            tool_name   TEXT,
            called_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            duration_ms INTEGER,
            status_code INTEGER     NOT NULL DEFAULT 200
        );

        CREATE INDEX IF NOT EXISTS mcp_usage_log_token_date
            ON mcp_usage_log(token_hash, called_at DESC);

        CREATE INDEX IF NOT EXISTS mcp_tokens_email
            ON mcp_tokens(customer_email);
    """)
    print("✓ Auth tables created / verified.")


async def cmd_create(
    pool: asyncpg.Pool,
    email: str,
    plan: str,
    notes: str,
) -> None:
    """Issue a new token for a customer."""
    if plan not in PLAN_LIMITS:
        print(f"✗ Invalid plan '{plan}'. Choose from: {', '.join(PLAN_LIMITS)}")
        sys.exit(1)

    token = generate_token()
    token_hash = hash_token(token)
    token_prefix = token[:15] + "..."   # "nyprop_a1b2c3d..." — safe to store/display
    daily_limit = PLAN_LIMITS[plan]

    expires_at: datetime | None = None
    if plan == "trial":
        expires_at = datetime.now(timezone.utc) + timedelta(days=TRIAL_DAYS)

    # Prevent accidental duplicates for the same email on the same plan
    existing = await pool.fetchval(
        "SELECT COUNT(*) FROM mcp_tokens WHERE customer_email = $1 AND revoked_at IS NULL",
        email,
    )
    if existing:
        print(f"⚠  {email} already has {existing} active token(s).")
        confirm = input("   Issue another? [y/N] ").strip().lower()
        if confirm != "y":
            print("Aborted.")
            return

    await pool.execute(
        """
        INSERT INTO mcp_tokens
            (token_hash, token_prefix, customer_email, plan, daily_limit, expires_at, notes)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        token_hash, token_prefix, email, plan, daily_limit, expires_at, notes,
    )

    print(f"\n✓ Token issued for {email}")
    print(f"  Plan       : {plan}  ({daily_limit} calls/day)")
    if expires_at:
        print(f"  Expires    : {expires_at.strftime('%Y-%m-%d')} ({TRIAL_DAYS}-day trial)")
    print()
    print("  ┌─ TOKEN — show to customer once, then discard ─────────────────┐")
    print(f"  │  {token}")
    print("  └────────────────────────────────────────────────────────────────┘")
    print()
    print("  Customer setup (copy-paste ready):")
    print(f"    claude mcp add --transport http nyc-property-intel \\")
    print(f'      "{_RAILWAY_PUBLIC_URL}" \\')
    print(f'      --header "Authorization: Bearer {token}" \\')
    print(f"      --scope user")
    print()


async def cmd_revoke(pool: asyncpg.Pool, email: str) -> None:
    """Revoke all active tokens for a customer."""
    result = await pool.execute(
        """
        UPDATE mcp_tokens
        SET revoked_at = NOW()
        WHERE customer_email = $1 AND revoked_at IS NULL
        """,
        email,
    )
    count = int(result.split()[-1])
    if count == 0:
        print(f"  No active tokens found for {email}")
    else:
        print(f"✓ Revoked {count} token(s) for {email}")
        print("  Note: cached tokens are invalidated within ~60 seconds on the server.")


async def cmd_list(pool: asyncpg.Pool) -> None:
    """List all tokens with current status and usage."""
    rows = await pool.fetch(
        """
        SELECT
            t.token_prefix,
            t.customer_email,
            t.plan,
            t.daily_limit,
            t.created_at,
            t.expires_at,
            t.revoked_at,
            COALESCE(SUM(u.call_count), 0)::int AS total_calls,
            MAX(u.date)                          AS last_active
        FROM mcp_tokens t
        LEFT JOIN mcp_daily_usage u ON u.token_hash = t.token_hash
        GROUP BY t.token_hash, t.token_prefix, t.customer_email, t.plan,
                 t.daily_limit, t.created_at, t.expires_at, t.revoked_at
        ORDER BY t.created_at DESC
        """
    )

    if not rows:
        print("No tokens found. Run `migrate` first, then `create`.")
        return

    now = datetime.now(timezone.utc)
    header = f"{'Token':22} {'Email':30} {'Plan':7} {'Limit/d':8} {'Calls':7} {'Last Active':12} Status"
    print(f"\n{header}")
    print("─" * len(header))

    for r in rows:
        if r["revoked_at"]:
            status = "revoked"
        elif r["expires_at"] and r["expires_at"].replace(tzinfo=timezone.utc) < now:
            status = "expired"
        else:
            status = "active"

        last = r["last_active"].strftime("%Y-%m-%d") if r["last_active"] else "never"
        print(
            f"{r['token_prefix']:22} {r['customer_email']:30} {r['plan']:7} "
            f"{r['daily_limit']:<8} {r['total_calls']:<7} {last:12} {status}"
        )
    print()


async def cmd_stats(pool: asyncpg.Pool, email: str | None) -> None:
    """Show daily usage breakdown, optionally filtered by email."""
    if email:
        rows = await pool.fetch(
            """
            SELECT t.customer_email, t.plan, u.date, u.call_count
            FROM mcp_tokens t
            JOIN mcp_daily_usage u ON u.token_hash = t.token_hash
            WHERE t.customer_email = $1
            ORDER BY u.date DESC
            LIMIT 30
            """,
            email,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT t.customer_email, t.plan, u.date, u.call_count
            FROM mcp_tokens t
            JOIN mcp_daily_usage u ON u.token_hash = t.token_hash
            ORDER BY u.date DESC, u.call_count DESC
            LIMIT 60
            """
        )

    if not rows:
        print("No usage data yet.")
        return

    print(f"\n{'Date':12} {'Email':30} {'Plan':8} {'Calls':>6}")
    print("─" * 60)
    for r in rows:
        print(
            f"{str(r['date']):12} {r['customer_email']:30} {r['plan']:8} {r['call_count']:>6}"
        )
    print()


# ── CLI entry point ───────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="NYC Property Intel — token management CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("migrate", help="Create auth tables (idempotent)")

    p_create = sub.add_parser("create", help="Issue a new token")
    p_create.add_argument("--email", required=True, help="Customer email")
    p_create.add_argument(
        "--plan",
        required=True,
        choices=list(PLAN_LIMITS),
        help=f"Subscription plan: {PLAN_LIMITS}",
    )
    p_create.add_argument("--notes", default="", help="Internal notes (optional)")

    p_revoke = sub.add_parser("revoke", help="Revoke tokens for a customer")
    p_revoke.add_argument("--email", required=True, help="Customer email")

    sub.add_parser("list", help="List all tokens")

    p_stats = sub.add_parser("stats", help="Show usage stats")
    p_stats.add_argument("--email", default=None, help="Filter by customer email")

    return parser


async def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    pool = await get_pool()
    try:
        if args.command == "migrate":
            await cmd_migrate(pool)
        elif args.command == "create":
            await cmd_create(pool, args.email, args.plan, args.notes)
        elif args.command == "revoke":
            await cmd_revoke(pool, args.email)
        elif args.command == "list":
            await cmd_list(pool)
        elif args.command == "stats":
            await cmd_stats(pool, getattr(args, "email", None))
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
