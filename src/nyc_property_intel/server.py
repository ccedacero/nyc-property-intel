"""MCP server entry point.

This module wires everything together:
  1. Imports the FastMCP instance from app.py
  2. Configures the database lifespan (startup/shutdown)
  3. Imports tool modules so their @mcp.tool() decorators register
  4. Starts the server

Run directly:
    uv run src/nyc_property_intel/server.py

Or via the project script:
    uv run nyc-property-intel
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from typing import Any

import asyncpg

from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Mount, Route
from starlette.types import ASGIApp, Receive, Scope, Send

from nyc_property_intel.analytics import capture as ph_capture
from nyc_property_intel.app import mcp
from nyc_property_intel.auth import PLAN_LIMITS, TokenAuth
from nyc_property_intel.chat import (
    _check_watch_ip_rate_limit,
    _get_client_ip,
    make_chat_handlers,
    make_signup_endpoint_handler,
)
from nyc_property_intel.config import settings
from nyc_property_intel.loops_webhook import is_disposable_domain, make_webhook_handler
from nyc_property_intel.db import db_lifespan
from nyc_property_intel.geoclient import close_client as _close_geoclient
from nyc_property_intel.socrata import close_client as _close_socrata

# ── Sentry error tracking (optional) ─────────────────────────────────
# Initialised at import time, before the Starlette app is built, so the
# Starlette integration auto-attaches to the ASGI middleware stack.
# No-op when SENTRY_DSN is unset (local dev).
if settings.sentry_dsn:
    import sentry_sdk
    from sentry_sdk.integrations.starlette import StarletteIntegration

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.sentry_environment,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        # Don't ship request bodies — they may contain emails or addresses.
        send_default_pii=False,
        integrations=[StarletteIntegration()],
    )

# ── Logging setup ─────────────────────────────────────────────────────

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)

# ── Register lifespan ────────────────────────────────────────────────

# Wrap the db lifespan to also clean up the geoclient httpx client.


@asynccontextmanager
async def server_lifespan(server: Any):
    """Combined lifespan: database pool + geoclient HTTP client."""
    async with db_lifespan(server):
        try:
            yield
        finally:
            try:
                await _close_geoclient()
            except Exception:
                logger.exception("Error closing geoclient HTTP client")
            try:
                await _close_socrata()
            except Exception:
                logger.exception("Error closing Socrata HTTP client")


mcp.settings.lifespan = server_lifespan


# ── Auth middleware ───────────────────────────────────────────────────

def _json_response(scope: Scope, status: int, body: dict, extra_headers: dict | None = None) -> Response:
    headers = {"Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    return Response(json.dumps(body), status_code=status, headers=headers)


async def _read_body(receive: Receive) -> bytes:
    """Buffer the full HTTP request body from the ASGI receive channel."""
    chunks: list[bytes] = []
    more = True
    while more:
        message = await receive()
        chunks.append(message.get("body", b""))
        more = message.get("more_body", False)
    return b"".join(chunks)


def _make_receive(body: bytes) -> Receive:
    """Return a receive callable that replays the already-buffered body."""
    consumed = False

    async def receive() -> dict:
        nonlocal consumed
        if not consumed:
            consumed = True
            return {"type": "http.request", "body": body, "more_body": False}
        # Body already consumed — park indefinitely (request is done).
        await asyncio.sleep(3600)
        return {"type": "http.disconnect"}  # unreachable in practice

    return receive


def _extract_tool_name(body: bytes) -> str | None:
    """Best-effort extraction of the tool name from an MCP JSON-RPC body."""
    try:
        data = json.loads(body)
        # MCP tools/call: {"method": "tools/call", "params": {"name": "lookup_property", ...}}
        if data.get("method") == "tools/call":
            return data.get("params", {}).get("name")
    except Exception:
        pass
    return None


class _BodySizeLimitMiddleware:
    """Reject requests whose Content-Length exceeds path-specific caps.

    Caps:
      /api/*  → 64 KB   (chat / signup / activate JSON bodies are tiny)
      /mcp    → 256 KB  (MCP tool-call payloads can be larger)
      else    → no cap

    Returns 413 Payload Too Large with no body so the client retries with
    a smaller payload rather than DoS-ing memory by streaming a multi-MB
    request body. We only check the Content-Length header — chunked
    requests without a length are rare against this app and Starlette's
    own request-body buffering gives a second line of defense.
    """

    _API_CAP = 64 * 1024
    _MCP_CAP = 256 * 1024

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        path = scope.get("path", "")
        cap: int | None = None
        if path.startswith("/api/"):
            cap = self._API_CAP
        elif path.startswith("/mcp") or path.startswith("/messages") or path.startswith("/sse"):
            cap = self._MCP_CAP

        if cap is not None:
            headers = {k.lower(): v for k, v in scope.get("headers", [])}
            cl_raw = headers.get(b"content-length", b"")
            try:
                content_length = int(cl_raw) if cl_raw else 0
            except ValueError:
                content_length = 0
            if content_length > cap:
                resp = _json_response(
                    scope, 413,
                    {"error": "Payload too large", "max_bytes": cap},
                )
                await resp(scope, receive, send)
                return

        await self._app(scope, receive, send)


class _TokenAuthMiddleware:
    """Per-customer token auth middleware with rate limiting and usage logging.

    On every HTTP request:
      1. Extract Bearer token from Authorization header.
      2. Validate against DB (with in-memory TTL cache).
      3. Check daily rate limit.
      4. Buffer request body to extract MCP tool name.
      5. Forward request to the MCP app.
      6. Fire-and-forget: record call (increment counter + write log row).

    Returns 401 for missing/invalid tokens, 429 for rate limit exceeded.
    Auth DB errors fail open (allow the request) to avoid blocking legitimate
    users during transient DB hiccups.
    """

    def __init__(self, app: ASGIApp, auth: TokenAuth) -> None:
        self._app = app
        self._auth = auth

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        # GET /mcp is used by Claude Code for initial connection/SSE discovery.
        # Allow it through unauthenticated so the server shows as "connected";
        # actual tool calls come in as POST and are always authenticated.
        if scope.get("method") == "GET":
            await self._app(scope, receive, send)
            return

        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        auth_header = headers.get(b"authorization", b"").decode("utf-8", errors="replace")

        if not auth_header.startswith("Bearer "):
            ph_capture("anonymous", "auth_failed", {"reason": "missing_token"})
            resp = _json_response(
                scope, 401,
                {"error": "Missing bearer token"},
            )
            await resp(scope, receive, send)
            return

        token = auth_header[7:]  # strip "Bearer "

        # ── Validate token ────────────────────────────────────────────
        token_info = await self._auth.validate(token)
        if token_info is None:
            ph_capture("anonymous", "auth_failed", {"reason": "invalid_token"})
            resp = _json_response(
                scope, 401,
                {"error": "Invalid or revoked token"},
            )
            await resp(scope, receive, send)
            return

        # ── Rate limit check ──────────────────────────────────────────
        # Use the lesser of the token's stored limit and the current PLAN_LIMITS
        # for that plan. This lets us tighten plan caps (e.g. trial 999999 → 10)
        # without backfilling existing rows — older tokens are clamped at read
        # time, while still permitting per-token bespoke higher limits if we
        # ever need them (the stored value wins when it's *lower* than the plan).
        plan_cap = PLAN_LIMITS.get(token_info.plan, token_info.daily_limit)
        effective_limit = min(token_info.daily_limit, plan_cap)
        allowed, current_count = await self._auth.check_rate_limit(
            token_info.token_hash, effective_limit
        )
        if not allowed:
            ph_capture(token_info.token_hash, "rate_limit_hit", {
                "plan": token_info.plan,
                "daily_limit": effective_limit,
                "used": current_count,
            })
            resp = _json_response(
                scope, 429,
                {
                    "error": "Daily rate limit exceeded",
                    "limit": effective_limit,
                    "used": current_count,
                    "resets": "midnight UTC",
                },
                {"Retry-After": "86400"},
            )
            logger.warning(
                "Rate limit hit: %s (%s plan, %d/%d calls today)",
                token_info.customer_email,
                token_info.plan,
                current_count,
                effective_limit,
            )
            await resp(scope, receive, send)
            return

        # ── Buffer body & extract tool name ──────────────────────────
        body = await _read_body(receive)
        tool_name = _extract_tool_name(body)

        # ── Forward request, track response ──────────────────────────
        start = time.monotonic()
        status_code: list[int] = [200]

        async def send_wrapper(message: dict) -> None:
            if message.get("type") == "http.response.start":
                status_code[0] = message.get("status", 200)
            await send(message)

        try:
            await self._app(scope, _make_receive(body), send_wrapper)
        finally:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            asyncio.create_task(
                self._auth.record_call(
                    token_info.token_hash,
                    tool_name,
                    elapsed_ms,
                    status_code[0],
                )
            )
            if tool_name:
                ph_capture(token_info.token_hash, "tool_called", {
                    "tool_name": tool_name,
                    "duration_ms": elapsed_ms,
                    "status_code": status_code[0],
                    "plan": token_info.plan,
                })

# ── Import tool modules ──────────────────────────────────────────────
# Each tool module uses @mcp.tool() decorators that register themselves
# when the module is imported. Add new tool module imports here.

from nyc_property_intel.tools import (  # noqa: E402
    analysis,  # noqa: F401
    comps,  # noqa: F401
    complaints_311,  # noqa: F401
    dob_complaints,  # noqa: F401
    evictions,  # noqa: F401
    fdny,  # noqa: F401
    nypd_crime,  # noqa: F401
    history,  # noqa: F401
    hpd_complaints,  # noqa: F401
    hpd_litigations,  # noqa: F401
    hpd_registration,  # noqa: F401
    issues,  # noqa: F401
    liens,  # noqa: F401
    lookup,  # noqa: F401
    neighborhood,  # noqa: F401
    permits,  # noqa: F401
    rentstab,  # noqa: F401
    tax,  # noqa: F401
)

# ── Entry point ──────────────────────────────────────────────────────

def main() -> None:
    """Start the MCP server.

    Transport is selected via the MCP_TRANSPORT environment variable:
      - "stdio"  (default) — local mode for Claude Desktop / Claude Code
      - "sse"              — hosted mode for Railway / cloud deployments
    """
    if not settings.socrata_app_token:
        logger.warning(
            "SOCRATA_APP_TOKEN is not set — Socrata-backed tools (311 complaints, "
            "NYPD crime, FDNY incidents) will use anonymous API access and may hit "
            "strict rate limits after ~100 requests/day. Register at "
            "https://data.cityofnewyork.us/profile/edit/developer_settings"
        )

    transport = os.getenv("MCP_TRANSPORT", "stdio")

    if transport in ("sse", "http"):
        import anyio
        import uvicorn
        from mcp.server.transport_security import TransportSecuritySettings

        port = int(os.getenv("PORT", "8000"))
        mcp.settings.host = "0.0.0.0"
        mcp.settings.port = port
        # FastMCP defaults to localhost-only allowed_hosts when initialized
        # without an explicit host, blocking Railway's forwarded Host header.
        # Explicitly allowlist the public hostname(s) via MCP_ALLOWED_HOSTS
        # (comma-separated). Falls back to disabling protection only if the
        # env var is not set.
        raw_hosts = os.getenv("MCP_ALLOWED_HOSTS", "")
        allowed_hosts = [h.strip() for h in raw_hosts.split(",") if h.strip()]
        mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=bool(allowed_hosts),
            allowed_hosts=allowed_hosts,
        )

        # "http" = Streamable HTTP (MCP spec 2025-03-26, single POST /mcp endpoint)
        # "sse"  = legacy SSE transport (GET /sse + POST /messages)
        # Streamable HTTP works through Railway/Fastly CDN; SSE does not (421).
        use_streamable = transport == "http"
        transport_name = "Streamable HTTP" if use_streamable else "SSE"

        raw_app = mcp.streamable_http_app() if use_streamable else mcp.sse_app()

        if settings.mcp_auth_disabled:
            logger.warning(
                "%s transport: MCP_AUTH_DISABLED=true — endpoint is UNAUTHENTICATED. "
                "Never use this in production.",
                transport_name,
            )
            mcp_app = raw_app
            auth = TokenAuth(settings.database_url)
        else:
            logger.info("%s transport: per-customer token auth enabled", transport_name)
            auth = TokenAuth(settings.database_url)
            mcp_app = _TokenAuthMiddleware(raw_app, auth)

        # ── Combined app: webhook route (no auth) + MCP (auth) ───────
        # Streamable HTTP needs session_manager.run() entered via lifespan;
        # Starlette does NOT auto-run a mounted sub-app's lifespan, so we
        # propagate it here. SSE has no such requirement.
        webhook_handler = make_webhook_handler(auth)
        signup_handler, activate_handler, chat_handler = make_chat_handlers(auth)
        # Public website signup endpoint — replaces the direct-to-Loops
        # form ID. See docs/signup-rebuild-plan-2026-05-06.md.
        api_signup_handler = make_signup_endpoint_handler(auth)
        allowed_origins = [
            o.strip()
            for o in settings.chat_allowed_origins.split(",")
            if o.strip()
        ]

        async def health_handler(request: Request) -> Response:
            """Liveness check: app is up and DB is reachable. Always cheap."""
            try:
                pool = await auth._get_pool()
                async with pool.acquire() as conn:
                    await conn.fetchval("SELECT 1")
            except Exception as exc:
                logger.warning("Health check DB ping failed: %s", exc)
                return Response(
                    json.dumps({"status": "degraded", "db": "unreachable"}),
                    status_code=503,
                    media_type="application/json",
                )
            return Response('{"status":"ok"}', media_type="application/json")

        async def healthz_handler(request: Request) -> Response:
            """Deep health check: DB reachable AND tier-1 syncs fresh (<48h).

            Used by Better Stack and similar uptime monitors. Returns 503 if
            any tier-1 dataset hasn't synced successfully in 48 hours so we
            page on stale data, not just downtime.
            """
            checks: dict[str, Any] = {"status": "ok"}
            try:
                pool = await auth._get_pool()
                async with pool.acquire() as conn:
                    await conn.fetchval("SELECT 1")
                    rows = await conn.fetch(
                        """
                        SELECT dataset_key,
                               EXTRACT(EPOCH FROM (NOW() - last_success_at)) / 3600 AS age_h
                          FROM sync_state
                         WHERE last_success_at IS NOT NULL
                        """
                    )
                stale = [
                    r["dataset_key"]
                    for r in rows
                    # Tier-2/3 datasets sync less often — only alert on tier-1.
                    if r["dataset_key"] in {
                        "hpd_violations",
                        "hpd_complaints_and_problems",
                        "hpd_litigations",
                        "dob_violations",
                        "ecb_violations",
                        "real_property_master",
                    }
                    and r["age_h"] is not None and r["age_h"] > 48
                ]
                checks["db"] = "ok"
                checks["stale_datasets"] = stale
                if stale:
                    checks["status"] = "degraded"
                    return Response(
                        json.dumps(checks),
                        status_code=503,
                        media_type="application/json",
                    )
            except Exception as exc:
                logger.warning("Healthz check failed: %s", exc)
                return Response(
                    json.dumps({"status": "degraded", "error": str(exc)[:120]}),
                    status_code=503,
                    media_type="application/json",
                )
            return Response(json.dumps(checks), media_type="application/json")

        async def report_handler(request: Request) -> Response:
            """Serve a persisted shareable report as JSON (feature 1.8, /r/<id>).

            Public and auth-free by design: the permalink is the referral loop.
            The static /report.html page fetches this and renders the markdown.
            """
            rid = request.path_params.get("id", "")
            # Slug is secrets.token_urlsafe(8) → URL-safe base64 (alnum, - and _).
            if not (6 <= len(rid) <= 32) or not all(
                c.isalnum() or c in "-_" for c in rid
            ):
                return Response(
                    '{"error":"not_found"}',
                    status_code=404,
                    media_type="application/json",
                )
            try:
                pool = await auth._get_pool()
                row = await pool.fetchrow(
                    "SELECT id, bbl, address, query, report_md, created_at "
                    "FROM shared_reports WHERE id = $1",
                    rid,
                )
            except asyncpg.UndefinedTableError:
                # Table not provisioned yet (no report has ever been written).
                # A missing table means the report definitionally does not
                # exist → 404, not a 503. The table self-provisions on the
                # first report write (chat._ensure_reports_table).
                return Response(
                    '{"error":"not_found"}',
                    status_code=404,
                    media_type="application/json",
                )
            except Exception as exc:
                logger.warning("report_handler DB error: %s", exc)
                return Response(
                    '{"error":"unavailable"}',
                    status_code=503,
                    media_type="application/json",
                )
            if row is None:
                return Response(
                    '{"error":"not_found"}',
                    status_code=404,
                    media_type="application/json",
                )
            payload = {
                "id": row["id"],
                "bbl": row["bbl"],
                "address": row["address"],
                "query": row["query"],
                "report_md": row["report_md"],
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            }
            return Response(
                json.dumps(payload),
                media_type="application/json",
                headers={"Cache-Control": "public, max-age=600"},
            )

        async def watch_handler(request: Request) -> Response:
            """Subscribe (email, bbl) to building-change alerts (feature 1.9).

            Public + auth-free, like the report permalink. Validates email shape
            + a non-disposable domain + a 10-digit BBL, then records the watch
            (baseline = the building's current open-risk snapshot, so the user
            is only alerted on future changes).
            """

            def _err(code: str, status: int) -> Response:
                return Response(
                    json.dumps({"error": code}),
                    status_code=status,
                    media_type="application/json",
                )

            try:
                body = await request.json()
            except Exception:
                return _err("invalid_json", 400)

            email = str(body.get("email") or "").strip().lower()
            bbl = str(body.get("bbl") or "").strip()
            address = str(body.get("address") or "").strip() or None

            if len(email) > 254 or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
                return _err("invalid_email", 400)
            try:
                if is_disposable_domain(email.split("@", 1)[1]):
                    return _err("disposable_email", 400)
            except Exception:
                pass  # never block a signup on a validator hiccup
            if not (bbl.isdigit() and len(bbl) == 10):
                return _err("invalid_bbl", 400)

            # Abuse control: IP rate limit (after shape validation, so malformed
            # probes don't consume the budget). Per-email cap + double-opt-in are
            # enforced in register_watch.
            if not _check_watch_ip_rate_limit(_get_client_ip(request)):
                return _err("rate_limited", 429)

            try:
                from nyc_property_intel.watch import _send_confirm_email, register_watch

                result = await register_watch(email, bbl, address)
            except Exception as exc:
                logger.warning("watch register failed for %s/%s: %s", email, bbl, exc)
                return _err("unavailable", 503)

            status = result.get("status")
            if status == "limit_exceeded":
                return _err("watch_limit", 429)
            if status == "pending":
                # New, unconfirmed email — send the double-opt-in confirmation.
                confirm_url = f"https://nycpropertyintel.com/watch-confirm?t={result['token']}"
                await _send_confirm_email(email, confirm_url)
                return Response(
                    '{"ok":true,"confirm_required":true}', media_type="application/json"
                )
            return Response('{"ok":true}', media_type="application/json")

        async def watch_confirm_handler(request: Request) -> Response:
            """Confirm a watch email (double-opt-in). POST {"token": "<id>"}."""
            try:
                body = await request.json()
            except Exception:
                body = {}
            token = str(body.get("token") or "").strip()
            if not (6 <= len(token) <= 32) or not all(
                c.isalnum() or c in "-_" for c in token
            ):
                return Response(
                    '{"error":"invalid_token"}', status_code=400, media_type="application/json"
                )
            try:
                from nyc_property_intel.watch import confirm_email

                email = await confirm_email(token)
            except Exception as exc:
                logger.warning("watch confirm failed: %s", exc)
                return Response(
                    '{"error":"unavailable"}', status_code=503, media_type="application/json"
                )
            if email is None:
                return Response(
                    '{"error":"not_found"}', status_code=404, media_type="application/json"
                )
            return Response('{"ok":true}', media_type="application/json")

        if use_streamable:
            @asynccontextmanager
            async def _combined_lifespan(app):
                async with mcp.session_manager.run():
                    yield
            starlette_app = Starlette(
                routes=[
                    Route("/health", health_handler, methods=["GET"]),
                    Route("/healthz", healthz_handler, methods=["GET"]),
                    Route("/webhook/loops", webhook_handler, methods=["POST"]),
                    Route("/api/signup", api_signup_handler, methods=["POST"]),
                    Route("/api/chat/signup", signup_handler, methods=["POST"]),
                    Route("/api/activate", activate_handler, methods=["POST"]),
                    Route("/api/chat", chat_handler, methods=["POST"]),
                    Route("/api/report/{id}", report_handler, methods=["GET"]),
                    Route("/api/watch", watch_handler, methods=["POST"]),
                    Route("/api/watch/confirm", watch_confirm_handler, methods=["POST"]),
                    Mount("/", mcp_app),
                ],
                lifespan=_combined_lifespan,
            )
        else:
            starlette_app = Starlette(routes=[
                Route("/health", health_handler, methods=["GET"]),
                Route("/healthz", healthz_handler, methods=["GET"]),
                Route("/webhook/loops", webhook_handler, methods=["POST"]),
                Route("/api/signup", api_signup_handler, methods=["POST"]),
                Route("/api/chat/signup", signup_handler, methods=["POST"]),
                Route("/api/activate", activate_handler, methods=["POST"]),
                Route("/api/chat", chat_handler, methods=["POST"]),
                Route("/api/report/{id}", report_handler, methods=["GET"]),
                Route("/api/watch", watch_handler, methods=["POST"]),
                Route("/api/watch/confirm", watch_confirm_handler, methods=["POST"]),
                Mount("/", mcp_app),
            ])

        # Body-size cap (innermost so it runs BEFORE CORS / route dispatch
        # consumes the body). Wrap the app *before* CORSMiddleware so a
        # too-large request returns 413 with the appropriate CORS headers
        # added by the outer wrapper.
        starlette_app = _BodySizeLimitMiddleware(starlette_app)

        starlette_app = CORSMiddleware(
            starlette_app,
            allow_origins=allowed_origins,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
            allow_credentials=True,
        )

        if settings.loops_api_key:
            logger.info("Loops webhook enabled at /webhook/loops")
        else:
            logger.warning(
                "LOOPS_API_KEY not set — webhook endpoint is live but will not push "
                "tokens to Loops contacts. Set LOOPS_API_KEY in Railway env vars."
            )

        logger.info(
            "Starting NYC Property Intel MCP server v0.1.0 "
            "(%s transport on port %d)",
            transport_name,
            port,
        )

        async def _run() -> None:
            config = uvicorn.Config(
                starlette_app,
                host="0.0.0.0",
                port=port,
                log_level=settings.log_level.lower(),
                proxy_headers=True,
                forwarded_allow_ips="*",
            )
            await uvicorn.Server(config).serve()

        anyio.run(_run)
    else:
        logger.info("Starting NYC Property Intel MCP server v0.1.0 (stdio)")
        mcp.run(transport=transport)  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
