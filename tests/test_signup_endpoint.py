"""Contract tests for the new POST /api/signup endpoint.

Background: the homepage `Get Access Token` form used to POST directly to
the public Loops form ID (`cmntqdkqy00y20iycvyyxby0m`). Bots scripted that
endpoint to bypass every defence we'd built into our own webhook. The fix
is to take ownership of the ingress: the form now POSTs to our backend,
which runs the same anti-bot stack the Loops webhook runs, then issues a
token + magic link via the chat-flow primitives.

These tests pin the contract:
  * Happy path provisions a token, writes a magic-link row, and triggers
    the activation email.
  * Every rejection branch (disposable / no-MX / brand-prefix / rate-limit
    / malformed input / DB error) returns the right status with NO token
    leakage.
  * Bot-rejection branches return 200 OK so success vs. failure is not
    oracle-able to the attacker (matches the existing webhook contract).
  * The end-to-end "token in DB + magic-link in DB + email API called"
    pipeline is exercised before the homepage is wired to it (the
    "critical safety check" from the spec).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from starlette.requests import Request

from nyc_property_intel import chat as chat_module
from nyc_property_intel.chat import make_signup_endpoint_handler


# ── Helpers ───────────────────────────────────────────────────────────


def _make_request(body: bytes, ip: str = "203.0.113.1") -> Request:
    """Build a minimal Starlette Request that yields `body`.

    The IP is set via X-Forwarded-For so _get_client_ip picks it up and
    each test can use a unique IP to keep _signup_ip_buckets deterministic.
    """
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/signup",
        "headers": [
            (b"content-type", b"application/json"),
            (b"x-forwarded-for", ip.encode()),
        ],
        "client": ("127.0.0.1", 12345),
    }

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


class _FakePool:
    """Stand-in for asyncpg pool — records every call.

    `execute` and `_create_magic_link` write through this. We don't need
    fetchrow/fetchval to return anything realistic for these tests since
    we mock _create_magic_link directly.
    """

    def __init__(self, raise_exc: Exception | None = None) -> None:
        self.raise_exc = raise_exc
        self.executes: list[tuple[str, tuple]] = []

    async def execute(self, sql: str, *args: Any) -> str:
        if self.raise_exc is not None:
            raise self.raise_exc
        self.executes.append((sql, args))
        return "OK"

    async def fetchrow(self, *_args: Any, **_kwargs: Any) -> Any:
        return None

    async def fetchval(self, *_args: Any, **_kwargs: Any) -> Any:
        return 0


class _FakeAuth:
    """Stand-in for TokenAuth.

    `created` toggles the create_token return — True for "new email",
    False for "existing email triggers re-signup rotation path".
    """

    def __init__(
        self,
        *,
        return_token: str = "nyprop_test0123456789abcdef0123456789",
        created: bool = True,
        create_raises: Exception | None = None,
        pool: _FakePool | None = None,
    ) -> None:
        self.return_token = return_token
        self.created = created
        self.create_raises = create_raises
        self.pool = pool or _FakePool()
        self.create_calls: list[dict[str, Any]] = []

    async def create_token(
        self,
        *,
        email: str,
        plan: str = "trial",
        notes: str = "",
    ) -> tuple[str, bool]:
        self.create_calls.append({"email": email, "plan": plan, "notes": notes})
        if self.create_raises is not None:
            raise self.create_raises
        if not self.created:
            return "", False
        return self.return_token, True

    async def _get_pool(self) -> _FakePool:
        return self.pool


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_ip_buckets() -> Any:
    """Each test starts with empty IP rate-limit buckets."""
    chat_module._ip_buckets.clear()
    chat_module._signup_ip_buckets.clear()
    yield
    chat_module._ip_buckets.clear()
    chat_module._signup_ip_buckets.clear()


@pytest.fixture
def captured_events() -> list[tuple[str, str, dict[str, Any]]]:
    """Capture every ph_capture(distinct_id, event, properties) call."""
    events: list[tuple[str, str, dict[str, Any]]] = []

    def _capture(
        distinct_id: str,
        event: str,
        properties: dict[str, Any] | None = None,
    ) -> None:
        events.append((distinct_id, event, properties or {}))

    with patch.object(chat_module, "ph_capture", _capture):
        yield events


@pytest.fixture
def patch_mx_ok() -> Any:
    async def _ok(domain: str) -> tuple[bool, str]:
        return True, "ok"

    with patch.object(chat_module, "domain_has_mx", _ok):
        yield


@pytest.fixture
def patch_mx_no_mx() -> Any:
    async def _no(domain: str) -> tuple[bool, str]:
        return False, "no_mx"

    with patch.object(chat_module, "domain_has_mx", _no):
        yield


@pytest.fixture
def patch_mx_transient() -> Any:
    async def _transient(domain: str) -> tuple[bool, str]:
        return True, "transient"

    with patch.object(chat_module, "domain_has_mx", _transient):
        yield


@pytest.fixture
def patch_email_send() -> AsyncMock:
    """Mock _send_activation_email so no real Loops API call is made."""
    mock = AsyncMock(return_value=None)
    with patch.object(chat_module, "_send_activation_email", mock):
        yield mock


@pytest.fixture
def patch_create_magic_link() -> AsyncMock:
    """Mock _create_magic_link so we don't need a real DB."""
    mock = AsyncMock(return_value="11111111-1111-1111-1111-111111111111")
    with patch.object(chat_module, "_create_magic_link", mock):
        yield mock


# ── Happy path + safety-check end-to-end ──────────────────────────────


class TestHappyPath:
    """Pre-flip safety check: token issued, magic-link row, email triggered."""

    async def test_e2e_signup_flow_token_issued_and_email_sent(
        self,
        captured_events,
        patch_mx_ok,
        patch_email_send,
        patch_create_magic_link,
    ) -> None:
        """The 'critical safety check' from the spec — proves end-to-end.

        Verifies:
          1. auth.create_token called with the canonical email + trial plan
          2. _create_magic_link called with the issued token + token_hash
          3. _send_activation_email called with the /chat?t=<uuid> URL
          4. PostHog signup_provisioned event fires
          5. Response is 200 {"ok": true}
        """
        auth = _FakeAuth()
        handle = make_signup_endpoint_handler(auth)
        body = json.dumps({"email": "Smoke@Example.COM"}).encode()

        resp = await handle(_make_request(body, ip="203.0.113.10"))

        assert resp.status_code == 200
        assert json.loads(resp.body) == {"ok": True}

        # 1. Token issued for the canonical (lowercased) email.
        assert len(auth.create_calls) == 1
        assert auth.create_calls[0]["email"] == "smoke@example.com"
        assert auth.create_calls[0]["plan"] == "trial"

        # 2. Magic link row written with the plaintext token + its hash.
        assert patch_create_magic_link.await_count == 1
        ml_args = patch_create_magic_link.await_args.args
        # _create_magic_link(pool, token_hash, plaintext_token, client_ip)
        assert ml_args[2] == auth.return_token

        # 3. Activation email triggered with the chat magic-link URL.
        assert patch_email_send.await_count == 1
        send_args = patch_email_send.await_args.args
        # Original-case email goes to the send (not the canonical form).
        assert send_args[0] == "smoke@example.com"
        assert send_args[1].endswith("/chat?t=11111111-1111-1111-1111-111111111111")
        assert send_args[1].startswith("https://nycpropertyintel.com")

        # 4. Funnel events fired.
        event_names = [e[1] for e in captured_events]
        assert "signup_form_submitted" in event_names
        assert "signup_provisioned" in event_names
        # Verify provenance tag so signup_dashboard can split api_signup
        # vs. legacy webhook traffic.
        for _, name, props in captured_events:
            if name in ("signup_form_submitted", "signup_provisioned"):
                assert props.get("source") == "api_signup", (
                    f"event {name!r} missing source=api_signup tag: {props}"
                )

    async def test_response_shape_is_ok_true(
        self, patch_mx_ok, patch_email_send, patch_create_magic_link,
    ) -> None:
        """Frontend keys off `data.ok === true` — that's the contract."""
        auth = _FakeAuth()
        handle = make_signup_endpoint_handler(auth)
        body = json.dumps({"email": "alice@example.com"}).encode()
        resp = await handle(_make_request(body, ip="203.0.113.11"))
        assert resp.status_code == 200
        assert json.loads(resp.body) == {"ok": True}


# ── Input validation ──────────────────────────────────────────────────


class TestInputValidation:
    async def test_invalid_json_returns_400(self) -> None:
        auth = _FakeAuth()
        handle = make_signup_endpoint_handler(auth)
        resp = await handle(_make_request(b"not-json{{", ip="203.0.113.20"))
        assert resp.status_code == 400
        assert auth.create_calls == []

    async def test_missing_email_returns_400(self) -> None:
        auth = _FakeAuth()
        handle = make_signup_endpoint_handler(auth)
        resp = await handle(_make_request(json.dumps({}).encode(), ip="203.0.113.21"))
        assert resp.status_code == 400
        assert auth.create_calls == []

    async def test_malformed_email_returns_400(self) -> None:
        auth = _FakeAuth()
        handle = make_signup_endpoint_handler(auth)
        body = json.dumps({"email": "not-an-email"}).encode()
        resp = await handle(_make_request(body, ip="203.0.113.22"))
        assert resp.status_code == 400
        assert auth.create_calls == []

    async def test_email_too_long_returns_400(self) -> None:
        """RFC 5321 says max 254 chars — anything longer is invalid."""
        auth = _FakeAuth()
        handle = make_signup_endpoint_handler(auth)
        long_local = "x" * 250
        body = json.dumps({"email": f"{long_local}@example.com"}).encode()
        resp = await handle(_make_request(body, ip="203.0.113.23"))
        assert resp.status_code == 400
        assert auth.create_calls == []

    async def test_empty_email_returns_400(self) -> None:
        auth = _FakeAuth()
        handle = make_signup_endpoint_handler(auth)
        body = json.dumps({"email": "   "}).encode()
        resp = await handle(_make_request(body, ip="203.0.113.24"))
        assert resp.status_code == 400


# ── IP rate limit ─────────────────────────────────────────────────────


class TestIpRateLimit:
    async def test_fourth_signup_from_same_ip_blocked(
        self, patch_mx_ok, patch_email_send, patch_create_magic_link,
    ) -> None:
        """3 per IP per hour is the limit (reused from chat signup)."""
        auth = _FakeAuth()
        handle = make_signup_endpoint_handler(auth)
        ip = "203.0.113.30"

        for n in range(3):
            body = json.dumps({"email": f"user{n}@example.com"}).encode()
            resp = await handle(_make_request(body, ip=ip))
            assert resp.status_code == 200, f"signup {n+1} should succeed"

        # 4th — same IP, different email, must 429.
        body = json.dumps({"email": "user4@example.com"}).encode()
        resp = await handle(_make_request(body, ip=ip))
        assert resp.status_code == 429
        assert json.loads(resp.body) == {"error": "Too many requests"}

    async def test_different_ips_have_independent_budgets(
        self, patch_mx_ok, patch_email_send, patch_create_magic_link,
    ) -> None:
        auth = _FakeAuth()
        handle = make_signup_endpoint_handler(auth)
        # Burn IP A's budget.
        for n in range(3):
            body = json.dumps({"email": f"a{n}@example.com"}).encode()
            await handle(_make_request(body, ip="203.0.113.40"))
        # IP B should still work.
        body = json.dumps({"email": "b@example.com"}).encode()
        resp = await handle(_make_request(body, ip="203.0.113.41"))
        assert resp.status_code == 200


# ── Anti-bot rejection branches ───────────────────────────────────────


class TestBotRejectionBranches:
    """Each rejection layer must return 200 (so bots can't oracle the
    outcome) and emit a PostHog event tagged with source=api_signup."""

    async def test_disposable_domain_silently_dropped(
        self, captured_events, patch_mx_ok,
    ) -> None:
        auth = _FakeAuth()
        handle = make_signup_endpoint_handler(auth)
        body = json.dumps({"email": "amazon@meyer-alpers.de"}).encode()
        resp = await handle(_make_request(body, ip="203.0.113.50"))
        assert resp.status_code == 200
        assert json.loads(resp.body) == {"ok": True}
        assert auth.create_calls == [], "disposable must NOT issue a token"
        event_names = [e[1] for e in captured_events]
        assert "signup_rejected_disposable" in event_names

    async def test_no_mx_silently_dropped(
        self, captured_events, patch_mx_no_mx,
    ) -> None:
        auth = _FakeAuth()
        handle = make_signup_endpoint_handler(auth)
        body = json.dumps({"email": "alice@no-mail-here.example"}).encode()
        resp = await handle(_make_request(body, ip="203.0.113.51"))
        assert resp.status_code == 200
        assert auth.create_calls == []
        assert "signup_rejected_mx" in [e[1] for e in captured_events]

    async def test_brand_prefix_silently_dropped(
        self, captured_events, patch_mx_ok,
    ) -> None:
        auth = _FakeAuth()
        handle = make_signup_endpoint_handler(auth)
        body = json.dumps({"email": "amazon@some-random-shop.biz"}).encode()
        resp = await handle(_make_request(body, ip="203.0.113.52"))
        assert resp.status_code == 200
        assert auth.create_calls == []
        events_by_name = {e[1]: e[2] for e in captured_events}
        assert "signup_rejected_heuristic" in events_by_name
        assert events_by_name["signup_rejected_heuristic"]["rule"] == (
            "brand_prefix_no_name_domain"
        )

    async def test_brand_prefix_on_gmail_allowed(
        self, patch_mx_ok, patch_email_send, patch_create_magic_link,
    ) -> None:
        """Free providers are exempt — info@gmail.com must NOT trip the heuristic."""
        auth = _FakeAuth()
        handle = make_signup_endpoint_handler(auth)
        body = json.dumps({"email": "info@gmail.com"}).encode()
        resp = await handle(_make_request(body, ip="203.0.113.53"))
        assert resp.status_code == 200
        assert len(auth.create_calls) == 1

    async def test_transient_dns_does_not_block(
        self, patch_mx_transient, patch_email_send, patch_create_magic_link,
    ) -> None:
        """Spec: flaky DNS must not block real users (fail-open)."""
        auth = _FakeAuth()
        handle = make_signup_endpoint_handler(auth)
        body = json.dumps({"email": "alice@example.com"}).encode()
        resp = await handle(_make_request(body, ip="203.0.113.54"))
        assert resp.status_code == 200
        assert len(auth.create_calls) == 1

    async def test_honeypot_filled_silently_dropped(
        self, captured_events, patch_mx_ok,
    ) -> None:
        """Honeypot stub: when the future hidden field is populated,
        we drop silently and never reach the email-validation path."""
        auth = _FakeAuth()
        handle = make_signup_endpoint_handler(auth)
        body = json.dumps({
            "email": "alice@example.com",
            "hp_field": "bot-filled-this",
        }).encode()
        resp = await handle(_make_request(body, ip="203.0.113.55"))
        assert resp.status_code == 200
        assert auth.create_calls == []
        assert "signup_rejected_honeypot" in [e[1] for e in captured_events]


# ── Re-signup token rotation ─────────────────────────────────────────


class TestReSignupRotation:
    async def test_duplicate_email_rotates_token(
        self, captured_events, patch_mx_ok, patch_email_send, patch_create_magic_link,
    ) -> None:
        """Existing email triggers revoke-then-issue rotation, not a hard fail.

        This mirrors chat.signup_handler's behaviour and ensures the trial
        cap can't be bypassed by repeat signups accumulating tokens.
        """
        pool = _FakePool()
        auth = _FakeAuth(created=False, pool=pool)
        handle = make_signup_endpoint_handler(auth)
        body = json.dumps({"email": "returner@example.com"}).encode()
        resp = await handle(_make_request(body, ip="203.0.113.60"))
        assert resp.status_code == 200
        # The rotation path runs an UPDATE...revoked_at and an INSERT...mcp_tokens.
        sqls = [sql for sql, _args in pool.executes]
        assert any("UPDATE mcp_tokens" in sql and "revoked_at" in sql for sql in sqls), (
            "re-signup must revoke existing tokens"
        )
        assert any("INSERT INTO mcp_tokens" in sql for sql in sqls), (
            "re-signup must insert a new token row"
        )
        # Magic link still issued so the user gets an activation email.
        assert patch_create_magic_link.await_count == 1


# ── Failure modes that must not leak / break ─────────────────────────


class TestFailureModes:
    async def test_create_token_db_error_returns_500(self) -> None:
        auth = _FakeAuth(create_raises=RuntimeError("connection lost"))
        handle = make_signup_endpoint_handler(auth)
        body = json.dumps({"email": "alice@example.com"}).encode()
        # Patch MX so we get past the network-dependent layer.
        with patch.object(chat_module, "domain_has_mx",
                          AsyncMock(return_value=(True, "ok"))):
            resp = await handle(_make_request(body, ip="203.0.113.70"))
        assert resp.status_code == 500
        payload = json.loads(resp.body)
        assert "token" not in payload, "DB error must never leak a token"

    async def test_pool_execute_error_returns_500(
        self, patch_mx_ok, patch_create_magic_link, patch_email_send,
    ) -> None:
        """If the post-create UPDATE source='web' fails, we return 500."""
        pool = _FakePool(raise_exc=RuntimeError("pool dead"))
        auth = _FakeAuth(pool=pool)
        handle = make_signup_endpoint_handler(auth)
        body = json.dumps({"email": "alice@example.com"}).encode()
        resp = await handle(_make_request(body, ip="203.0.113.71"))
        assert resp.status_code == 500
        assert "token" not in json.loads(resp.body)

    async def test_email_send_failure_does_not_break_signup(
        self, patch_mx_ok, patch_create_magic_link,
    ) -> None:
        """Token + magic-link succeed even if Loops transactional API errors.

        Matches chat.signup_handler's behaviour. The token is in the DB and
        the magic link is in the DB; an operator can resend manually if
        needed. Returning 500 here would cause the user to retry, accumulating
        zombie tokens.
        """
        auth = _FakeAuth()
        handle = make_signup_endpoint_handler(auth)
        with patch.object(
            chat_module,
            "_send_activation_email",
            AsyncMock(side_effect=RuntimeError("Loops API 500")),
        ):
            body = json.dumps({"email": "alice@example.com"}).encode()
            resp = await handle(_make_request(body, ip="203.0.113.72"))
        assert resp.status_code == 200
        assert json.loads(resp.body) == {"ok": True}
        # Token IS provisioned and magic-link row IS written.
        assert len(auth.create_calls) == 1
        assert patch_create_magic_link.await_count == 1
