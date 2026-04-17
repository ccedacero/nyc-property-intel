"""Async database layer backed by asyncpg.

Provides a lazily-initialized connection pool, helper query functions,
and JSON-safe row serialization. Pool lifecycle is tied to the MCP
server lifespan so connections are cleaned up on shutdown.

Usage from tool modules:

    from nyc_property_intel.db import fetch_one, fetch_all

    row = await fetch_one("SELECT * FROM properties WHERE bbl = $1", bbl)
    rows = await fetch_all("SELECT * FROM violations WHERE bbl = $1", bbl)
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import signal
from contextlib import asynccontextmanager, suppress
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import asyncpg
from mcp.server.fastmcp.exceptions import ToolError

from nyc_property_intel.config import settings


def _redact_dsn(dsn: str) -> str:
    """Return the DSN with the password replaced by ****."""
    try:
        from urllib.parse import urlparse, urlunparse
        parsed = urlparse(dsn)
        if parsed.password:
            netloc = f"{parsed.username}:****@{parsed.hostname}"
            if parsed.port:
                netloc += f":{parsed.port}"
            return urlunparse(parsed._replace(netloc=netloc))
    except Exception:
        pass
    return "<redacted>"

logger = logging.getLogger(__name__)

# ── Module-level pool reference ───────────────────────────────────────
_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    """Return the connection pool, creating it lazily on first call.

    The pool is configured with conservative defaults suitable for an
    MCP server that handles one user at a time but may run several
    concurrent tool calls within a single request.
    """
    global _pool
    if _pool is None or _pool._closed:
        logger.info("Creating asyncpg connection pool → %s", _redact_dsn(settings.database_url))
        _pool = await asyncpg.create_pool(
            dsn=settings.database_url,
            min_size=1,
            max_size=10,
            command_timeout=30,
        )
        logger.info("Connection pool ready (min=1, max=10)")
    return _pool


async def close_pool() -> None:
    """Gracefully close the connection pool if it exists."""
    global _pool
    if _pool is not None and not _pool._closed:
        logger.info("Closing asyncpg connection pool")
        await _pool.close()
        _pool = None


# ── Row serialization ─────────────────────────────────────────────────

def row_to_dict(record: asyncpg.Record | None) -> dict[str, Any] | None:
    """Convert an asyncpg Record to a plain dict with JSON-safe values.

    Handles the types that commonly appear in NYC property data:
    - datetime / date  → ISO-8601 string
    - Decimal          → float (safe for display; not for accounting math)
    - UUID             → string
    - bytes            → hex string
    - timedelta        → total seconds (float)
    - None             → None (passed through)
    """
    if record is None:
        return None
    result: dict[str, Any] = {}
    for key, value in record.items():
        result[key] = _serialize_value(value)
    return result


def _serialize_value(value: Any) -> Any:
    """Recursively serialize a single value to a JSON-compatible type."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, list):
        return [_serialize_value(item) for item in value]
    return value


# ── Query helpers ─────────────────────────────────────────────────────

async def fetch_one(
    query: str,
    *args: Any,
) -> dict[str, Any] | None:
    """Execute a query and return the first row as a dict, or None.

    Args:
        query: SQL query with $1, $2, ... placeholders.
        *args: Positional parameters bound to $1, $2, ...

    Returns:
        A JSON-safe dict of column→value, or None if no row matched.
    """
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            record = await conn.fetchrow(query, *args)
            return row_to_dict(record)
    except asyncpg.UndefinedTableError:
        raise  # Let callers handle missing tables via graceful degradation.
    except asyncpg.TooManyConnectionsError as exc:
        logger.error("Connection pool exhausted in fetch_one: %s", exc)
        raise ToolError(
            "Database is under heavy load. Please try again in a moment."
        ) from exc
    except asyncpg.PostgresConnectionError as exc:
        logger.error("Database connection error in fetch_one: %s", exc)
        raise ToolError(
            "Unable to connect to the property database. "
            "Please check that PostgreSQL is running and try again."
        ) from exc
    except asyncpg.InterfaceError as exc:
        logger.error("Database interface error in fetch_one: %s", exc)
        raise ToolError(
            "Lost connection to the property database. Please try again."
        ) from exc
    except asyncpg.PostgresError as exc:
        logger.error(
            "Database error in fetch_one: %s (sqlstate=%s)",
            exc,
            getattr(exc, "sqlstate", "unknown"),
        )
        raise ToolError("A database error occurred. Please try again.") from exc


async def fetch_all(
    query: str,
    *args: Any,
) -> list[dict[str, Any]]:
    """Execute a query and return all rows as a list of dicts.

    Args:
        query: SQL query with $1, $2, ... placeholders.
        *args: Positional parameters bound to $1, $2, ...

    Returns:
        A list of JSON-safe dicts. Empty list if no rows matched.
    """
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            records = await conn.fetch(query, *args)
            return [row_to_dict(r) for r in records]
    except asyncpg.UndefinedTableError:
        raise  # Let callers handle missing tables via graceful degradation.
    except asyncpg.TooManyConnectionsError as exc:
        logger.error("Connection pool exhausted in fetch_all: %s", exc)
        raise ToolError(
            "Database is under heavy load. Please try again in a moment."
        ) from exc
    except asyncpg.PostgresConnectionError as exc:
        logger.error("Database connection error in fetch_all: %s", exc)
        raise ToolError(
            "Unable to connect to the property database. "
            "Please check that PostgreSQL is running and try again."
        ) from exc
    except asyncpg.InterfaceError as exc:
        logger.error("Database interface error in fetch_all: %s", exc)
        raise ToolError(
            "Lost connection to the property database. Please try again."
        ) from exc
    except asyncpg.PostgresError as exc:
        logger.error(
            "Database error in fetch_all: %s (sqlstate=%s)",
            exc,
            getattr(exc, "sqlstate", "unknown"),
        )
        raise ToolError("A database error occurred. Please try again.") from exc


# ── Lifespan for FastMCP ──────────────────────────────────────────────

@asynccontextmanager
async def db_lifespan(server: Any):
    """Async context manager that FastMCP calls on startup/shutdown.

    Usage in server.py:
        mcp.settings.lifespan = db_lifespan
    """
    logger.info("MCP lifespan: starting up")
    await get_pool()
    try:
        yield
    finally:
        logger.info("MCP lifespan: shutting down")
        await close_pool()


# ── Cleanup safety nets ──────────────────────────────────────────────

def _sync_cleanup() -> None:
    """Best-effort synchronous cleanup for atexit / signal handlers."""
    if _pool is not None and not _pool._closed:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(close_pool())
        except RuntimeError:
            # No running event loop — server already shut down cleanly.
            pass


atexit.register(_sync_cleanup)

# Handle SIGTERM gracefully (e.g., Docker stop, Railway shutdown).
# signal.signal can fail in non-main threads or restricted environments.
with suppress(OSError, ValueError):
    signal.signal(signal.SIGTERM, lambda *_: _sync_cleanup())
