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
from cryptography.fernet import Fernet, InvalidToken
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse

from nyc_property_intel.analytics import capture as ph_capture
from nyc_property_intel.app import mcp, MCP_INSTRUCTIONS
from nyc_property_intel.auth import PLAN_LIMITS, TRIAL_DAYS, TokenAuth, generate_token, hash_token
from nyc_property_intel.config import settings

logger = logging.getLogger(__name__)

# RFC 5321 max email length is 254; reject anything with control chars or that looks wrong.
_EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')

_SITE_BASE = "https://nycpropertyintel.com"
_LOOPS_API_BASE = "https://app.loops.so/api/v1"
_CHAT_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 4096
_MAX_ROUNDS = 5          # max agentic tool-call rounds per request
_MAX_TOOL_CALLS = 5      # max individual tool calls per request
_STREAM_TIMEOUT = 60.0   # seconds
_MAX_MSG_LEN = 2000      # max user message length
_MAX_HISTORY = 20        # max messages in conversation history

# Module-level Anthropic client (created lazily)
_anthropic_client: anthropic.AsyncAnthropic | None = None

# Cached Anthropic-format tools list (built once on first request)
_anthropic_tools: list[dict] | None = None

# In-memory IP rate limiter: {ip: (count, window_start_ts)}
_ip_buckets: dict[str, tuple[int, float]] = {}
_IP_RATE_LIMIT = 10  # requests per minute per IP


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
    now = time.monotonic()
    count, window_start = _ip_buckets.get(ip, (0, now))
    if now - window_start > 60:
        _ip_buckets[ip] = (1, now)
        return True
    if count >= _IP_RATE_LIMIT:
        return False
    _ip_buckets[ip] = (count + 1, window_start)
    return True


def _get_client_ip(request: Request) -> str:
    # NOTE: X-Forwarded-For is set by Railway's proxy and is not spoofable from outside
    # Railway's infrastructure (Railway appends the real client IP on the right).
    # We use the rightmost value so a client-supplied header cannot bypass the limit.
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[-1].strip()  # rightmost = added by our trusted proxy
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

async def _create_magic_link(pool, token_hash: str, plaintext_token: str) -> str:
    """Insert a magic link row and return the UUID string."""
    link_id = str(uuid.uuid4())
    await pool.execute(
        """
        INSERT INTO web_magic_links (id, token_hash, encrypted_token)
        VALUES ($1, $2, $3)
        """,
        link_id,
        token_hash,
        _encrypt_token(plaintext_token),
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
                        "content": json.dumps({"error": "Tool call budget exceeded"}),
                    })
                    continue

                tool_calls_made += 1
                yield f"data: {json.dumps({'type': 'tool_start', 'name': block.name})}\n\n"

                try:
                    result = await asyncio.wait_for(
                        mcp._tool_manager.call_tool(block.name, block.input or {}, convert_result=False),
                        timeout=45.0,
                    )
                    result_str = json.dumps(result, default=str)
                except asyncio.TimeoutError:
                    result_str = json.dumps({"error": f"{block.name} timed out"})
                except Exception as exc:
                    logger.warning("Tool %s error: %s", block.name, exc)
                    result_str = json.dumps({"error": str(exc)})

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

async def _count_analyze_trial(pool, token_hash: str) -> int:
    """Return total analyze_property calls in the last 30 days for this token."""
    try:
        row = await pool.fetchrow(
            """
            SELECT COUNT(*) AS cnt FROM mcp_usage_log
            WHERE token_hash = $1
              AND tool_name = 'analyze_property'
              AND called_at >= NOW() - INTERVAL '30 days'
            """,
            token_hash,
        )
        return int(row["cnt"]) if row else 0
    except Exception:
        return 0  # fail open


# ── Handler factory ───────────────────────────────────────────────────

def make_chat_handlers(auth: TokenAuth):
    """Return (signup_handler, activate_handler, chat_handler) bound to auth."""

    async def signup_handler(request: Request) -> JSONResponse:
        """Provision a trial token and email an activation link to the user."""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)

        email = str(body.get("email", "")).strip().lower()
        if not email or len(email) > 254 or not _EMAIL_RE.match(email):
            return JSONResponse({"error": "Invalid email"}, status_code=400)

        try:
            token, created = await auth.create_token(
                email=email,
                plan="trial",
                notes="web chat signup",
            )
        except Exception:
            logger.exception("DB error provisioning token for %s", email)
            return JSONResponse({"error": "Service error"}, status_code=500)

        try:
            from datetime import datetime, timedelta, timezone
            pool = await auth._get_pool()
            if not created:
                # Existing user: old plaintext is unrecoverable (only hash stored).
                # Generate a fresh token so we can build a magic link.
                logger.info("Web chat signup: %s already has token — issuing fresh magic link", email)
                token = generate_token()
                t_hash = hash_token(token)
                expires_at = datetime.now(timezone.utc) + timedelta(days=TRIAL_DAYS)
                await pool.execute(
                    """
                    INSERT INTO mcp_tokens
                        (token_hash, token_prefix, customer_email, plan, daily_limit, expires_at, notes)
                    VALUES ($1, $2, $3, 'trial', $4, $5, 'web chat re-signup')
                    ON CONFLICT (token_hash) DO NOTHING
                    """,
                    t_hash, token[:15] + "...", email,
                    PLAN_LIMITS.get("trial", 10), expires_at,
                )
            else:
                t_hash = hash_token(token)
                await pool.execute(
                    "UPDATE mcp_tokens SET source = 'web' WHERE token_hash = $1",
                    t_hash,
                )
            link_id = await _create_magic_link(pool, t_hash, token)
        except Exception:
            logger.exception("Failed to create magic link for %s", email)
            return JSONResponse({"error": "Service error"}, status_code=500)

        activation_url = f"{_SITE_BASE}/chat?t={link_id}"
        if created:
            ph_capture(email, "web_chat_signup", {"plan": "trial"})

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
            row = await pool.fetchrow(
                """
                SELECT id, encrypted_token
                FROM web_magic_links
                WHERE id = $1
                  AND used_at IS NULL
                  AND expires_at > NOW()
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

        try:
            await pool.execute(
                "UPDATE web_magic_links SET used_at = NOW() WHERE id = $1",
                magic_token,
            )
        except Exception:
            logger.warning("Failed to mark magic link %s as used", magic_token[:8])

        ph_capture("anonymous", "magic_link_activated", {})
        return JSONResponse({"token": plaintext})

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
        if last_msg.get("role") != "user":
            return JSONResponse({"error": "Last message must be from user"}, status_code=400)

        user_text = str(last_msg.get("content", ""))
        if not user_text.strip():
            return JSONResponse({"error": "Empty message"}, status_code=400)
        if len(user_text) > _MAX_MSG_LEN:
            return JSONResponse({"error": f"Message too long (max {_MAX_MSG_LEN} chars)"}, status_code=400)

        # Sanitize: strip null bytes from all messages
        for msg in messages:
            if isinstance(msg.get("content"), str):
                msg["content"] = msg["content"].replace("\x00", "")

        # ── Auth: Bearer token or free-tier cookie ────────────────────
        auth_header = request.headers.get("authorization", "")
        token_info = None
        is_authenticated = False
        query_count = 0
        anon_analyze_count = 0

        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            token_info = await auth.validate(token)
            if token_info is None:
                return JSONResponse({"error": "Invalid or expired token"}, status_code=401)

            # analyze_property trial limit enforced mid-stream in stream_with_analyze_limit

            is_authenticated = True
        else:
            # Anonymous path — check signed cookie
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

        # ── Build streaming response ──────────────────────────────────
        new_cookie_val: str | None = None
        if not is_authenticated:
            # Pre-mark analyze as "used" (a=1) so next request will block it.
            # anon_analyze_count still holds the value from THIS request (0 or 1).
            new_cookie_val = make_session_cookie(query_count + 1, max(anon_analyze_count, 1))

        # Build messages in Anthropic format (only role+content, strip extras)
        anthropic_messages = [
            {"role": m["role"], "content": m["content"]}
            for m in messages
            if m.get("role") in ("user", "assistant") and m.get("content")
        ]

        async def stream_with_analyze_limit():
            """Wrap _agentic_stream to enforce analyze_property limit mid-stream."""
            analyze_calls_this_request = 0
            pool = await auth._get_pool() if token_info else None

            # Fetch 30-day analyze count for trial users
            analyze_count_trial = 0
            if token_info and pool:
                analyze_count_trial = await _count_analyze_trial(pool, token_info.token_hash)

            async for chunk in _agentic_stream(anthropic_messages):
                # Intercept tool_start for analyze_property to enforce limits
                try:
                    parsed = json.loads(chunk.removeprefix("data: ").strip())
                    if parsed.get("type") == "tool_start" and parsed.get("name") == "analyze_property":
                        if token_info and (analyze_count_trial + analyze_calls_this_request) >= settings.chat_analyze_trial_limit:
                            yield f"data: {json.dumps({'type': 'text_delta', 'text': '\n\n*You have used all 5 full analysis reports included in your trial. Upgrade to continue.*'})}\n\n"
                            yield f"data: {json.dumps({'type': 'done'})}\n\n"
                            return
                        if not token_info and anon_analyze_count >= 1:
                            yield f"data: {json.dumps({'type': 'text_delta', 'text': '\n\n**Sign up for free to run full property analysis reports.** The free trial includes 3 queries — create an account to unlock 10 queries/day and up to 5 full analysis reports.'})}\n\n"
                            yield f"data: {json.dumps({'type': 'done'})}\n\n"
                            return
                        # Record this analyze call immediately so concurrent requests see it
                        if token_info and pool:
                            asyncio.create_task(
                                auth.record_call(token_info.token_hash, "analyze_property", 0, 200)
                            )
                        analyze_calls_this_request += 1
                except Exception:
                    pass
                yield chunk

            # Record general web_chat request
            if token_info:
                try:
                    asyncio.create_task(
                        auth.record_call(token_info.token_hash, "web_chat", 0, 200)
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
