import os

import pytest


@pytest.fixture
def empire_state_bbl():
    return "1008350001"


@pytest.fixture
def brooklyn_brownstone_bbl():
    return "3011620044"


@pytest.fixture
def queens_multifamily_bbl():
    return "4004920033"


# ── Integration test fixtures ────────────────────────────────────────

# Set DATABASE_URL early so the Settings singleton picks it up on first import.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://nycdb:nycdb@localhost:5432/nycdb",
)


@pytest.fixture(autouse=True)
async def _init_db_pool_for_integration():
    """Initialize the asyncpg pool before each integration test, close it after.

    Each test function gets its own event loop (pytest-asyncio default),
    so we must create a fresh pool per test to avoid cross-loop errors.
    The module-level _pool in db.py is reset on each iteration.
    """
    import nyc_property_intel.db as _db

    # Force a fresh pool on the current event loop.
    await _db.close_pool()
    pool = await _db.get_pool()
    yield pool
    await _db.close_pool()
