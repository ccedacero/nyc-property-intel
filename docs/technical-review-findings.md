# Technical Review Findings — Must-Fix Before Implementation

**Date:** 2026-03-18

## Critical Bugs (Will Crash on First Use)

### 1. asyncpg Returns Non-JSON-Serializable Types
asyncpg returns `datetime.date`, `Decimal`, `UUID`, etc. FastMCP calls `json.dumps()` on tool returns → `TypeError`.
**Fix:** Add serialization layer in `db.py`:
```python
def _serialize(obj):
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    return obj

def row_to_dict(row) -> dict:
    return {k: _serialize(v) for k, v in dict(row).items()}
```

### 2. No Connection Pool Cleanup
Claude Desktop can SIGKILL the server. Leaked connections accumulate until Postgres hits `max_connections=100`.
**Fix:** Register `atexit` + `signal.SIGTERM` handlers. Set `min_size=1`.

### 3. Interval String Concat Type Error
`$5 || ' months'` — asyncpg sends `$5` as integer, Postgres rejects `int || text`.
**Fix:** Use `make_interval(months => $5)`.

## Critical Data Bugs (Wrong Results)

### 4. ACRIS DEED Filter Too Narrow
`classcodedescrip LIKE '%DEED%'` misses RPTT, correction deeds, deed-in-lieu.
**Fix:** Whitelist doc types: `DEED, DEDL, DEDC, RPTT, CTOR, CORRD`.

### 5. Sales History Duplicates
`UNION ALL` between `dof_sales` and `dof_annual_sales` — same sales appear in both.
**Fix:** `DISTINCT ON (bbl, saledate, saleprice)`.

### 6. PAD Fallback Broken
`lhnd`/`hhnd` are TEXT — lexicographic `<=`/`>=` fails. Queens hyphenated addresses break entirely.
**Fix:** Cast `lhousenum::int`, handle Queens format separately.

### 7. Condo BBLs Wrong Owner
ACRIS deeds on unit lots (1001+), PLUTO maps to parent lot. ~15-20% of Manhattan.
**Fix:** Query both unit + parent lot, cross-reference `pluto_latest.condono`.

### 8. PLUTO `ownername` Stale
Lags 6-18 months behind actual transfers.
**Fix:** Label as `assessment_roll_owner`, use ACRIS grantee as canonical.

## Important Fixes

### 9. Circular Import
Tool modules import `mcp` from `server.py`.
**Fix:** Define `mcp` in `app.py`, import from there in both server and tools.

### 10. httpx Client Per-Request
Creates new TCP+TLS connection per call, adds 200-500ms latency.
**Fix:** Single `httpx.AsyncClient` at startup, reuse.

### 11. Violation UNION Conflates HPD/DOB
DOB `violationcategory` != HPD `class` (A/B/C). Status counts silently wrong.
**Fix:** Keep HPD and DOB as separate summary sections.

### 12. No BBL Validation
Invalid BBLs silently return empty results.
**Fix:** Validate 10-digit, starts with 1-5, before every query.

### 13. No Data Freshness in Responses
Plan mentions it but no tool schema includes it.
**Fix:** Add `data_as_of` field to every response.

### 14. No Tool-Level Timeout for Compound Tools
`analyze_property` makes 8 queries — could exceed Claude Desktop's ~60s expectation.
**Fix:** `asyncio.gather()` for concurrent sub-queries, `asyncio.wait_for(timeout=45)`.

## Datasets to Add (Plan Skips These)

- `dob_certificate_occupancy` — C of O is critical for legal use verification (move to Phase 1)
- `rentstab_v2` — check if extends past 2017 (plan assumes it doesn't)
- Lis pendens via ACRIS `LPND` doc type — already in ACRIS data, just add filter

## Tool Consolidation (13 → 6)

| New Tool | Merges |
|----------|--------|
| `lookup_property` | Keep as-is |
| `get_property_history` | ownership + transactions + sales |
| `get_property_issues` | violations + permits + HPD registration |
| `get_financials` | tax + rent stabilization + liens |
| `search_comps` | comparable sales + neighborhood stats |
| `analyze_property` | investment analysis (compound, build FIRST) |
