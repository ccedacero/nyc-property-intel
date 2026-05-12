"""Web chat endpoints.

Routes (all mounted in server.py):
    POST /api/chat/signup  — provision trial token, create magic link, send activation email
    POST /api/activate     — validate magic link UUID, return plaintext token to browser
    POST /api/chat         — agentic chat with tool_use loop + SSE streaming
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
import re
import uuid
from collections.abc import AsyncIterator

import anthropic
import httpx
from cachetools import TTLCache
from cryptography.fernet import Fernet, InvalidToken
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse

from nyc_property_intel.analytics import capture as ph_capture
from nyc_property_intel.app import mcp, MCP_INSTRUCTIONS
from nyc_property_intel.auth import PLAN_LIMITS, TRIAL_DAYS, TokenAuth, generate_token, hash_token, normalize_email
from nyc_property_intel.config import settings
from nyc_property_intel.loops_webhook import (
    _split_email,
    domain_has_mx,
    is_brand_prefix_suspicious,
    is_disposable_domain,
)

logger = logging.getLogger(__name__)

# RFC 5321 max email length is 254; reject anything with control chars or that looks wrong.
_EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')

_SITE_BASE = "https://nycpropertyintel.com"
_LOOPS_API_BASE = "https://app.loops.so/api/v1"
_CHAT_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 4096
_MAX_ROUNDS = 8          # max agentic tool-call rounds per request
_MAX_TOOL_CALLS = 12     # max individual tool calls per request
# Tools that don't count against the per-request budget. lookup_property
# is a prerequisite (Claude must resolve a BBL before any data tool can
# run) and may legitimately need 2-3 retries with different address
# formats (esp. hyphenated Queens house numbers). Counting it would
# starve the supplemental data tools.
_BUDGET_EXEMPT_TOOLS = frozenset({"lookup_property"})
_STREAM_TIMEOUT = 60.0   # seconds
_MAX_MSG_LEN = 2000      # max user message length
_MAX_HISTORY = 20        # max messages in conversation history

# Module-level Anthropic client (created lazily)
_anthropic_client: anthropic.AsyncAnthropic | None = None

# Cached Anthropic-format tools list (built once on first request)
_anthropic_tools: list[dict] | None = None

# In-memory IP rate limiters — TTLCache caps memory and auto-evicts expired entries.
# Without a max size, these would grow unbounded under a flood of unique IPs.
_IP_RATE_LIMIT = 10          # requests per minute per IP (chat endpoint)
_SIGNUP_IP_RATE_LIMIT = 3    # signups per IP per hour
_SIGNUP_IP_WINDOW = 3600     # 1 hour in seconds

_ip_buckets: TTLCache = TTLCache(maxsize=50_000, ttl=60)
_signup_ip_buckets: TTLCache = TTLCache(maxsize=10_000, ttl=_SIGNUP_IP_WINDOW)


# ── Client + tools ────────────────────────────────────────────────────

def _get_client() -> anthropic.AsyncAnthropic:
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _anthropic_client


def _get_anthropic_tools() -> list[dict]:
    """Build Anthropic tool_use format from registered MCP tools (cached)."""
    global _anthropic_tools
    if _anthropic_tools is not None:
        return _anthropic_tools

    tools = mcp._tool_manager.list_tools()
    result = []
    for i, t in enumerate(tools):
        entry: dict = {
            "name": t.name,
            "description": t.description or "",
            "input_schema": t.parameters,
        }
        # Mark last tool for prompt caching
        if i == len(tools) - 1:
            entry["cache_control"] = {"type": "ephemeral"}
        result.append(entry)
    _anthropic_tools = result
    return result


# ── IP rate limiting ──────────────────────────────────────────────────

def _check_ip_rate_limit(ip: str) -> bool:
    count = _ip_buckets.get(ip, 0)
    if count >= _IP_RATE_LIMIT:
        return False
    _ip_buckets[ip] = count + 1
    return True


def _check_signup_ip_rate_limit(ip: str) -> bool:
    """Allow at most 3 new signups per IP per hour."""
    count = _signup_ip_buckets.get(ip, 0)
    if count >= _SIGNUP_IP_RATE_LIMIT:
        return False
    _signup_ip_buckets[ip] = count + 1
    return True


_normalize_email = normalize_email


def _is_private_ip(ip: str) -> bool:
    """RFC 1918 + loopback. Note: only 172.16.0.0/12 (172.16-172.31) is private,
    NOT all of 172.x — Google for instance owns 172.217.x and that's public."""
    if ip.startswith(("10.", "192.168.", "127.")):
        return True
    if ip.startswith("172."):
        try:
            return 16 <= int(ip.split(".")[1]) <= 31
        except (ValueError, IndexError):
            return False
    return False


def _get_client_ip(request: Request) -> str:
    # Railway's public networking runs through Fastly, which sets Fastly-Client-IP
    # to the real client IP before appending its own edge IP to X-Forwarded-For.
    # Check CDN-specific headers first, then fall back to XFF.
    #
    # SECURITY NOTE: these headers are spoofable by anyone who can reach the
    # Railway hostname directly. Until we whitelist the Fastly IP range as
    # the only trusted upstream, we log a warning when the immediate peer is
    # not in any expected CDN range so spoofing attempts are detectable in
    # production. See Fix 9 for the longer-term fix.
    cdn_header_value = ""
    cdn_header_name = ""
    for header in ("fastly-client-ip", "cf-connecting-ip", "x-real-ip"):
        val = request.headers.get(header, "").strip()
        if val:
            cdn_header_value = val
            cdn_header_name = header
            break

    if cdn_header_value:
        peer = request.client.host if request.client else None
        # Heuristic tripwire: a CDN-trusted header is set but the immediate
        # peer is private/loopback (i.e. probably localhost in tests, fine)
        # OR appears to be a public IP that's NOT one of our known CDN
        # ranges. Without a precise Fastly allowlist we just log and let
        # the value through — flipping to fail-closed is a future hardening.
        if peer and not _is_private_ip(peer):
            # peer is public — could be a direct hit on the Railway hostname
            # bypassing Fastly. Log so we can see this in Sentry / logs.
            logger.warning(
                "Spoofable CDN header set but peer is public: peer=%s header=%s value=%s",
                peer, cdn_header_name, cdn_header_value,
            )
        return cdn_header_value

    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        # With Fastly in front, the rightmost XFF is the Fastly edge PoP IP, not
        # the client. Use the leftmost (first) non-private IP instead.
        ips = [ip.strip() for ip in forwarded.split(",")]
        for ip in ips:
            if ip and not _is_private_ip(ip):
                return ip
        return ips[0]
    return request.client.host if request.client else "unknown"


# ── Cookie signing ────────────────────────────────────────────────────

_COOKIE_MAX_AGE = 86400 * 7  # 7 days — must match Set-Cookie max_age


def make_session_cookie(query_count: int, analyze_count: int = 0) -> str:
    """Return a signed cookie value encoding free-tier query + analyze counts."""
    payload = base64.urlsafe_b64encode(
        json.dumps({"q": query_count, "a": analyze_count, "t": int(time.time())}).encode()
    ).decode().rstrip("=")
    sig = hmac.new(
        settings.cookie_secret.encode(),
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()  # Full 64-char (256-bit) signature
    return f"{payload}.{sig}"


def read_session_cookie(value: str) -> tuple[int, int]:
    """Return (query_count, analyze_count) from signed cookie, or (0, 0) if invalid/expired."""
    if not settings.cookie_secret:
        return 0, 0
    try:
        payload, sig = value.rsplit(".", 1)
        expected = hmac.new(
            settings.cookie_secret.encode(),
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return 0, 0
        data = json.loads(base64.urlsafe_b64decode(payload + "=="))
        # Reject cookies older than max age — prevents replay of old cookies
        if int(time.time()) - int(data.get("t", 0)) > _COOKIE_MAX_AGE:
            return 0, 0
        return max(0, int(data.get("q", 0))), max(0, int(data.get("a", 0)))
    except Exception:
        return 0, 0


# ── Fernet helpers ────────────────────────────────────────────────────

def _fernet() -> Fernet:
    return Fernet(settings.web_chat_token_key.encode())


def _encrypt_token(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def _decrypt_token(encrypted: str) -> str | None:
    try:
        return _fernet().decrypt(encrypted.encode()).decode()
    except InvalidToken:
        return None


# ── Magic link DB helpers ─────────────────────────────────────────────

async def _create_magic_link(pool, token_hash: str, plaintext_token: str, client_ip: str = "") -> str:
    """Insert a magic link row and return the UUID string."""
    link_id = str(uuid.uuid4())
    await pool.execute(
        """
        INSERT INTO web_magic_links (id, token_hash, encrypted_token, created_by_ip)
        VALUES ($1, $2, $3, $4)
        """,
        link_id,
        token_hash,
        _encrypt_token(plaintext_token),
        client_ip or None,
    )
    return link_id


# ── Loops transactional email ─────────────────────────────────────────

async def _send_activation_email(email: str, activation_url: str) -> None:
    if not settings.loops_api_key:
        logger.warning("LOOPS_API_KEY not set — activation email not sent to %s", email)
        return
    if not settings.loops_chat_transactional_id:
        logger.warning(
            "LOOPS_CHAT_TRANSACTIONAL_ID not set — skipping activation email for %s. "
            "Activation URL: %s",
            email,
            activation_url,
        )
        return
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{_LOOPS_API_BASE}/transactional",
            headers={
                "Authorization": f"Bearer {settings.loops_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "transactionalId": settings.loops_chat_transactional_id,
                "email": email,
                "dataVariables": {"activationUrl": activation_url},
            },
        )
    if not resp.is_success:
        logger.error(
            "Failed to send activation email to %s: %s %s",
            email, resp.status_code, resp.text,
        )
    else:
        logger.info("Activation email sent to %s", email)


# ── Agentic loop ──────────────────────────────────────────────────────

def _block_to_dict(block) -> dict:
    """Convert an Anthropic content block object to a plain dict."""
    if block.type == "text":
        return {"type": "text", "text": block.text}
    if block.type == "tool_use":
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input or {}}
    return {"type": block.type}


async def _agentic_stream(messages: list[dict]) -> AsyncIterator[str]:
    """Run the Claude + tool_use agentic loop and yield SSE lines."""
    client = _get_client()
    tools = _get_anthropic_tools()
    tool_calls_made = 0

    for _round in range(_MAX_ROUNDS):
        tool_use_blocks: list = []
        had_text = False

        try:
            async with client.messages.stream(
                model=_CHAT_MODEL,
                max_tokens=_MAX_TOKENS,
                system=[
                    {
                        "type": "text",
                        "text": MCP_INSTRUCTIONS,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=messages,
                tools=tools,
            ) as stream:
                async for event in stream:
                    if event.type == "content_block_delta":
                        if event.delta.type == "text_delta" and event.delta.text:
                            had_text = True
                            yield f"data: {json.dumps({'type': 'text_delta', 'text': event.delta.text})}\n\n"

                final = await stream.get_final_message()

        except anthropic.AuthenticationError:
            logger.error("Anthropic auth failed — check ANTHROPIC_API_KEY")
            yield f"data: {json.dumps({'type': 'error', 'message': 'Service configuration error'})}\n\n"
            return
        except anthropic.RateLimitError:
            yield f"data: {json.dumps({'type': 'error', 'message': 'Service busy, please retry in a moment'})}\n\n"
            return
        except Exception:
            logger.exception("Anthropic streaming error")
            yield f"data: {json.dumps({'type': 'error', 'message': 'Unexpected error'})}\n\n"
            return

        if final.stop_reason == "end_turn":
            break

        if final.stop_reason == "tool_use":
            # Build assistant turn with full content
            assistant_content = [_block_to_dict(b) for b in final.content]
            messages.append({"role": "assistant", "content": assistant_content})

            tool_results = []
            for block in final.content:
                if block.type != "tool_use":
                    continue

                if tool_calls_made >= _MAX_TOOL_CALLS:
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": "<tool_result>\n" + json.dumps({"error": "Tool call budget exceeded"}) + "\n</tool_result>",
                    })
                    continue

                if block.name not in _BUDGET_EXEMPT_TOOLS:
                    tool_calls_made += 1
                yield f"data: {json.dumps({'type': 'tool_start', 'name': block.name})}\n\n"

                try:
                    result = await asyncio.wait_for(
                        mcp._tool_manager.call_tool(block.name, block.input or {}, convert_result=False),
                        timeout=45.0,
                    )
                    result_str = "<tool_result>\n" + json.dumps(result, default=str) + "\n</tool_result>"
                except asyncio.TimeoutError:
                    logger.warning("Tool %s timed out", block.name)
                    result_str = "<tool_result>\n" + json.dumps({"error": "Tool timed out"}) + "\n</tool_result>"
                except Exception as exc:
                    logger.warning("Tool %s error: %s", block.name, exc)
                    result_str = "<tool_result>\n" + json.dumps({"error": "Tool execution failed"}) + "\n</tool_result>"

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_str,
                })
                yield f"data: {json.dumps({'type': 'tool_done', 'name': block.name})}\n\n"

            messages.append({"role": "user", "content": tool_results})
        else:
            break

    yield f"data: {json.dumps({'type': 'done'})}\n\n"


# ── analyze_property daily limit ──────────────────────────────────────

async def _count_analyze_today(pool, token_hash: str) -> int:
    """Return analyze_property calls made today (UTC) for this token.

    Resets at midnight UTC, matching the daily query counter in
    mcp_daily_usage (which is keyed on CURRENT_DATE).
    """
    try:
        row = await pool.fetchrow(
            """
            SELECT COUNT(*) AS cnt FROM mcp_usage_log
            WHERE token_hash = $1
              AND tool_name = 'analyze_property'
              AND called_at >= date_trunc('day', NOW() AT TIME ZONE 'UTC')
            """,
            token_hash,
        )
        return int(row["cnt"]) if row else 0
    except Exception:
        return 0  # fail open


# ── Anonymous chat tracking ───────────────────────────────────────────

# Per-process random fallback used when ANON_IP_HASH_SECRET is unset. This
# keeps hashes consistent within a single instance but resets on each deploy;
# set the env var in Railway for stable hashes across restarts.
_ANON_IP_HASH_FALLBACK: str | None = None


def _anon_ip_secret() -> str:
    """Return the IP-hash secret, falling back to a random per-process value.

    Reading from settings each call (rather than at import time) keeps tests
    that monkeypatch settings.anon_ip_hash_secret working.
    """
    import os
    secret = (
        getattr(settings, "anon_ip_hash_secret", "")
        or os.environ.get("ANON_IP_HASH_SECRET", "")
    )
    if secret:
        return secret
    global _ANON_IP_HASH_FALLBACK
    if _ANON_IP_HASH_FALLBACK is None:
        # uuid4 is fine — we only need an unguessable per-process value.
        _ANON_IP_HASH_FALLBACK = uuid.uuid4().hex
        logger.warning(
            "ANON_IP_HASH_SECRET not set — using per-process fallback. "
            "Anon IP hashes will not be stable across deploys."
        )
    return _ANON_IP_HASH_FALLBACK


def _hash_ip(ip: str) -> str:
    """Return a 32-char hex digest of (ip || secret), or empty string if ip is empty."""
    if not ip:
        return ""
    secret = _anon_ip_secret()
    return hashlib.sha256(f"{ip}{secret}".encode()).hexdigest()[:32]


async def _record_anon_chat_query(
    pool,
    ip_hash: str,
    query_count: int,
    anon_session_id: str | None = None,
) -> None:
    """Insert one row into anon_chat_queries.

    Failures must NOT bubble out — anon-tracking is observability, not a
    user-visible feature. We log at warning level and swallow.

    The INSERT is also wrapped in a try/except inside the SQL call site so
    that even if the table is missing (e.g. migration somehow rolled back),
    the chat path keeps working.
    """
    if pool is None:
        return
    try:
        await pool.execute(
            """
            INSERT INTO anon_chat_queries (ip_hash, anon_session_id, query_count)
            VALUES ($1, $2, $3)
            """,
            ip_hash or None,
            anon_session_id,
            query_count,
        )
    except Exception as exc:
        # Includes UndefinedTableError if the migration hasn't been applied —
        # we deliberately do NOT crash the chat handler in that case.
        logger.warning("Failed to record anon chat query: %s", exc)


# ── Shared anti-bot check ─────────────────────────────────────────────
#
# Both /api/chat/signup (legacy chat-flow signup) and /api/signup (the
# public homepage form) used to run anti-bot logic independently. The
# chat-flow path skipped layers 1-3 entirely, so disposable / brand-prefix
# bots had a clean ingress. Extract a single helper used by both endpoints
# so they stay in sync. Returns a JSONResponse to bail out with (always
# 200 OK to avoid oracle-ing the result), or None if the email passes.

async def _anti_bot_check(email: str, source: str) -> JSONResponse | None:
    """Run disposable-domain → MX → brand-prefix checks.

    Returns a 200-OK JSONResponse (silent reject) if any layer fires, or
    None if the email passes all checks. Emits the same PostHog
    `signup_rejected_*` events as loops_webhook.py and the /api/signup
    handler so funnel analytics stay consistent.
    """
    local, domain = _split_email(email)
    if not domain:
        # Caller already validated email shape with _EMAIL_RE so this should
        # not happen in practice — but if it does, drop silently.
        ph_capture(email, "signup_rejected_malformed", {"source": source})
        return JSONResponse({"ok": True})

    # Layer 1 — disposable domain
    if is_disposable_domain(domain):
        logger.warning(
            "%s rejected DISPOSABLE — email=%s domain=%s",
            source, email, domain,
        )
        ph_capture(
            email,
            "signup_rejected_disposable",
            {"domain": domain, "source": source},
        )
        return JSONResponse({"ok": True})

    # Layer 2 — MX record validity (transient DNS failures fall through)
    has_mx, mx_reason = await domain_has_mx(domain)
    if not has_mx:
        logger.warning(
            "%s rejected NO_MX — email=%s domain=%s reason=%s",
            source, email, domain, mx_reason,
        )
        ph_capture(
            email,
            "signup_rejected_mx",
            {"domain": domain, "reason": mx_reason, "source": source},
        )
        return JSONResponse({"ok": True})

    # Layer 3 — brand-prefix on no-name domain
    if is_brand_prefix_suspicious(local, domain):
        logger.warning(
            "%s rejected HEURISTIC — email=%s local=%s domain=%s",
            source, email, local, domain,
        )
        ph_capture(
            email,
            "signup_rejected_heuristic",
            {
                "rule": "brand_prefix_no_name_domain",
                "local": local,
                "domain": domain,
                "source": source,
            },
        )
        return JSONResponse({"ok": True})

    return None


async def _rotate_token_and_create_magic_link(
    pool,
    email_canonical: str,
    client_ip: str,
    *,
    created: bool,
    token: str,
    rotate_notes: str,
) -> str:
    """Atomically rotate (revoke+issue) the token and create the magic-link row.

    For new users (created=True) the already-issued token row is tagged
    source='web' and the magic-link row is added. For existing users
    (created=False) all three writes — revoke previous tokens, INSERT new
    token, INSERT magic-link — run inside a single transaction so a
    partial failure can't leave the customer with no active token.

    For test compatibility ``pool`` may be a fake object that exposes
    ``execute`` directly without ``acquire``/``transaction`` (e.g. the
    in-test ``_FakePool``). We detect this and fall back to per-call
    ``pool.execute`` writes.
    """
    from datetime import datetime, timedelta, timezone

    # Real asyncpg pools have .acquire(); test fakes don't. The fallback
    # path is non-transactional but acceptable for unit tests that aren't
    # exercising the failure-mid-rotation case.
    has_transaction = hasattr(pool, "acquire")

    async def _do_writes(executor) -> str:
        nonlocal token
        if not created:
            logger.info(
                "Web signup: %s re-signing up — revoking existing tokens and issuing fresh magic link",
                email_canonical,
            )
            await executor.execute(
                "UPDATE mcp_tokens SET revoked_at = NOW() "
                "WHERE customer_email = $1 AND revoked_at IS NULL",
                email_canonical,
            )
            token = generate_token()
            t_hash = hash_token(token)
            expires_at = datetime.now(timezone.utc) + timedelta(days=TRIAL_DAYS)
            await executor.execute(
                """
                INSERT INTO mcp_tokens
                    (token_hash, token_prefix, customer_email, plan,
                     daily_limit, expires_at, notes)
                VALUES ($1, $2, $3, 'trial', $4, $5, $6)
                ON CONFLICT (token_hash) DO NOTHING
                """,
                t_hash, token[:15] + "...", email_canonical,
                PLAN_LIMITS.get("trial", 10), expires_at, rotate_notes,
            )
        else:
            t_hash = hash_token(token)
            await executor.execute(
                "UPDATE mcp_tokens SET source = 'web' WHERE token_hash = $1",
                t_hash,
            )
        # Delegate the magic-link insert to the module-level helper so
        # tests that patch `_create_magic_link` continue to intercept it.
        # Pass the executor (conn or pool) so this insert participates in
        # the surrounding transaction when one exists.
        return await _create_magic_link(executor, t_hash, token, client_ip)

    if has_transaction:
        async with pool.acquire() as conn:
            async with conn.transaction():
                return await _do_writes(conn)
    else:
        return await _do_writes(pool)


# ── Handler factory ───────────────────────────────────────────────────

def make_chat_handlers(auth: TokenAuth):
    """Return (signup_handler, activate_handler, chat_handler) bound to auth."""

    async def signup_handler(request: Request) -> JSONResponse:
        """Provision a trial token and email an activation link to the user."""
        # IP rate limit FIRST — before any expensive parsing / DNS work, so
        # bots can't burn the 400 / 200 paths to enumerate or fingerprint.
        client_ip = _get_client_ip(request)
        if not _check_signup_ip_rate_limit(client_ip):
            logger.warning("Signup rate limit hit for IP %s", client_ip)
            return JSONResponse({"error": "Too many requests"}, status_code=429)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)

        email = str(body.get("email", "")).strip().lower()
        if not email or len(email) > 254 or not _EMAIL_RE.match(email):
            return JSONResponse({"error": "Invalid email"}, status_code=400)

        # Anti-bot: disposable / MX / brand-prefix. Returns 200 OK silently
        # on rejection so bots can't enumerate. Same checks as /api/signup.
        rejection = await _anti_bot_check(email, source="chat_signup")
        if rejection is not None:
            return rejection

        # Normalize for deduplication — keep original for sending
        email_canonical = _normalize_email(email)

        try:
            token, created = await auth.create_token(
                email=email_canonical,
                plan="trial",
                notes="web chat signup",
            )
        except Exception:
            logger.exception("DB error provisioning token for %s", email)
            return JSONResponse({"error": "Service error"}, status_code=500)

        try:
            pool = await auth._get_pool()
            link_id = await _rotate_token_and_create_magic_link(
                pool, email_canonical, client_ip,
                created=created, token=token,
                rotate_notes="web chat re-signup",
            )
        except Exception:
            logger.exception("Failed to create magic link for %s", email_canonical)
            return JSONResponse({"error": "Service error"}, status_code=500)

        activation_url = f"{_SITE_BASE}/chat?t={link_id}"
        if created:
            ph_capture(email_canonical, "web_chat_signup", {"plan": "trial"})

        try:
            await _send_activation_email(email, activation_url)
        except Exception:
            logger.exception("Failed to send activation email to %s", email)

        return JSONResponse({"ok": True})

    async def activate_handler(request: Request) -> JSONResponse:
        """Validate a magic link UUID and return the plaintext token."""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)

        magic_token = str(body.get("magic_token", "")).strip()
        if not magic_token:
            logger.warning("activate_handler: missing magic_token — body keys: %s", list(body.keys()))
            return JSONResponse({"error": "Missing magic_token"}, status_code=400)
        try:
            uuid.UUID(magic_token)
        except ValueError:
            logger.warning("activate_handler: invalid UUID format: %r", magic_token[:40])
            return JSONResponse({"error": "Invalid token"}, status_code=400)

        try:
            pool = await auth._get_pool()
            # Atomic check-and-mark: eliminates TOCTOU race where two concurrent
            # requests could both pass the used_at IS NULL check before either marks it used.
            row = await pool.fetchrow(
                """
                UPDATE web_magic_links
                SET used_at = NOW()
                WHERE id = $1
                  AND used_at IS NULL
                  AND expires_at > NOW()
                RETURNING encrypted_token
                """,
                magic_token,
            )
        except Exception:
            logger.exception("DB error during magic link lookup")
            return JSONResponse({"error": "Service error"}, status_code=500)

        if row is None:
            return JSONResponse({"error": "Link expired or already used"}, status_code=410)

        plaintext = _decrypt_token(row["encrypted_token"])
        if plaintext is None:
            logger.error("Fernet decryption failed for magic link %s", magic_token[:8])
            return JSONResponse({"error": "Service error"}, status_code=500)

        ph_capture("anonymous", "magic_link_activated", {})
        # Return the plaintext token in the JSON body so the browser can store
        # it in localStorage and send it as a Bearer header on /api/chat. We
        # also set an HttpOnly cookie as defence-in-depth — the chat handler
        # accepts either. Without the JSON field, the frontend's
        # `activateMagicLink` (site/js/chat.js) treats the response as a no-op:
        # `data.token` is undefined, authState stays "anon", queryCount stays
        # at FREE_LIMIT, and the next user query immediately re-shows the
        # email gate — i.e. the activation link appears to do nothing.
        response = JSONResponse({"ok": True, "token": plaintext})
        response.set_cookie(
            key="nyc_pi_token",
            value=plaintext,
            httponly=True,
            secure=True,
            samesite="none",  # cross-origin: Vercel frontend → Railway backend
            max_age=30 * 24 * 3600,
            path="/api/chat",
        )
        return response

    async def chat_handler(request: Request) -> JSONResponse | StreamingResponse:
        """Agentic SSE chat endpoint."""

        # ── IP rate limit ─────────────────────────────────────────────
        client_ip = _get_client_ip(request)
        if not _check_ip_rate_limit(client_ip):
            return JSONResponse({"error": "Too many requests"}, status_code=429)

        # ── Parse body ────────────────────────────────────────────────
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)

        raw_messages = body.get("messages", [])
        if not isinstance(raw_messages, list) or not raw_messages:
            return JSONResponse({"error": "messages must be a non-empty list"}, status_code=400)

        # Truncate history and validate last user message
        messages = raw_messages[-_MAX_HISTORY:]
        last_msg = messages[-1]
        # Defend against malformed payloads: messages must be dicts. Without
        # this guard `last_msg.get(...)` raises AttributeError if the client
        # sends ints/strings, surfacing as 500 instead of 400.
        if not isinstance(last_msg, dict) or last_msg.get("role") != "user":
            return JSONResponse({"error": "Last message must be a user dict"}, status_code=400)

        # Content must be a string — null-byte strip below assumes str.
        last_content = last_msg.get("content", "")
        if not isinstance(last_content, str):
            return JSONResponse({"error": "Message content must be a string"}, status_code=400)
        user_text = last_content
        if not user_text.strip():
            return JSONResponse({"error": "Empty message"}, status_code=400)
        if len(user_text) > _MAX_MSG_LEN:
            return JSONResponse({"error": f"Message too long (max {_MAX_MSG_LEN} chars)"}, status_code=400)

        # Sanitize: strip null bytes from all messages (only dicts with str content)
        for msg in messages:
            if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                msg["content"] = msg["content"].replace("\x00", "")

        # ── Auth: HttpOnly cookie (web), Bearer header (CLI/API), or free-tier cookie ──
        auth_header = request.headers.get("authorization", "")
        token_info = None
        is_authenticated = False
        query_count = 0
        anon_analyze_count = 0

        # Web activation sets an HttpOnly cookie; CLI/API uses Authorization header.
        cookie_token = request.cookies.get("nyc_pi_token")
        raw_token = cookie_token or (auth_header[7:] if auth_header.startswith("Bearer ") else None)

        if raw_token:
            token_info = await auth.validate(raw_token)
            if token_info is None:
                resp = JSONResponse({"error": "Invalid or expired token"}, status_code=401)
                if cookie_token:
                    # Clear the stale cookie so the browser doesn't keep retrying
                    resp.delete_cookie("nyc_pi_token", path="/api/chat", samesite="none", secure=True)
                return resp

            # Enforce daily web-chat query limit for trial tokens
            allowed, used_count = await auth.check_rate_limit(
                token_info.token_hash, settings.chat_daily_query_limit
            )
            if not allowed:
                return JSONResponse(
                    {
                        "error": "daily_limit_reached",
                        "message": (
                            f"You've used all {settings.chat_daily_query_limit} queries for today. "
                            "Your limit resets at midnight UTC."
                        ),
                        "used": used_count,
                        "limit": settings.chat_daily_query_limit,
                    },
                    status_code=429,
                )

            is_authenticated = True
        else:
            # Anonymous path. Two checks:
            #   (1) Cheap fast-path: signed cookie counter. Bots that clear
            #       cookies trivially bypass this — that's why we follow up
            #       with the IP-hash count below.
            #   (2) Authoritative: COUNT(*) over the last 24h from
            #       anon_chat_queries keyed on ip_hash. This is the upper
            #       bound — once the IP-hash trips it, no cookie state can
            #       extend the budget.
            # Edge case: rotating IPs (cellular / mobile users) can hit the
            # limit faster than expected because each IP starts fresh. We
            # accept that false positive in exchange for closing the
            # cookie-clearing bypass. Documented in
            # docs/launch-playbook-pricing.md / launch-playbook-product-activation.md.
            cookie_val = request.cookies.get("nyprop_sess", "")
            query_count, anon_analyze_count = read_session_cookie(cookie_val) if cookie_val else (0, 0)

            if query_count >= settings.chat_free_query_limit:
                return JSONResponse(
                    {
                        "error": "free_limit_reached",
                        "message": "Enter your email to continue",
                        "free_queries_used": query_count,
                    },
                    status_code=402,
                )

        # Pre-compute IP hash for anonymous tracking. We never store the raw IP.
        # `client_ip` was already extracted above for IP rate limiting (handles
        # Fastly / CF / x-forwarded-for chain). If extraction failed entirely
        # _get_client_ip returns "unknown"; treat that as missing → empty hash.
        if client_ip and client_ip != "unknown":
            anon_ip_hash = _hash_ip(client_ip)
        else:
            anon_ip_hash = ""
            logger.warning("Anon chat: no client IP available for hashing")

        # Authoritative anon limit: COUNT(*) over last 24h by ip_hash.
        # Closes the cookie-clearing bypass. Skipped when:
        #   - request is authenticated (paid path, different limits apply)
        #   - we have no IP hash (can't enforce; fail-open is safer than
        #     blocking every anonymous user when extraction fails)
        if not is_authenticated and anon_ip_hash:
            try:
                pool = await auth._get_pool()
                row = await pool.fetchrow(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM anon_chat_queries
                    WHERE ip_hash = $1
                      AND called_at > NOW() - INTERVAL '24 hours'
                    """,
                    anon_ip_hash,
                )
                ip_count = int(row["cnt"]) if row and row["cnt"] is not None else 0
            except Exception as exc:
                # Table missing (migration not yet applied) or transient DB
                # blip — fail open so a DB outage doesn't lock everyone out.
                logger.warning("Anon IP-hash limit check failed (fail-open): %s", exc)
                ip_count = 0

            if ip_count >= settings.chat_free_query_limit:
                return JSONResponse(
                    {
                        "error": "free_limit_reached",
                        "message": "Enter your email to continue",
                        "free_queries_used": ip_count,
                    },
                    status_code=402,
                )

        # ── Build streaming response ──────────────────────────────────
        new_cookie_val: str | None = None
        if not is_authenticated:
            # Pre-mark analyze as "used" (a=1) so next request will block it.
            # anon_analyze_count still holds the value from THIS request (0 or 1).
            new_cookie_val = make_session_cookie(query_count + 1, max(anon_analyze_count, 1))

        # Build messages in Anthropic format. Strip any client-supplied assistant
        # turns — they could be fabricated to inject instructions into Claude's context.
        # We only trust user turns from the client; assistant turns come from our own
        # streaming responses that the client mirrors back. Filtering to user-only and
        # re-injecting paired assistant turns from trusted history would be ideal, but
        # for now we only accept alternating pairs where the client is the source.
        # Simple protection: drop any standalone assistant message at the start.
        raw_anthropic = [
            {"role": m["role"], "content": m["content"]}
            for m in messages
            if isinstance(m, dict)
            and m.get("role") in ("user", "assistant")
            and m.get("content")
        ]
        # Ensure the conversation starts with a user turn (never an injected assistant turn)
        anthropic_messages = []
        for msg in raw_anthropic:
            if not anthropic_messages and msg["role"] == "assistant":
                continue  # drop leading assistant messages
            anthropic_messages.append(msg)

        async def stream_with_analyze_limit():
            """Wrap _agentic_stream to enforce analyze_property limit mid-stream."""
            analyze_calls_this_request = 0
            pool = await auth._get_pool() if token_info else None

            # Fetch today's analyze count for trial users (resets at midnight UTC).
            analyze_count_today = 0
            if token_info and pool:
                analyze_count_today = await _count_analyze_today(pool, token_info.token_hash)

            async for chunk in _agentic_stream(anthropic_messages):
                # Intercept tool_start for analyze_property to enforce limits
                try:
                    parsed = json.loads(chunk.removeprefix("data: ").strip())
                    if parsed.get("type") == "tool_start" and parsed.get("name") == "analyze_property":
                        if token_info and (analyze_count_today + analyze_calls_this_request) >= settings.chat_analyze_trial_limit:
                            limit = settings.chat_analyze_trial_limit
                            msg = (
                                f"\n\n*You have used all {limit} full analysis reports for today. "
                                "Your limit resets at midnight UTC.*"
                            )
                            yield f"data: {json.dumps({'type': 'text_delta', 'text': msg})}\n\n"
                            yield f"data: {json.dumps({'type': 'done'})}\n\n"
                            return
                        if not token_info and anon_analyze_count >= 1:
                            yield f"data: {json.dumps({'type': 'text_delta', 'text': '\n\n**Full due-diligence reports require a free account.** Sign up below to get 10 queries/day including up to 5 full analysis reports — no credit card required.'})}\n\n"
                            yield f"data: {json.dumps({'type': 'done'})}\n\n"
                            return
                        analyze_calls_this_request += 1
                except Exception:
                    pass
                yield chunk

            # Record exactly one call per request. Use "analyze_property" when
            # analyze ran so _count_analyze_today stays accurate for the daily cap.
            if token_info:
                try:
                    tool_name = "analyze_property" if analyze_calls_this_request else "web_chat"
                    asyncio.create_task(
                        auth.record_call(token_info.token_hash, tool_name, 0, 200)
                    )
                except Exception:
                    pass
            else:
                # Anonymous (pre-email-gate) path. Log a row to anon_chat_queries
                # so we can measure the top-of-funnel — the auth path above is the
                # only place this used to be tracked, leaving anon traffic invisible.
                # Wrapped in try/except: a DB failure here must NEVER break the chat.
                try:
                    anon_pool = pool
                    if anon_pool is None:
                        try:
                            anon_pool = await auth._get_pool()
                        except Exception:
                            anon_pool = None
                    if anon_pool is not None:
                        # query_count from the cookie was the count BEFORE this request;
                        # store the post-request value so the row reflects "this is the Nth
                        # free query for this visitor".
                        asyncio.create_task(
                            _record_anon_chat_query(
                                anon_pool,
                                anon_ip_hash,
                                query_count + 1,
                            )
                        )
                except Exception:
                    pass

        response = StreamingResponse(
            stream_with_analyze_limit(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

        # Set session cookie for anonymous users
        if new_cookie_val:
            response.set_cookie(
                "nyprop_sess",
                new_cookie_val,
                httponly=True,
                secure=True,
                samesite="none",  # cross-origin: Vercel → Railway
                max_age=86400 * 7,
                path="/api",
            )

        return response

    return signup_handler, activate_handler, chat_handler


# ── Public /api/signup handler (replaces direct-to-Loops form) ────────
#
# This endpoint is the new ingress for the homepage `Get Access Token`
# form. It used to POST directly to the public Loops form ID
# (`cmntqdkqy00y20iycvyyxby0m`) — see docs/signup-bot-architecture-2026-05-06.md.
#
# Behaviour:
#   1. Validate JSON + email shape (400 on bad input).
#   2. IP rate-limit (3 per IP per hour, reuses _check_signup_ip_rate_limit
#      from the chat path so the two share a budget).
#   3. Run anti-bot checks (disposable domain → MX → brand-prefix
#      heuristic, reusing helpers from loops_webhook.py).
#      Failed checks return 200 OK silently (so bots can't oracle the
#      result) and fire a PostHog `signup_rejected_*` event.
#   4. Issue or rotate the customer's token, write a magic-link row,
#      send the activation email via the SAME Loops transactional template
#      already used by the chat magic-link flow.
#   5. Return 200 {"ok": true}. The frontend shows "Check your inbox".
#
# Future-compatible inputs (accepted, NOT YET enforced — Phase D will
# wire these to actual Cloudflare Turnstile validation):
#   - hp_field: honeypot field. If non-empty when present, drop silently.
#   - turnstile_token: Cloudflare Turnstile token. When
#     SIGNUP_REQUIRE_TURNSTILE=true (env var, default false), validated
#     server-side. Today: accepted and ignored.
#   - started_at_ms: unix-ms when form was loaded; used for time-on-form
#     check (drops submissions <1.5s after load). Today: accepted, not
#     enforced.

def make_signup_endpoint_handler(auth: TokenAuth):
    """Return a Starlette POST /api/signup handler bound to the given TokenAuth.

    This is the backend replacement for the public Loops form ID. It runs
    every existing anti-bot check, then issues a token + magic link via
    the same chat-flow primitives, then returns 200 {"ok": true}.
    """

    async def signup_endpoint(request: Request) -> JSONResponse:
        # ── IP rate limit FIRST ──────────────────────────────────────
        # Was previously after JSON parse + honeypot, so bots could burn
        # the 400 path with malformed JSON to fingerprint the server.
        client_ip = _get_client_ip(request)
        if not _check_signup_ip_rate_limit(client_ip):
            logger.warning("/api/signup rate limit hit for IP %s", client_ip)
            return JSONResponse({"error": "Too many requests"}, status_code=429)

        # ── Parse body ───────────────────────────────────────────────
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)

        # ── Honeypot stub (accepted today, dropped silently when set) ─
        # When the frontend wires this up, bots that auto-fill every field
        # will trip it. Today the field is always absent so this is a no-op.
        hp_field = body.get("hp_field", "")
        if isinstance(hp_field, str) and hp_field.strip():
            # Silent drop — never let the bot know it tripped the wire.
            ph_capture(
                "anonymous",
                "signup_rejected_honeypot",
                {"source": "api_signup"},
            )
            return JSONResponse({"ok": True})

        # ── Email shape ──────────────────────────────────────────────
        email = str(body.get("email", "")).strip().lower()
        if not email or len(email) > 254 or not _EMAIL_RE.match(email):
            return JSONResponse({"error": "Invalid email"}, status_code=400)

        # Funnel-top event — fires for every well-formed POST that passes
        # rate-limit + email-shape, regardless of whether the email gets
        # a token. Pairs with the existing webhook event of the same name.
        ph_capture(email, "signup_form_submitted", {"source": "api_signup"})

        # ── Anti-bot: disposable / MX / brand-prefix ─────────────────
        # Shared with chat.signup_handler so both endpoints stay in sync.
        rejection = await _anti_bot_check(email, source="api_signup")
        if rejection is not None:
            return rejection

        # ── Issue / rotate token ─────────────────────────────────────
        email_canonical = _normalize_email(email)
        try:
            token, created = await auth.create_token(
                email=email_canonical,
                plan="trial",
                notes="api_signup endpoint",
            )
        except Exception:
            logger.exception("/api/signup DB error provisioning token for %s", email)
            return JSONResponse({"error": "Service error"}, status_code=500)

        try:
            pool = await auth._get_pool()
            link_id = await _rotate_token_and_create_magic_link(
                pool, email_canonical, client_ip,
                created=created, token=token,
                rotate_notes="api_signup re-signup",
            )
        except Exception:
            logger.exception(
                "/api/signup failed to create magic link for %s", email_canonical,
            )
            return JSONResponse({"error": "Service error"}, status_code=500)

        # Activation URL points at the chat magic-link page, which already
        # knows how to consume a `t=<uuid>` query parameter via /api/activate
        # and store the plaintext token in localStorage / HttpOnly cookie.
        activation_url = f"{_SITE_BASE}/chat?t={link_id}"

        if created:
            ph_capture(
                email_canonical,
                "signup_provisioned",
                {"plan": "trial", "source": "api_signup"},
            )

        # Email send is best-effort — the token is already in the DB and
        # can be retrieved by an operator via scripts/manage_tokens.py if
        # the email fails. This matches chat.signup_handler's behaviour.
        try:
            await _send_activation_email(email, activation_url)
        except Exception:
            logger.exception(
                "/api/signup failed to send activation email to %s "
                "(token is provisioned; resend via manage_tokens.py)",
                email,
            )

        return JSONResponse({"ok": True})

    return signup_endpoint
