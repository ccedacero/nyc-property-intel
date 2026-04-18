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
import time
from contextlib import asynccontextmanager
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Mount, Route
from starlette.types import ASGIApp, Receive, Scope, Send

from nyc_property_intel.analytics import capture as ph_capture
from nyc_property_intel.app import mcp
from nyc_property_intel.auth import TokenAuth
from nyc_property_intel.config import settings
from nyc_property_intel.loops_webhook import make_webhook_handler
from nyc_property_intel.db import db_lifespan
from nyc_property_intel.geoclient import close_client as _close_geoclient
from nyc_property_intel.socrata import close_client as _close_socrata

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
        allowed, current_count = await self._auth.check_rate_limit(
            token_info.token_hash, token_info.daily_limit
        )
        if not allowed:
            ph_capture(token_info.token_hash, "rate_limit_hit", {
                "plan": token_info.plan,
                "daily_limit": token_info.daily_limit,
                "used": current_count,
            })
            resp = _json_response(
                scope, 429,
                {
                    "error": "Daily rate limit exceeded",
                    "limit": token_info.daily_limit,
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
                token_info.daily_limit,
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
        if use_streamable:
            @asynccontextmanager
            async def _combined_lifespan(app):
                async with mcp.session_manager.run():
                    yield
            starlette_app = Starlette(
                routes=[
                    Route("/webhook/loops", webhook_handler, methods=["POST"]),
                    Mount("/", mcp_app),
                ],
                lifespan=_combined_lifespan,
            )
        else:
            starlette_app = Starlette(routes=[
                Route("/webhook/loops", webhook_handler, methods=["POST"]),
                Mount("/", mcp_app),
            ])

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
