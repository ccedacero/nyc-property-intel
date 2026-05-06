"""Regression tests for the magic-link activation flow.

The bug being guarded against: the `/api/activate` handler used to return
`{"ok": True}` with the plaintext token only set as an HttpOnly cookie. The
browser-side `activateMagicLink` (site/js/chat.js) checks `if (data.token)`
before promoting the user to `authState = "trial"` and clearing the email
gate. With no `token` in the response body, the frontend silently no-op'd:
the cookie was set, but the UI continued to show the email gate, and the
next query immediately re-tripped the gate — i.e. the activation link
appeared to do nothing.

These tests pin the contract:
  * a successful activation MUST return a JSON body containing the token
  * a successful activation MUST also set an HttpOnly cookie (defence in depth)
  * an invalid / used / expired magic link MUST NOT leak any token
  * malformed payloads MUST return 400 without touching the DB
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request

from nyc_property_intel import chat as chat_module
from nyc_property_intel.chat import make_chat_handlers


# ── Helpers ───────────────────────────────────────────────────────────


def _make_request(body: bytes, path: str = "/api/activate") -> Request:
    """Build a minimal Starlette Request that yields `body`."""
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [(b"content-type", b"application/json")],
        "client": ("127.0.0.1", 12345),
    }

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


class _FakePool:
    """Stand-in for asyncpg pool — records the SQL and returns a canned row."""

    def __init__(self, row: Any | None = None, raise_exc: Exception | None = None) -> None:
        self.row = row
        self.raise_exc = raise_exc
        self.calls: list[tuple[str, tuple]] = []

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        self.calls.append((sql, args))
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.row

    async def execute(self, *_args: Any, **_kwargs: Any) -> str:
        return "OK"

    async def fetchval(self, *_args: Any, **_kwargs: Any) -> Any:
        return 0


class _FakeAuth:
    """Stand-in for TokenAuth that yields a fake pool."""

    def __init__(self, pool: _FakePool) -> None:
        self._pool = pool

    async def _get_pool(self) -> _FakePool:
        return self._pool


# ── Tests ─────────────────────────────────────────────────────────────


class TestActivateHandlerReturnsToken:
    """The critical regression: response body MUST include the token."""

    async def test_success_returns_token_in_json_body(self) -> None:
        """A valid magic link returns {"ok": true, "token": "nyprop_..."}.

        This is the property the frontend depends on to set
        localStorage[nyc_pi_token] and switch authState → "trial".
        """
        plaintext = "nyprop_abcdef0123456789abcdef0123456789"
        # _decrypt_token is patched, so the encrypted_token value is opaque.
        pool = _FakePool(row={"encrypted_token": "encrypted-blob"})
        auth = _FakeAuth(pool)

        _, activate, _ = make_chat_handlers(auth)

        link_id = str(uuid.uuid4())
        body = json.dumps({"magic_token": link_id}).encode()
        request = _make_request(body)

        with patch.object(chat_module, "_decrypt_token", return_value=plaintext):
            response = await activate(request)

        assert response.status_code == 200
        payload = json.loads(response.body)
        assert payload["ok"] is True
        assert payload["token"] == plaintext, (
            "Frontend keys off data.token; without it the activation flow "
            "silently no-ops — the original production bug."
        )

    async def test_success_sets_httponly_cookie(self) -> None:
        """Defence in depth: cookie is also set so non-localStorage clients work."""
        plaintext = "nyprop_abcdef0123456789abcdef0123456789"
        pool = _FakePool(row={"encrypted_token": "blob"})
        auth = _FakeAuth(pool)
        _, activate, _ = make_chat_handlers(auth)

        request = _make_request(json.dumps({"magic_token": str(uuid.uuid4())}).encode())

        with patch.object(chat_module, "_decrypt_token", return_value=plaintext):
            response = await activate(request)

        cookie_header = response.headers.get("set-cookie", "")
        assert "nyc_pi_token=" in cookie_header
        assert plaintext in cookie_header
        assert "HttpOnly" in cookie_header
        assert "Secure" in cookie_header
        # Cross-origin Vercel → Railway requires SameSite=None.
        assert "samesite=none" in cookie_header.lower()

    async def test_atomic_consume_query_marks_used(self) -> None:
        """The SQL must atomically UPDATE used_at and only succeed once."""
        pool = _FakePool(row={"encrypted_token": "blob"})
        auth = _FakeAuth(pool)
        _, activate, _ = make_chat_handlers(auth)

        link_id = str(uuid.uuid4())
        request = _make_request(json.dumps({"magic_token": link_id}).encode())

        with patch.object(chat_module, "_decrypt_token", return_value="nyprop_x"):
            await activate(request)

        # Exactly one DB call, and it must be the atomic UPDATE...RETURNING.
        assert len(pool.calls) == 1
        sql, args = pool.calls[0]
        assert "UPDATE web_magic_links" in sql
        assert "SET used_at = NOW()" in sql
        assert "used_at IS NULL" in sql
        assert "expires_at > NOW()" in sql
        assert "RETURNING encrypted_token" in sql
        assert args == (link_id,)


class TestActivateHandlerRejectsBadInput:
    """Token must NEVER leak when the link is invalid, expired, or malformed."""

    async def test_already_used_link_returns_410_no_token(self) -> None:
        """If the UPDATE returns no row, link is used/expired — return 410."""
        pool = _FakePool(row=None)  # atomic UPDATE found nothing
        auth = _FakeAuth(pool)
        _, activate, _ = make_chat_handlers(auth)

        request = _make_request(json.dumps({"magic_token": str(uuid.uuid4())}).encode())
        response = await activate(request)

        assert response.status_code == 410
        payload = json.loads(response.body)
        assert "token" not in payload, "Expired-link response must not leak any token field"
        assert "error" in payload

    async def test_missing_magic_token_returns_400(self) -> None:
        pool = _FakePool()
        auth = _FakeAuth(pool)
        _, activate, _ = make_chat_handlers(auth)

        request = _make_request(json.dumps({}).encode())
        response = await activate(request)

        assert response.status_code == 400
        # No DB lookup should have happened.
        assert pool.calls == []

    async def test_non_uuid_magic_token_returns_400(self) -> None:
        pool = _FakePool()
        auth = _FakeAuth(pool)
        _, activate, _ = make_chat_handlers(auth)

        request = _make_request(json.dumps({"magic_token": "not-a-uuid"}).encode())
        response = await activate(request)

        assert response.status_code == 400
        # Defence: never reach the DB with an attacker-supplied non-UUID.
        assert pool.calls == []

    async def test_invalid_json_body_returns_400(self) -> None:
        pool = _FakePool()
        auth = _FakeAuth(pool)
        _, activate, _ = make_chat_handlers(auth)

        request = _make_request(b"not-json{{")
        response = await activate(request)

        assert response.status_code == 400
        assert pool.calls == []

    async def test_decrypt_failure_returns_500_no_token(self) -> None:
        """Fernet failure must not leak the encrypted blob or anything else."""
        pool = _FakePool(row={"encrypted_token": "tampered-blob"})
        auth = _FakeAuth(pool)
        _, activate, _ = make_chat_handlers(auth)

        request = _make_request(json.dumps({"magic_token": str(uuid.uuid4())}).encode())

        with patch.object(chat_module, "_decrypt_token", return_value=None):
            response = await activate(request)

        assert response.status_code == 500
        payload = json.loads(response.body)
        assert "token" not in payload

    async def test_db_error_returns_500_no_token(self) -> None:
        pool = _FakePool(raise_exc=RuntimeError("connection lost"))
        auth = _FakeAuth(pool)
        _, activate, _ = make_chat_handlers(auth)

        request = _make_request(json.dumps({"magic_token": str(uuid.uuid4())}).encode())
        response = await activate(request)

        assert response.status_code == 500
        payload = json.loads(response.body)
        assert "token" not in payload


class TestChatHandlerCookieAuth:
    """The chat handler accepts the HttpOnly cookie set by /api/activate."""

    async def test_chat_handler_reads_nyc_pi_token_cookie(self) -> None:
        """A request carrying only the cookie (no Authorization header) MUST
        be treated as authenticated. Otherwise the cookie set by /api/activate
        would be useless and we'd be back to the original symptom."""

        # Build a chat-style request with the cookie only.
        cookie_token = "nyprop_cookieabcdef0123456789abcdef0123456789"
        body = json.dumps({"messages": [{"role": "user", "content": "hi"}]}).encode()
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/chat",
            "headers": [
                (b"content-type", b"application/json"),
                (b"cookie", f"nyc_pi_token={cookie_token}".encode()),
            ],
            "client": ("127.0.0.1", 12345),
        }

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": body, "more_body": False}

        request = Request(scope, receive)

        # Stub auth: validate() returns a TokenInfo, check_rate_limit allows.
        from nyc_property_intel.auth import TokenInfo

        auth = MagicMock()
        auth.validate = AsyncMock(
            return_value=TokenInfo(
                token_hash="hash",
                token_prefix="nyprop_cookieab...",
                customer_email="user@example.com",
                plan="trial",
                daily_limit=10,
            )
        )
        auth.check_rate_limit = AsyncMock(return_value=(True, 0))
        auth._get_pool = AsyncMock(return_value=_FakePool())
        auth.record_call = AsyncMock(return_value=None)

        # We don't need the streaming body to actually run — just verify
        # validate() was called with the cookie value.
        with patch.object(chat_module, "_get_anthropic_tools", return_value=[]):
            _, _, chat_handler = make_chat_handlers(auth)
            # Reset the IP rate-limit bucket so the test is deterministic.
            chat_module._ip_buckets.clear()
            response = await chat_handler(request)

        # The handler should have validated the token from the cookie.
        auth.validate.assert_awaited_once_with(cookie_token)
        # And NOT short-circuited to a 401/402.
        assert response.status_code == 200, (
            f"Expected 200 (streaming start) for cookie-auth, got {response.status_code}"
        )
