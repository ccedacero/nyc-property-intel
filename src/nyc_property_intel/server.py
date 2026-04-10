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
from nyc_property_intel.geoclient import close_client

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
            await close_client()


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
    transport = os.getenv("MCP_TRANSPORT", "stdio")

    if transport == "sse":
        import anyio
        import uvicorn

        port = int(os.getenv("PORT", "8000"))
        mcp.settings.host = "0.0.0.0"
        mcp.settings.port = port

        if settings.mcp_server_token:
            logger.info("SSE transport: bearer token auth enabled")
            starlette_app = _BearerTokenMiddleware(
                mcp.sse_app(), settings.mcp_server_token
            )
        else:
            logger.warning(
                "SSE transport: MCP_SERVER_TOKEN is not set — "
                "endpoint is unauthenticated. Set MCP_SERVER_TOKEN for production."
            )
            starlette_app = mcp.sse_app()

        logger.info(
            "Starting NYC Property Intel MCP server v0.1.0 "
            "(SSE transport on port %d)",
            port,
        )

        async def _run_sse() -> None:
            config = uvicorn.Config(
                starlette_app,
                host="0.0.0.0",
                port=port,
                log_level=settings.log_level.lower(),
            )
            await uvicorn.Server(config).serve()

        anyio.run(_run_sse)
    else:
        logger.info("Starting NYC Property Intel MCP server v0.1.0 (stdio)")
        mcp.run(transport=transport)  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
