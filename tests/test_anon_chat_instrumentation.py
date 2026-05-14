"""Regression tests for anonymous chat instrumentation (anon_chat_queries).

Background — see `docs/usage-tracking-audit-2026-05-06.md`. Anonymous chat
requests (the 3 free queries before the email gate) used to leave zero
server-side trace; the only signal was a signed cookie on the visitor's
client. Migration `011_anon_chat_queries.sql` introduced a lightweight log
table and `chat.py` now inserts one row per anon request.

These tests pin the contract:

  * a successful anon request inserts exactly one row into anon_chat_queries
  * the row's ip_hash is sha256(ip || secret)[:32] — never the raw IP
  * a missing IP results in an empty hash and does NOT crash the handler
  * a DB failure during the insert is swallowed — chat still succeeds
  * the auth-only `record_call` write for tokenised users is unchanged

Existing test files (chat activation, etc.) must keep passing — these tests
mock the DB pool the same way `test_chat_activation.py` does.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from starlette.requests import Request

from nyc_property_intel import chat as chat_module
from nyc_property_intel.chat import (
    _hash_ip,
    _record_anon_chat_query,
    make_chat_handlers,
    make_session_cookie,
)
from nyc_property_intel.config import settings


# ── Helpers ───────────────────────────────────────────────────────────


def _make_request(
    body: bytes,
    path: str = "/api/chat",
    headers: list[tuple[bytes, bytes]] | None = None,
    cookies: dict[str, str] | None = None,
    client: tuple[str, int] | None = ("203.0.113.42", 12345),
) -> Request:
    """Build a minimal Starlette Request that yields `body`."""
    hdrs: list[tuple[bytes, bytes]] = [(b"content-type", b"application/json")]
    if headers:
        hdrs.extend(headers)
    if cookies:
        cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items()).encode()
        hdrs.append((b"cookie", cookie_str))
    scope: dict[str, Any] = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": hdrs,
        "client": client,
    }

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


class _RecordingPool:
    """Stand-in for asyncpg pool that records every execute() call."""

    def __init__(self, raise_on_execute: Exception | None = None) -> None:
        self.executes: list[tuple[str, tuple]] = []
        self.raise_on_execute = raise_on_execute

    async def execute(self, sql: str, *args: Any, **_kwargs: Any) -> str:
        self.executes.append((sql, args))
        if self.raise_on_execute is not None:
            raise self.raise_on_execute
        return "OK"

    async def fetchrow(self, *_a: Any, **_kw: Any) -> Any:
        return None

    async def fetchval(self, *_a: Any, **_kw: Any) -> Any:
        return 0


class _FakeAuth:
    """Stand-in for TokenAuth used by the chat handler."""

    def __init__(self, pool: _RecordingPool) -> None:
        self._pool = pool
        self.record_call_calls: list[tuple] = []

    async def _get_pool(self) -> _RecordingPool:
        return self._pool

    async def validate(self, _token: str) -> Any:
        return None  # all tests in this module exercise the anon path

    async def check_rate_limit(self, *_a: Any, **_kw: Any) -> tuple[bool, int]:
        return True, 0

    async def record_call(self, *args: Any, **_kw: Any) -> None:
        self.record_call_calls.append(args)


async def _drain_streaming_response(response: Any) -> str:
    """Iterate the StreamingResponse body so its post-stream `finally`
    blocks (and the trailing instrumentation code) actually execute."""
    chunks: list[str] = []
    body_iter = response.body_iterator
    async for raw in body_iter:
        if isinstance(raw, bytes):
            chunks.append(raw.decode())
        else:
            chunks.append(str(raw))
    return "".join(chunks)


async def _fake_agentic_stream(_messages: list[dict]):
    """Yield a single `done` SSE chunk so the chat handler's stream wrapper
    runs to completion without making real Anthropic calls."""
    yield f"data: {json.dumps({'type': 'done'})}\n\n"


# ── Pure-function tests ───────────────────────────────────────────────


class TestHashIp:
    def test_empty_ip_returns_empty_hash(self) -> None:
        assert _hash_ip("") == ""

    def test_hash_is_32_hex_chars(self) -> None:
        h = _hash_ip("1.2.3.4")
        assert len(h) == 32
        assert all(c in "0123456789abcdef" for c in h)

    def test_hash_uses_secret_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "anon_ip_hash_secret", "fixed-test-secret")
        # Reset module-level fallback so the test's secret is the only thing
        # mixed in (safety: no crossover from earlier tests).
        monkeypatch.setattr(chat_module, "_ANON_IP_HASH_FALLBACK", None)
        expected = hashlib.sha256(b"1.2.3.4fixed-test-secret").hexdigest()[:32]
        assert _hash_ip("1.2.3.4") == expected

    def test_same_ip_same_hash_within_process(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "anon_ip_hash_secret", "")
        monkeypatch.setattr(chat_module, "_ANON_IP_HASH_FALLBACK", None)
        a = _hash_ip("203.0.113.7")
        b = _hash_ip("203.0.113.7")
        assert a == b
        # Different IP → different hash (with overwhelming probability).
        assert _hash_ip("203.0.113.8") != a


class TestRecordAnonChatQuery:
    async def test_inserts_a_row(self) -> None:
        pool = _RecordingPool()
        await _record_anon_chat_query(pool, "deadbeef" * 4, query_count=2)

        assert len(pool.executes) == 1
        sql, args = pool.executes[0]
        assert "INSERT INTO anon_chat_queries" in sql
        assert "ip_hash" in sql and "query_count" in sql
        assert args[0] == "deadbeef" * 4
        assert args[2] == 2  # query_count

    async def test_empty_ip_hash_inserts_null(self) -> None:
        pool = _RecordingPool()
        await _record_anon_chat_query(pool, "", query_count=1)

        sql, args = pool.executes[0]
        # First positional arg becomes None when ip_hash is empty.
        assert args[0] is None
        assert args[2] == 1

    async def test_db_failure_is_swallowed(self) -> None:
        pool = _RecordingPool(raise_on_execute=RuntimeError("db down"))
        # MUST NOT raise.
        await _record_anon_chat_query(pool, "abc", query_count=1)
        assert len(pool.executes) == 1  # was attempted

    async def test_pool_none_is_noop(self) -> None:
        # When the auth pool can't be acquired we pass None — the function
        # must accept this without raising.
        await _record_anon_chat_query(None, "abc", query_count=1)


# ── End-to-end handler tests ──────────────────────────────────────────


class TestChatHandlerAnonPath:
    """The /api/chat endpoint, taken from a fresh anonymous visitor."""

    async def test_anon_query_inserts_row(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An anon query → one INSERT into anon_chat_queries."""
        pool = _RecordingPool()
        auth = _FakeAuth(pool)
        _, _, chat_handler = make_chat_handlers(auth)

        # Pin the secret so the resulting ip_hash is deterministic.
        monkeypatch.setattr(settings, "anon_ip_hash_secret", "test-secret")
        monkeypatch.setattr(chat_module, "_ANON_IP_HASH_FALLBACK", None)
        monkeypatch.setattr(chat_module, "_agentic_stream", _fake_agentic_stream)

        # NOTE: Must be a property-related query so the regex pre-flight
        # in chat._classify_intent doesn't short-circuit it as gibberish.
        # Bare "hello" / "hi" never reaches the agentic loop anymore.
        body = json.dumps(
            {"messages": [{"role": "user", "content": "350 5th Ave Manhattan"}]}
        ).encode()
        request = _make_request(body)
        response = await chat_handler(request)
        await _drain_streaming_response(response)

        # Allow asyncio.create_task() background work to complete.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        # Filter out any unrelated executes (the anon path only does the
        # one INSERT we care about; auth.record_call is mocked).
        anon_inserts = [
            (sql, args) for sql, args in pool.executes
            if "anon_chat_queries" in sql
        ]
        assert len(anon_inserts) == 1, (
            f"Expected exactly one anon_chat_queries insert, got "
            f"{len(anon_inserts)}: {pool.executes}"
        )
        sql, args = anon_inserts[0]
        # ip_hash present + correct shape.
        assert args[0] is not None
        assert len(args[0]) == 32
        # query_count = 1 because the visitor's cookie is empty (q=0 + 1).
        assert args[2] == 1
        # And the existing record_call path for tokenised users was NOT
        # touched — this is the anon branch.
        assert auth.record_call_calls == []

    async def test_missing_ip_does_not_crash(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No client + no XFF/Fastly headers → ip_hash empty, but request
        must still succeed."""
        pool = _RecordingPool()
        auth = _FakeAuth(pool)
        _, _, chat_handler = make_chat_handlers(auth)

        monkeypatch.setattr(chat_module, "_agentic_stream", _fake_agentic_stream)

        # Property-related query to pass the regex pre-flight (see note above).
        body = json.dumps(
            {"messages": [{"role": "user", "content": "350 5th Ave Manhattan"}]}
        ).encode()
        # client=None forces request.client to be None, simulating a totally
        # missing client identity.
        request = _make_request(body, client=None)
        response = await chat_handler(request)
        text = await _drain_streaming_response(response)

        await asyncio.sleep(0)
        await asyncio.sleep(0)

        # Stream finished cleanly.
        assert "done" in text

        anon_inserts = [
            args for sql, args in pool.executes
            if "anon_chat_queries" in sql
        ]
        # Either no row (because client_ip resolved to 'unknown' and we
        # short-circuited) or one row with NULL ip_hash. Both are
        # acceptable; what matters is no crash and the handler returned 200.
        assert response.status_code == 200
        if anon_inserts:
            assert anon_inserts[0][0] is None  # ip_hash arg is None

    async def test_db_error_does_not_break_chat(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the anon_chat_queries INSERT raises, the chat response must
        still complete normally — observability failures are non-fatal."""
        pool = _RecordingPool(raise_on_execute=RuntimeError("table missing"))
        auth = _FakeAuth(pool)
        _, _, chat_handler = make_chat_handlers(auth)

        monkeypatch.setattr(chat_module, "_agentic_stream", _fake_agentic_stream)

        body = json.dumps(
            {"messages": [{"role": "user", "content": "x"}]}
        ).encode()
        request = _make_request(body)
        # No exception should escape.
        response = await chat_handler(request)
        text = await _drain_streaming_response(response)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert response.status_code == 200
        assert "done" in text
        # The session cookie still got set — the anon flow is fully intact.
        cookie_header = response.headers.get("set-cookie", "")
        assert "nyprop_sess=" in cookie_header

    async def test_free_limit_reached_does_not_record(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the visitor has already used their free queries the handler
        returns 402 BEFORE invoking the LLM — and we should NOT log a row,
        since no product call was actually served. Read the limit from
        settings dynamically so the test stays correct on future tuning."""
        pool = _RecordingPool()
        auth = _FakeAuth(pool)
        _, _, chat_handler = make_chat_handlers(auth)

        monkeypatch.setattr(settings, "cookie_secret", "test-cookie-secret")
        cookie_val = make_session_cookie(
            query_count=settings.chat_free_query_limit, analyze_count=1
        )

        body = json.dumps(
            {"messages": [{"role": "user", "content": "x"}]}
        ).encode()
        request = _make_request(body, cookies={"nyprop_sess": cookie_val})

        response = await chat_handler(request)
        await asyncio.sleep(0)

        assert response.status_code == 402
        anon_inserts = [
            args for sql, args in pool.executes
            if "anon_chat_queries" in sql
        ]
        assert anon_inserts == [], (
            "The free-limit gate must short-circuit BEFORE any anon "
            "tracking row is written — otherwise the email-gate page would "
            "register fake traffic on every retry."
        )
