"""Tests for scripts/cleanup_idle_tokens.py.

Two layers:
  - Pure-unit tests for the internal-email allowlist and the SQL constants.
    These run anywhere — no DB required.
  - Integration tests (gated by @pytest.mark.integration) that spin up a
    synthetic mcp_tokens / mcp_usage_log dataset in the local Postgres,
    run the cleanup pass, and assert which rows ended up revoked.

Run unit-only:
    uv run pytest tests/test_cleanup_idle_tokens.py -v

Run integration too (needs local Postgres):
    DATABASE_URL=postgresql://nycdb:nycdb@localhost:5432/nycdb \\
        uv run pytest tests/test_cleanup_idle_tokens.py -m integration -v
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest

# scripts/ isn't a package — use the same sys.path trick the existing
# tests use for modules under scripts/ (see tests/test_coerce.py, which
# imports from scripts.sync_delta via project-level pytest config).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from cleanup_idle_tokens import (  # noqa: E402
    IDLE_DAYS,
    INTERNAL_DOMAIN,
    INTERNAL_EMAIL_ALLOWLIST,
    REVOKE_NOTE,
    REVOKE_TOKEN_SQL,
    SELECT_IDLE_TOKENS_SQL,
    cleanup_idle_tokens,
    is_internal_email,
)


# ── Unit: is_internal_email ───────────────────────────────────────────


class TestIsInternalEmail:
    """Internal-account allowlist must reject auto-revoke."""

    @pytest.mark.parametrize("email", [
        "qa@nycpropertyintel.com",
        "qa+verify-50@nycpropertyintel.com",
        "dev-internal@nycpropertyintel.com",
        "cristiancedacero@gmail.com",
        "devtzitest@gmail.com",
        "launchhero.test@gmail.com",
    ])
    def test_explicit_allowlist(self, email: str) -> None:
        assert is_internal_email(email) is True

    @pytest.mark.parametrize("email", [
        "anyone@nycpropertyintel.com",
        "future-employee@nycpropertyintel.com",
        "qa+brand-new-tag@nycpropertyintel.com",
    ])
    def test_internal_domain_catches_everything(self, email: str) -> None:
        """The @nycpropertyintel.com domain check catches addresses we
        haven't explicitly enumerated (plus-tags etc)."""
        assert is_internal_email(email) is True

    def test_case_insensitive(self) -> None:
        assert is_internal_email("QA@NycPropertyIntel.com") is True
        assert is_internal_email("CristianCedacero@Gmail.com") is True

    def test_strips_whitespace(self) -> None:
        assert is_internal_email("  qa@nycpropertyintel.com  ") is True

    @pytest.mark.parametrize("email", [
        "external@example.com",
        "random@gmail.com",
        "user@company.net",
        "scammer@nycpropertyintel.com.evil.tld",  # not really our domain
    ])
    def test_external_emails_not_internal(self, email: str) -> None:
        assert is_internal_email(email) is False

    def test_empty_or_none(self) -> None:
        assert is_internal_email("") is False
        assert is_internal_email(None) is False  # type: ignore[arg-type]

    def test_constants_sane(self) -> None:
        # Catch any accidental edits that break the contract.
        assert INTERNAL_DOMAIN == "@nycpropertyintel.com"
        assert "qa@nycpropertyintel.com" in INTERNAL_EMAIL_ALLOWLIST
        assert "cristiancedacero@gmail.com" in INTERNAL_EMAIL_ALLOWLIST
        assert IDLE_DAYS == 21


# ── Unit: SQL constants are well-formed ───────────────────────────────


class TestSqlShape:
    """Cheap structural checks on the SQL strings — catches typos."""

    def test_select_query_filters(self) -> None:
        s = SELECT_IDLE_TOKENS_SQL
        assert "FROM mcp_tokens" in s
        assert "plan = 'trial'" in s
        assert "revoked_at IS NULL" in s
        # Must use a parameter for the idle threshold.
        assert "$1" in s
        # Critical: NULL tool_name rows (init/list_tools handshakes) must NOT
        # rescue a token from idle status. We only count rows with a real
        # tool_name as "real product use".
        assert "tool_name IS NOT NULL" in s
        assert "NOT EXISTS" in s

    def test_revoke_query_preserves_notes(self) -> None:
        s = REVOKE_TOKEN_SQL
        assert "revoked_at = NOW()" in s
        # Existing notes column must be preserved, not overwritten.
        # Either CASE or COALESCE pattern is acceptable; both branches
        # keep prior notes intact.
        assert "notes" in s
        assert "$2" in s
        # Idempotency guard.
        assert "revoked_at IS NULL" in s

    def test_revoke_note_is_descriptive(self) -> None:
        assert "auto-revoked" in REVOKE_NOTE
        assert "idle" in REVOKE_NOTE


# ── Integration: synthetic dataset against the real local DB ──────────
#
# These tests build a small synthetic scenario in the live local Postgres
# (the same one the integration test suite already uses) and run the
# cleanup_idle_tokens coroutine end-to-end. We use a unique email suffix
# per test run so multiple runs don't collide and so we can clean up
# precisely without disturbing real tokens.


@pytest.fixture
async def synthetic_tokens(request, monkeypatch):
    """Insert a fixed synthetic dataset of tokens + usage rows; yield labels.

    Yields a dict mapping a human-readable label to the token_hash so tests
    can assert on individual rows. Tears down everything it inserted on exit.

    SAFETY: clears RAILWAY_DB from the test process env so cleanup_idle_tokens
    can never accidentally hit the production Railway DB. Tests must run
    against DATABASE_URL only (defaulted to the local nycdb).
    """
    import asyncpg

    # Critical: cleanup_idle_tokens picks RAILWAY_DB before DATABASE_URL.
    # Drop it for the duration of the test to force local-DB use.
    monkeypatch.delenv("RAILWAY_DB", raising=False)

    db_url = os.environ.get("DATABASE_URL", "postgresql://nycdb:nycdb@localhost:5432/nycdb")
    # Defensive: if DATABASE_URL points to a remote/Railway host, refuse to run
    # the destructive integration tests. Local Postgres only.
    if "rlwy" in db_url or "railway" in db_url.lower():
        pytest.skip("DATABASE_URL points to a remote DB — refusing to run destructive integration tests")
    suffix = f"cleanup-test-{uuid.uuid4().hex[:8]}"
    email_suffix = f"+{suffix}@example.com"

    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2, command_timeout=10)
    inserted_hashes: list[str] = []

    def mk_hash(label: str) -> str:
        return f"hash-{suffix}-{label}"

    now = datetime.now(timezone.utc)

    rows: dict[str, dict] = {
        # SHOULD be revoked: 25-day-old trial, no usage at all.
        "stale_no_usage": {
            "token_hash": mk_hash("stale_no_usage"),
            "email": f"stale_no_usage{email_suffix}",
            "plan": "trial",
            "created_at": now - timedelta(days=25),
            "revoked_at": None,
            "notes": "external signup",
        },
        # SHOULD be revoked: 25d trial, only init/list_tools (NULL tool_name).
        "stale_only_handshakes": {
            "token_hash": mk_hash("stale_only_handshakes"),
            "email": f"stale_only_handshakes{email_suffix}",
            "plan": "trial",
            "created_at": now - timedelta(days=25),
            "revoked_at": None,
            "notes": None,
        },
        # SHOULD NOT be revoked: 25d old but made a real call.
        "stale_real_usage": {
            "token_hash": mk_hash("stale_real_usage"),
            "email": f"stale_real_usage{email_suffix}",
            "plan": "trial",
            "created_at": now - timedelta(days=25),
            "revoked_at": None,
            "notes": None,
        },
        # SHOULD NOT be revoked: only 3 days old (under threshold).
        "fresh_no_usage": {
            "token_hash": mk_hash("fresh_no_usage"),
            "email": f"fresh_no_usage{email_suffix}",
            "plan": "trial",
            "created_at": now - timedelta(days=3),
            "revoked_at": None,
            "notes": None,
        },
        # SHOULD NOT be revoked: pro plan, not trial.
        "stale_pro_no_usage": {
            "token_hash": mk_hash("stale_pro_no_usage"),
            "email": f"stale_pro_no_usage{email_suffix}",
            "plan": "pro",
            "created_at": now - timedelta(days=30),
            "revoked_at": None,
            "notes": None,
        },
        # SHOULD NOT be revoked: already revoked.
        "already_revoked": {
            "token_hash": mk_hash("already_revoked"),
            "email": f"already_revoked{email_suffix}",
            "plan": "trial",
            "created_at": now - timedelta(days=20),
            "revoked_at": now - timedelta(days=5),
            "notes": "manually revoked earlier",
        },
        # SHOULD NOT be revoked: internal domain, even with no usage + old.
        "stale_internal_domain": {
            "token_hash": mk_hash("stale_internal_domain"),
            "email": "qa+verify-50@nycpropertyintel.com",
            "plan": "trial",
            "created_at": now - timedelta(days=25),
            "revoked_at": None,
            "notes": None,
        },
        # SHOULD NOT be revoked: explicit allowlist external email.
        "stale_allowlisted": {
            "token_hash": mk_hash("stale_allowlisted"),
            "email": "cristiancedacero@gmail.com",
            "plan": "trial",
            "created_at": now - timedelta(days=25),
            "revoked_at": None,
            "notes": None,
        },
    }

    async with pool.acquire() as conn:
        # Skip if auth tables aren't present in this DB (e.g. clean nycdb).
        exists = await conn.fetchval(
            "SELECT to_regclass('public.mcp_tokens')"
        )
        if not exists:
            await pool.close()
            pytest.skip("mcp_tokens table not present in local DB — run scripts/manage_tokens.py migrate first")

        for label, r in rows.items():
            await conn.execute(
                """
                INSERT INTO mcp_tokens
                    (token_hash, token_prefix, customer_email, plan, daily_limit,
                     created_at, expires_at, revoked_at, notes)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                """,
                r["token_hash"],
                r["token_hash"][:15] + "...",
                r["email"],
                r["plan"],
                50,
                r["created_at"],
                None,
                r["revoked_at"],
                r["notes"],
            )
            inserted_hashes.append(r["token_hash"])

        # One real product call for stale_real_usage (tool_name IS NOT NULL).
        await conn.execute(
            """
            INSERT INTO mcp_usage_log (token_hash, tool_name, called_at, status_code)
            VALUES ($1, $2, $3, $4)
            """,
            rows["stale_real_usage"]["token_hash"],
            "lookup_property",
            now - timedelta(days=2),
            200,
        )
        # Handshake-only rows for stale_only_handshakes (tool_name = NULL).
        await conn.execute(
            """
            INSERT INTO mcp_usage_log (token_hash, tool_name, called_at, status_code)
            VALUES ($1, NULL, $2, 200), ($1, NULL, $3, 200)
            """,
            rows["stale_only_handshakes"]["token_hash"],
            now - timedelta(days=8),
            now - timedelta(days=4),
        )

    try:
        yield {"rows": rows, "suffix": suffix, "pool": pool}
    finally:
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM mcp_usage_log WHERE token_hash = ANY($1::text[])",
                inserted_hashes,
            )
            await conn.execute(
                "DELETE FROM mcp_tokens WHERE token_hash = ANY($1::text[])",
                inserted_hashes,
            )
        await pool.close()


@pytest.mark.integration
async def test_dry_run_does_not_modify(synthetic_tokens):
    """--dry-run must not change revoked_at on any row."""
    rows = synthetic_tokens["rows"]
    pool = synthetic_tokens["pool"]

    # Snapshot revoked_at before
    before = {}
    async with pool.acquire() as conn:
        # Sanity: confirm the fixture committed all 8 rows.
        all_count = await conn.fetchval(
            "SELECT COUNT(*) FROM mcp_tokens WHERE token_hash = ANY($1::text[])",
            [r["token_hash"] for r in rows.values()],
        )
        assert all_count == len(rows), f"fixture didn't insert all rows: {all_count}/{len(rows)}"
        for label, r in rows.items():
            before[label] = await conn.fetchval(
                "SELECT revoked_at FROM mcp_tokens WHERE token_hash = $1",
                r["token_hash"],
            )

    await cleanup_idle_tokens(dry_run=True)

    # Snapshot after — every value must be identical to before.
    async with pool.acquire() as conn:
        for label, r in rows.items():
            after = await conn.fetchval(
                "SELECT revoked_at FROM mcp_tokens WHERE token_hash = $1",
                r["token_hash"],
            )
            assert after == before[label], f"dry-run modified {label}"


@pytest.mark.integration
async def test_real_run_revokes_only_idle_external_trials(synthetic_tokens):
    """A real run must revoke exactly the rows we expect — and no others."""
    rows = synthetic_tokens["rows"]
    pool = synthetic_tokens["pool"]

    await cleanup_idle_tokens(dry_run=False)

    expected_revoked = {"stale_no_usage", "stale_only_handshakes"}
    expected_untouched = {
        "stale_real_usage",
        "fresh_no_usage",
        "stale_pro_no_usage",
        "already_revoked",
        "stale_internal_domain",
        "stale_allowlisted",
    }

    async with pool.acquire() as conn:
        for label in expected_revoked:
            r = rows[label]
            row = await conn.fetchrow(
                "SELECT revoked_at, notes FROM mcp_tokens WHERE token_hash = $1",
                r["token_hash"],
            )
            assert row["revoked_at"] is not None, f"{label} should be revoked"
            assert REVOKE_NOTE in (row["notes"] or ""), (
                f"{label} should have auto-revoke note appended; got {row['notes']!r}"
            )
            # Pre-existing note (if any) must be preserved.
            if r["notes"]:
                assert r["notes"] in row["notes"], (
                    f"{label}: existing note clobbered. Was {r['notes']!r}, now {row['notes']!r}"
                )

        for label in expected_untouched:
            r = rows[label]
            row = await conn.fetchrow(
                "SELECT revoked_at FROM mcp_tokens WHERE token_hash = $1",
                r["token_hash"],
            )
            if r["revoked_at"] is None:
                assert row["revoked_at"] is None, (
                    f"{label} should NOT be revoked but is. "
                    "Likely the filter logic is wrong."
                )
            else:
                # already_revoked: timestamp must not have moved.
                assert row["revoked_at"] == r["revoked_at"], (
                    f"{label} revoked_at was overwritten"
                )
