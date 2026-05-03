# Data infrastructure fix plan — 2026-05-02

Plan to remediate every issue surfaced by the [coverage audit](data-coverage-audit-2026-05-02.md). Phases are ordered by dependency, not severity. Each phase has concrete acceptance criteria so we can mark it done.

---

## Executive summary

| Phase | What | Why this order | ETA |
|---:|---|---|---|
| **0** | Pre-flight: snapshot critical tables | Cheap insurance before any reset | 10 min |
| **1** | Fix `_coerce()` date parsing + add tests + install `resend` | All downstream backfills depend on this fix; without it, every reset re-corrupts dates | 30 min |
| **2** | Backfill 2 corrupted datasets (`dob_complaints`, `dobjobs`) | Closes column-corruption bug + dobjobs FROZEN_SOURCE gap | ~30 min run |
| **3** | Create tier-3 monthly cron service on Railway | Unblocks `hpd_registrations`, ACRIS sub-tables ongoing sync, and the 3 NEVER_SYNCED backfills | 15 min setup |
| **4** | Backfill 3 NEVER_SYNCED datasets | Most expensive step; depends on tier-3 service existing | 1–3 hr each, run sequentially |
| **5** | Re-run audits, verify, set up weekly audit cron | Lock in regression detection | 30 min |
| **6** | Reclassify `nyc_311_complaints` from tier-2 to tier-1 (optional) | NYC updates 311 daily; weekly sync drops up to 7 days of complaints between runs | 5 min |

Total wall time: ~6–10 hours of which ~5 hours is unattended sync runs.

---

## Phase 0 — Pre-flight snapshots

Before any `--reset` against a multi-million-row table, take cheap PG snapshots of just the date columns. If something goes wrong we can `UPDATE FROM` the snapshot. Storage cost is negligible (a few hundred MB).

```sql
CREATE TABLE dobjobs_dates_snapshot_20260502 AS
  SELECT job, doc,
         latestactiondate, prefilingdate, paid, fullypaid,
         assigned, approved, fullypermitted, dobrundate,
         signoffdate, specialactiondate
    FROM dobjobs;

CREATE TABLE dob_complaints_dates_snapshot_20260502 AS
  SELECT complaintnumber,
         dateentered, dispositiondate, inspectiondate, dobrundate
    FROM dob_complaints;
```

Acceptance: `\dt+ *_snapshot_20260502` shows both tables non-empty in Railway prod.

These snapshots are mostly NULL anyway (that's the bug), but for `dobjobs` ~30% have real values from the original NYCDB bulk load — those are worth preserving in case the reset somehow drops them.

---

## Phase 1 — Code fixes

### 1.1 Fix `_coerce()` date format handling

**File**: `scripts/sync_delta.py:519-546`

**Problem**: only handles ISO 8601. M/D/YYYY (Socrata's `eabe-havv`, `ic3t-wcy2`) and YYYYMMDD (`3h2n-5cm9`) silently become NULL.

**Approach**: drop in a `_parse_flexible_date()` / `_parse_flexible_datetime()` helper that tries known formats in order. Explicit format list (no `dateutil.parser.parse`) for predictability — we know exactly which formats Socrata uses.

```python
from datetime import date, datetime

# Date formats observed across NYC Socrata datasets:
#   - ISO 8601:           "2014-01-06T00:00:00.000"   (most datasets)
#   - M/D/YYYY:           "06/23/2023"                (ic3t-wcy2, eabe-havv)
#   - M/D/YYYY HH:MM:SS:  "06/24/2023 00:00:00"       (ic3t-wcy2.dobrundate)
#   - YYYYMMDD:           "19881031"                  (3h2n-5cm9)
# Sentinels we should treat as None (not 9999-01-20):
#   - "Y9990120" / "Y" prefix     (3h2n-5cm9 "no expiration")
#   - "0" or empty                (3h2n-5cm9 min sentinel)

_DATE_PARSE_CACHE: dict[str, date | None] = {}  # bounded by uniqueness of source values

def _parse_flexible_date(s: str) -> date | None:
    if s in _DATE_PARSE_CACHE:
        return _DATE_PARSE_CACHE[s]
    result = _parse_flexible_date_inner(s)
    if len(_DATE_PARSE_CACHE) < 10000:  # cap cache to avoid unbounded growth
        _DATE_PARSE_CACHE[s] = result
    return result

def _parse_flexible_date_inner(s: str) -> date | None:
    s = s.strip()
    if not s or s == "0" or s.startswith("Y"):
        return None
    # ISO: take "YYYY-MM-DD" prefix, ignore time part
    iso_prefix = s.split("T", 1)[0].split(" ", 1)[0]
    if len(iso_prefix) == 10 and iso_prefix[4] == "-" and iso_prefix[7] == "-":
        try:
            return date.fromisoformat(iso_prefix)
        except ValueError:
            pass
    # M/D/YYYY (with optional HH:MM:SS that we discard)
    if "/" in s:
        date_part = s.split(" ", 1)[0]
        try:
            return datetime.strptime(date_part, "%m/%d/%Y").date()
        except ValueError:
            pass
    # YYYYMMDD
    if len(s) == 8 and s.isdigit():
        try:
            return date(int(s[:4]), int(s[4:6]), int(s[6:]))
        except ValueError:
            pass
    return None
```

`_coerce()` body changes from:
```python
if pg_type == "date":
    return date.fromisoformat(s.split("T", 1)[0])
if pg_type in ("timestamp without time zone", "timestamp with time zone"):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))
```
to:
```python
if pg_type == "date":
    return _parse_flexible_date(s)
if pg_type in ("timestamp without time zone", "timestamp with time zone"):
    return _parse_flexible_datetime(s)  # mirror logic, returns datetime
```

**Risk**: silent format ambiguity. `"04/05/2023"` is M/D (April 5) in US format and D/M (May 4) in many other countries. NYC Socrata is explicitly US format per their docs, so we're safe — but worth a unit test asserting `_parse_flexible_date("04/05/2023")` → `date(2023, 4, 5)`.

### 1.2 Add unit tests for `_coerce()`

**File**: `tests/test_coerce.py` (new)

Minimum coverage:
- ISO 8601 with and without time/timezone suffix
- M/D/YYYY single-digit month and day
- M/D/YYYY HH:MM:SS
- YYYYMMDD
- Empty string, `None`, `"0"`, sentinels (`"Y9990120"`)
- Garbage strings → returns `None` (no crash)
- Cache hit returns same value

This is the regression test that would have caught the original bug.

### 1.3 `uv add resend`

```bash
cd /Users/devtzi/dev/nyc-property-intel
uv add resend
```

Updates `pyproject.toml` and `uv.lock`. Once deployed, alerting.py's import will succeed and `RESEND_API_KEY` (already set on cron services per `nyc-property-intel-cron-weekly` env vars) will activate the email path.

### 1.4 Optional but worth: log when `_coerce` parses successfully via fallback

Add a `logger.debug` when the ISO path fails but a fallback works. Surfaces format changes from upstream early. Don't log every parse — debug-level only, sampled.

### Phase 1 acceptance criteria

- [ ] `pytest tests/test_coerce.py` passes locally
- [ ] `git push` deploys; `nyc-property-intel-cron-weekly` next run shows no `resend package not installed` warning
- [ ] Manual smoke test: `railway run --service nyc-property-intel-cron sh -c 'cd /app && uv run python scripts/sync_delta.py dob_now_jobs'` (an aligned dataset) still works post-deploy
- [ ] No regression in Phase 0 ALIGNED datasets (rerun `coverage_audit.py`, no new high-NULL columns)

---

## Phase 2 — Backfill the 2 corrupted datasets

Order: smallest first (`dob_complaints`), so we validate the fix before committing to the bigger run.

### 2.1 `dob_complaints --reset`

```bash
railway run --service nyc-property-intel-cron sh -c \
  'cd /app && uv run python scripts/sync_delta.py dob_complaints --reset 2>&1 | tail -100'
```

- 3.08M rows; 62 Socrata pages at 50K each
- ETA: ~10–20 min (DB upsert is the bottleneck, not Socrata)
- UPSERT on `(complaintnumber)` PK — every existing row gets its date columns repopulated; non-source columns (NYCDB augmentation, if any) are preserved by the `updatable_cols` filter

**Acceptance**:
```sql
SELECT
  COUNT(*) AS total,
  COUNT(*) FILTER (WHERE dateentered IS NULL) AS null_date,
  COUNT(*) FILTER (WHERE dispositiondate IS NULL) AS null_disp,
  COUNT(*) FILTER (WHERE dobrundate IS NULL) AS null_run
FROM dob_complaints;
```
Expected: `null_date` drops from 3,080,742 → near-0 (Socrata's actual NULL count for `date_entered`).

### 2.2 `dobjobs --reset`

```bash
railway run --service nyc-property-intel-cron sh -c \
  'cd /app && uv run python scripts/sync_delta.py dobjobs --reset 2>&1 | tail -100'
```

- Pulls 2.7M Socrata rows; UPSERTs on `(job, doc)` PK
- ETA: ~15–30 min
- **Two effects**: closes the FROZEN_SOURCE 901K-row historical gap AND repopulates date columns
- **Risk to verify**: NYCDB-augmented post-2020 rows must not be touched. Since Socrata's max is 2020-05-21, Socrata never returns post-2020 (job, doc) pairs, so UPSERT can't match them.

**Pre-check before running**:
```sql
-- How many post-2020 rows exist locally that wouldn't be in Socrata?
SELECT COUNT(*) FROM dobjobs WHERE latestactiondate > '2020-05-21';
-- Expect: ~95K (matches our prior breakdown: 35K + 60K)
```

**Acceptance** (post-run):
- Total row count: ~2.81M (was 1.81M, gained ~900K Socrata historical, kept ~95K NYCDB augmentation, minus dedup overlap)
- `null_dob_run = 0` (was 1,813,200 = 100%)
- Pre/post date-column NULL rates fall to source levels
- The 901K row count gain matches the FROZEN_SOURCE gap finding

If anything looks weird, restore from `dobjobs_dates_snapshot_20260502`.

### Phase 2 acceptance criteria

- [ ] `scripts/column_null_audit.py` shows no >50% NULL columns on `dob_complaints` or `dobjobs` (excluding columns that are real source NULLs)
- [ ] `scripts/coverage_audit.py` shows `dobjobs` count within 1% of Socrata
- [ ] Snapshot tables can be dropped after 1 week of stable operation

---

## Phase 3 — Tier-3 monthly cron service

### 3.1 Create the service on Railway (manual, via UI)

1. In the `amusing-expression` project, click "+ New" → "Empty Service" or "Duplicate" off `nyc-property-intel-cron-weekly`
2. Name: `nyc-property-intel-cron-monthly`
3. Source: same GitHub repo as the other cron services
4. Settings → Cron Schedule: `0 2 1 * *` (1st of each month, 02:00 UTC)
5. Variables: copy from `nyc-property-intel-cron-weekly`, then override `SYNC_TIER=3`
6. Deploy

### 3.2 Verify dispatch works

The startup command in `railway.toml:5` matches `*cron*` in the service name and runs `nyc-property-intel-sync` which → `sync_all.py` which reads `SYNC_TIER` env var. Should "just work".

Manual verification: trigger a one-off run from Railway UI, watch logs:
- Expect: `running 11 datasets: hpd_registrations, fdny_incidents, nypd_crime_complaints, real_property_legals, real_property_parties, ...`
- Each dataset should report `rc=0`
- ACRIS sub-tables should be near no-ops (just refresh) since they're already aligned
- `hpd_registrations`, `fdny_incidents`, `nypd_crime_complaints` will run incremental sync from their existing cursors → minimal new rows

### Phase 3 acceptance criteria

- [ ] Service `nyc-property-intel-cron-monthly` exists, status Ready
- [ ] One-off manual run completes with all 11 datasets `rc=0` (or `rc=1` only on drift warnings, which are now non-fatal per commit `f4d88dc`)
- [ ] `sync_state.last_run_at` populates for `hpd_registrations`, `fdny_incidents`, `nypd_crime_complaints` after the run

---

## Phase 4 — Backfill the 3 NEVER_SYNCED datasets

Run **one at a time**, smallest first. `--reset` clears the cursor so the next run starts from the beginning of the Socrata dataset and pages through all rows.

### 4.1 `nyc_311_complaints --reset`

- 21M rows total; we're missing 3.1M (mostly 2020 + recent 3 weeks)
- ETA: ~30–60 min
- Cursor restart pulls the entire dataset, but UPSERT on `(unique_key)` makes it idempotent — existing 17.8M rows are no-op updates

```bash
railway run --service nyc-property-intel-cron-monthly sh -c \
  'cd /app && uv run python scripts/sync_delta.py nyc_311_complaints --reset 2>&1 | tail -100'
```

### 4.2 `fdny_incidents --reset`

- 12M rows total; we're missing 7.3M (history before 2018 + recent 11 months)
- ETA: ~1–2 hours
- Annual update frequency means after this initial fill, monthly cron is overkill (could move to quarterly or annual)

```bash
railway run --service nyc-property-intel-cron-monthly sh -c \
  'cd /app && uv run python scripts/sync_delta.py fdny_incidents --reset 2>&1 | tail -100'
```

### 4.3 `nypd_crime_complaints --reset`

- 10M rows total; we're missing 9.5M (everything before 2025)
- **ETA: ~1.5–3 hours** — largest single backfill
- Run during off-hours; there's a small chance Railway's TCP proxy times out or the connection drops. The sync advances cursor per page in upsert mode, so a mid-run failure can be resumed by re-running (without `--reset`)

```bash
railway run --service nyc-property-intel-cron-monthly sh -c \
  'cd /app && uv run python scripts/sync_delta.py nypd_crime_complaints --reset 2>&1 | tail -100'
```

### 4.4 Open decision: do we want all 19 years of NYPD crime data?

The product is property due diligence. Recent crime data informs neighborhood safety scoring. Crime from 2008 is much less load-bearing.

| Option | Storage | Useful for | Drawback |
|---|---|---|---|
| Full backfill (all 9.5M missing) | ~5 GB (with indexes) | Long-term trend analysis | More to scan; older data noisier |
| Limit to 10 years (2016–2025) | ~3 GB | Most due-diligence use cases | Code change to filter at sync time |
| Status quo (only 2025) | <500 MB | Current snapshot | 94% gap is bad |

**Recommendation**: full backfill. Storage on Railway Postgres is the cheapest cost in the stack, and recent product features (neighborhood crime trend) benefit from longer history. Re-evaluate if Railway DB size becomes an issue.

Same call for FDNY — full backfill recommended.

### Phase 4 acceptance criteria

- [ ] `coverage_audit.py` reclassifies `nyc_311_complaints`, `fdny_incidents`, `nypd_crime_complaints` from NEVER_SYNCED to ALIGNED
- [ ] All three have `last_run_at` populated and within 1% of Socrata count
- [ ] No new high-NULL columns surface in `column_null_audit.py`

---

## Phase 5 — Verify and lock in regression detection

### 5.1 Re-run both audits

```bash
RAILWAY_DB="postgresql://..." uv run python scripts/coverage_audit.py
RAILWAY_DB="postgresql://..." uv run python scripts/column_null_audit.py
```

Compare to baseline (`docs/data-coverage-audit-2026-05-02.md`). Expect:
- ALIGNED: 23 of 23 (was 17)
- HEALTHY (count + columns): 22 of 23 (was 15) — `dobjobs` stays FROZEN_SOURCE/LOCAL_AHEAD permanently because Socrata stopped updating
- Zero datasets in NEVER_SYNCED, COLUMN_CORRUPTION

### 5.2 Add a weekly audit cron (optional but recommended)

Schedule `coverage_audit.py` + `column_null_audit.py` to run weekly on the existing `nyc-property-intel-cron-weekly` service (it has `SYNC_TIER=2` so adding an audit step is a code change — modify `sync_all.py` to also invoke the audits when run with a new flag, or create a 4th cron service).

Cleaner option: a separate `nyc-property-intel-cron-audit` service running just the audits weekly and emailing the diff vs last week.

### 5.3 Drop Phase 0 snapshot tables after 1 week

```sql
DROP TABLE dobjobs_dates_snapshot_20260502;
DROP TABLE dob_complaints_dates_snapshot_20260502;
```

---

## Phase 6 — Optional: reclassify `nyc_311_complaints` to tier-1

NYC publishes 311 daily (`updateFrequency: Daily` per Socrata metadata). We currently sync it weekly via tier-2. Worst case, complaints sit ~6 days behind reality.

Code change in `scripts/sync_delta.py` line 297: `tier=2` → `tier=1`.

Trade-off:
- Pro: data freshness improves to ~24h
- Con: tier-1 cron run gets longer (~1-2 min added for daily incremental on 21M-row table — actually low because cursor-based, fetches only new rows)

Cheap win, but not blocking anything. Defer until after Phases 1–5 land cleanly.

---

## Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `_parse_flexible_date` mis-parses an ambiguous string | Low | High (silent corruption again) | Unit tests; explicit format strings (no dateutil "smart" mode); cache + log when fallback fires |
| Long backfill (NYPD) connection drops mid-run | Medium | Medium (resume from cursor) | Run from inside Railway VPC (`railway run --service`); upsert mode advances cursor per-page so resumable; `--reset` only first time |
| UPSERT on dobjobs nukes NYCDB-augmented post-2020 rows | Low | High (lose unique data) | Pre-flight snapshot table; verify Socrata's max ≤ 2020-05-21 before running |
| Tier-3 cron crashes on first run from `dobjobs`-style drift | Low | Low | Already neutralized in commit `f4d88dc` — `sync_all.py` only exits non-zero on real failures |
| 19 years of NYPD data exhausts Railway storage tier | Low | Medium | Monitor `pg_database_size` post-backfill; trim if needed |

---

## Rollback plan per phase

| Phase | If it goes wrong | Recovery |
|---|---|---|
| 1 (code fix) | `_coerce` regression | `git revert` the commit; redeploy. No data damage since fix is read-side only at sync time. |
| 2 (corrupted backfill) | Date columns wrong post-reset | Restore from `*_dates_snapshot_20260502` via `UPDATE … FROM`. Tables are PK-indexed. |
| 3 (tier-3 service) | Service won't start | Service is a clean addition; deleting it on Railway has zero side effects. |
| 4 (NEVER_SYNCED backfill) | Wrong rows pulled / count off | Re-run `--reset` for the affected dataset. UPSERT idempotency means re-runs converge. |
| 5–6 (verification, tier reclass) | n/a (no destructive ops) | n/a |

---

## Open decisions — please confirm before executing

1. **Full backfill on NYPD/FDNY history?** Recommendation: yes, full. Want a date floor instead?
2. **`nyc_311_complaints` to tier-1?** Recommendation: yes, but defer until everything else is stable.
3. **Weekly audit cron service?** Recommendation: yes, low cost, catches future regressions.
4. **Run order**: Phases 1 → 2 → 3 → 4 → 5 → 6 sequentially, or parallelize Phases 3 and 2? Recommendation: sequential. Phase 3 depends on Phase 1 deploying anyway; Phase 4 can't start until Phase 3 is up.

---

## Concrete next step

Confirm the four open decisions above, then I can ship Phase 1 (code fix + tests + `uv add resend`) immediately. Phase 2 follows after that deploy is live and we've spot-checked one ALIGNED dataset still works correctly.
