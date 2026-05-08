"""Security-layer unit tests.

Tests:
  - ``escape_like`` in ``nyc_property_intel.utils`` — LIKE metacharacter escaping.
  - ``_TokenAuthMiddleware`` in ``nyc_property_intel.server`` — ASGI bearer
    token enforcement (delegating to a fake TokenAuth).

No real database connections required — TokenAuth is faked.
"""

from __future__ import annotations

import pytest
import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from nyc_property_intel.auth import TokenInfo, hash_token
from nyc_property_intel.utils import escape_like
from nyc_property_intel.server import _TokenAuthMiddleware


# ── escape_like ───────────────────────────────────────────────────────


class TestEscapeLike:
    """Tests for ``escape_like`` — LIKE/ILIKE metacharacter escaping."""

    def test_plain_string_unchanged(self):
        assert escape_like("hello") == "hello"

    def test_percent_escaped(self):
        assert escape_like("50%") == "50\\%"

    def test_underscore_escaped(self):
        assert escape_like("_foo") == "\\_foo"

    def test_both_percent_and_underscore_escaped(self):
        assert escape_like("a%b_c") == "a\\%b\\_c"

    def test_backslash_escaped_first(self):
        # Backslash must be replaced before % and _ so it is not double-escaped.
        assert escape_like("back\\slash") == "back\\\\slash"

    def test_multiple_percents(self):
        assert escape_like("foo%bar%") == "foo\\%bar\\%"

    def test_empty_string_unchanged(self):
        assert escape_like("") == ""

    def test_hyphen_unchanged(self):
        assert escape_like("normal-text") == "normal-text"

    def test_backslash_then_percent(self):
        # "\\%" → the backslash is escaped to "\\\\", then "%" to "\\%"
        assert escape_like("\\%") == "\\\\\\%"

    def test_only_underscores(self):
        assert escape_like("___") == "\\_\\_\\_"


# ── _TokenAuthMiddleware ──────────────────────────────────────────────

# A minimal inner ASGI app used as the protected target.
_TEST_TOKEN = "nyprop_super_secret_token_123"


async def _ok_endpoint(request: Request) -> PlainTextResponse:
    return PlainTextResponse("OK")


_inner_app = Starlette(
    routes=[
        Route("/", _ok_endpoint, methods=["GET", "POST"]),
        Route("/ping", _ok_endpoint, methods=["GET", "POST"]),
    ]
)


class _FakeTokenAuth:
    """Minimal fake of TokenAuth for middleware unit tests.

    Validates exactly one token (``_TEST_TOKEN``) and always allows it
    (no rate-limiting, no DB writes). Mimics the public surface of
    ``TokenAuth`` that ``_TokenAuthMiddleware`` actually calls.
    """

    def __init__(self, valid_token: str = _TEST_TOKEN) -> None:
        self._valid_token = valid_token
        self._info = TokenInfo(
            token_hash=hash_token(valid_token),
            token_prefix=valid_token[:15] + "...",
            customer_email="test@example.com",
            plan="trial",
            daily_limit=10,
        )

    async def validate(self, token: str):
        if token == self._valid_token:
            return self._info
        return None

    async def check_rate_limit(self, token_hash: str, daily_limit: int):
        return True, 0

    async def record_call(self, token_hash, tool_name, duration_ms, status_code):
        return None


_protected_app = _TokenAuthMiddleware(_inner_app, _FakeTokenAuth())


@pytest.mark.asyncio
class TestBearerTokenMiddleware:
    """Tests for ``_TokenAuthMiddleware`` using httpx + ASGITransport."""

    async def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_protected_app),
            base_url="http://testserver",
        )

    async def test_no_auth_header_returns_401(self):
        async with await self._client() as client:
            # Use POST: GET requests are intentionally allowed through
            # unauthenticated (used by Claude Code for SSE discovery).
            response = await client.post("/")
        assert response.status_code == 401

    async def test_wrong_token_returns_401(self):
        async with await self._client() as client:
            response = await client.post(
                "/", headers={"Authorization": "Bearer wrong-token"}
            )
        assert response.status_code == 401

    async def test_correct_token_returns_200(self):
        async with await self._client() as client:
            response = await client.post(
                "/", headers={"Authorization": f"Bearer {_TEST_TOKEN}"}
            )
        assert response.status_code == 200

    async def test_correct_token_body_is_ok(self):
        async with await self._client() as client:
            response = await client.post(
                "/", headers={"Authorization": f"Bearer {_TEST_TOKEN}"}
            )
        assert response.text == "OK"

    async def test_401_body_is_json_with_error_key(self):
        async with await self._client() as client:
            response = await client.post("/")
        body = response.json()
        assert "error" in body

    async def test_401_content_type_is_json(self):
        async with await self._client() as client:
            response = await client.post("/")
        assert "application/json" in response.headers.get("content-type", "")

    async def test_get_passes_through_without_auth(self):
        """GET is allowed unauthenticated — used by Claude Code SSE discovery."""
        async with await self._client() as client:
            response = await client.get("/")
        assert response.status_code == 200

    async def test_lifespan_scope_passes_through(self):
        """Non-HTTP/websocket scopes (e.g. lifespan) must not be auth-gated."""
        received: list[dict] = []

        async def inner_app(scope, receive, send):
            received.append(scope)

        middleware = _TokenAuthMiddleware(inner_app, _FakeTokenAuth())
        lifespan_scope = {"type": "lifespan"}
        await middleware(lifespan_scope, None, None)  # type: ignore[arg-type]
        assert received == [lifespan_scope]
