"""Anti-bot hardening tests for the Loops webhook handler.

Covers:
  * pure-function unit tests for each anti-bot helper
  * integration-style tests of the full handler with a mocked TokenAuth,
    mocked DNS, and a captured posthog event stream

No real database, network, or DNS resolution is performed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from starlette.requests import Request

from nyc_property_intel import loops_webhook
from nyc_property_intel.loops_webhook import (
    _CUSTOM_DISPOSABLE,
    domain_has_mx,
    is_brand_prefix_suspicious,
    is_disposable_domain,
    make_webhook_handler,
)


# ── Fake TokenAuth ────────────────────────────────────────────────────


class FakeTokenAuth:
    """Stand-in for nyc_property_intel.auth.TokenAuth that records calls."""

    def __init__(
        self,
        *,
        return_token: str = "nyprop_testtoken1234567890abcdef",
        created: bool = True,
        raise_exc: Exception | None = None,
    ) -> None:
        self.return_token = return_token
        self.created = created
        self.raise_exc = raise_exc
        self.calls: list[dict[str, Any]] = []

    async def create_token(
        self,
        *,
        email: str,
        plan: str = "trial",
        notes: str = "",
    ) -> tuple[str, bool]:
        self.calls.append({"email": email, "plan": plan, "notes": notes})
        if self.raise_exc is not None:
            raise self.raise_exc
        if not self.created:
            return "", False
        return self.return_token, True


# ── Helpers to build a Loops-signed Starlette request ────────────────


_SECRET = "whsec_" + base64.b64encode(b"unit-test-secret-key").decode()


def _sign(body: bytes, msg_id: str = "msg_1", msg_ts: str = "1700000000") -> dict[str, str]:
    secret_bytes = base64.b64decode(_SECRET.removeprefix("whsec_"))
    signed = f"{msg_id}.{msg_ts}.{body.decode()}".encode()
    sig = base64.b64encode(hmac.new(secret_bytes, signed, hashlib.sha256).digest()).decode()
    return {
        "webhook-id": msg_id,
        "webhook-timestamp": msg_ts,
        "webhook-signature": f"v1,{sig}",
    }


def _make_request(body: bytes, headers: dict[str, str]) -> Request:
    """Build a minimal Starlette Request that yields `body` and exposes `headers`."""
    encoded_headers = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/webhook/loops",
        "headers": encoded_headers,
    }

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


def _payload(email: str = "alice@example.com") -> bytes:
    return json.dumps({"eventName": "contact.created", "contact": {"email": email}}).encode()


# ── Pure-function tests ───────────────────────────────────────────────


class TestIsDisposableDomain:
    def test_known_lib_disposable(self) -> None:
        assert is_disposable_domain("mailinator.com") is True

    def test_custom_observed_disposable(self) -> None:
        # All four production-observed domains must block, regardless of
        # whether they're in the upstream lib list.
        for d in ("lohinja.com", "immenseignite.info", "web-ster.com", "meyer-alpers.de"):
            assert is_disposable_domain(d) is True, f"{d} should be blocked"

    def test_legitimate_domain_passes(self) -> None:
        assert is_disposable_domain("gmail.com") is False
        assert is_disposable_domain("example.com") is False

    def test_case_insensitive(self) -> None:
        assert is_disposable_domain("MAILINATOR.COM") is True
        assert is_disposable_domain("Web-Ster.com") is True

    def test_custom_set_includes_all_four_observed(self) -> None:
        # Belt-and-braces: the 3 not in upstream MUST live in our custom set.
        for d in ("immenseignite.info", "web-ster.com", "meyer-alpers.de"):
            assert d in _CUSTOM_DISPOSABLE, f"{d} missing from _CUSTOM_DISPOSABLE"


class TestIsBrandPrefixSuspicious:
    def test_canonical_amazon_meyer_alpers(self) -> None:
        assert is_brand_prefix_suspicious("amazon", "meyer-alpers.de") is True

    def test_brand_on_free_provider_allowed(self) -> None:
        assert is_brand_prefix_suspicious("amazon", "gmail.com") is False
        assert is_brand_prefix_suspicious("info", "yahoo.com") is False

    def test_brand_on_own_domain_allowed(self) -> None:
        # We don't want to block the (rare) case of a brand using their own domain.
        assert is_brand_prefix_suspicious("amazon", "amazon.com") is False
        assert is_brand_prefix_suspicious("paypal", "paypal.co.uk") is False

    def test_role_prefix_on_random_domain(self) -> None:
        assert is_brand_prefix_suspicious("admin", "random-shop.biz") is True
        assert is_brand_prefix_suspicious("noreply", "random-shop.biz") is True

    def test_normal_user_not_suspicious(self) -> None:
        assert is_brand_prefix_suspicious("alice", "example.com") is False
        assert is_brand_prefix_suspicious("bob", "meyer-alpers.de") is False


class TestDomainHasMx:
    @pytest.mark.asyncio
    async def test_no_mx_returns_false(self) -> None:
        import dns.resolver

        with patch.object(
            dns.resolver.Resolver,
            "resolve",
            side_effect=dns.resolver.NoAnswer(),
        ):
            ok, reason = await domain_has_mx("no-mx.example")
        assert ok is False
        assert reason == "no_mx"

    @pytest.mark.asyncio
    async def test_nxdomain_returns_false(self) -> None:
        import dns.resolver

        with patch.object(
            dns.resolver.Resolver,
            "resolve",
            side_effect=dns.resolver.NXDOMAIN(),
        ):
            ok, reason = await domain_has_mx("does-not-exist.invalid")
        assert ok is False
        assert reason == "no_mx"

    @pytest.mark.asyncio
    async def test_transient_dns_fails_open(self) -> None:
        """Per spec: flaky DNS must NOT block real users."""
        import dns.exception

        with patch(
            "dns.resolver.Resolver.resolve",
            side_effect=dns.exception.Timeout(),
        ):
            ok, reason = await domain_has_mx("flaky.example")
        assert ok is True
        assert reason == "transient"

    @pytest.mark.asyncio
    async def test_resolves_returns_true(self) -> None:
        import dns.resolver

        # Build a fake answers iterable that has len() and is truthy.
        fake_answers = [object()]
        with patch.object(
            dns.resolver.Resolver,
            "resolve",
            return_value=fake_answers,
        ):
            ok, reason = await domain_has_mx("example.com")
        assert ok is True
        assert reason == "ok"


# ── Full-handler integration tests ────────────────────────────────────


@pytest.fixture(autouse=True)
def _patch_settings() -> Any:
    """Force the webhook secret + clear API key so we have a deterministic env."""
    with patch.object(loops_webhook.settings, "loops_webhook_secret", _SECRET), \
         patch.object(loops_webhook.settings, "loops_api_key", None):
        yield


@pytest.fixture
def captured_events() -> list[tuple[str, str, dict[str, Any]]]:
    """Capture every ph_capture(distinct_id, event, properties) call."""
    events: list[tuple[str, str, dict[str, Any]]] = []

    def _capture(distinct_id: str, event: str, properties: dict[str, Any] | None = None) -> None:
        events.append((distinct_id, event, properties or {}))

    with patch.object(loops_webhook, "ph_capture", _capture):
        yield events


@pytest.fixture
def patch_mx_ok() -> Any:
    async def _ok(domain: str) -> tuple[bool, str]:
        return True, "ok"

    with patch.object(loops_webhook, "domain_has_mx", _ok):
        yield


@pytest.fixture
def patch_mx_no_mx() -> Any:
    async def _no(domain: str) -> tuple[bool, str]:
        return False, "no_mx"

    with patch.object(loops_webhook, "domain_has_mx", _no):
        yield


@pytest.fixture
def patch_mx_transient() -> Any:
    async def _transient(domain: str) -> tuple[bool, str]:
        return True, "transient"

    with patch.object(loops_webhook, "domain_has_mx", _transient):
        yield


@pytest.mark.asyncio
async def test_signature_failure_still_rejected(captured_events) -> None:
    """The new anti-bot logic must NOT have weakened signature enforcement."""
    auth = FakeTokenAuth()
    handle = make_webhook_handler(auth)
    body = _payload("alice@example.com")
    bad_headers = {
        "webhook-id": "msg_1",
        "webhook-timestamp": "1700000000",
        "webhook-signature": "v1,deadbeef",
    }
    resp = await handle(_make_request(body, bad_headers))
    assert resp.status_code == 401
    assert auth.calls == []
    assert captured_events == []  # no posthog events on signature failure


@pytest.mark.asyncio
async def test_allowed_signup_provisions_token(captured_events, patch_mx_ok) -> None:
    auth = FakeTokenAuth()
    handle = make_webhook_handler(auth)
    body = _payload("alice@example.com")
    resp = await handle(_make_request(body, _sign(body)))
    assert resp.status_code == 200
    assert json.loads(resp.body)["email"] == "alice@example.com"
    assert len(auth.calls) == 1
    assert auth.calls[0]["email"] == "alice@example.com"

    event_names = [e[1] for e in captured_events]
    assert "signup_provisioned" in event_names
    # Legacy event preserved for existing dashboards.
    assert "token_provisioned" in event_names


@pytest.mark.asyncio
async def test_disposable_domain_blocked_returns_200(captured_events, patch_mx_ok) -> None:
    auth = FakeTokenAuth()
    handle = make_webhook_handler(auth)
    body = _payload("amazon@meyer-alpers.de")
    resp = await handle(_make_request(body, _sign(body)))
    # 200 OK so Loops doesn't retry.
    assert resp.status_code == 200
    assert json.loads(resp.body)["skipped"] == "disposable_domain"
    # Token MUST NOT be provisioned.
    assert auth.calls == []
    event_names = [e[1] for e in captured_events]
    # Funnel-top event fires first, then the forensic event (Phase C),
    # then the rejection event. Other events MUST NOT appear.
    assert event_names == [
        "signup_form_submitted",
        "signup_via_legacy_webhook",
        "signup_rejected_disposable",
    ]
    # The rejection event is last and carries the offending domain.
    assert captured_events[-1][2]["domain"] == "meyer-alpers.de"


@pytest.mark.asyncio
async def test_signup_form_submitted_event_fires_for_every_valid_payload(
    captured_events, patch_mx_ok
) -> None:
    """Funnel-top event must fire BEFORE any rejection or provisioning logic
    so we can compute rejection rate per source from PostHog alone."""
    auth = FakeTokenAuth()
    handle = make_webhook_handler(auth)
    body = _payload("alice@example.com")
    await handle(_make_request(body, _sign(body)))
    event_names = [e[1] for e in captured_events]
    assert event_names[0] == "signup_form_submitted"
    assert captured_events[0][2]["source"] == "loops_webhook"


@pytest.mark.asyncio
async def test_lib_disposable_domain_blocked(captured_events, patch_mx_ok) -> None:
    """A domain in the upstream curated list (not in our custom set) must also block."""
    auth = FakeTokenAuth()
    handle = make_webhook_handler(auth)
    body = _payload("bob@mailinator.com")
    resp = await handle(_make_request(body, _sign(body)))
    assert resp.status_code == 200
    assert json.loads(resp.body)["skipped"] == "disposable_domain"
    assert auth.calls == []
    assert [e[1] for e in captured_events] == [
        "signup_form_submitted",
        "signup_via_legacy_webhook",
        "signup_rejected_disposable",
    ]


@pytest.mark.asyncio
async def test_no_mx_blocked(captured_events, patch_mx_no_mx) -> None:
    auth = FakeTokenAuth()
    handle = make_webhook_handler(auth)
    body = _payload("nobody@no-mail-here.example")
    resp = await handle(_make_request(body, _sign(body)))
    assert resp.status_code == 200
    assert json.loads(resp.body)["skipped"] == "no_mx"
    assert auth.calls == []
    assert [e[1] for e in captured_events] == [
        "signup_form_submitted",
        "signup_via_legacy_webhook",
        "signup_rejected_mx",
    ]


@pytest.mark.asyncio
async def test_transient_dns_does_not_block(captured_events, patch_mx_transient) -> None:
    """Spec: transient DNS failures must err on the side of allowing."""
    auth = FakeTokenAuth()
    handle = make_webhook_handler(auth)
    body = _payload("alice@example.com")
    resp = await handle(_make_request(body, _sign(body)))
    assert resp.status_code == 200
    assert json.loads(resp.body).get("email") == "alice@example.com"
    assert len(auth.calls) == 1  # token IS provisioned
    assert "signup_provisioned" in [e[1] for e in captured_events]
    assert "signup_rejected_mx" not in [e[1] for e in captured_events]


@pytest.mark.asyncio
async def test_brand_prefix_heuristic_blocked(captured_events, patch_mx_ok) -> None:
    """`amazon@<no-name>` even with a valid MX must trip the heuristic."""
    auth = FakeTokenAuth()
    handle = make_webhook_handler(auth)
    # NB: NOT one of the 4 disposable domains (so we know layer 3 is firing,
    # not layer 1). Use a synthetic obscure domain that has MX in our fixture.
    body = _payload("amazon@some-random-shop.biz")
    resp = await handle(_make_request(body, _sign(body)))
    assert resp.status_code == 200
    assert json.loads(resp.body)["skipped"] == "heuristic_brand_prefix"
    assert auth.calls == []
    assert [e[1] for e in captured_events] == [
        "signup_form_submitted",
        "signup_via_legacy_webhook",
        "signup_rejected_heuristic",
    ]
    assert captured_events[-1][2]["rule"] == "brand_prefix_no_name_domain"


@pytest.mark.asyncio
async def test_brand_prefix_on_gmail_allowed(captured_events, patch_mx_ok) -> None:
    """Free providers are exempt from the brand-prefix heuristic."""
    auth = FakeTokenAuth()
    handle = make_webhook_handler(auth)
    body = _payload("info@gmail.com")
    resp = await handle(_make_request(body, _sign(body)))
    assert resp.status_code == 200
    assert json.loads(resp.body).get("email") == "info@gmail.com"
    assert len(auth.calls) == 1


@pytest.mark.asyncio
async def test_duplicate_email_skipped_with_event(captured_events, patch_mx_ok) -> None:
    """If create_token reports `created=False`, log a duplicate event and 200."""
    auth = FakeTokenAuth(created=False)
    handle = make_webhook_handler(auth)
    body = _payload("alice@example.com")
    resp = await handle(_make_request(body, _sign(body)))
    assert resp.status_code == 200
    assert json.loads(resp.body)["skipped"] == "duplicate"
    assert [e[1] for e in captured_events] == [
        "signup_form_submitted",
        "signup_via_legacy_webhook",
        "signup_rejected_duplicate",
    ]


@pytest.mark.asyncio
async def test_db_error_returns_500_for_retry(captured_events, patch_mx_ok) -> None:
    """DB failures should return 500 so Loops retries (existing contract)."""
    auth = FakeTokenAuth(raise_exc=RuntimeError("connection lost"))
    handle = make_webhook_handler(auth)
    body = _payload("alice@example.com")
    resp = await handle(_make_request(body, _sign(body)))
    assert resp.status_code == 500


@pytest.mark.asyncio
async def test_missing_email_returns_400(captured_events, patch_mx_ok) -> None:
    auth = FakeTokenAuth()
    handle = make_webhook_handler(auth)
    body = json.dumps({"eventName": "contact.created", "contact": {}}).encode()
    resp = await handle(_make_request(body, _sign(body)))
    assert resp.status_code == 400
    assert auth.calls == []


@pytest.mark.asyncio
async def test_non_signup_event_skipped(captured_events) -> None:
    """Non-contact-created events are still passed through with 200, no checks."""
    auth = FakeTokenAuth()
    handle = make_webhook_handler(auth)
    body = json.dumps({"eventName": "testing.testEvent", "contact": {}}).encode()
    resp = await handle(_make_request(body, _sign(body)))
    assert resp.status_code == 200
    assert json.loads(resp.body)["skipped"] == "testing.testEvent"
    assert auth.calls == []


# ── Phase C: forensic event for legacy webhook hits ──────────────────


@pytest.mark.asyncio
async def test_legacy_webhook_fires_forensic_event(
    captured_events, patch_mx_ok,
) -> None:
    """After the homepage cuts over to /api/signup, every hit on this route
    is interesting. The new `signup_via_legacy_webhook` event lets us see
    in PostHog who is still POSTing to the published Loops form ID — the
    data point that gates eventual deletion of this route. See
    docs/signup-rebuild-plan-2026-05-06.md §C.
    """
    auth = FakeTokenAuth()
    handle = make_webhook_handler(auth)
    body = _payload("alice@example.com")
    headers = _sign(body)
    headers["user-agent"] = "ScriptedBot/1.0"
    resp = await handle(_make_request(body, headers))
    assert resp.status_code == 200

    # The forensic event MUST fire alongside the existing funnel events.
    event_names = [e[1] for e in captured_events]
    assert "signup_via_legacy_webhook" in event_names, (
        "forensic event missing — without it, signup_dashboard can't tell "
        "Loops-form bots apart from new /api/signup traffic"
    )
    forensic = [e for e in captured_events if e[1] == "signup_via_legacy_webhook"][0]
    # User-Agent is captured (truncated for safety) so we can spot common
    # bot UA strings in the forensic logs.
    assert forensic[2]["user_agent"] == "ScriptedBot/1.0"
    # The PostHog event happens BEFORE token provisioning so we still capture
    # the trace even when the email later gets dropped by an anti-bot layer.
    assert event_names.index("signup_via_legacy_webhook") < event_names.index(
        "signup_provisioned"
    )
