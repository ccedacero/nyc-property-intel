"""Security-layer unit tests.

Tests:
  - ``escape_like`` in ``nyc_property_intel.utils`` — LIKE metacharacter escaping.
  - ``_BearerTokenMiddleware`` in ``nyc_property_intel.server`` — ASGI bearer
    token enforcement.

No database connections required.
"""

from __future__ import annotations

import pytest
import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from nyc_property_intel.utils import escape_like
from nyc_property_intel.server import _BearerTokenMiddleware


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


# ── _BearerTokenMiddleware ────────────────────────────────────────────

# A minimal inner ASGI app used as the protected target.
_TEST_TOKEN = "super-secret-token-123"


async def _ok_endpoint(request: Request) -> PlainTextResponse:
    return PlainTextResponse("OK")


_inner_app = Starlette(routes=[Route("/", _ok_endpoint), Route("/ping", _ok_endpoint)])
_protected_app = _BearerTokenMiddleware(_inner_app, _TEST_TOKEN)


@pytest.mark.asyncio
class TestBearerTokenMiddleware:
    """Tests for ``_BearerTokenMiddleware`` using httpx + ASGITransport."""

    async def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_protected_app),
            base_url="http://testserver",
        )

    async def test_no_auth_header_returns_401(self):
        async with await self._client() as client:
            response = await client.get("/")
        assert response.status_code == 401

    async def test_wrong_token_returns_401(self):
        async with await self._client() as client:
            response = await client.get(
                "/", headers={"Authorization": "Bearer wrong-token"}
            )
        assert response.status_code == 401

    async def test_correct_token_returns_200(self):
        async with await self._client() as client:
            response = await client.get(
                "/", headers={"Authorization": f"Bearer {_TEST_TOKEN}"}
            )
        assert response.status_code == 200

    async def test_correct_token_body_is_ok(self):
        async with await self._client() as client:
            response = await client.get(
                "/", headers={"Authorization": f"Bearer {_TEST_TOKEN}"}
            )
        assert response.text == "OK"

    async def test_401_has_www_authenticate_header(self):
        async with await self._client() as client:
            response = await client.get("/")
        assert response.headers.get("www-authenticate") == "Bearer"

    async def test_401_body_is_json_with_error_key(self):
        async with await self._client() as client:
            response = await client.get("/")
        body = response.json()
        assert "error" in body

    async def test_401_content_type_is_json(self):
        async with await self._client() as client:
            response = await client.get("/")
        assert "application/json" in response.headers.get("content-type", "")

    async def test_bearer_scheme_is_case_sensitive(self):
        # "bearer" (lowercase) is not the same as "Bearer"
        async with await self._client() as client:
            response = await client.get(
                "/", headers={"Authorization": f"bearer {_TEST_TOKEN}"}
            )
        assert response.status_code == 401

    async def test_token_with_extra_space_is_rejected(self):
        async with await self._client() as client:
            response = await client.get(
                "/", headers={"Authorization": f"Bearer  {_TEST_TOKEN}"}
            )
        assert response.status_code == 401

    async def test_lifespan_scope_passes_through(self):
        """Non-HTTP/websocket scopes (e.g. lifespan) must not be auth-gated."""
        received: list[dict] = []

        async def inner_app(scope, receive, send):
            received.append(scope)

        middleware = _BearerTokenMiddleware(inner_app, _TEST_TOKEN)
        lifespan_scope = {"type": "lifespan"}
        await middleware(lifespan_scope, None, None)  # type: ignore[arg-type]
        assert received == [lifespan_scope]
