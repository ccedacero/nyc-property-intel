"""Tests for shareable report permalinks (feature 1.8, /r/<id>).

Covers the capture helpers in chat.py that persist a completed full analysis:

  * _last_user_text  — extracts the query from the conversation
  * _BBL_RE / _ADDR_RE — pull the resolved BBL/address out of a tool result
  * _persist_shared_report — generates a slug, inserts one row, enforces the
    min-length skip and the field caps, and never persists a trivial answer

The DB pool is mocked the same way the other chat tests mock it — no live DB.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from nyc_property_intel import chat as chat_module
from nyc_property_intel.chat import (
    _ADDR_RE,
    _BBL_RE,
    _REPORT_MAX_CHARS,
    _REPORT_MIN_CHARS,
    _last_user_text,
    _persist_shared_report,
)


# ── _last_user_text ───────────────────────────────────────────────────


def test_last_user_text_string_content():
    msgs = [{"role": "user", "content": "Who owns 350 5th Ave?"}]
    assert _last_user_text(msgs) == "Who owns 350 5th Ave?"


def test_last_user_text_block_list_content():
    msgs = [
        {"role": "user", "content": [{"type": "text", "text": "full DD on 1 Wall St"}]}
    ]
    assert _last_user_text(msgs) == "full DD on 1 Wall St"


def test_last_user_text_picks_most_recent_user_turn():
    msgs = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "..."},
        {"role": "user", "content": "second"},
    ]
    assert _last_user_text(msgs) == "second"


def test_last_user_text_no_user_turn():
    assert _last_user_text([{"role": "assistant", "content": "hi"}]) == ""


# ── BBL / address extraction ──────────────────────────────────────────


def test_bbl_regex_extracts_ten_digit_bbl():
    tool_result = '<tool_result>\n{"bbl": "1008200001", "owner": "ACME"}\n</tool_result>'
    m = _BBL_RE.search(tool_result)
    assert m and m.group(1) == "1008200001"


def test_bbl_regex_handles_unquoted_numeric():
    assert _BBL_RE.search('{"bbl": 2025180028}').group(1) == "2025180028"


def test_addr_regex_extracts_address():
    tool_result = '{"address": "350 5TH AVENUE, MANHATTAN", "bbl": "1008200001"}'
    assert _ADDR_RE.search(tool_result).group(1) == "350 5TH AVENUE, MANHATTAN"


# ── _persist_shared_report ────────────────────────────────────────────


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_persist_skips_trivial_report():
    """A too-short answer (error / empty) must not create a permalink."""
    short = "x" * (_REPORT_MIN_CHARS - 1)
    with patch.object(chat_module, "get_pool", new=AsyncMock()) as gp:
        rid = _run(_persist_shared_report("1008200001", "350 5th Ave", "q", short))
    assert rid is None
    gp.assert_not_called()  # never even touched the pool


def _insert_call_args(fake_pool):
    """Return the args tuple of the single INSERT INTO shared_reports call."""
    inserts = [
        c for c in fake_pool.execute.await_args_list
        if "INSERT INTO shared_reports" in c.args[0]
    ]
    assert len(inserts) == 1, f"expected one INSERT, got {len(inserts)}"
    return inserts[0].args


def test_persist_inserts_and_returns_id():
    long_md = "# Report\n\n" + ("data point. " * 60)  # comfortably over the floor
    assert len(long_md) >= _REPORT_MIN_CHARS

    fake_pool = AsyncMock()
    # Reset the once-per-process guard so the DDL ensure runs in this test.
    chat_module._reports_table_ready = False
    with patch.object(chat_module, "get_pool", new=AsyncMock(return_value=fake_pool)):
        rid = _run(
            _persist_shared_report("1008200001", "350 5TH AVE", "who owns it", long_md)
        )

    assert isinstance(rid, str) and len(rid) >= 6
    # The DDL ensure ran (table + index) before the insert.
    ddl = [c.args[0] for c in fake_pool.execute.await_args_list if "CREATE TABLE" in c.args[0]]
    assert len(ddl) == 1
    args = _insert_call_args(fake_pool)
    # args = (sql, id, bbl, address, query, report_md)
    assert args[1] == rid
    assert args[2] == "1008200001"
    assert args[3] == "350 5TH AVE"
    assert args[4] == "who owns it"
    assert args[5] == long_md.strip()  # stored markdown is whitespace-stripped


def test_persist_caps_field_lengths():
    long_md = "z" * (_REPORT_MAX_CHARS + 5000)
    fake_pool = AsyncMock()
    with patch.object(chat_module, "get_pool", new=AsyncMock(return_value=fake_pool)):
        rid = _run(
            _persist_shared_report(
                "1008200001", "A" * 300, "Q" * 2000, long_md
            )
        )
    args = _insert_call_args(fake_pool)
    assert len(args[3]) == 160               # address capped
    assert len(args[4]) == 1000              # query capped
    assert len(args[5]) == _REPORT_MAX_CHARS  # report_md capped
    assert rid is not None


# ── owner_token_hash (migration 016 — "Your Reports" retention surface) ──


def test_persist_stamps_owner_token_hash():
    """Authenticated callers' reports carry their token_hash so they show up
    in the private /reports history."""
    long_md = "# Report\n\n" + ("data point. " * 60)
    fake_pool = AsyncMock()
    chat_module._reports_table_ready = False
    with patch.object(chat_module, "get_pool", new=AsyncMock(return_value=fake_pool)):
        _run(
            _persist_shared_report(
                "1008200001", "350 5TH AVE", "q", long_md,
                owner_token_hash="abc123hash",
            )
        )
    args = _insert_call_args(fake_pool)
    # args = (sql, id, bbl, address, query, report_md, owner_token_hash)
    assert args[6] == "abc123hash"


def test_persist_owner_defaults_to_none_for_anonymous():
    """Anonymous (free-tier) reports stay owner-less — anonymous permalinks."""
    long_md = "# Report\n\n" + ("data point. " * 60)
    fake_pool = AsyncMock()
    chat_module._reports_table_ready = False
    with patch.object(chat_module, "get_pool", new=AsyncMock(return_value=fake_pool)):
        _run(_persist_shared_report("1008200001", "350 5TH AVE", "q", long_md))
    args = _insert_call_args(fake_pool)
    assert args[6] is None
