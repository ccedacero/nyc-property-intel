"""Tests for "watch this building" alerts (feature 1.9).

Covers the alerting brain in watch.py with a mocked pool — no live DB:

  * diff_increases  — the core decision: alert only on an INCREASE, with
    correct pluralization and per-signal labels
  * snapshot_counts — shape + missing-building → zeros
  * register_watch  — baseline is the CURRENT snapshot (alert only on future
    change), returns a slug
  * process_watches — alerts on an increase, stays silent on flat/decrease,
    and honors dry_run
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from nyc_property_intel import watch as watch_module
from nyc_property_intel.watch import (
    diff_increases,
    process_watches,
    register_watch,
    snapshot_counts,
)

ZERO = {"hpd_open": 0, "dob_open": 0, "ecb_active": 0, "litigations": 0}


# ── diff_increases (pure core logic) ──────────────────────────────────


def test_diff_no_change_is_empty():
    assert diff_increases(ZERO, ZERO) == []


def test_diff_single_increase_singular():
    prev = dict(ZERO)
    cur = {**ZERO, "hpd_open": 1}
    assert diff_increases(prev, cur) == ["1 new open HPD violation"]


def test_diff_increase_plural():
    cur = {**ZERO, "hpd_open": 3}
    assert diff_increases(ZERO, cur) == ["3 new open HPD violations"]


def test_diff_multiple_signals():
    prev = {"hpd_open": 2, "dob_open": 0, "ecb_active": 1, "litigations": 0}
    cur = {"hpd_open": 5, "dob_open": 1, "ecb_active": 1, "litigations": 2}
    out = diff_increases(prev, cur)
    assert out == [
        "3 new open HPD violations",
        "1 new open DOB violation",
        "2 new HPD litigation cases",
    ]


def test_diff_decrease_never_alerts():
    prev = {**ZERO, "hpd_open": 5}
    cur = {**ZERO, "hpd_open": 2}
    assert diff_increases(prev, cur) == []


def test_diff_mixed_only_reports_increases():
    prev = {"hpd_open": 5, "dob_open": 0, "ecb_active": 3, "litigations": 1}
    cur = {"hpd_open": 2, "dob_open": 0, "ecb_active": 4, "litigations": 1}  # hpd down, ecb up
    assert diff_increases(prev, cur) == ["1 new active ECB violation"]


# ── snapshot_counts ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_snapshot_counts_shape():
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(
        return_value={"hpd_open": 7, "dob_open": 2, "ecb_active": 1, "litigations": 4}
    )
    snap = await snapshot_counts(pool, "2025180028")
    assert snap == {"hpd_open": 7, "dob_open": 2, "ecb_active": 1, "litigations": 4}


@pytest.mark.asyncio
async def test_snapshot_counts_missing_building_is_zeros():
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)
    assert await snapshot_counts(pool, "0000000000") == ZERO


# ── register_watch ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_register_watch_baselines_to_current_snapshot():
    watch_module._watch_table_ready = True  # skip DDL
    pool = AsyncMock()
    # snapshot query, then the INSERT ... RETURNING id
    pool.fetchrow = AsyncMock(
        side_effect=[
            {"hpd_open": 3, "dob_open": 0, "ecb_active": 1, "litigations": 2},
            {"id": "abc123XY"},
        ]
    )
    with patch.object(watch_module, "get_pool", AsyncMock(return_value=pool)):
        wid = await register_watch("Person@Example.com", "2025180028", "132 W 169 St")
    assert wid == "abc123XY"
    # fetchrow(query, wid, email, bbl, address, snap_json):
    #   args[0]=SQL, args[1]=wid, args[2]=email, args[3]=bbl, args[4]=address, args[5]=baseline
    insert_call = pool.fetchrow.await_args_list[1]
    assert '"hpd_open": 3' in insert_call.args[5]   # baseline = current snapshot
    assert insert_call.args[2] == "person@example.com"  # email normalized to lowercase


# ── process_watches ───────────────────────────────────────────────────


def _mock_pool_for(rows, snapshot):
    pool = AsyncMock()
    pool.execute = AsyncMock()
    pool.fetch = AsyncMock(return_value=rows)
    pool.fetchrow = AsyncMock(return_value=snapshot)  # snapshot_counts
    pool.fetchval = AsyncMock(return_value=True)  # cooldown passed / report url lookup
    return pool


@pytest.mark.asyncio
async def test_process_alerts_on_increase_dry_run():
    watch_module._watch_table_ready = True
    rows = [
        {
            "id": "w1",
            "email": "a@b.com",
            "bbl": "2025180028",
            "address": "132 W 169 St",
            "last_seen": {"hpd_open": 2, "dob_open": 0, "ecb_active": 0, "litigations": 0},
            "last_notified_at": None,
        }
    ]
    current = {"hpd_open": 5, "dob_open": 0, "ecb_active": 0, "litigations": 0}
    pool = _mock_pool_for(rows, current)
    with patch.object(watch_module, "get_pool", AsyncMock(return_value=pool)):
        stats = await process_watches(dry_run=True)
    assert stats["checked"] == 1
    assert stats["alerted"] == 1


@pytest.mark.asyncio
async def test_process_silent_when_flat():
    watch_module._watch_table_ready = True
    seen = {"hpd_open": 4, "dob_open": 1, "ecb_active": 0, "litigations": 0}
    rows = [
        {
            "id": "w2",
            "email": "a@b.com",
            "bbl": "2025180028",
            "address": None,
            "last_seen": dict(seen),
            "last_notified_at": None,
        }
    ]
    pool = _mock_pool_for(rows, dict(seen))  # current == last_seen
    with patch.object(watch_module, "get_pool", AsyncMock(return_value=pool)):
        stats = await process_watches(dry_run=True)
    assert stats["checked"] == 1
    assert stats["alerted"] == 0


@pytest.mark.asyncio
async def test_process_silent_on_decrease():
    watch_module._watch_table_ready = True
    rows = [
        {
            "id": "w3",
            "email": "a@b.com",
            "bbl": "2025180028",
            "address": None,
            "last_seen": {"hpd_open": 9, "dob_open": 0, "ecb_active": 0, "litigations": 0},
            "last_notified_at": None,
        }
    ]
    current = {"hpd_open": 3, "dob_open": 0, "ecb_active": 0, "litigations": 0}
    pool = _mock_pool_for(rows, current)
    with patch.object(watch_module, "get_pool", AsyncMock(return_value=pool)):
        stats = await process_watches(dry_run=True)
    assert stats["alerted"] == 0
