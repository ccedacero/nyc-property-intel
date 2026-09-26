"""Regression: the 5/day full-report (analyze_property) sub-cap must apply to
TRIAL tokens only — never to a paying plan.

The bug (found 2026-09-26, pre-revenue): the web-chat analyze cap at
chat.py `stream_with_analyze_limit` gated on `if token_info` with no plan
check, so a paying Pro user hit the same "you've used all 5 reports today"
wall as a free trial user — directly contradicting the Pro promise of full,
uncapped access. Paid plans are bounded only by their overall daily_limit.

These tests drive the real streaming handler with a stubbed agentic stream
that emits more analyze_property tool_starts than the trial sub-cap allows,
and assert the cap fires for `trial` but not for `pro`/`team`.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from starlette.requests import Request

from nyc_property_intel import chat as chat_module
from nyc_property_intel.auth import TokenInfo
from nyc_property_intel.chat import make_chat_handlers

CAP_MESSAGE = "full analysis reports for today"  # substring of the block notice


class _FakePool:
    """Minimal asyncpg-pool stand-in — swallows the handler's DB calls."""

    async def execute(self, *_a: Any, **_k: Any) -> str:
        return "OK"

    async def fetchval(self, *_a: Any, **_k: Any) -> Any:
        return 0

    async def fetchrow(self, *_a: Any, **_k: Any) -> Any:
        return None


def _chat_request(token: str) -> Request:
    body = json.dumps({"messages": [{"role": "user", "content": "analyze 350 5th ave"}]}).encode()
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/chat",
        "headers": [
            (b"content-type", b"application/json"),
            (b"authorization", f"Bearer {token}".encode()),
        ],
        "client": ("127.0.0.1", 12345),
    }

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


def _auth_for_plan(plan: str) -> MagicMock:
    auth = MagicMock()
    auth.validate = AsyncMock(
        return_value=TokenInfo(
            token_hash="hash",
            token_prefix="nyprop_x...",
            customer_email="buyer@example.com",
            plan=plan,
            daily_limit=500 if plan != "trial" else 10,
        )
    )
    auth.check_rate_limit = AsyncMock(return_value=(True, 0))
    auth._get_pool = AsyncMock(return_value=_FakePool())
    auth.record_call = AsyncMock(return_value=None)
    return auth


async def _six_analyze_starts(_messages: Any, owner_token_hash: Any = None):
    """Stubbed agentic stream: six analyze_property tool_starts (> the 5 cap)."""
    for _ in range(6):
        yield f"data: {json.dumps({'type': 'tool_start', 'name': 'analyze_property'})}\n\n"
    yield f"data: {json.dumps({'type': 'done'})}\n\n"


async def _run_chat(auth: MagicMock) -> tuple[str, int]:
    """Drive the chat handler; return (full stream text, _count_analyze_today call count)."""
    _, _, chat_handler, _ = make_chat_handlers(auth)
    chat_module._ip_buckets.clear()
    with patch.object(chat_module, "_get_anthropic_tools", return_value=[]), \
         patch.object(chat_module, "_agentic_stream", _six_analyze_starts), \
         patch.object(chat_module, "_count_analyze_today", AsyncMock(return_value=0)) as cnt:
        response = await chat_handler(_chat_request("nyprop_xtoken"))
        body = b""
        async for chunk in response.body_iterator:
            body += chunk if isinstance(chunk, bytes) else chunk.encode()
    return body.decode(), cnt.await_count


class TestAnalyzeCapIsTrialOnly:
    async def test_trial_token_is_report_capped(self) -> None:
        text, _ = await _run_chat(_auth_for_plan("trial"))
        assert CAP_MESSAGE in text, "trial must still hit the 5-report/day sub-cap"

    async def test_pro_token_is_not_report_capped(self) -> None:
        text, count_calls = await _run_chat(_auth_for_plan("pro"))
        assert CAP_MESSAGE not in text, (
            "a paying Pro user must NOT hit the trial report sub-cap — this is the bug"
        )
        # The per-day analyze count isn't even fetched for a paid plan.
        assert count_calls == 0

    async def test_team_token_is_not_report_capped(self) -> None:
        text, _ = await _run_chat(_auth_for_plan("team"))
        assert CAP_MESSAGE not in text
