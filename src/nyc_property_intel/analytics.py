"""PostHog product analytics — fire-and-forget server-side event capture.

Usage:
    from nyc_property_intel.analytics import capture
    capture("token_hash_abc123", "tool_called", {"tool_name": "lookup_property"})

No-ops silently when POSTHOG_API_KEY is not set, so local dev requires no config.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_posthog: Any = None
_initialized = False


def _get_client() -> Any:
    global _posthog, _initialized
    if _initialized:
        return _posthog

    _initialized = True
    from nyc_property_intel.config import settings

    if not settings.posthog_api_key:
        return None

    try:
        from posthog import Posthog
        _posthog = Posthog(
            project_api_key=settings.posthog_api_key,
            host="https://us.i.posthog.com",
        )
        logger.info("PostHog analytics initialized")
    except ImportError:
        logger.warning("posthog package not installed — analytics disabled")

    return _posthog


def capture(distinct_id: str, event: str, properties: dict[str, Any] | None = None) -> None:
    """Send a PostHog event. Non-blocking — SDK batches in a background thread."""
    client = _get_client()
    if client is None:
        return
    try:
        client.capture(distinct_id=distinct_id, event=event, properties=properties or {})
    except Exception as exc:
        logger.debug("PostHog capture error: %s", exc)
