"""Loops.so webhook handler — auto-provisions MCP tokens for new signups.

Flow:
  1. Customer fills out your Loops form (early access signup)
  2. Loops fires a POST to /webhook/loops (contactCreated event)
  3. We run anti-bot checks (disposable domain, MX, brand-prefix heuristic).
     Failed checks → log + posthog event + return 200 OK without provisioning.
  4. We generate a trial token and store it in mcp_tokens
  5. We call the Loops API to set mcp_token on the contact
  6. A Loops automation detects mcp_token is set → sends Email 2
     with setup instructions and {{contact.mcp_token}} filled in

Setup checklist (one-time, in Loops dashboard):
  1. Create a custom contact property named exactly: mcp_token (type: Text)
  2. Settings → API → copy your API key → set LOOPS_API_KEY in Railway
  3. Settings → Webhooks → add endpoint:
       https://nyc-property-intel-production.up.railway.app/webhook/loops
       Event: Contact Created
       Copy the signing secret → set LOOPS_WEBHOOK_SECRET in Railway
  4. Build an automation:
       Trigger: Contact property updated → mcp_token → is set
       Action:  Send email → [your token delivery email]

Anti-bot layered defense (applied in order, after signature validation):
  Layer 1 — disposable email domain blocklist
    union(curated `disposable-email-domains` Python pkg, _CUSTOM_DISPOSABLE)
  Layer 2 — MX record validity (DNS lookup, 3s timeout, fail-open on transient)
  Layer 3 — brand-prefix-on-no-name-domain heuristic
    catches `amazon@meyer-alpers.de` and similar
  Duplicate guard — re-uses auth.create_token's own
    "no second active token per email" idempotency.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging

import httpx
from disposable_email_domains import blocklist as _DISPOSABLE_LIB_BLOCKLIST
from starlette.requests import Request
from starlette.responses import JSONResponse

from nyc_property_intel.analytics import capture as ph_capture
from nyc_property_intel.auth import TokenAuth, hash_token
from nyc_property_intel.config import settings

logger = logging.getLogger(__name__)

_LOOPS_API_BASE = "https://app.loops.so/api/v1"


# ── Anti-bot constants ────────────────────────────────────────────────

# Production-observed disposable domains not present in the curated lib list
# (verified at build time with `'<d>' in disposable_email_domains.blocklist`).
# Keep alphabetised; document the date observed for future pruning.
_CUSTOM_DISPOSABLE: frozenset[str] = frozenset({
    "immenseignite.info",   # observed 2026-04
    "lohinja.com",          # already in lib but kept for belt-and-braces
    "meyer-alpers.de",      # observed 2026-04 (amazon@meyer-alpers.de)
    "web-ster.com",         # observed 2026-04
})

# Brand / role prefixes that almost never legitimately appear on a no-name
# domain. `amazon@meyer-alpers.de` is the canonical fingerprint.
_BRAND_PREFIXES: frozenset[str] = frozenset({
    "admin",
    "amazon",
    "apple",
    "google",
    "info",
    "meta",
    "microsoft",
    "noreply",
    "paypal",
    "support",
    "webmaster",
})

# Major free providers — local part can legitimately be anything here, so
# the brand-prefix heuristic must NOT fire on these domains.
_MAJOR_FREE_PROVIDERS: frozenset[str] = frozenset({
    "gmail.com",
    "googlemail.com",
    "hotmail.com",
    "icloud.com",
    "outlook.com",
    "protonmail.com",
    "proton.me",
    "yahoo.com",
})

_DNS_TIMEOUT_SECONDS = 3.0


# ── Signature verification ────────────────────────────────────────────

def _verify_signature(body: bytes, request_headers: dict, secret: str) -> bool:
    """Verify Loops Standard Webhooks HMAC-SHA256 signature.

    Loops uses Standard Webhooks (https://www.standardwebhooks.com/) headers:
        webhook-id, webhook-timestamp, webhook-signature
    Signed content: "{webhook-id}.{webhook-timestamp}.{raw_body}"
    Key: base64-decode(secret after "whsec_" prefix)
    Signature header format: "v1,<base64_hmac>" (space-separated if multiple)
    """
    try:
        secret_bytes = base64.b64decode(secret.removeprefix("whsec_"))
        msg_id        = request_headers.get("webhook-id", "")
        msg_timestamp = request_headers.get("webhook-timestamp", "")
        msg_signature = request_headers.get("webhook-signature", "")

        if not all([msg_id, msg_timestamp, msg_signature]):
            return False

        signed = f"{msg_id}.{msg_timestamp}.{body.decode()}".encode()
        expected = base64.b64encode(
            hmac.new(secret_bytes, signed, hashlib.sha256).digest()
        ).decode()

        # Header may contain multiple sigs: "v1,aaa v1,bbb"
        for entry in msg_signature.split(" "):
            if entry.startswith("v1,"):
                if hmac.compare_digest(expected, entry[3:]):
                    return True
        return False
    except Exception:
        return False


# ── Anti-bot checks ───────────────────────────────────────────────────

def _split_email(email: str) -> tuple[str, str]:
    """Return (local_part, domain) lowercased. Empty domain on malformed input."""
    local, _, domain = email.lower().partition("@")
    return local, domain


def is_disposable_domain(domain: str) -> bool:
    """True if the domain is on the curated lib list OR our custom list."""
    domain = domain.lower()
    return domain in _DISPOSABLE_LIB_BLOCKLIST or domain in _CUSTOM_DISPOSABLE


async def domain_has_mx(domain: str) -> tuple[bool, str]:
    """DNS-resolve MX records for `domain`.

    Returns:
      (True, "ok")       — at least one MX record found
      (False, "no_mx")   — domain resolves but has no MX records (NoAnswer/NXDOMAIN)
      (True, "transient") — DNS error / timeout; fail-open per spec, but caller
                            can log the reason. We do NOT block on transient
                            failures so flaky DNS doesn't reject real users.
    """
    # dnspython is synchronous; run in a thread with a tight timeout so we
    # don't block the event loop.
    import dns.exception
    import dns.resolver

    def _resolve() -> tuple[bool, str]:
        resolver = dns.resolver.Resolver()
        resolver.timeout = _DNS_TIMEOUT_SECONDS
        resolver.lifetime = _DNS_TIMEOUT_SECONDS
        try:
            answers = resolver.resolve(domain, "MX")
            return (len(answers) > 0, "ok" if answers else "no_mx")
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
            return (False, "no_mx")
        except (dns.resolver.NoNameservers, dns.exception.Timeout, OSError) as exc:
            logger.warning("MX lookup transient failure for %s: %s", domain, exc)
            return (True, "transient")
        except Exception as exc:  # noqa: BLE001 — be defensive, fail open
            logger.warning("MX lookup unexpected error for %s: %s", domain, exc)
            return (True, "transient")

    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_resolve),
            timeout=_DNS_TIMEOUT_SECONDS + 1.0,
        )
    except asyncio.TimeoutError:
        logger.warning("MX lookup wall-clock timeout for %s", domain)
        return (True, "transient")


def is_brand_prefix_suspicious(local: str, domain: str) -> bool:
    """Brand/role prefix on a no-name domain.

    Returns True if:
      * local part exactly matches a known brand/role keyword, AND
      * domain is NOT one of that brand's own domains, AND
      * domain is NOT a major free provider (gmail/yahoo/etc.).

    Examples:
      amazon@meyer-alpers.de  -> True   (catches the canonical bot pattern)
      info@gmail.com          -> False  (free provider, allowed)
      amazon@amazon.com       -> False  (brand on its own domain)
      admin@example.com       -> True   (admin on no-name domain)
    """
    local = local.lower()
    domain = domain.lower()
    if local not in _BRAND_PREFIXES:
        return False
    if domain in _MAJOR_FREE_PROVIDERS:
        return False
    # Brand on its own domain (e.g. amazon@amazon.com or amazon@amazon.co.uk)
    # — domain root starts with the brand keyword. Belt-and-braces; brands
    # rarely use this signup path but we don't want false positives.
    domain_root = domain.split(".")[0]
    if domain_root == local:
        return False
    return True


# ── Loops API ─────────────────────────────────────────────────────────

async def _set_loops_contact_property(email: str, token: str) -> None:
    """Write mcp_token onto the Loops contact so Email 2 can use {{contact.mcp_token}}."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.put(
            f"{_LOOPS_API_BASE}/contacts/update",
            headers={
                "Authorization": f"Bearer {settings.loops_api_key}",
                "Content-Type": "application/json",
            },
            json={"email": email, "mcp_token": token},
        )
        resp.raise_for_status()
    logger.info("Loops contact property mcp_token set for %s", email)


# ── Webhook handler ───────────────────────────────────────────────────

def _reject_200(reason: str, email: str, **extra) -> JSONResponse:
    """Return a 200 OK rejection payload.

    Loops retries on non-2xx — we always return 2xx so a rejected signup
    does not get re-driven. The body indicates the reason for human/log audit
    but Loops itself does not act on it.
    """
    body = {"ok": True, "skipped": reason, "email": email}
    body.update(extra)
    return JSONResponse(body)


def make_webhook_handler(auth: TokenAuth):
    """Return a Starlette request handler bound to the given TokenAuth instance."""

    async def handle(request: Request) -> JSONResponse:
        body = await request.body()

        # ── Signature check (MUST stay first) ─────────────────────────
        headers_lower = {k.lower(): v for k, v in request.headers.items()}
        if not settings.loops_webhook_secret:
            logger.error("LOOPS_WEBHOOK_SECRET not set — rejecting webhook request")
            return JSONResponse({"error": "Webhook not configured"}, status_code=503)
        if not _verify_signature(body, headers_lower, settings.loops_webhook_secret):
            logger.warning("Loops webhook: invalid signature — rejected")
            return JSONResponse({"error": "Invalid signature"}, status_code=401)

        # ── Parse payload ─────────────────────────────────────────────
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)

        # Loops uses "eventName" field (e.g. "contact.created", "testing.testEvent")
        event_name = payload.get("eventName", payload.get("type", ""))
        if event_name not in ("contact.created", "contactCreated"):
            logger.debug("Loops webhook: ignoring event '%s'", event_name)
            return JSONResponse({"ok": True, "skipped": event_name})

        contact = payload.get("contact", payload.get("data", {}).get("contact", {}))
        email = contact.get("email", "").strip().lower()
        if not email:
            logger.warning("Loops webhook: contactCreated payload missing email")
            return JSONResponse({"error": "Missing email"}, status_code=400)

        # Funnel-top event: fires for every signup that passes signature + payload
        # validation, BEFORE any rejection or token provisioning. Pairs with the
        # downstream signup_provisioned / signup_rejected_* events to compute
        # rejection rate per source.
        ph_capture(email, "signup_form_submitted", {"source": "loops_webhook"})

        # Forensic tripwire — once the homepage form switches to /api/signup
        # (Phase B of the signup-rebuild), every hit on this route is one of:
        #   (a) a Loops dashboard test fire (rare, easy to recognise by email),
        #   (b) a stale browser cached against the old form action (transient),
        #   (c) a bot still scripting the published Loops form ID directly.
        # Logging the email + UA gives us the data we need to decide when it's
        # safe to delete this route entirely. PostHog property names are kept
        # short and stable so signup_dashboard / future SQL can group on them.
        # See docs/signup-rebuild-plan-2026-05-06.md §C.
        ph_capture(
            email,
            "signup_via_legacy_webhook",
            {
                "user_agent": (
                    request.headers.get("user-agent", "")[:200] or None
                ),
                "event_name": event_name,
            },
        )

        local, domain = _split_email(email)
        if not domain:
            logger.warning("Loops webhook: malformed email '%s' — rejecting", email)
            ph_capture(email, "signup_rejected_malformed", {"source": "loops_webhook"})
            return _reject_200("malformed_email", email)

        logger.info("Loops webhook: new signup — %s", email)

        # ── Layer 1: disposable domain blocklist ──────────────────────
        if is_disposable_domain(domain):
            logger.warning(
                "Loops webhook: rejected DISPOSABLE — email=%s domain=%s",
                email, domain,
            )
            ph_capture(
                email,
                "signup_rejected_disposable",
                {"domain": domain, "source": "loops_webhook"},
            )
            return _reject_200("disposable_domain", email, domain=domain)

        # ── Layer 2: MX record validity ───────────────────────────────
        has_mx, mx_reason = await domain_has_mx(domain)
        if not has_mx:
            logger.warning(
                "Loops webhook: rejected NO_MX — email=%s domain=%s reason=%s",
                email, domain, mx_reason,
            )
            ph_capture(
                email,
                "signup_rejected_mx",
                {"domain": domain, "reason": mx_reason, "source": "loops_webhook"},
            )
            return _reject_200("no_mx", email, domain=domain)
        # NOTE: transient DNS failures fall through (fail-open) — see domain_has_mx.

        # ── Layer 3: brand-prefix-on-no-name-domain heuristic ─────────
        if is_brand_prefix_suspicious(local, domain):
            logger.warning(
                "Loops webhook: rejected HEURISTIC — email=%s local=%s domain=%s "
                "(brand prefix on non-major-free domain)",
                email, local, domain,
            )
            ph_capture(
                email,
                "signup_rejected_heuristic",
                {
                    "rule": "brand_prefix_no_name_domain",
                    "local": local,
                    "domain": domain,
                    "source": "loops_webhook",
                },
            )
            return _reject_200("heuristic_brand_prefix", email, domain=domain)

        # ── Provision token ───────────────────────────────────────────
        try:
            token, created = await auth.create_token(
                email=email,
                plan="trial",
                notes="auto-provisioned via Loops webhook",
            )
        except Exception:
            logger.exception("Loops webhook: DB error provisioning token for %s", email)
            # Return 500 so Loops retries the webhook
            return JSONResponse({"error": "DB error"}, status_code=500)

        if not created:
            # Idempotency: email already has an active token. This serves as our
            # per-email rate limit at the webhook layer (covers Loops re-deliveries
            # and any same-email duplicate signups while the token is active).
            logger.info(
                "Loops webhook: %s already has an active token — skipping (duplicate)",
                email,
            )
            ph_capture(
                email,
                "signup_rejected_duplicate",
                {"source": "loops_webhook"},
            )
            return _reject_200("duplicate", email)

        ph_capture(
            email,
            "signup_provisioned",
            {"plan": "trial", "source": "loops_webhook"},
        )
        # Keep legacy event name too for any existing dashboards.
        ph_capture(email, "token_provisioned", {"plan": "trial", "source": "loops_webhook"})

        # ── Push token to Loops contact ───────────────────────────────
        if settings.loops_api_key:
            try:
                await _set_loops_contact_property(email, token)
            except Exception:
                logger.exception(
                    "Loops webhook: token created for %s but failed to set Loops property. "
                    "Token hash prefix: %s — set manually via manage_tokens.py",
                    email, hash_token(token)[:12],
                )
                # Don't return error — token is in DB, just needs manual follow-up
        else:
            logger.warning(
                "LOOPS_API_KEY not set — token provisioned for %s but NOT sent. "
                "Run: DATABASE_URL=... uv run python scripts/manage_tokens.py list",
                email,
            )

        return JSONResponse({"ok": True, "email": email})

    return handle
