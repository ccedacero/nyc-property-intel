# Data Refresh Strategy

> Versioned runbook for keeping NYC Open Data tables fresh in our Postgres.
> Last updated: 2026-04-26

## TL;DR

- **Strategy:** cursor-based incremental delta sync via Socrata API.
- **State:** single `sync_state` table tracks per-dataset cursor + last run.
- **Scheduling:** Railway Cron service. Daily / weekly / monthly tiers.
- **Reliability:** resumable (cursor advances per committed page); idempotent UPSERTs; exponential retry; alerting via Resend on drift > 5%.
- **Why not full snapshot:** the old approach (`/rows.csv?accessType=DOWNLOAD` for entire datasets) was throttled, slow, and unverifiable at NYC-scale (17M+ rows).

---

## Tiers

### Tier 1 — daily (deal-critical, source updates daily)

| Table | Source dataset | Cursor column | Primary key |
|---|---|---|---|
| `hpd_violations` | `wvxf-dwi5` | `novissueddate` | `violationid` |
| `hpd_complaints_and_problems` | `ygpa-z7cr` | `receiveddate` | `problemid` |
| `hpd_litigations` | `59kj-x8nc` | `caseopendate` | `litigationid` |
| `dob_violations` | `3h2n-5cm9` | `issuedate` | `isndobbisviol` |
| `dob_complaints` | (varies) | `dateentered` | (varies) |
| `dobjobs` | `ic3t-wcy2` | `latestactiondate` | `job_filing_number` |
| `dob_now_jobs` | `8613-p88w` | `current_status_date` | `job_filing_number` |
| `ecb_violations` | `6bgk-3dad` | `issuedate` | `ecbviolationnumber` |
| `real_property_master` (ACRIS) | `bnx9-e6tj` | `recorded_datetime` | `document_id` |
| `real_property_legals` | `8h5j-fqxa` | (linked to master) | composite |
| `real_property_parties` | `636b-3b5g` | (linked to master) | composite |

### Tier 2 — weekly

| Table | Source dataset | Cursor | PK |
|---|---|---|---|
| `dof_sales` | `yg7y-7jbu` | `saledate` | `(borough, block, lot, saledate, sale_price)` |
| `nyc_311_complaints` | `erm2-nwe9` | `created_date` | `unique_key` |
| `marshal_evictions_all` | (varies) | `executeddate` | `court_index_number` |
| `personal_property_master` (ACRIS) | `sv7x-dduq` | `recorded_datetime` | `document_id` |

### Tier 3 — monthly / quarterly / annual

| Table | Source cadence | Notes |
|---|---|---|
| `nypd_crime_complaints` | Quarterly | Schedule monthly to catch the release window |
| `fdny_incidents` | Annual | One-shot per year — **currently 12 mo stale** |
| `hpd_registrations` | Daily but stable | Monthly is fine |
| `pluto_latest` | ~2x/year | DCP releases ~Jul + ~Jan |
| `dof_property_valuation_and_assessments` | Annual | Tentative roll Jan, final May |
| `dof_exemptions` | Annual | |
| `rentstab` | Annual | DOF release ~Jul |
| ACRIS lookup codes (`*_codes`) | Rare | Yearly is generous |

---

## Architecture

### Sync state table

```sql
CREATE TABLE sync_state (
    dataset_key       TEXT PRIMARY KEY,        -- our internal key, e.g. 'hpd_violations'
    socrata_id        TEXT NOT NULL,           -- e.g. 'wvxf-dwi5'
    table_name        TEXT NOT NULL,
    cursor_column     TEXT NOT NULL,
    cursor_value      TEXT,                    -- ISO date string; null = full backfill
    last_run_at       TIMESTAMPTZ,
    last_success_at   TIMESTAMPTZ,
    last_error        TEXT,
    rows_added_total  BIGINT DEFAULT 0,
    expected_rows     BIGINT,                  -- from Socrata metadata, for drift alerts
    actual_rows       BIGINT
);
```

### Worker loop (per dataset)

```
1. Read cursor_value from sync_state.
2. Loop:
     GET /resource/{id}.json
       ?$where={cursor_col} > '{cursor_value}'
       &$order={cursor_col} ASC
       &$limit=50000
   - If page empty → done.
   - COPY into a temp staging table.
   - INSERT INTO target SELECT * FROM staging ON CONFLICT (pk) DO UPDATE.
   - Commit.
   - Update sync_state.cursor_value to MAX(cursor_col) seen.
   - Sleep 250ms.
3. Compare actual_rows vs expected_rows from /api/views/{id}.json. Alert on drift > 5%.
4. REFRESH MATERIALIZED VIEW CONCURRENTLY (Tier 1 only).
```

### Properties

- **Idempotent** — re-running mid-failure picks up exactly where it left off (cursor advances after commit).
- **Resumable** — a crash at row 4M of 5M doesn't restart from zero.
- **Fast** — 50K new rows takes ~30 sec; vs ~6 hr for a full re-download.
- **Verifiable** — `expected_rows` from Socrata metadata vs `actual_rows` after each run.

### Failure modes

| Failure | Behavior |
|---|---|
| HTTP 429 (throttle) | Backoff: 5s → 15s → 30s → 60s → 120s, then exit non-zero |
| Connection reset | Same backoff |
| New column in source | Ignored at sync time. Add via SQL migration when noticed. **Do not auto-evolve schema.** |
| Source returns < page_size | End of stream confirmed. |
| Worker crash mid-page | Cursor unchanged; next run retries. |
| Drift > 5% | Email alert via Resend. |
| Drift > 10% | Email alert + non-zero exit (paged). |

---

## Operational

### Where things run

- **Workers:** Railway Cron service. Single Python process per cron tick.
- **DB:** existing Railway Postgres.
- **Secrets:** Railway env vars. Reuse `SOCRATA_APP_TOKEN` and `DATABASE_URL`.
- **Alerting:** Resend → `cristian.cedacero@gmail.com`.

### Railway Cron deployment

The cron runs as a **separate Railway service** in the same project (so the main MCP/web app stays up). It shares the DB and env vars.

**Setup steps:**

1. In the Railway project, create a new service from the same GitHub repo. Name it `nyc-property-intel-cron`.

2. In that service's **Settings → Deploy**, set:
   - **Start Command:** `uv run python scripts/sync_all.py --tier 1`
   - **Restart Policy:** `NEVER` (cron jobs are one-shot, don't auto-restart on success exit)

3. In **Settings → Cron Schedule**, set:
   - **Schedule:** `0 6 * * *` (daily at 6am UTC = 2am ET)

4. In **Variables**, link the same Postgres reference and add:
   - `DATABASE_URL` — `${{Postgres.DATABASE_URL}}` (reference, auto-rotated)
   - `SOCRATA_APP_TOKEN` — copy from main service (or rotate: NYC retired the previous one)
   - `RESEND_API_KEY` — get from resend.com dashboard
   - `ALERT_EMAIL_TO` — `cristian.cedacero@gmail.com` (already the default)
   - `ALERT_FROM_EMAIL` — must be a Resend-verified sender domain

5. Manually trigger one run to verify (Railway dashboard → "Run Now"). Watch the logs.

### Required env vars

| Name | Used by | Required? | Default |
|---|---|---|---|
| `DATABASE_URL` | sync_delta, sync_all | yes | — |
| `SOCRATA_APP_TOKEN` | sync_delta | recommended | falls back to anonymous (rate-limited) |
| `RESEND_API_KEY` | alerting | optional (silent no-op without) | — |
| `ALERT_EMAIL_TO` | alerting | optional | `cristian.cedacero@gmail.com` |
| `ALERT_FROM_EMAIL` | alerting | optional | `alerts@nycpropertyintel.com` |

### Schedule (UTC)

```
# Tier 1 — daily 06:00 UTC (02:00 ET)
0 6 * * *   uv run python scripts/sync_all.py --tier 1

# Tier 2 — weekly Sunday 07:00 UTC
0 7 * * 0   uv run python scripts/sync_all.py --tier 2

# Tier 3 — monthly 1st day 08:00 UTC
0 8 1 * *   uv run python scripts/sync_all.py --tier 3
```

### Health endpoint

`GET /health/data` returns:

```json
{
  "datasets": {
    "hpd_violations":    { "last_success_at": "2026-04-26T02:14:33Z", "rows": 10822300, "lag_hours": 23 },
    "dob_violations":    { "last_success_at": "2026-04-26T03:02:11Z", "rows":  2473610, "lag_hours": 22 }
  }
}
```

Surfaced in the chat UI as "Data current as of 2026-04-26".

---

## Phased rollout

| Phase | Scope | Status |
|---|---|---|
| 1 | `sync_state` migration + `sync_delta.py` skeleton, prove on `hpd_violations` | ✅ done |
| 2 | Onboard rest of Tier 1; add PK migrations | ✅ done (5 of 6) |
| 2.5 | Reconcile schema mismatches; dedupe + PK on `real_property_master` | ✅ done |
| 2.6 | Row-hash PK strategy for `dobjobs` + `dob_complaints` (source-level dupes) | pending |
| 3 | Railway Cron service + Resend alerting | ✅ code + docs done; needs Railway dashboard setup |
| 4a | Tier 2 — `marshal_evictions_all` onboarded | ✅ done |
| 4b | Tier 2/3 — `dof_sales` (truncated source field names), `nypd_crime` (verify Historic vs YTD), `fdny_incidents` (scope), `nyc_311_complaints` | pending |
| 5 | `/health/data` endpoint + chat UI freshness badge | pending |

## Phase 2.6 plan — row-hash PK for tables with source-level dupes

**Problem:** `dobjobs` and `dob_complaints` have ~30%+ duplicate rows from the source (NYC's data quality issue, not ours). Their natural keys (`job`, `complaintnumber`) are not unique. The local `id` PK is a synthetic value not present in Socrata API responses, so we can't UPSERT on it.

**Solution:** add `row_hash CHAR(64)` PK = SHA256 of canonical row content.

```sql
ALTER TABLE dobjobs ADD COLUMN row_hash CHAR(64);
UPDATE dobjobs SET row_hash = encode(sha256(
    COALESCE(job::text,'') || '|' ||
    COALESCE(doc::text,'') || '|' ||
    COALESCE(jobtype,'') || '|' ||
    COALESCE(latestactiondate::text,'') || '|' ||
    COALESCE(jobstatus,'') || ...
), 'hex');
ALTER TABLE dobjobs ADD CONSTRAINT dobjobs_row_hash_pkey PRIMARY KEY (row_hash);
DROP COLUMN id;  -- or keep nullable for backward compat
```

In `sync_delta.py`, compute `row_hash` for each incoming row before staging. UPSERT on `row_hash`. Identical rows from source = same hash = no-op. New row content (status changed, etc.) = new hash = new row.

## Recovery procedures

### Cursor stuck (worker reports no new rows but source has new data)

```sql
-- Force re-sync the last N days
UPDATE sync_state
SET cursor_value = (CURRENT_DATE - 30)::text
WHERE dataset_key = 'hpd_violations';
```

### Drift > 10% (likely missed updates)

Backfill the trailing window:

```sql
UPDATE sync_state SET cursor_value = NULL WHERE dataset_key = '...';
```

The next run starts from the beginning. Because of `ON CONFLICT DO UPDATE`, this is safe — existing rows are upserted, not duplicated.

### Schema change in source

1. `ALTER TABLE` to add the new column.
2. The sync script auto-detects new columns in Socrata response and includes them in the staging COPY (since v2 — TBD).

For v1: new columns are ignored. Add them via migration.
