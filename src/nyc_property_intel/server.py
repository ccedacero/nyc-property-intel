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
from contextlib import asynccontextmanager
from typing import Any


@asynccontextmanager
async def server_lifespan(server: Any):
    """Combined lifespan: database pool + geoclient HTTP client."""
    async with db_lifespan(server):
        try:
            yield
        finally:
            await close_client()


mcp._lifespan = server_lifespan

# ── Import tool modules ──────────────────────────────────────────────
# Each tool module uses @mcp.tool() decorators that register themselves
# when the module is imported. Add new tool module imports here.

from nyc_property_intel.tools import (
    analysis,  # noqa: F401
    comps,  # noqa: F401
    history,  # noqa: F401
    issues,  # noqa: F401
    lookup,  # noqa: F401
)

# ── Entry point ──────────────────────────────────────────────────────

def main() -> None:
    """Start the MCP server."""
    logger.info("Starting NYC Property Intel MCP server v0.1.0")
    mcp.run()


if __name__ == "__main__":
    main()
