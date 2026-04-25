"""Loops.so webhook handler — auto-provisions MCP tokens for new signups.

Flow:
  1. Customer fills out your Loops form (early access signup)
  2. Loops fires a POST to /webhook/loops (contactCreated event)
  3. We generate a trial token and store it in mcp_tokens
  4. We call the Loops API to set mcp_token on the contact
  5. A Loops automation detects mcp_token is set → sends Email 2
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
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse

from nyc_property_intel.analytics import capture as ph_capture
from nyc_property_intel.auth import TokenAuth
from nyc_property_intel.config import settings

logger = logging.getLogger(__name__)

_LOOPS_API_BASE = "https://app.loops.so/api/v1"


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

def make_webhook_handler(auth: TokenAuth):
    """Return a Starlette request handler bound to the given TokenAuth instance."""

    async def handle(request: Request) -> JSONResponse:
        body = await request.body()

        # ── Signature check ───────────────────────────────────────────
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

        logger.info("Loops webhook: new signup — %s", email)

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
            logger.info("Loops webhook: %s already has an active token — skipping", email)
            return JSONResponse({"ok": True, "skipped": "duplicate"})

        ph_capture(email, "token_provisioned", {"plan": "trial", "source": "loops_webhook"})

        # ── Push token to Loops contact ───────────────────────────────
        if settings.loops_api_key:
            try:
                await _set_loops_contact_property(email, token)
            except Exception:
                logger.exception(
                    "Loops webhook: token created for %s but failed to set Loops property. "
                    "Token: %s — set manually via manage_tokens.py",
                    email, token[:20] + "...",
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
