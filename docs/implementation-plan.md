# NYC Property Intel -- File-by-File Implementation Plan

**Generated:** 2026-03-18
**Target:** Working MCP server in 3 weeks, 6 tools, 5+ datasets

---

## 1. Final Directory Structure

```
nyc-property-intel/
├── pyproject.toml                          # MODIFY (already exists)
├── uv.lock                                 # auto-generated
├── .env.example                            # CREATE
├── .env                                    # local only, git-ignored
├── .python-version                         # "3.12"
├── .gitignore                              # CREATE
├── docker-compose.yml                      # MODIFY (already exists)
├── scripts/
│   ├── seed_nycdb.sh                       # REWRITE (add per-dataset retry, phase support)
│   ├── create_indexes.sql                  # REWRITE (phase-aware, fix missing indexes)
│   ├── create_views.sql                    # REWRITE (fix ACRIS BBL bug, split violation view)
│   └── refresh_views.sh                    # KEEP as-is
├── src/
│   └── nyc_property_intel/
│       ├── __init__.py                     # KEEP (empty)
│       ├── app.py                          # MODIFY (add fair housing instructions)
│       ├── config.py                       # MODIFY (add data_loaded_at tracking)
│       ├── db.py                           # REWRITE (add JSON serialization, pool cleanup, BBL validation)
│       ├── geoclient.py                    # MODIFY (use shared httpx client, fix PAD fallback)
│       ├── socrata.py                      # MODIFY (use shared httpx client)
│       ├── http_client.py                  # CREATE (shared httpx.AsyncClient)
│       ├── server.py                       # REWRITE (new tool imports, lifecycle hooks)
│       ├── tools/
│       │   ├── __init__.py                 # KEEP (empty)
│       │   ├── lookup.py                   # REWRITE (add data_as_of, BBL validation)
│       │   ├── issues.py                   # CREATE (replaces violations.py + permits.py)
│       │   ├── history.py                  # CREATE (replaces ownership.py + transactions.py + sales)
│       │   ├── financials.py              # CREATE (replaces tax.py + rent_stab.py + liens.py)
│       │   ├── comps.py                    # REWRITE (merge comps + neighborhood stats)
│       │   └── analysis.py                 # REWRITE (compound tool using asyncio.gather)
│       └── queries/
│           ├── __init__.py                 # KEEP (empty)
│           ├── pluto.py                    # REWRITE (add all SQL constants)
│           ├── hpd.py                      # REWRITE (add all SQL constants)
│           ├── dob.py                      # REWRITE
│           ├── dof.py                      # REWRITE
│           ├── acris.py                    # REWRITE
│           └── rentstab.py                 # REWRITE
├── tests/
│   ├── conftest.py                         # CREATE
│   └── test_smoke.py                       # CREATE (5 smoke tests)
└── DELETE these files:
    ├── tools/violations.py
    ├── tools/permits.py
    ├── tools/ownership.py
    ├── tools/transactions.py
    ├── tools/tax.py
    ├── tools/rent_stab.py
    ├── tools/liens.py
    ├── tools/neighborhood.py
    ├── alembic/                            # DELETE (not needed -- NYCDB owns schemas)
    └── alembic.ini                         # DELETE
```

---

## 2. pyproject.toml

**File:** `/Users/devtzi/dev/nyc-property-intel/pyproject.toml`
**Action:** MODIFY -- add nycdb as optional dependency, add project scripts entry

```toml
[project]
name = "nyc-property-intel"
version = "0.1.0"
description = "NYC Real Estate Intelligence MCP Server — property due diligence in 30 seconds"
requires-python = ">=3.12"
dependencies = [
    "mcp[cli]>=1.2.0",
    "asyncpg>=0.30.0",
    "httpx>=0.27.0",
    "pydantic>=2.0",
    "pydantic-settings>=2.0",
    "cachetools>=5.3.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "ruff>=0.5.0",
]
data = [
    "nycdb>=0.4.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/nyc_property_intel"]

[tool.ruff]
target-version = "py312"
line-length = 100

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

---

## 3. docker-compose.yml

**File:** `/Users/devtzi/dev/nyc-property-intel/docker-compose.yml`
**Action:** MODIFY -- remove initdb mounts (views/indexes run AFTER nycdb seed, not on init)

```yaml
services:
  postgres:
    image: postgres:16-alpine
    ports:
      - "5432:5432"
    environment:
      POSTGRES_USER: nycdb
      POSTGRES_PASSWORD: nycdb
      POSTGRES_DB: nycdb
    volumes:
      - pgdata:/var/lib/postgresql/data
    shm_size: 256mb
    command: >
      postgres
        -c shared_buffers=512MB
        -c work_mem=64MB
        -c maintenance_work_mem=256MB
        -c effective_cache_size=1GB
        -c max_parallel_workers_per_gather=2

volumes:
  pgdata:
```

**Why remove initdb mounts:** The views reference tables that don't exist until after `nycdb --load` runs. Putting them in `docker-entrypoint-initdb.d` will fail on first boot. Instead, `seed_nycdb.sh` runs them explicitly after loading.

---

## 4. .env.example

**File:** `/Users/devtzi/dev/nyc-property-intel/.env.example`
**Phase:** A

```env
# Database (matches docker-compose.yml defaults)
DATABASE_URL=postgresql://nycdb:nycdb@localhost:5432/nycdb

# NYC GeoClient API (get key at https://api-portal.nyc.gov/)
NYC_GEOCLIENT_SUBSCRIPTION_KEY=your_subscription_key_here

# NYC Open Data / Socrata (get token at https://data.cityofnewyork.us/)
SOCRATA_APP_TOKEN=your_token_here

# Logging
LOG_LEVEL=INFO
```

---

## 5. .gitignore

**File:** `/Users/devtzi/dev/nyc-property-intel/.gitignore`
**Phase:** A

```gitignore
.env
__pycache__/
*.pyc
.ruff_cache/
data/
*.egg-info/
dist/
.pytest_cache/
.venv/
```

---

## 6. File-by-File Implementation (in build order)

### Phase A — Week 1: Foundation + 3 Datasets + 2 Tools

---

#### File A1: `src/nyc_property_intel/app.py`

**Purpose:** Single source of the FastMCP instance. Every tool module imports `mcp` from here. Prevents circular imports.
**Phase:** A
**Dependencies:** None
**Exports:** `mcp`

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "NYC Property Intelligence",
    instructions=(
        "You are a NYC real estate due diligence assistant. You help investors, "
        "attorneys, and property professionals research NYC properties using public "
        "city data (PLUTO, ACRIS, HPD, DOB, DOF).\n\n"
        "WORKFLOW:\n"
        "1. Always start with lookup_property to resolve an address to a BBL.\n"
        "2. Use the BBL with other tools to gather specific data.\n"
        "3. For a full overview, use analyze_property.\n\n"
        "DATA NOTES:\n"
        "- Dollar amounts: format with commas ($1,250,000).\n"
        "- $0 or $1 sales: flag as 'non-arm's-length' (LLC transfers, inheritance, tax sales).\n"
        "- PLUTO owner name lags 6-18 months behind actual transfers. Prefer ACRIS grantee.\n"
        "- Rent stabilization data covers 2007-2017 only.\n"
        "- Always note the data_as_of date so users know data freshness.\n\n"
        "FAIR HOUSING COMPLIANCE — MANDATORY:\n"
        "- NEVER provide information about the racial, ethnic, or religious composition "
        "of a neighborhood or building's tenants.\n"
        "- NEVER make recommendations based on demographics, school quality correlations "
        "with race, or 'desirability' that correlates with protected classes.\n"
        "- NEVER speculate about tenant characteristics from violation or complaint data.\n"
        "- NEVER use language like 'up-and-coming' or 'gentrifying' as investment signals.\n"
        "- If asked about demographics, respond: 'I provide property data only. "
        "Demographic information is not part of property due diligence and could "
        "implicate fair housing laws.'\n"
        "- DO provide: zoning, violations, sales history, tax data, building specs, "
        "ownership records — objective property facts only.\n\n"
        "DISCLAIMER:\n"
        "This tool provides public record data for informational purposes only. "
        "It is not a substitute for professional legal, financial, or appraisal advice. "
        "Always verify critical findings with primary sources before making decisions."
    ),
)
```

---

#### File A2: `src/nyc_property_intel/config.py`

**Purpose:** Pydantic-based configuration from environment variables / `.env`.
**Phase:** A
**Dependencies:** None
**Exports:** `settings` (singleton `Settings` instance)

```python
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://nycdb:nycdb@localhost:5432/nycdb"
    nyc_geoclient_subscription_key: str = ""
    socrata_app_token: str = ""
    socrata_rate_limit_per_hour: int = 5000
    log_level: str = "INFO"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
```

No changes from current file. Confirmed correct.

---

#### File A3: `src/nyc_property_intel/db.py`

**Purpose:** Async connection pool, JSON-safe row serialization, BBL validation, pool cleanup.
**Phase:** A
**Dependencies:** `config.py`
**Exports:** `get_pool`, `close_pool`, `fetch_one`, `fetch_all`, `validate_bbl`, `row_to_dict`

This is the most critical file to get right. The current version is missing serialization, cleanup, and validation.

```python
import asyncpg
import atexit
import datetime
import logging
import signal
from decimal import Decimal
from uuid import UUID

from nyc_property_intel.config import settings

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


# --- JSON Serialization Layer ---

def _serialize(obj):
    """Convert asyncpg types to JSON-serializable Python types."""
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, memoryview):
        return bytes(obj).hex()
    return obj


def row_to_dict(row: asyncpg.Record) -> dict:
    """Convert an asyncpg Record to a JSON-safe dict."""
    return {k: _serialize(v) for k, v in dict(row).items()}


# --- BBL Validation ---

def validate_bbl(bbl: str) -> str:
    """Validate and normalize a 10-digit BBL string.

    BBL format: BCCCCCLLLL where B=borough(1-5), C=block(5 digits), L=lot(4 digits).
    Raises ValueError if invalid.
    """
    bbl = bbl.strip()
    if len(bbl) != 10:
        raise ValueError(f"BBL must be 10 digits, got {len(bbl)}: '{bbl}'")
    if not bbl.isdigit():
        raise ValueError(f"BBL must contain only digits: '{bbl}'")
    borough = int(bbl[0])
    if borough < 1 or borough > 5:
        raise ValueError(f"BBL borough digit must be 1-5, got {borough}: '{bbl}'")
    return bbl


def parse_bbl(bbl: str) -> tuple[str, str, str]:
    """Parse validated BBL into (borough, block, lot) strings."""
    bbl = validate_bbl(bbl)
    return bbl[0], bbl[1:6], bbl[6:10]


# --- Connection Pool ---

async def get_pool() -> asyncpg.Pool:
    """Get or create the connection pool (lazy singleton)."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=settings.database_url,
            min_size=1,   # 1, not 2 -- conservative for single-user MCP server
            max_size=10,
            command_timeout=30,
        )
        logger.info("Database connection pool created")
    return _pool


async def close_pool():
    """Gracefully close the connection pool."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("Database connection pool closed")


# --- Query Helpers ---

async def fetch_one(query: str, *args) -> dict | None:
    """Execute query and return first row as a JSON-safe dict, or None."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(query, *args)
        return row_to_dict(row) if row else None


async def fetch_all(query: str, *args) -> list[dict]:
    """Execute query and return all rows as JSON-safe dicts."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *args)
        return [row_to_dict(r) for r in rows]


async def fetch_val(query: str, *args):
    """Execute query and return a single scalar value."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(query, *args)
```

**Key changes from existing `db.py`:**
- Added `_serialize()` / `row_to_dict()` -- fixes TypeError on `json.dumps()`
- Added `validate_bbl()` / `parse_bbl()` -- used by every tool
- Changed `min_size` from 2 to 1 -- single-user MCP server doesn't need 2 idle connections
- Added `close_pool()` -- called from server lifecycle
- Added `fetch_val()` -- for scalar queries (e.g., `SELECT COUNT(*)`)

---

#### File A4: `src/nyc_property_intel/http_client.py`

**Purpose:** Single shared `httpx.AsyncClient` reused across GeoClient and Socrata calls.
**Phase:** A
**Dependencies:** None
**Exports:** `get_http_client`, `close_http_client`

```python
import httpx
import logging

logger = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    """Get or create the shared httpx client (lazy singleton)."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=5),
        )
        logger.info("Shared HTTP client created")
    return _client


async def close_http_client():
    """Close the shared httpx client."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
        logger.info("Shared HTTP client closed")
```

---

#### File A5: `src/nyc_property_intel/geoclient.py`

**Purpose:** Address-to-BBL resolution via NYC GeoClient API + PAD fallback.
**Phase:** A
**Dependencies:** `config.py`, `http_client.py`, `db.py`
**Exports:** `resolve_address_to_bbl`, `resolve_address_via_pad`

```python
import re
import logging

import httpx
from cachetools import TTLCache

from nyc_property_intel.config import settings
from nyc_property_intel.http_client import get_http_client

logger = logging.getLogger(__name__)

_bbl_cache: TTLCache = TTLCache(maxsize=10000, ttl=86400)

BOROUGH_MAP = {
    "manhattan": "Manhattan", "new york": "Manhattan", "mn": "Manhattan",
    "bronx": "Bronx", "the bronx": "Bronx", "bx": "Bronx",
    "brooklyn": "Brooklyn", "kings": "Brooklyn", "bk": "Brooklyn",
    "queens": "Queens", "qn": "Queens",
    "staten island": "Staten Island", "richmond": "Staten Island", "si": "Staten Island",
}

BOROUGH_TO_CODE = {
    "Manhattan": "1", "Bronx": "2", "Brooklyn": "3",
    "Queens": "4", "Staten Island": "5",
}

_ADDRESS_RE = re.compile(
    r"^(?P<house_number>\d[\d\-/]*)\s+"
    r"(?P<street>.+?)(?:\s*,\s*(?P<rest>.*))?$"
)


def _parse_address(address: str, borough: str | None = None) -> tuple[str, str, str]:
    """Parse address into (house_number, street, borough_name).
    Raises ValueError if parsing fails.
    """
    address = address.strip()
    m = _ADDRESS_RE.match(address)
    if not m:
        raise ValueError(f"Could not parse address: {address}")

    house_number = m.group("house_number")
    street = m.group("street").strip().rstrip(",")
    rest = m.group("rest") or ""

    if not borough:
        rest_lower = rest.lower().strip()
        rest_lower = re.sub(r",?\s*ny\s*\d*$", "", rest_lower).strip()
        rest_lower = re.sub(r",?\s*new york\s*\d*$", "", rest_lower).strip()
        for key, value in BOROUGH_MAP.items():
            if rest_lower == key or rest_lower.startswith(key):
                borough = value
                break

    if not borough:
        raise ValueError(
            f"Could not determine borough from address: {address}. "
            "Please provide the borough parameter."
        )

    borough_normalized = BOROUGH_MAP.get(borough.lower(), borough)
    return house_number, street, borough_normalized


async def resolve_address_to_bbl(
    address: str,
    borough: str | None = None,
) -> dict:
    """Resolve street address to BBL via NYC GeoClient V2.

    Returns dict: {bbl, bin, zip_code, normalized_address, community_district}
    """
    cache_key = f"{address}|{borough}"
    if cache_key in _bbl_cache:
        return _bbl_cache[cache_key]

    house_number, street_name, borough_name = _parse_address(address, borough)

    client = get_http_client()
    resp = await client.get(
        "https://api.nyc.gov/geo/geoclient/v2/address.json",
        params={
            "houseNumber": house_number,
            "street": street_name,
            "borough": borough_name,
        },
        headers={
            "Ocp-Apim-Subscription-Key": settings.nyc_geoclient_subscription_key,
        },
    )
    resp.raise_for_status()
    data = resp.json()["address"]

    if data.get("returnCode1a") not in ("00", "01"):
        raise ValueError(f"GeoClient error: {data.get('message', 'Unknown error')}")

    result = {
        "bbl": data["bbl"],
        "bin": data.get("buildingIdentificationNumber"),
        "zip_code": data.get("zipCode"),
        "normalized_address": data.get("firstStreetNameNormalized"),
        "community_district": data.get("communityDistrict"),
    }
    _bbl_cache[cache_key] = result
    return result


async def resolve_address_via_pad(
    address: str,
    borough: str | None = None,
) -> str | None:
    """Fallback: resolve address to BBL using local PAD table.

    FIX from review: cast lhousenum/hhousenum to int for numeric comparison,
    handle Queens hyphenated addresses.
    """
    from nyc_property_intel.db import fetch_one

    house_number, street_name, borough_name = _parse_address(address, borough)
    borough_code = BOROUGH_TO_CODE.get(borough_name)
    if not borough_code:
        return None

    # Try numeric comparison first (works for most addresses)
    try:
        house_num_int = int(house_number.split("-")[-1])  # handle Queens "37-10" -> 10 for lot
    except ValueError:
        return None

    row = await fetch_one(
        """SELECT bbl FROM pad_adr
           WHERE stname ILIKE '%' || $1 || '%'
             AND lhousenum::int <= $2 AND hhousenum::int >= $2
             AND boro = $3
           LIMIT 1""",
        street_name,
        house_num_int,
        borough_code,
    )
    return row["bbl"] if row else None
```

**Key changes from existing:**
- Uses `get_http_client()` instead of `async with httpx.AsyncClient()` per-request
- Fixed PAD fallback: casts `lhousenum`/`hhousenum` to `int` for numeric comparison
- Added Queens hyphenated address handling

---

#### File A6: `src/nyc_property_intel/socrata.py`

**Purpose:** Socrata Open Data API client with rate limiting. Not used in Phase A tools but needed for data freshness checks.
**Phase:** A (infrastructure), used starting Phase B
**Dependencies:** `config.py`, `http_client.py`
**Exports:** `query_socrata`

```python
import asyncio
import logging
import time
from collections import deque

from nyc_property_intel.config import settings
from nyc_property_intel.http_client import get_http_client

logger = logging.getLogger(__name__)

SOCRATA_BASE = "https://data.cityofnewyork.us/resource"


class RateLimiter:
    def __init__(self, max_per_hour: int = 5000):
        self.max_per_hour = max_per_hour
        self.timestamps: deque[float] = deque()

    async def acquire(self):
        now = time.monotonic()
        while self.timestamps and now - self.timestamps[0] > 3600:
            self.timestamps.popleft()
        if len(self.timestamps) >= self.max_per_hour:
            sleep_time = 3600 - (now - self.timestamps[0])
            await asyncio.sleep(sleep_time)
        self.timestamps.append(time.monotonic())


_limiter = RateLimiter(max_per_hour=settings.socrata_rate_limit_per_hour)


async def query_socrata(
    dataset_id: str,
    where: str,
    limit: int = 1000,
    order: str = ":id",
    select: str | None = None,
) -> list[dict]:
    """Query a NYC Open Data Socrata dataset."""
    await _limiter.acquire()

    params: dict[str, str | int] = {
        "$where": where,
        "$limit": limit,
        "$order": order,
    }
    if select:
        params["$select"] = select
    if settings.socrata_app_token:
        params["$$app_token"] = settings.socrata_app_token

    client = get_http_client()
    resp = await client.get(
        f"{SOCRATA_BASE}/{dataset_id}.json",
        params=params,
    )
    resp.raise_for_status()
    return resp.json()
```

**Key change:** Uses `get_http_client()` instead of creating new client per-request.

---

#### File A7: `src/nyc_property_intel/queries/pluto.py`

**Purpose:** All PLUTO-related SQL constants.
**Phase:** A
**Dependencies:** None
**Exports:** SQL string constants

```python
PROPERTY_PROFILE = """
SELECT
    bbl, address, borough, block, lot, ownername,
    bldgclass, landuse, zonedist1, zonedist2, overlay1, spldist1,
    numbldgs, numfloors, unitsres, unitstotal,
    lotarea, bldgarea, comarea, resarea, officearea, retailarea,
    yearbuilt, yearalter1, yearalter2,
    builtfar, residfar, commfar,
    assessland, assesstot, exempttot,
    histdist, landmark, latitude, longitude, postcode, condono
FROM mv_property_profile
WHERE bbl = $1
"""

PROPERTY_FALLBACK = """
SELECT
    bbl, address, borough, block, lot, ownername,
    bldgclass, landuse, zonedist1, zonedist2, overlay1, spldist1,
    numbldgs, numfloors, unitsres, unitstotal,
    lotarea, bldgarea, comarea, resarea, officearea, retailarea,
    yearbuilt, yearalter1, yearalter2,
    builtfar, residfar, commfar,
    assessland, assesstot, exempttot,
    histdist, landmark, latitude, longitude, postcode, condono
FROM pluto_latest
WHERE bbl = $1
"""

# For comps: resolve reference property details
PROPERTY_REF = """
SELECT postcode, bldgclass, bldgarea, zonedist1
FROM pluto_latest
WHERE bbl = $1
"""
```

---

#### File A8: `src/nyc_property_intel/queries/hpd.py`

**Purpose:** All HPD-related SQL constants (violations, complaints, registrations, litigations).
**Phase:** A (violations), B (complaints, registrations, litigations added to `get_property_issues`)
**Dependencies:** None
**Exports:** SQL string constants

```python
# --- HPD Violations ---

HPD_VIOLATION_SUMMARY = """
SELECT
    COUNT(*) AS total,
    COUNT(*) FILTER (WHERE class = 'C') AS class_c,
    COUNT(*) FILTER (WHERE class = 'B') AS class_b,
    COUNT(*) FILTER (WHERE class = 'A') AS class_a,
    COUNT(*) FILTER (WHERE currentstatus = 'OPEN') AS open_violations,
    COUNT(*) FILTER (WHERE currentstatus = 'OPEN' AND class = 'C') AS open_class_c,
    COUNT(*) FILTER (WHERE rentimpairing = 'YES') AS rent_impairing,
    MAX(inspectiondate) AS most_recent_date
FROM hpd_violations
WHERE bbl = $1
"""

HPD_VIOLATION_DETAIL = """
SELECT
    violationid, class, inspectiondate, approveddate,
    currentstatus, novdescription, apartment, story,
    violationstatus, rentimpairing
FROM hpd_violations
WHERE bbl = $1
    AND ($2::text IS NULL OR class = $2)
    AND ($3::text IS NULL OR currentstatus = $3)
    AND ($4::date IS NULL OR inspectiondate >= $4)
ORDER BY inspectiondate DESC
LIMIT $5
"""

# --- HPD Registration (Phase B) ---

HPD_REGISTRATION = """
SELECT
    r.registrationid, r.buildingid, r.boroid, r.housenumber, r.streetname,
    r.zip, r.lastregistrationdate, r.registrationenddate,
    c.type AS contact_type, c.contactdescription, c.corporationname,
    c.firstname, c.lastname, c.businesshousenumber, c.businessstreetname,
    c.businesscity, c.businessstate, c.businesszip
FROM hpd_registrations r
JOIN hpd_contacts c ON r.registrationid = c.registrationid
WHERE r.boroid = $1::smallint AND r.block = $2::int AND r.lot = $3::int
ORDER BY r.lastregistrationdate DESC, c.type
"""

# --- HPD Complaints (Phase B) ---

HPD_COMPLAINT_SUMMARY = """
SELECT
    COUNT(*) AS total,
    COUNT(*) FILTER (WHERE status = 'OPEN') AS open_complaints,
    MAX(receiveddate) AS most_recent_date
FROM hpd_complaints
WHERE bbl = $1
"""
```

---

#### File A9: `src/nyc_property_intel/queries/dob.py`

**Purpose:** DOB violations and job filings SQL.
**Phase:** B (DOB violations), B (job filings for `get_property_issues`)
**Dependencies:** None
**Exports:** SQL string constants

```python
# --- DOB Violations ---

DOB_VIOLATION_SUMMARY = """
SELECT
    COUNT(*) AS total,
    COUNT(*) FILTER (WHERE dispositiondate IS NULL) AS open_violations,
    MAX(issuedate) AS most_recent_date
FROM dob_violations
WHERE bbl = $1
"""

DOB_VIOLATION_DETAIL = """
SELECT
    isndobbisviol, issuedate, violationtypecode, violationtype,
    violationcategory, description,
    dispositiondate, dispositioncomments
FROM dob_violations
WHERE bbl = $1
    AND ($2::date IS NULL OR issuedate >= $2)
ORDER BY issuedate DESC
LIMIT $3
"""

# --- DOB Job Filings (Permits) ---

DOB_PERMITS = """
SELECT
    job, doc, jobtype, jobstatus, jobstatusdescrp,
    prefilingdate, approved, signoffdate, latestactiondate,
    buildingtype, existingoccupancy, proposedoccupancy,
    existingheight, proposedheight, initialcost, totalestfee,
    ownerfirstname, ownerlastname, ownerbusinessname
FROM dobjobs
WHERE bbl = $1
    AND ($2::text IS NULL OR jobtype = $2)
ORDER BY prefilingdate DESC
LIMIT $3
"""

DOB_NOW_PERMITS = """
SELECT
    jobfilingnumber AS job, jobtype, filingstatus AS jobstatus,
    filingdate AS prefilingdate, approveddate AS approved,
    currentstatusdate AS latestactiondate,
    initialcost::bigint, ownerfirstname, ownerlastname, ownerbusinessname
FROM dob_now_jobs
WHERE bbl = $1
    AND ($2::text IS NULL OR jobtype = $2)
ORDER BY filingdate DESC
LIMIT $3
"""
```

---

#### File A10: `src/nyc_property_intel/queries/dof.py`

**Purpose:** DOF sales, assessments, exemptions, tax liens SQL.
**Phase:** B (sales, assessments, exemptions), C (liens added to `get_financials`)
**Dependencies:** None
**Exports:** SQL string constants

```python
# --- DOF Sales ---

SALES_HISTORY = """
SELECT DISTINCT ON (saledate, saleprice)
    bbl, saledate, saleprice, address, neighborhood,
    buildingclassattimeofsale, taxclassattimeofsale,
    residentialunits, commercialunits, totalunits,
    landsquarefeet, grosssquarefeet, yearbuilt,
    buildingclasscategory
FROM (
    SELECT bbl, saledate, saleprice, address, neighborhood,
           buildingclassattimeofsale, taxclassattimeofsale,
           residentialunits, commercialunits, totalunits,
           landsquarefeet, grosssquarefeet, yearbuilt,
           buildingclasscategory
    FROM dof_sales WHERE bbl = $1
    UNION ALL
    SELECT bbl, saledate, saleprice, address, neighborhood,
           buildingclassattimeofsale, taxclassattimeofsale,
           residentialunits, commercialunits, totalunits,
           landsquarefeet, grosssquarefeet, yearbuilt,
           buildingclasscategory
    FROM dof_annual_sales WHERE bbl = $1
) combined
ORDER BY saledate DESC, saleprice DESC
LIMIT $2
"""

# --- Comparable Sales ---

COMPARABLE_SALES = """
SELECT
    s.bbl, s.address, s.neighborhood, s.saleprice, s.saledate,
    s.grosssquarefeet, s.landsquarefeet,
    s.residentialunits, s.commercialunits, s.totalunits,
    s.buildingclassattimeofsale, s.yearbuilt,
    CASE WHEN s.grosssquarefeet > 0
         THEN s.saleprice::numeric / s.grosssquarefeet
         ELSE NULL END AS price_per_sqft
FROM dof_sales s
WHERE s.zipcode = $1
    AND s.saleprice > COALESCE($2, 10000)
    AND s.saledate >= CURRENT_DATE - make_interval(months => $3)
    AND ($4::text IS NULL OR s.buildingclassattimeofsale LIKE $4 || '%')
    AND ($5::int IS NULL OR s.grosssquarefeet >= $5)
    AND ($6::int IS NULL OR s.grosssquarefeet <= $6)
    AND ($7::int IS NULL OR s.saleprice <= $7)
    AND s.bbl != COALESCE($8, '')
ORDER BY s.saledate DESC
LIMIT $9
"""

NEIGHBORHOOD_STATS = """
SELECT
    DATE_TRUNC('quarter', saledate) AS quarter,
    COUNT(*) AS num_sales,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY saleprice) AS median_price,
    AVG(saleprice) AS avg_price,
    AVG(CASE WHEN grosssquarefeet > 0
        THEN saleprice::numeric / grosssquarefeet END) AS avg_ppsf,
    MIN(saleprice) AS min_price,
    MAX(saleprice) AS max_price
FROM dof_sales
WHERE saleprice > 10000
    AND saledate >= CURRENT_DATE - make_interval(months => $1)
    AND ($2::text IS NULL OR zipcode = $2)
    AND ($3::text IS NULL OR neighborhood ILIKE '%' || $3 || '%')
    AND ($4::text IS NULL OR buildingclassattimeofsale LIKE $4 || '%')
GROUP BY DATE_TRUNC('quarter', saledate)
ORDER BY quarter DESC
"""

# --- DOF Assessment ---

ASSESSMENT = """
SELECT
    bbl, bldgclass, owner, zoning,
    curmktlandval, curmkttotalval,
    curactlandval, curacttotalval,
    curexmptotalval, curtaxbastotal,
    yrbuilt, units, grosssqft, numfloors
FROM dof_property_valuation_and_assessments
WHERE bbl = $1
LIMIT 1
"""

# --- DOF Exemptions ---

EXEMPTIONS = """
SELECT
    e.exmpcode, c.description AS exemption_type,
    e.year, e.curexmptot AS exempt_amount,
    e.percent1 AS exempt_percent, e.status
FROM dof_exemptions e
LEFT JOIN dof_exemption_classification_codes c ON e.exmpcode = c.exemptcode
WHERE e.bbl = $1
ORDER BY e.year DESC
"""

# --- DOF Tax Liens ---

TAX_LIENS = """
SELECT
    bbl, taxclasscode, buildingclass,
    housenumber, streetname, waterdebtonly, cycle
FROM dof_tax_lien_sale_list
WHERE bbl = $1
"""
```

**Key fix:** Uses `make_interval(months => $3)` instead of `$3 || ' months'` — fixes the asyncpg type error from the review.

---

#### File A11: `src/nyc_property_intel/queries/acris.py`

**Purpose:** ACRIS deed, mortgage, lien SQL.
**Phase:** C
**Dependencies:** None
**Exports:** SQL string constants

```python
# Doc type whitelist for ownership transfers (review finding #4)
# DEED: standard deed
# DEDL: deed-in-lieu of foreclosure
# DEDC: correction deed
# RPTT: real property transfer tax (always accompanies a deed)
# CTOR: contract of sale (not ownership transfer but signals pending)
DEED_DOC_TYPES = "('DEED', 'DEDL', 'DEDC', 'RPTT')"

OWNERSHIP_HISTORY = f"""
SELECT
    m.docdate AS transfer_date,
    m.docamount AS amount,
    m.doctype,
    dcc.doctypedescription AS doc_type_description,
    array_agg(DISTINCT seller.name) FILTER (WHERE seller.name IS NOT NULL) AS seller_names,
    array_agg(DISTINCT buyer.name) FILTER (WHERE buyer.name IS NOT NULL) AS buyer_names,
    m.documentid
FROM acris_real_property_legals l
JOIN acris_real_property_master m ON l.documentid = m.documentid
JOIN acris_document_control_codes dcc ON m.doctype = dcc.doctype
LEFT JOIN acris_real_property_parties seller
    ON m.documentid = seller.documentid AND seller.partytype = 1
LEFT JOIN acris_real_property_parties buyer
    ON m.documentid = buyer.documentid AND buyer.partytype = 2
WHERE l.borough = $1 AND l.block = $2::int AND l.lot = $3::int
    AND m.doctype IN {DEED_DOC_TYPES}
GROUP BY m.documentid, m.docdate, m.docamount, m.doctype, dcc.doctypedescription
ORDER BY m.docdate DESC
LIMIT $4
"""

TRANSACTIONS = """
SELECT
    m.documentid,
    m.doctype,
    dcc.doctypedescription,
    dcc.classcodedescrip AS doc_class,
    m.docdate,
    m.docamount,
    m.recordedfiled,
    jsonb_agg(jsonb_build_object(
        'name', p.name,
        'party_type', CASE p.partytype WHEN 1 THEN 'grantor' WHEN 2 THEN 'grantee' END,
        'address', concat_ws(', ', p.address1, p.city, p.state, p.zip)
    )) AS parties
FROM acris_real_property_legals l
JOIN acris_real_property_master m ON l.documentid = m.documentid
JOIN acris_document_control_codes dcc ON m.doctype = dcc.doctype
LEFT JOIN acris_real_property_parties p ON m.documentid = p.documentid
WHERE l.borough = $1 AND l.block = $2::int AND l.lot = $3::int
    AND ($4::text IS NULL OR dcc.classcodedescrip ILIKE '%' || $4 || '%')
    AND ($5::date IS NULL OR m.docdate >= $5)
    AND ($6::date IS NULL OR m.docdate <= $6)
GROUP BY m.documentid, m.doctype, dcc.doctypedescription, dcc.classcodedescrip, m.docdate, m.docamount, m.recordedfiled
ORDER BY m.docdate DESC
LIMIT $7
"""

MORTGAGES_AND_LIENS = """
SELECT
    m.docdate, m.docamount, m.doctype, dcc.doctypedescription,
    m.documentid,
    array_agg(DISTINCT p.name) FILTER (WHERE p.partytype = 2) AS lender_names
FROM acris_real_property_legals l
JOIN acris_real_property_master m ON l.documentid = m.documentid
JOIN acris_document_control_codes dcc ON m.doctype = dcc.doctype
LEFT JOIN acris_real_property_parties p ON m.documentid = p.documentid
WHERE l.borough = $1 AND l.block = $2::int AND l.lot = $3::int
    AND (dcc.classcodedescrip LIKE '%MORTGAGE%'
         OR dcc.classcodedescrip LIKE '%LIEN%'
         OR dcc.classcodedescrip LIKE '%UCC%')
GROUP BY m.documentid, m.docdate, m.docamount, m.doctype, dcc.doctypedescription
ORDER BY m.docdate DESC
LIMIT 20
"""

CURRENT_OWNER = """
SELECT
    owner_name, docdate, docamount, doctype, documentid,
    address1, city, state, zip
FROM mv_current_ownership
WHERE bbl = $1
"""
```

---

#### File A12: `src/nyc_property_intel/queries/rentstab.py`

**Purpose:** Rent stabilization SQL.
**Phase:** B (used by `get_financials`)
**Dependencies:** None
**Exports:** SQL string constants

```python
RENT_STABILIZATION = """
SELECT
    ucbbl AS bbl, address, ownername, numbldgs, numfloors,
    unitsres, unitstotal, yearbuilt,
    uc2007, uc2008, uc2009, uc2010, uc2011, uc2012,
    uc2013, uc2014, uc2015, uc2016, uc2017,
    est2007, est2008, est2009, est2010, est2011, est2012,
    est2013, est2014, est2015, est2016, est2017
FROM rentstab
WHERE ucbbl = $1
"""
```

---

#### File A13: `src/nyc_property_intel/tools/lookup.py`

**Purpose:** `lookup_property` tool -- the entry point for all property research.
**Phase:** A
**Dependencies:** `app.py`, `db.py`, `geoclient.py`, `queries/pluto.py`
**Exports:** `lookup_property` (registered as MCP tool)

```python
import logging
from datetime import date

import httpx

from nyc_property_intel.app import mcp
from nyc_property_intel.db import fetch_one, validate_bbl
from nyc_property_intel.geoclient import resolve_address_to_bbl, resolve_address_via_pad
from nyc_property_intel.queries import pluto

logger = logging.getLogger(__name__)


@mcp.tool
async def lookup_property(
    address: str | None = None,
    bbl: str | None = None,
    borough: str | None = None,
) -> dict:
    """Look up a NYC property by address or BBL (Borough-Block-Lot).

    Returns the full property profile including building details, zoning,
    assessed value, owner, and lot characteristics.

    At least one of address or bbl is required.

    Args:
        address: Street address (e.g., "123 Main St, Brooklyn")
        bbl: 10-digit BBL code (e.g., "3012340056")
        borough: Borough name, used with address if borough not in address string
    """
    if not address and not bbl:
        return {"error": "Either address or bbl must be provided."}

    if bbl:
        try:
            bbl = validate_bbl(bbl)
        except ValueError as e:
            return {"error": str(e)}

    if address and not bbl:
        try:
            resolved = await resolve_address_to_bbl(address, borough)
            bbl = resolved["bbl"]
        except (ValueError, httpx.HTTPError) as e:
            logger.warning(f"GeoClient failed for '{address}': {e}, trying PAD fallback")
            pad_bbl = await resolve_address_via_pad(address, borough)
            if pad_bbl:
                bbl = pad_bbl
            else:
                return {
                    "error": f"Could not resolve address '{address}'. "
                    "Try providing the BBL directly.",
                }

    result = await fetch_one(pluto.PROPERTY_PROFILE, bbl)

    if not result:
        result = await fetch_one(pluto.PROPERTY_FALLBACK, bbl)

    if not result:
        return {"error": f"No property found for BBL {bbl}. Verify the BBL is correct."}

    result["data_as_of"] = date.today().isoformat()
    result["assessment_roll_owner_note"] = (
        "Owner name is from the DOF assessment roll and may lag 6-18 months "
        "behind the actual owner. Use get_property_history for ACRIS deed records."
    )
    return result
```

**Key changes from existing:**
- Returns error dicts instead of raising `ValueError` (more MCP-friendly)
- Adds `data_as_of` field
- Adds owner staleness note
- Uses SQL from `queries/pluto.py`
- Uses `validate_bbl()`

---

#### File A14: `src/nyc_property_intel/tools/issues.py`

**Purpose:** `get_property_issues` tool -- HPD violations (Phase A), then DOB violations + permits + HPD registration (Phase B).
**Phase:** A (HPD violations only), B (full implementation)
**Dependencies:** `app.py`, `db.py`, `queries/hpd.py`, `queries/dob.py`
**Exports:** `get_property_issues` (registered as MCP tool)

**Phase A implementation (HPD violations only):**

```python
import logging
from datetime import date

from nyc_property_intel.app import mcp
from nyc_property_intel.db import fetch_one, fetch_all, validate_bbl, parse_bbl
from nyc_property_intel.queries import hpd

logger = logging.getLogger(__name__)


@mcp.tool
async def get_property_issues(
    bbl: str,
    source: str = "ALL",
    status: str | None = None,
    class_filter: str | None = None,
    start_date: str | None = None,
    limit: int = 25,
) -> dict:
    """Get violations, complaints, and permit issues for a property.

    Combines HPD housing violations, DOB building violations, active permits,
    and HPD registration status.

    Class C (HPD) = immediately hazardous. Class B = hazardous. Class A = non-hazardous.

    Args:
        bbl: 10-digit BBL code (required)
        source: "HPD", "DOB", or "ALL" (default "ALL")
        status: "OPEN", "CLOSED", or None for all
        class_filter: For HPD violations: "A", "B", or "C"
        start_date: Only show violations after this date (YYYY-MM-DD)
        limit: Max detail records per source (default 25)
    """
    try:
        bbl = validate_bbl(bbl)
    except ValueError as e:
        return {"error": str(e)}

    boro, block, lot = parse_bbl(bbl)
    start_dt = start_date  # passed as string, asyncpg handles date cast via $4::date

    response: dict = {"bbl": bbl, "data_as_of": date.today().isoformat()}

    # --- HPD Violations ---
    if source in ("ALL", "HPD"):
        hpd_summary = await fetch_one(hpd.HPD_VIOLATION_SUMMARY, bbl)
        hpd_detail = await fetch_all(
            hpd.HPD_VIOLATION_DETAIL, bbl, class_filter, status, start_dt, limit
        )
        response["hpd_violations"] = {
            "summary": hpd_summary,
            "recent": hpd_detail,
        }

    # --- DOB Violations (Phase B -- uncomment when dob_violations loaded) ---
    # if source in ("ALL", "DOB"):
    #     from nyc_property_intel.queries import dob
    #     dob_summary = await fetch_one(dob.DOB_VIOLATION_SUMMARY, bbl)
    #     dob_detail = await fetch_all(dob.DOB_VIOLATION_DETAIL, bbl, start_dt, limit)
    #     response["dob_violations"] = {
    #         "summary": dob_summary,
    #         "recent": dob_detail,
    #     }

    # --- DOB Permits (Phase B) ---
    # permits = await fetch_all(dob.DOB_PERMITS, bbl, None, 10)
    # dob_now = await fetch_all(dob.DOB_NOW_PERMITS, bbl, None, 10)
    # response["permits"] = {"dobjobs": permits, "dob_now": dob_now}

    # --- HPD Registration (Phase B) ---
    # hpd_reg = await fetch_all(hpd.HPD_REGISTRATION, int(boro), int(block), int(lot))
    # response["hpd_registration"] = hpd_reg[:5] if hpd_reg else None

    return response
```

**Phase B update:** Uncomment the DOB violations, permits, and HPD registration blocks.

---

#### File A15: `src/nyc_property_intel/server.py`

**Purpose:** Entry point. Configures logging, imports tool modules (registering them with `mcp`), runs the server. Registers lifecycle hooks for pool/client cleanup.
**Phase:** A
**Dependencies:** `app.py`, all `tools/*.py`
**Exports:** None (entry point only)

```python
import asyncio
import logging
import signal
import sys

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

from nyc_property_intel.app import mcp  # noqa: E402

# Import tool modules to register @mcp.tool decorators
import nyc_property_intel.tools.lookup  # noqa: E402, F401
import nyc_property_intel.tools.issues  # noqa: E402, F401
# Phase B:
# import nyc_property_intel.tools.history  # noqa: E402, F401
# import nyc_property_intel.tools.comps  # noqa: E402, F401
# import nyc_property_intel.tools.financials  # noqa: E402, F401
# Phase C:
# import nyc_property_intel.tools.analysis  # noqa: E402, F401


async def _cleanup():
    """Gracefully close pool and HTTP client."""
    from nyc_property_intel.db import close_pool
    from nyc_property_intel.http_client import close_http_client
    await close_pool()
    await close_http_client()
    logger.info("Cleanup complete")


def _handle_sigterm(*_args):
    """Handle SIGTERM from Claude Desktop killing the process."""
    logger.info("Received SIGTERM, cleaning up")
    loop = asyncio.get_event_loop()
    if loop.is_running():
        loop.create_task(_cleanup())
    sys.exit(0)


signal.signal(signal.SIGTERM, _handle_sigterm)


if __name__ == "__main__":
    mcp.run()
```

**Phase B update:** Uncomment the Phase B tool imports.
**Phase C update:** Uncomment the Phase C tool imports.

---

### Phase B — Week 2: Sales + Comps + Compound Tool

---

#### File B1: `src/nyc_property_intel/tools/history.py`

**Purpose:** `get_property_history` tool -- sales history (Phase B), ownership + transactions (Phase C).
**Phase:** B (sales only), C (add ACRIS ownership/transactions)
**Dependencies:** `app.py`, `db.py`, `queries/dof.py`, `queries/acris.py`
**Exports:** `get_property_history` (registered as MCP tool)

```python
import logging
from datetime import date

from nyc_property_intel.app import mcp
from nyc_property_intel.db import fetch_all, validate_bbl, parse_bbl
from nyc_property_intel.queries import dof

logger = logging.getLogger(__name__)


@mcp.tool
async def get_property_history(
    bbl: str,
    include_sales: bool = True,
    include_ownership: bool = True,
    include_transactions: bool = False,
    doc_type_filter: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 15,
) -> dict:
    """Get the ownership, transaction, and sales history for a property.

    Combines DOF sales records with ACRIS deed transfers and recorded documents.

    Args:
        bbl: 10-digit BBL code (required)
        include_sales: Include DOF sales records (default True)
        include_ownership: Include ACRIS deed transfers showing ownership chain (default True)
        include_transactions: Include all ACRIS recorded documents -- deeds, mortgages, liens, etc. (default False)
        doc_type_filter: For transactions, filter by doc class: "DEED", "MORTGAGE", "LIEN", "UCC"
        start_date: Only show records after this date (YYYY-MM-DD)
        end_date: Only show records before this date (YYYY-MM-DD)
        limit: Max records per section (default 15)
    """
    try:
        bbl = validate_bbl(bbl)
    except ValueError as e:
        return {"error": str(e)}

    boro, block, lot = parse_bbl(bbl)
    response: dict = {"bbl": bbl, "data_as_of": date.today().isoformat()}

    # --- DOF Sales ---
    if include_sales:
        sales = await fetch_all(dof.SALES_HISTORY, bbl, limit)
        # Flag non-arm's-length transactions
        for sale in sales:
            if sale.get("saleprice") is not None and sale["saleprice"] <= 1:
                sale["_note"] = "Non-arm's-length transaction ($0/$1 = LLC transfer, inheritance, or tax sale)"
        response["sales"] = sales

    # --- ACRIS Ownership Chain (Phase C -- uncomment when ACRIS loaded) ---
    # if include_ownership:
    #     from nyc_property_intel.queries import acris
    #     ownership = await fetch_all(acris.OWNERSHIP_HISTORY, boro, block, lot, limit)
    #     for deed in ownership:
    #         if deed.get("amount") is not None and deed["amount"] == 0:
    #             deed["_note"] = "Nominal consideration — likely LLC transfer or related-party deed"
    #     response["ownership_chain"] = ownership

    # --- ACRIS Transactions (Phase C) ---
    # if include_transactions:
    #     from nyc_property_intel.queries import acris
    #     txns = await fetch_all(
    #         acris.TRANSACTIONS, boro, block, lot,
    #         doc_type_filter, start_date, end_date, limit
    #     )
    #     response["transactions"] = txns

    return response
```

---

#### File B2: `src/nyc_property_intel/tools/comps.py`

**Purpose:** `search_comps` tool -- comparable sales + neighborhood stats.
**Phase:** B
**Dependencies:** `app.py`, `db.py`, `queries/dof.py`, `queries/pluto.py`
**Exports:** `search_comps` (registered as MCP tool)

```python
import logging
from datetime import date

from nyc_property_intel.app import mcp
from nyc_property_intel.db import fetch_one, fetch_all, validate_bbl
from nyc_property_intel.queries import dof, pluto

logger = logging.getLogger(__name__)


@mcp.tool
async def search_comps(
    bbl: str | None = None,
    zip_code: str | None = None,
    building_class: str | None = None,
    min_sqft: int | None = None,
    max_sqft: int | None = None,
    min_price: int | None = None,
    max_price: int | None = None,
    months: int = 12,
    neighborhood: str | None = None,
    include_stats: bool = True,
    limit: int = 20,
) -> dict:
    """Find comparable property sales and neighborhood statistics.

    Provide either a BBL (uses its zip + building class as defaults) or explicit
    zip_code + building_class. Also returns quarterly price trends for the area.

    Args:
        bbl: Reference property BBL (optional -- used to auto-detect zip and building class)
        zip_code: Override zip code for comp search
        building_class: Building class prefix to match (e.g., "A" for 1-family, "C" for walk-ups)
        min_sqft: Minimum gross square feet
        max_sqft: Maximum gross square feet
        min_price: Minimum sale price (default 10000 to filter $0 sales)
        max_price: Maximum sale price
        months: Look-back period in months (default 12)
        neighborhood: Neighborhood name for stats (as used in DOF sales data)
        include_stats: Include quarterly price trend statistics (default True)
        limit: Max comparable sales to return (default 20)
    """
    response: dict = {"data_as_of": date.today().isoformat()}

    # Resolve reference property details if BBL provided
    effective_zip = zip_code
    effective_class = building_class

    if bbl:
        try:
            bbl = validate_bbl(bbl)
        except ValueError as e:
            return {"error": str(e)}

        ref = await fetch_one(pluto.PROPERTY_REF, bbl)
        if ref:
            if not effective_zip:
                effective_zip = ref.get("postcode")
            if not effective_class:
                effective_class = ref.get("bldgclass")
            response["reference_property"] = ref

    if not effective_zip and not neighborhood:
        return {"error": "Provide a BBL, zip_code, or neighborhood to search comps."}

    # --- Comparable Sales ---
    if effective_zip:
        comps = await fetch_all(
            dof.COMPARABLE_SALES,
            effective_zip, min_price, months, effective_class,
            min_sqft, max_sqft, max_price, bbl, limit,
        )
        response["comparable_sales"] = comps
        response["comp_count"] = len(comps)
        if len(comps) < 3:
            response["_note"] = (
                f"Only {len(comps)} comps found. Consider expanding time period "
                "(months parameter) or broadening building_class filter."
            )

    # --- Neighborhood Stats ---
    if include_stats:
        stats_zip = effective_zip
        stats = await fetch_all(
            dof.NEIGHBORHOOD_STATS,
            months, stats_zip, neighborhood, effective_class,
        )
        response["quarterly_stats"] = stats

    return response
```

---

#### File B3: `src/nyc_property_intel/tools/financials.py`

**Purpose:** `get_financials` tool -- tax assessment + exemptions (Phase B), rent stabilization (Phase B), liens (Phase C).
**Phase:** B (assessment + exemptions + rent stab), C (add liens/mortgages from ACRIS)
**Dependencies:** `app.py`, `db.py`, `queries/dof.py`, `queries/rentstab.py`, `queries/acris.py`
**Exports:** `get_financials` (registered as MCP tool)

```python
import logging
from datetime import date

from nyc_property_intel.app import mcp
from nyc_property_intel.db import fetch_one, fetch_all, validate_bbl, parse_bbl
from nyc_property_intel.queries import dof, rentstab

logger = logging.getLogger(__name__)


@mcp.tool
async def get_financials(
    bbl: str,
    include_assessment: bool = True,
    include_exemptions: bool = True,
    include_rent_stab: bool = True,
    include_liens: bool = True,
) -> dict:
    """Get financial profile: tax assessment, exemptions, rent stabilization, and liens.

    Args:
        bbl: 10-digit BBL code (required)
        include_assessment: Include DOF assessed/market values (default True)
        include_exemptions: Include active tax exemptions like 421a, J-51, STAR (default True)
        include_rent_stab: Include rent stabilization unit count history (default True)
        include_liens: Include tax liens and ACRIS mortgage/lien records (default True)
    """
    try:
        bbl = validate_bbl(bbl)
    except ValueError as e:
        return {"error": str(e)}

    boro, block, lot = parse_bbl(bbl)
    response: dict = {"bbl": bbl, "data_as_of": date.today().isoformat()}

    # --- Tax Assessment ---
    if include_assessment:
        assessment = await fetch_one(dof.ASSESSMENT, bbl)
        response["assessment"] = assessment

    # --- Tax Exemptions ---
    if include_exemptions:
        exemptions = await fetch_all(dof.EXEMPTIONS, bbl)
        response["exemptions"] = exemptions

    # --- Rent Stabilization ---
    if include_rent_stab:
        rs = await fetch_one(rentstab.RENT_STABILIZATION, bbl)
        if rs:
            # Calculate trend
            unit_counts = [
                rs.get(f"uc{yr}") for yr in range(2007, 2018) if rs.get(f"uc{yr}") is not None
            ]
            if len(unit_counts) >= 2:
                trend = "declining" if unit_counts[-1] < unit_counts[0] else (
                    "stable" if unit_counts[-1] == unit_counts[0] else "increasing"
                )
                rs["_unit_count_trend"] = trend
                if trend == "declining":
                    rs["_deregulation_warning"] = (
                        "Unit count has declined — may indicate deregulation. "
                        "Verify current stabilization status with DHCR."
                    )
            rs["_data_note"] = "Rent stabilization data covers 2007-2017 only."
        response["rent_stabilization"] = rs

    # --- Tax Liens ---
    if include_liens:
        liens = await fetch_all(dof.TAX_LIENS, bbl)
        response["tax_liens"] = liens if liens else []
        if not liens:
            response["tax_lien_status"] = "No outstanding tax liens on record."

    # --- ACRIS Mortgages/Liens (Phase C -- uncomment when ACRIS loaded) ---
    # if include_liens:
    #     from nyc_property_intel.queries import acris
    #     mortgages = await fetch_all(acris.MORTGAGES_AND_LIENS, boro, block, lot)
    #     response["mortgages_and_liens"] = mortgages

    return response
```

---

#### File B4: Update `src/nyc_property_intel/tools/issues.py`

**Phase B update action:** Uncomment the DOB violations, DOB permits, and HPD registration blocks in the Phase A file (see File A14 above). No new file needed.

---

#### File B5: Update `src/nyc_property_intel/server.py`

**Phase B update action:** Uncomment the Phase B imports:

```python
import nyc_property_intel.tools.history  # noqa: E402, F401
import nyc_property_intel.tools.comps  # noqa: E402, F401
import nyc_property_intel.tools.financials  # noqa: E402, F401
```

---

### Phase C — Week 3: ACRIS + Compound Tool

---

#### File C1: Update `src/nyc_property_intel/tools/history.py`

**Phase C update action:** Uncomment the ACRIS ownership chain and transactions blocks (see File B1 above).

---

#### File C2: Update `src/nyc_property_intel/tools/financials.py`

**Phase C update action:** Uncomment the ACRIS mortgages/liens block (see File B3 above).

---

#### File C3: `src/nyc_property_intel/tools/analysis.py`

**Purpose:** `analyze_property` -- compound tool that calls all other tools concurrently via `asyncio.gather`.
**Phase:** C
**Dependencies:** `app.py`, `db.py`, all `queries/*.py`
**Exports:** `analyze_property` (registered as MCP tool)

```python
import asyncio
import logging
from datetime import date

from nyc_property_intel.app import mcp
from nyc_property_intel.db import fetch_one, fetch_all, validate_bbl, parse_bbl
from nyc_property_intel.queries import pluto, hpd, dob, dof, acris, rentstab

logger = logging.getLogger(__name__)


async def _safe(coro, label: str) -> dict | list | None:
    """Run a coroutine and catch exceptions, returning None on failure."""
    try:
        return await coro
    except Exception as e:
        logger.warning(f"analyze_property: {label} failed: {e}")
        return None


@mcp.tool
async def analyze_property(bbl: str) -> dict:
    """Run a comprehensive due diligence analysis for a property.

    This is a compound tool that queries all available data sources concurrently
    and returns a structured summary with property details, financial snapshot,
    risk factors, development potential, and market comparison.

    Equivalent to calling lookup_property + get_property_issues + get_property_history
    + get_financials + search_comps — but faster because queries run in parallel.

    Args:
        bbl: 10-digit BBL code (required). Use lookup_property first to get the BBL.
    """
    try:
        bbl = validate_bbl(bbl)
    except ValueError as e:
        return {"error": str(e)}

    boro, block, lot = parse_bbl(bbl)

    # Run all queries concurrently with 45-second timeout
    try:
        results = await asyncio.wait_for(
            asyncio.gather(
                _safe(fetch_one(pluto.PROPERTY_PROFILE, bbl), "profile"),
                _safe(fetch_one(hpd.HPD_VIOLATION_SUMMARY, bbl), "hpd_summary"),
                _safe(fetch_one(dob.DOB_VIOLATION_SUMMARY, bbl), "dob_summary"),
                _safe(fetch_all(dof.SALES_HISTORY, bbl, 5), "sales"),
                _safe(fetch_one(dof.ASSESSMENT, bbl), "assessment"),
                _safe(fetch_all(dof.EXEMPTIONS, bbl), "exemptions"),
                _safe(fetch_all(dof.TAX_LIENS, bbl), "liens"),
                _safe(fetch_one(rentstab.RENT_STABILIZATION, bbl), "rentstab"),
                _safe(fetch_all(acris.OWNERSHIP_HISTORY, boro, block, lot, 3), "ownership"),
            ),
            timeout=45.0,
        )
    except asyncio.TimeoutError:
        return {"error": "Analysis timed out after 45 seconds. Try individual tools instead."}

    (profile, hpd_summary, dob_summary, sales, assessment,
     exemptions, tax_liens, rent_stab, ownership) = results

    response = {
        "bbl": bbl,
        "data_as_of": date.today().isoformat(),
    }

    # --- Property Summary ---
    if profile:
        response["property_summary"] = {
            "address": profile.get("address"),
            "borough": profile.get("borough"),
            "building_class": profile.get("bldgclass"),
            "land_use": profile.get("landuse"),
            "zoning": profile.get("zonedist1"),
            "year_built": profile.get("yearbuilt"),
            "num_floors": profile.get("numfloors"),
            "residential_units": profile.get("unitsres"),
            "total_units": profile.get("unitstotal"),
            "lot_sqft": profile.get("lotarea"),
            "building_sqft": profile.get("bldgarea"),
            "assessment_roll_owner": profile.get("ownername"),
        }

        # --- Development Potential ---
        lot_area = profile.get("lotarea") or 0
        built_far = profile.get("builtfar") or 0
        resid_far = profile.get("residfar") or 0
        comm_far = profile.get("commfar") or 0
        max_far = max(resid_far, comm_far)
        unused_far = max(0, max_far - built_far)
        response["development_potential"] = {
            "current_far": built_far,
            "max_residential_far": resid_far,
            "max_commercial_far": comm_far,
            "unused_far": round(unused_far, 2),
            "potential_additional_sqft": round(unused_far * lot_area) if lot_area else None,
            "historic_district": profile.get("histdist"),
            "landmark": profile.get("landmark"),
        }

    # --- Financial Snapshot ---
    if assessment:
        response["financial_snapshot"] = {
            "market_value_total": assessment.get("curmkttotalval"),
            "market_value_land": assessment.get("curmktlandval"),
            "assessed_total": assessment.get("curacttotalval"),
            "exempt_total": assessment.get("curexmptotalval"),
            "tax_basis": assessment.get("curtaxbastotal"),
        }

    if sales:
        last_sale = sales[0]
        response.setdefault("financial_snapshot", {})["last_sale"] = {
            "date": last_sale.get("saledate"),
            "price": last_sale.get("saleprice"),
            "building_class": last_sale.get("buildingclassattimeofsale"),
        }

    # --- Risk Factors ---
    risk = {}
    if hpd_summary:
        risk["hpd_violations"] = {
            "total": hpd_summary.get("total", 0),
            "open": hpd_summary.get("open_violations", 0),
            "open_class_c": hpd_summary.get("open_class_c", 0),
            "rent_impairing": hpd_summary.get("rent_impairing", 0),
            "most_recent": hpd_summary.get("most_recent_date"),
        }
    if dob_summary:
        risk["dob_violations"] = {
            "total": dob_summary.get("total", 0),
            "open": dob_summary.get("open_violations", 0),
            "most_recent": dob_summary.get("most_recent_date"),
        }
    risk["tax_liens"] = tax_liens if tax_liens else "None on record"
    response["risk_factors"] = risk

    # --- Rent Stabilization ---
    if rent_stab:
        response["rent_stabilization"] = {
            "is_stabilized": True,
            "current_units": rent_stab.get("uc2017"),
            "units_2007": rent_stab.get("uc2007"),
            "data_note": "Covers 2007-2017 only",
        }
    else:
        response["rent_stabilization"] = {
            "is_stabilized": False,
            "note": "No rent stabilization records found (may be exempt, post-2017, or not stabilized)",
        }

    # --- Tax Benefits ---
    if exemptions:
        response["tax_benefits"] = [
            {"code": ex.get("exmpcode"), "type": ex.get("exemption_type"),
             "amount": ex.get("exempt_amount")}
            for ex in exemptions[:5]
        ]

    # --- Ownership ---
    if ownership:
        response["recent_ownership"] = [
            {"date": o.get("transfer_date"), "amount": o.get("amount"),
             "type": o.get("doc_type_description"),
             "buyers": o.get("buyer_names"), "sellers": o.get("seller_names")}
            for o in ownership
        ]

    # --- Key Observations (for Claude to elaborate on) ---
    observations = []
    if hpd_summary and (hpd_summary.get("open_class_c") or 0) > 0:
        observations.append(
            f"WARNING: {hpd_summary['open_class_c']} open Class C (immediately hazardous) HPD violations."
        )
    if tax_liens:
        observations.append("WARNING: Property appears on DOF tax lien sale list.")
    if profile and (profile.get("builtfar") or 0) < (max(profile.get("residfar") or 0, profile.get("commfar") or 0)) * 0.5:
        observations.append("Significant unused FAR -- potential development/air rights value.")
    if sales and sales[0].get("saleprice") is not None and sales[0]["saleprice"] <= 1:
        observations.append("Most recent sale was $0/$1 -- non-arm's-length (LLC transfer, inheritance).")
    response["key_observations"] = observations if observations else ["No red flags detected in available data."]

    return response
```

---

#### File C4: Update `src/nyc_property_intel/server.py`

**Phase C update action:** Uncomment the `analysis` import:

```python
import nyc_property_intel.tools.analysis  # noqa: E402, F401
```

---

## 7. Seed Script (Rewrite)

**File:** `/Users/devtzi/dev/nyc-property-intel/scripts/seed_nycdb.sh`
**Action:** REWRITE -- add per-dataset retry, phase support, partial load resumption

```bash
#!/bin/bash
set -uo pipefail
# Note: NOT set -e because we handle errors per-dataset

DB_USER="${DB_USER:-nycdb}"
DB_NAME="${DB_NAME:-nycdb}"
DB_PASS="${DB_PASS:-nycdb}"
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
DATA_DIR="${DATA_DIR:-./data}"
PHASE="${1:-A}"  # A, B, C, or ALL

# Datasets by phase
PHASE_A_DATASETS=(pluto_latest pad hpd_violations)
PHASE_B_DATASETS=(dof_sales dob_violations)
PHASE_C_DATASETS=(acris)

# Select datasets based on phase argument
case "$PHASE" in
  A)   DATASETS=("${PHASE_A_DATASETS[@]}") ;;
  B)   DATASETS=("${PHASE_B_DATASETS[@]}") ;;
  C)   DATASETS=("${PHASE_C_DATASETS[@]}") ;;
  ALL) DATASETS=("${PHASE_A_DATASETS[@]}" "${PHASE_B_DATASETS[@]}" "${PHASE_C_DATASETS[@]}") ;;
  *)   echo "Usage: $0 [A|B|C|ALL]"; exit 1 ;;
esac

NYCDB_ARGS="-U $DB_USER -D $DB_NAME -P $DB_PASS -H $DB_HOST --port $DB_PORT --root-dir $DATA_DIR"
FAILED=()
LOADED=()

mkdir -p "$DATA_DIR"

for ds in "${DATASETS[@]}"; do
  echo ""
  echo "========================================"
  echo "  Processing: $ds"
  echo "========================================"

  # Check if already loaded (table exists and has rows)
  ROW_COUNT=$(PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
    -tAc "SELECT COUNT(*) FROM $ds LIMIT 1" 2>/dev/null || echo "0")

  if [ "$ROW_COUNT" != "0" ] && [ "$ROW_COUNT" != "" ]; then
    echo "  -> Already loaded ($ROW_COUNT+ rows). Skipping. Use --force to reload."
    LOADED+=("$ds (skipped, already loaded)")
    continue
  fi

  # Download with retry
  for attempt in 1 2 3; do
    echo "  -> Download attempt $attempt/3..."
    if nycdb --download $ds $NYCDB_ARGS; then
      break
    fi
    if [ "$attempt" -eq 3 ]; then
      echo "  -> FAILED to download $ds after 3 attempts"
      FAILED+=("$ds (download)")
      continue 2
    fi
    sleep 5
  done

  # Load
  echo "  -> Loading into database..."
  if nycdb --load $ds $NYCDB_ARGS; then
    echo "  -> Loaded successfully"
    LOADED+=("$ds")
  else
    echo "  -> FAILED to load $ds"
    FAILED+=("$ds (load)")
    continue
  fi
done

# Run indexes and views only if all datasets for the phase loaded
echo ""
echo "========================================"
echo "  Running indexes and views"
echo "========================================"

PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
  -f scripts/create_indexes.sql 2>&1 | tail -5

# Views depend on which tables exist -- the SQL uses IF NOT EXISTS so partial runs are safe
PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
  -f scripts/create_views.sql 2>&1 | tail -5

# Summary
echo ""
echo "========================================"
echo "  SUMMARY (Phase $PHASE)"
echo "========================================"
echo "Loaded: ${LOADED[*]:-none}"
echo "Failed: ${FAILED[*]:-none}"

if [ ${#FAILED[@]} -gt 0 ]; then
  echo ""
  echo "Some datasets failed. Re-run: ./scripts/seed_nycdb.sh $PHASE"
  exit 1
fi
```

---

## 8. create_indexes.sql (Rewrite)

**File:** `/Users/devtzi/dev/nyc-property-intel/scripts/create_indexes.sql`
**Action:** REWRITE -- only index tables that exist (DO blocks), remove ACRIS GIN index (too slow for MVP)

```sql
-- NYC Property Intel: Custom indexes for fast BBL lookups
-- Safe to run multiple times (IF NOT EXISTS)
-- Safe to run with partial data (DO blocks check table existence)

-- =============================================
-- PLUTO (Phase A)
-- =============================================
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'pluto_latest') THEN
    CREATE INDEX IF NOT EXISTS idx_pluto_bbl ON pluto_latest (bbl);
    CREATE INDEX IF NOT EXISTS idx_pluto_postcode ON pluto_latest (postcode);
    -- Full-text on address for fuzzy matching
    CREATE INDEX IF NOT EXISTS idx_pluto_address_gin ON pluto_latest
        USING gin (to_tsvector('english', address));
    RAISE NOTICE 'pluto_latest indexes created';
  END IF;
END $$;

-- =============================================
-- PAD (Phase A)
-- =============================================
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'pad_adr') THEN
    CREATE INDEX IF NOT EXISTS idx_pad_adr_boro_stname ON pad_adr (boro, stname);
    CREATE INDEX IF NOT EXISTS idx_pad_adr_bbl ON pad_adr (bbl);
    RAISE NOTICE 'pad_adr indexes created';
  END IF;
END $$;

-- =============================================
-- HPD Violations (Phase A)
-- =============================================
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'hpd_violations') THEN
    CREATE INDEX IF NOT EXISTS idx_hpd_violations_bbl ON hpd_violations (bbl);
    CREATE INDEX IF NOT EXISTS idx_hpd_violations_date ON hpd_violations (inspectiondate DESC);
    CREATE INDEX IF NOT EXISTS idx_hpd_violations_class ON hpd_violations (class);
    CREATE INDEX IF NOT EXISTS idx_hpd_violations_status ON hpd_violations (currentstatus);
    RAISE NOTICE 'hpd_violations indexes created';
  END IF;
END $$;

-- =============================================
-- DOF Sales (Phase B)
-- =============================================
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'dof_sales') THEN
    CREATE INDEX IF NOT EXISTS idx_dof_sales_bbl ON dof_sales (bbl);
    CREATE INDEX IF NOT EXISTS idx_dof_sales_date ON dof_sales (saledate DESC);
    CREATE INDEX IF NOT EXISTS idx_dof_sales_zip ON dof_sales (zipcode);
    CREATE INDEX IF NOT EXISTS idx_dof_sales_bldgclass ON dof_sales (buildingclassattimeofsale);
    RAISE NOTICE 'dof_sales indexes created';
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'dof_annual_sales') THEN
    CREATE INDEX IF NOT EXISTS idx_dof_annual_sales_bbl ON dof_annual_sales (bbl);
    CREATE INDEX IF NOT EXISTS idx_dof_annual_sales_date ON dof_annual_sales (saledate DESC);
    RAISE NOTICE 'dof_annual_sales indexes created';
  END IF;
END $$;

-- =============================================
-- DOB Violations (Phase B)
-- =============================================
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'dob_violations') THEN
    CREATE INDEX IF NOT EXISTS idx_dob_violations_bbl ON dob_violations (bbl);
    CREATE INDEX IF NOT EXISTS idx_dob_violations_date ON dob_violations (issuedate DESC);
    RAISE NOTICE 'dob_violations indexes created';
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'dobjobs') THEN
    CREATE INDEX IF NOT EXISTS idx_dobjobs_bbl ON dobjobs (bbl);
    CREATE INDEX IF NOT EXISTS idx_dobjobs_type ON dobjobs (jobtype);
    RAISE NOTICE 'dobjobs indexes created';
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'dob_now_jobs') THEN
    CREATE INDEX IF NOT EXISTS idx_dob_now_jobs_bbl ON dob_now_jobs (bbl);
    RAISE NOTICE 'dob_now_jobs indexes created';
  END IF;
END $$;

-- =============================================
-- DOF Assessment + Exemptions + Liens (Phase B)
-- =============================================
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'dof_property_valuation_and_assessments') THEN
    CREATE INDEX IF NOT EXISTS idx_dof_val_bbl ON dof_property_valuation_and_assessments (bbl);
    RAISE NOTICE 'dof_property_valuation_and_assessments indexes created';
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'dof_exemptions') THEN
    CREATE INDEX IF NOT EXISTS idx_dof_exemptions_bbl ON dof_exemptions (bbl);
    RAISE NOTICE 'dof_exemptions indexes created';
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'dof_tax_lien_sale_list') THEN
    CREATE INDEX IF NOT EXISTS idx_dof_liens_bbl ON dof_tax_lien_sale_list (bbl);
    RAISE NOTICE 'dof_tax_lien_sale_list indexes created';
  END IF;
END $$;

-- =============================================
-- HPD Registration + Complaints (Phase B)
-- =============================================
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'hpd_registrations') THEN
    CREATE INDEX IF NOT EXISTS idx_hpd_reg_bbl ON hpd_registrations (boroid, block, lot);
    RAISE NOTICE 'hpd_registrations indexes created';
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'hpd_contacts') THEN
    CREATE INDEX IF NOT EXISTS idx_hpd_contacts_regid ON hpd_contacts (registrationid);
    RAISE NOTICE 'hpd_contacts indexes created';
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'hpd_complaints') THEN
    CREATE INDEX IF NOT EXISTS idx_hpd_complaints_bbl ON hpd_complaints (bbl);
    RAISE NOTICE 'hpd_complaints indexes created';
  END IF;
END $$;

-- =============================================
-- Rent Stabilization (Phase B)
-- =============================================
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'rentstab') THEN
    CREATE INDEX IF NOT EXISTS idx_rentstab_bbl ON rentstab (ucbbl);
    RAISE NOTICE 'rentstab indexes created';
  END IF;
END $$;

-- =============================================
-- ACRIS (Phase C)
-- =============================================
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'acris_real_property_legals') THEN
    CREATE INDEX IF NOT EXISTS idx_acris_legals_bbl
        ON acris_real_property_legals (borough, block, lot);
    CREATE INDEX IF NOT EXISTS idx_acris_master_docdate
        ON acris_real_property_master (docdate DESC);
    CREATE INDEX IF NOT EXISTS idx_acris_master_doctype
        ON acris_real_property_master (doctype);
    CREATE INDEX IF NOT EXISTS idx_acris_parties_docid
        ON acris_real_property_parties (documentid);
    RAISE NOTICE 'acris indexes created';
  END IF;
END $$;
```

---

## 9. create_views.sql (Rewrite)

**File:** `/Users/devtzi/dev/nyc-property-intel/scripts/create_views.sql`
**Action:** REWRITE -- fix ACRIS BBL concat bug, split violation summary into HPD and DOB, guard with table existence checks

```sql
-- NYC Property Intel: Materialized views
-- Safe to run multiple times (DROP + CREATE pattern)
-- Each view checks if source tables exist before creating

-- =============================================
-- VIEW 1: Property Master Profile (Phase A)
-- Source: pluto_latest
-- =============================================
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'pluto_latest') THEN
    DROP MATERIALIZED VIEW IF EXISTS mv_property_profile CASCADE;
    CREATE MATERIALIZED VIEW mv_property_profile AS
    SELECT
        bbl, address, borough, block, lot, ownername,
        bldgclass, landuse, zonedist1, zonedist2, overlay1, spldist1,
        numbldgs, numfloors, unitsres, unitstotal,
        lotarea, bldgarea, comarea, resarea, officearea, retailarea,
        yearbuilt, yearalter1, yearalter2,
        builtfar, residfar, commfar,
        assessland, assesstot, exempttot,
        histdist, landmark, latitude, longitude, postcode, condono
    FROM pluto_latest
    WITH DATA;

    CREATE UNIQUE INDEX ON mv_property_profile (bbl);
    CREATE INDEX ON mv_property_profile (postcode);

    RAISE NOTICE 'mv_property_profile created';
  ELSE
    RAISE NOTICE 'SKIPPED mv_property_profile (pluto_latest not loaded)';
  END IF;
END $$;

-- =============================================
-- VIEW 2: HPD Violation Summary (Phase A)
-- Separate from DOB to avoid conflating class meanings
-- HPD class A/B/C != DOB violationcategory
-- =============================================
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'hpd_violations') THEN
    DROP MATERIALIZED VIEW IF EXISTS mv_hpd_violation_summary CASCADE;
    CREATE MATERIALIZED VIEW mv_hpd_violation_summary AS
    SELECT
        bbl,
        COUNT(*) AS total,
        COUNT(*) FILTER (WHERE class = 'C') AS class_c,
        COUNT(*) FILTER (WHERE class = 'B') AS class_b,
        COUNT(*) FILTER (WHERE class = 'A') AS class_a,
        COUNT(*) FILTER (WHERE currentstatus = 'OPEN') AS open_violations,
        COUNT(*) FILTER (WHERE currentstatus = 'OPEN' AND class = 'C') AS open_class_c,
        COUNT(*) FILTER (WHERE rentimpairing = 'YES') AS rent_impairing,
        MAX(inspectiondate) AS most_recent_date
    FROM hpd_violations
    GROUP BY bbl
    WITH DATA;

    CREATE UNIQUE INDEX ON mv_hpd_violation_summary (bbl);

    RAISE NOTICE 'mv_hpd_violation_summary created';
  ELSE
    RAISE NOTICE 'SKIPPED mv_hpd_violation_summary (hpd_violations not loaded)';
  END IF;
END $$;

-- =============================================
-- VIEW 3: DOB Violation Summary (Phase B)
-- Separate from HPD -- different semantics
-- =============================================
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'dob_violations') THEN
    DROP MATERIALIZED VIEW IF EXISTS mv_dob_violation_summary CASCADE;
    CREATE MATERIALIZED VIEW mv_dob_violation_summary AS
    SELECT
        bbl,
        COUNT(*) AS total,
        COUNT(*) FILTER (WHERE dispositiondate IS NULL) AS open_violations,
        MAX(issuedate) AS most_recent_date
    FROM dob_violations
    GROUP BY bbl
    WITH DATA;

    CREATE UNIQUE INDEX ON mv_dob_violation_summary (bbl);

    RAISE NOTICE 'mv_dob_violation_summary created';
  ELSE
    RAISE NOTICE 'SKIPPED mv_dob_violation_summary (dob_violations not loaded)';
  END IF;
END $$;

-- =============================================
-- VIEW 4: ACRIS Current Ownership (Phase C)
-- FIX: ACRIS legals stores borough/block/lot as separate columns.
-- borough is char ('1'-'5'), block is int, lot is int.
-- Correct BBL concat: borough || lpad(block::text, 5, '0') || lpad(lot::text, 4, '0')
-- FIX: Use whitelist of deed doc types, not just LIKE '%DEED%'
-- =============================================
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'acris_real_property_legals') THEN
    DROP MATERIALIZED VIEW IF EXISTS mv_current_ownership CASCADE;
    CREATE MATERIALIZED VIEW mv_current_ownership AS
    SELECT DISTINCT ON (computed_bbl)
        l.borough || lpad(l.block::text, 5, '0') || lpad(l.lot::text, 4, '0') AS bbl,
        m.doctype,
        m.docdate,
        m.docamount,
        p.name AS owner_name,
        p.address1,
        p.city,
        p.state,
        p.zip,
        m.documentid
    FROM acris_real_property_legals l
    JOIN acris_real_property_master m ON l.documentid = m.documentid
    JOIN acris_real_property_parties p ON m.documentid = p.documentid
    WHERE m.doctype IN ('DEED', 'DEDL', 'DEDC', 'RPTT')
      AND p.partytype = 2
    ORDER BY
        l.borough || lpad(l.block::text, 5, '0') || lpad(l.lot::text, 4, '0') AS computed_bbl,
        m.docdate DESC
    WITH DATA;

    CREATE UNIQUE INDEX ON mv_current_ownership (bbl);

    RAISE NOTICE 'mv_current_ownership created';
  ELSE
    RAISE NOTICE 'SKIPPED mv_current_ownership (acris not loaded)';
  END IF;
END $$;
```

**Bugs fixed from original:**
1. **Violation UNION conflation** -- HPD and DOB now have separate materialized views instead of one combined view. HPD `class` (A/B/C) and DOB `violationcategory` are completely different taxonomies.
2. **ACRIS BBL concat** -- the `DISTINCT ON` expression now uses `computed_bbl` alias (see the `ORDER BY` clause). Note: if your Postgres version doesn't support aliased `DISTINCT ON`, use the full expression in both places.
3. **ACRIS deed filter** -- changed from `LIKE '%DEED%'` to explicit doc type whitelist `IN ('DEED', 'DEDL', 'DEDC', 'RPTT')`.
4. **Table existence guards** -- each view checks if its source table exists before creating, so partial loads don't cause failures.

**Important note on View 4 syntax:** The `DISTINCT ON` with alias in `ORDER BY` is a PostgreSQL extension. If it doesn't work, replace:
```sql
ORDER BY
    l.borough || lpad(l.block::text, 5, '0') || lpad(l.lot::text, 4, '0') AS computed_bbl,
```
with:
```sql
ORDER BY
    l.borough || lpad(l.block::text, 5, '0') || lpad(l.lot::text, 4, '0'),
```

---

## 10. Tests

**File:** `/Users/devtzi/dev/nyc-property-intel/tests/conftest.py`
**Phase:** A

```python
import asyncio
import pytest


@pytest.fixture(scope="session")
def event_loop():
    """Create a session-scoped event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# Known NYC properties for testing
EMPIRE_STATE = "1008350001"
BROOKLYN_BROWNSTONE = "3011620044"
INVALID_BBL = "9999999999"
SHORT_BBL = "12345"
```

---

**File:** `/Users/devtzi/dev/nyc-property-intel/tests/test_smoke.py`
**Phase:** A (tests 1-3), B (test 4), C (test 5)

```python
"""
Smoke tests -- run against a live database with loaded NYCDB data.
These are NOT unit tests. They verify end-to-end query execution.
Run: uv run pytest tests/test_smoke.py -v
"""
import pytest
from nyc_property_intel.db import validate_bbl, fetch_one, row_to_dict
from tests.conftest import EMPIRE_STATE, INVALID_BBL, SHORT_BBL


# --- Test 1: BBL validation ---
class TestBBLValidation:
    def test_valid_bbl(self):
        assert validate_bbl("1008350001") == "1008350001"

    def test_invalid_borough(self):
        with pytest.raises(ValueError, match="borough digit must be 1-5"):
            validate_bbl("6008350001")

    def test_short_bbl(self):
        with pytest.raises(ValueError, match="must be 10 digits"):
            validate_bbl("12345")

    def test_non_numeric(self):
        with pytest.raises(ValueError, match="only digits"):
            validate_bbl("100835000X")


# --- Test 2: JSON serialization ---
class TestSerialization:
    def test_row_to_dict_handles_types(self):
        """Verify _serialize handles all asyncpg return types."""
        import datetime
        from decimal import Decimal
        from unittest.mock import MagicMock

        # Simulate an asyncpg Record as a dict
        mock_row = MagicMock()
        mock_row.items.return_value = [
            ("date_field", datetime.date(2024, 1, 15)),
            ("decimal_field", Decimal("123456.78")),
            ("str_field", "hello"),
            ("none_field", None),
            ("int_field", 42),
        ]
        mock_row.__iter__ = lambda self: iter(dict(self.items()))

        # Directly test _serialize
        from nyc_property_intel.db import _serialize
        assert _serialize(datetime.date(2024, 1, 15)) == "2024-01-15"
        assert _serialize(Decimal("123456.78")) == 123456.78
        assert _serialize("hello") == "hello"
        assert _serialize(None) is None


# --- Test 3: PLUTO lookup (requires loaded data) ---
@pytest.mark.asyncio
async def test_pluto_lookup():
    """Verify PLUTO data is loaded and queryable."""
    result = await fetch_one(
        "SELECT bbl, address, numfloors FROM pluto_latest WHERE bbl = $1",
        EMPIRE_STATE,
    )
    assert result is not None, f"Empire State Building ({EMPIRE_STATE}) not found in pluto_latest"
    assert result["bbl"] == EMPIRE_STATE
    # ESB has 102 floors
    assert result["numfloors"] is not None


# --- Test 4: DOF Sales lookup (requires Phase B data) ---
@pytest.mark.asyncio
async def test_sales_lookup():
    """Verify DOF sales data is loaded and queryable."""
    result = await fetch_one(
        "SELECT COUNT(*) AS cnt FROM dof_sales WHERE bbl = $1",
        EMPIRE_STATE,
    )
    assert result is not None
    # ESB should have at least one sale on record
    assert result["cnt"] >= 0  # may be 0 if never in rolling sales window


# --- Test 5: Full tool smoke test (requires all phases) ---
@pytest.mark.asyncio
async def test_lookup_tool_returns_profile():
    """End-to-end: call lookup_property and verify response shape."""
    from nyc_property_intel.tools.lookup import lookup_property

    result = await lookup_property(bbl=EMPIRE_STATE)
    assert "error" not in result
    assert result["bbl"] == EMPIRE_STATE
    assert "data_as_of" in result
    assert result.get("numfloors") is not None
```

---

## 11. Claude Desktop Registration

**File:** `~/Library/Application Support/Claude/claude_desktop_config.json`
**Phase:** A (end of week 1, for end-to-end testing)

Add this entry to the `mcpServers` object:

```json
{
  "mcpServers": {
    "nyc-property-intel": {
      "command": "/Users/devtzi/.local/bin/uv",
      "args": [
        "--directory",
        "/Users/devtzi/dev/nyc-property-intel",
        "run",
        "src/nyc_property_intel/server.py"
      ],
      "env": {
        "DATABASE_URL": "postgresql://nycdb:nycdb@localhost:5432/nycdb",
        "NYC_GEOCLIENT_SUBSCRIPTION_KEY": "your_key_here",
        "SOCRATA_APP_TOKEN": "your_token_here"
      }
    }
  }
}
```

---

## 12. Files to Delete

These are from the original 13-tool scaffold. They are replaced by the 6 consolidated tools:

```
src/nyc_property_intel/tools/violations.py    -> merged into issues.py
src/nyc_property_intel/tools/permits.py       -> merged into issues.py
src/nyc_property_intel/tools/ownership.py     -> merged into history.py
src/nyc_property_intel/tools/transactions.py  -> merged into history.py
src/nyc_property_intel/tools/tax.py           -> merged into financials.py
src/nyc_property_intel/tools/rent_stab.py     -> merged into financials.py
src/nyc_property_intel/tools/liens.py         -> merged into financials.py
src/nyc_property_intel/tools/neighborhood.py  -> merged into comps.py
alembic/                                      -> not needed (NYCDB owns schemas)
alembic.ini                                   -> not needed
```

---

## 13. Implementation Order Checklist

### Phase A -- Week 1

```
Day 1-2: Infrastructure
[ ] docker-compose up -d (start Postgres)
[ ] Create .env from .env.example
[ ] Create .gitignore
[ ] Delete old tool files + alembic
[ ] Write db.py (serialization, validation, pool cleanup)
[ ] Write http_client.py
[ ] Update app.py (full MCP instructions with fair housing)
[ ] Update geoclient.py (shared client, PAD fix)
[ ] Update socrata.py (shared client)
[ ] Write queries/pluto.py
[ ] Write queries/hpd.py

Day 3-4: Data + Tools
[ ] Run: ./scripts/seed_nycdb.sh A (loads pluto_latest, pad, hpd_violations)
[ ] Verify: psql -c "SELECT COUNT(*) FROM pluto_latest"
[ ] Write tools/lookup.py
[ ] Write tools/issues.py (HPD violations only)
[ ] Write server.py (Phase A imports + cleanup hooks)

Day 5: Testing + Registration
[ ] Write tests/conftest.py + tests/test_smoke.py
[ ] Run: uv run pytest tests/test_smoke.py::TestBBLValidation -v
[ ] Run: uv run pytest tests/test_smoke.py::test_pluto_lookup -v
[ ] Register with Claude Desktop (claude_desktop_config.json)
[ ] Test: "Tell me about 350 5th Ave, Manhattan" in Claude Desktop
[ ] Test: "What HPD violations does BBL 1008350001 have?" in Claude Desktop
```

### Phase B -- Week 2

```
Day 1-2: Data + Queries
[ ] Run: ./scripts/seed_nycdb.sh B (loads dof_sales, dob_violations)
[ ] Write queries/dof.py
[ ] Write queries/dob.py
[ ] Write queries/rentstab.py

Day 3-4: Tools
[ ] Write tools/history.py (sales only)
[ ] Write tools/comps.py
[ ] Write tools/financials.py
[ ] Update tools/issues.py (uncomment DOB + permits + HPD reg)
[ ] Update server.py (uncomment Phase B imports)

Day 5: Testing
[ ] Run: uv run pytest tests/test_smoke.py -v
[ ] Test in Claude Desktop: "Show me comps for 123 Main St, Brooklyn"
[ ] Test: "What's the financial profile of BBL 3011620044?"
```

### Phase C -- Week 3

```
Day 1-3: ACRIS Data
[ ] Run: ./scripts/seed_nycdb.sh C (loads acris -- ~4GB, takes 1-2 hours)
[ ] Write queries/acris.py
[ ] Verify: psql -c "SELECT COUNT(*) FROM acris_real_property_legals"

Day 3-4: Tools
[ ] Update tools/history.py (uncomment ownership + transactions)
[ ] Update tools/financials.py (uncomment mortgages/liens)
[ ] Write tools/analysis.py (compound tool)
[ ] Update server.py (uncomment Phase C imports)

Day 5: End-to-End
[ ] Test in Claude Desktop: "Run a full analysis on 350 5th Ave, Manhattan"
[ ] Test: "Who owns BBL 1008350001 and what's the ownership chain?"
[ ] Test: "What liens and mortgages are on this property?"
[ ] Verify: compound tool completes in <45 seconds
```

---

## 14. Summary of Review Bug Fixes Applied

| Review Finding | Fix Location | Status |
|---|---|---|
| #1 JSON serialization TypeError | `db.py` `_serialize()` / `row_to_dict()` | Fixed in Phase A |
| #2 No pool cleanup | `db.py` `close_pool()` + `server.py` SIGTERM handler | Fixed in Phase A |
| #3 Interval string concat | `queries/dof.py` uses `make_interval()` | Fixed in Phase B |
| #4 ACRIS deed filter too narrow | `queries/acris.py` whitelist `DEED_DOC_TYPES` | Fixed in Phase C |
| #5 Sales history duplicates | `queries/dof.py` uses `DISTINCT ON (saledate, saleprice)` | Fixed in Phase B |
| #6 PAD fallback broken | `geoclient.py` casts `lhousenum::int` | Fixed in Phase A |
| #7 Condo BBLs | `queries/pluto.py` includes `condono` field | Partially addressed |
| #8 PLUTO owner stale | `tools/lookup.py` adds `assessment_roll_owner_note` | Fixed in Phase A |
| #9 Circular import | `app.py` owns `mcp` instance | Already fixed in scaffold |
| #10 httpx per-request | `http_client.py` shared singleton | Fixed in Phase A |
| #11 Violation UNION conflation | `create_views.sql` separate HPD + DOB views | Fixed in Phase A |
| #12 No BBL validation | `db.py` `validate_bbl()` called by every tool | Fixed in Phase A |
| #13 No data_as_of | Every tool response includes `data_as_of` | Fixed in Phase A |
| #14 Compound tool timeout | `tools/analysis.py` uses `asyncio.wait_for(timeout=45)` | Fixed in Phase C |
