"""Per-customer token authentication, rate limiting, and usage logging.

Design:
- Tokens are "nyprop_" + 32 random hex chars (128 bits of entropy).
- Only the SHA-256 hash of each token is stored in the database.
  The plaintext token is shown to the customer once and never persisted.
- An in-memory TTL cache (60 s) avoids a DB round-trip on every request.
  Revocation takes effect within one cache TTL window (~60 s).
- Rate limiting uses a per-token daily counter in mcp_daily_usage.
  The check is read-before-write (slight over-limit possible under
  concurrent load at the limit boundary — acceptable for MVP).
- Usage logging is fire-and-forget (asyncio.create_task) so it does
  not add latency to tool responses. Log failures are swallowed with
  a warning.

Database tables (created by `scripts/manage_tokens.py migrate`):
  mcp_tokens        — one row per issued token
  mcp_daily_usage   — daily call counter per token (for rate limiting)
  mcp_usage_log     — detailed call log (for analytics / billing)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import time
from dataclasses import dataclass

import asyncpg

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────

TOKEN_PREFIX = "nyprop_"
_CACHE_TTL = 60.0  # seconds before re-validating against DB

PLAN_LIMITS: dict[str, int] = {
    "trial": 50,
    "pro": 500,
    "team": 2000,
}

TRIAL_DAYS = 7


# ── Token generation ──────────────────────────────────────────────────

def generate_token() -> str:
    """Generate a new customer token: nyprop_ + 32 random hex chars."""
    return TOKEN_PREFIX + secrets.token_hex(16)


def hash_token(token: str) -> str:
    """Return the SHA-256 hex digest of a token."""
    return hashlib.sha256(token.encode()).hexdigest()


# ── Data model ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TokenInfo:
    token_hash: str
    token_prefix: str       # e.g. "nyprop_a1b2c3..." — for display only
    customer_email: str
    plan: str
    daily_limit: int


# ── Core auth class ───────────────────────────────────────────────────

class TokenAuth:
    """Validates customer tokens, enforces rate limits, logs usage.

    Maintains its own asyncpg pool (min=1, max=3) separate from the
    main data pool so auth queries don't compete with tool queries.
    """

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._pool: asyncpg.Pool | None = None
        # {token_hash: (TokenInfo, cached_at_monotonic)}
        self._cache: dict[str, tuple[TokenInfo, float]] = {}

    # ── Pool management ───────────────────────────────────────────────

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None or self._pool._closed:
            self._pool = await asyncpg.create_pool(
                self._database_url,
                min_size=1,
                max_size=3,
                command_timeout=5,
            )
        return self._pool

    async def close(self) -> None:
        if self._pool and not self._pool._closed:
            await self._pool.close()
            self._pool = None

    # ── Token validation ──────────────────────────────────────────────

    async def validate(self, token: str) -> TokenInfo | None:
        """Return TokenInfo if the token is valid and active, else None.

        Checks the in-memory cache first; falls back to a DB query.
        Invalid / revoked / expired tokens return None.
        """
        if not token.startswith(TOKEN_PREFIX):
            return None  # Fast reject — not our format

        token_hash = hash_token(token)

        # Cache hit?
        cached = self._cache.get(token_hash)
        if cached is not None:
            info, cached_at = cached
            if time.monotonic() - cached_at < _CACHE_TTL:
                return info
            del self._cache[token_hash]

        # DB lookup
        try:
            pool = await self._get_pool()
            row = await pool.fetchrow(
                """
                SELECT token_hash, token_prefix, customer_email, plan, daily_limit
                FROM mcp_tokens
                WHERE token_hash = $1
                  AND revoked_at IS NULL
                  AND (expires_at IS NULL OR expires_at > NOW())
                """,
                token_hash,
            )
        except Exception as exc:
            logger.error("Auth DB error during token validation: %s", exc)
            return None

        if row is None:
            return None

        info = TokenInfo(
            token_hash=row["token_hash"],
            token_prefix=row["token_prefix"],
            customer_email=row["customer_email"],
            plan=row["plan"],
            daily_limit=row["daily_limit"],
        )
        self._cache[token_hash] = (info, time.monotonic())
        return info

    def invalidate_cache(self, token_hash: str) -> None:
        """Force re-validation on next request (e.g., after revocation)."""
        self._cache.pop(token_hash, None)

    # ── Rate limiting ─────────────────────────────────────────────────

    async def check_rate_limit(self, token_hash: str, daily_limit: int) -> tuple[bool, int]:
        """Return (allowed, current_count) without modifying the counter.

        A slightly-stale read is acceptable — under concurrent load at
        the exact limit boundary a token may make 1-2 extra calls before
        the limit kicks in. This is fine for MVP.
        """
        try:
            pool = await self._get_pool()
            row = await pool.fetchrow(
                """
                SELECT call_count FROM mcp_daily_usage
                WHERE token_hash = $1 AND date = CURRENT_DATE
                """,
                token_hash,
            )
            count = row["call_count"] if row else 0
            return count < daily_limit, count
        except Exception as exc:
            logger.error("Auth DB error during rate limit check: %s", exc)
            return True, 0  # Fail open — don't block on DB errors

    # ── Usage recording ───────────────────────────────────────────────

    async def record_call(
        self,
        token_hash: str,
        tool_name: str | None,
        duration_ms: int,
        status_code: int,
    ) -> None:
        """Increment daily counter and write a log row.

        Both writes happen in a single transaction.
        Failures are logged as warnings — never raised to the caller.
        """
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(
                        """
                        INSERT INTO mcp_daily_usage (token_hash, date, call_count)
                        VALUES ($1, CURRENT_DATE, 1)
                        ON CONFLICT (token_hash, date)
                        DO UPDATE SET call_count = mcp_daily_usage.call_count + 1
                        """,
                        token_hash,
                    )
                    await conn.execute(
                        """
                        INSERT INTO mcp_usage_log
                            (token_hash, tool_name, duration_ms, status_code)
                        VALUES ($1, $2, $3, $4)
                        """,
                        token_hash,
                        tool_name,
                        duration_ms,
                        status_code,
                    )
        except Exception as exc:
            logger.warning("Failed to record usage for %s: %s", token_hash[:16], exc)
