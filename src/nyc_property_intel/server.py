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

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from nyc_property_intel.app import mcp
from nyc_property_intel.config import settings
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


# ── Bearer token middleware (SSE transport only) ─────────────────────


class _BearerTokenMiddleware:
    """Pure-ASGI middleware that enforces a static bearer token.

    Unlike Starlette's BaseHTTPMiddleware this does NOT buffer the response
    body, so it is safe to wrap SSE (streaming) endpoints.
    """

    def __init__(self, app: ASGIApp, token: str) -> None:
        self._app = app
        self._token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            headers = {k.lower(): v for k, v in scope.get("headers", [])}
            auth = headers.get(b"authorization", b"").decode("utf-8", errors="replace")
            expected = f"Bearer {self._token}"
            if auth != expected:
                if scope["type"] == "http":
                    response = Response(
                        '{"error":"Unauthorized"}',
                        status_code=401,
                        media_type="application/json",
                        headers={"WWW-Authenticate": "Bearer"},
                    )
                    await response(scope, receive, send)
                else:
                    await send({"type": "websocket.close", "code": 1008})
                return
        await self._app(scope, receive, send)

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

        if settings.mcp_server_token:
            logger.info("%s transport: bearer token auth enabled", transport_name)
            raw_app = mcp.streamable_http_app() if use_streamable else mcp.sse_app()
            starlette_app = _BearerTokenMiddleware(raw_app, settings.mcp_server_token)
        else:
            logger.warning(
                "%s transport: MCP_SERVER_TOKEN is not set — "
                "endpoint is unauthenticated. Set MCP_SERVER_TOKEN for production.",
                transport_name,
            )
            starlette_app = mcp.streamable_http_app() if use_streamable else mcp.sse_app()

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
