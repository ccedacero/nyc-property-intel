# Data coverage audit — 2026-05-02

Comparison of local Postgres tables (Railway prod) to Socrata source-of-truth, run 2026-05-02 14:01 UTC.

> **Connection verified**: audit ran via `RAILWAY_DB=postgresql://postgres:***@switchback.proxy.rlwy.net:33576/railway` — that's the public TCP-proxy hostname for the Railway "Postgres" service. The cron services (`nyc-property-intel-cron`, `nyc-property-intel-cron-weekly`) hit the same instance through the VPC-internal hostname `postgres.railway.internal:5432`. Sample query confirmed: `SELECT current_database()` → `railway`, 23 rows in `sync_state` matching DATASETS in `sync_delta.py`.

> **Methods**: `scripts/coverage_audit.py` (count + min/max cursor vs Socrata) followed by `scripts/column_null_audit.py` (NULL rates on every `date`/`timestamp` column). Both tools reproducible — set `RAILWAY_DB` and run.

---

## Why count-match alone is not enough — read this first

Initial pass classified 17 of 23 datasets as "ALIGNED" based on row counts within 1% of Socrata. The follow-up column-NULL audit overturned that for **2 datasets**:

- `dob_complaints` count-matches Socrata (+424 rows, 0.01%) but **every single date column is 100% NULL** — silent corruption from a `_coerce()` bug that nukes M/D/YYYY-format dates Socrata returns for some datasets.
- `dobjobs` is `FROZEN_SOURCE` AND has the same `_coerce()` corruption — 55–100% NULL on 10 date columns.

So the headline "17 healthy" was wrong. Updated below.

---

## Corrected TL;DR

| Status | Count | Datasets |
|---|---:|---|
| ✅ **HEALTHY** (count + columns) | **15** | hpd_violations, hpd_complaints_and_problems, dob_violations, ecb_violations, dob_now_jobs, marshal_evictions_all, real_property_master + all 8 ACRIS sub-tables |
| 🩹 **HARMLESS_SCHEMA_DRIFT** (column exists locally, not in Socrata) | 3 cols on 3 datasets | `hpd_litigations.findingdate`, `personal_property_master.assessmentdate`, `real_property_master.docdate` (originally bulk-loaded from NYCDB; Socrata doesn't ship these fields) |
| 🐛 **COLUMN_CORRUPTION** (_coerce bug — M/D/YYYY → NULL) | 2 | `dob_complaints` (100% NULL on 4 date cols), `dobjobs` (55–100% NULL on 10 date cols) |
| 🧊 **FROZEN_SOURCE** | 1 | `dobjobs` (Socrata stopped 2020-05-21; 901K historical rows missing) — also has bug above |
| ⚠️ **MINOR_DRIFT** | 1 | `hpd_registrations` (-4.95%, ~10K rows behind, 1 month stale) |
| ➕ **LOCAL_AHEAD** (harmless surplus) | 1 | `dob_complaints` (+424 rows, 0.01%) |
| 🚨 **NEVER_SYNCED** (massive historical gaps) | 3 | `nyc_311_complaints` (-14.9%), `fdny_incidents` (-61.8%), `nypd_crime_complaints` (-94.2%) |

Some datasets fall in multiple buckets (e.g. `dobjobs` is FROZEN_SOURCE + COLUMN_CORRUPTION).

---

## Per-dataset summary

| Dataset | Tier | Local rows | Socrata rows | Diff | Healthy? | Issues |
|---|---:|---:|---:|---:|:---:|---|
| `hpd_violations` | 1 | 10,886,435 | 10,889,395 | -0.03% | ✅ | high NULL on optional re-cert cols are real source NULLs |
| `hpd_complaints_and_problems` | 1 | 16,038,450 | 16,038,458 | -0.00% | ✅ | — |
| `hpd_litigations` | 1 | 237,369 | 237,860 | -0.21% | ✅ | `findingdate` 100% NULL but column not in Socrata |
| `dob_violations` | 1 | 2,473,906 | 2,474,046 | -0.01% | ✅ | dispositiondate NULL rate matches Socrata |
| `ecb_violations` | 1 | 1,808,951 | 1,809,319 | -0.02% | ✅ | — |
| `real_property_master` | 1 | 16,947,488 | 16,958,800 | -0.07% | ✅ | `docdate` 28% NULL but column not in Socrata |
| `dobjobs` | 1 | 1,813,200 | 2,714,844 | **-33.21%** | ❌❌ | FROZEN_SOURCE + 10 date cols 55–100% NULL from _coerce bug |
| `dob_complaints` | 1 | 3,081,172 | 3,080,748 | +0.01% | ❌ | Counts match BUT all 4 date cols are 100% NULL — _coerce bug |
| `dob_now_jobs` | 1 | 897,138 | 897,256 | -0.01% | ✅ | firstpermitdate 39.6% NULL is real source NULL |
| `marshal_evictions_all` | 2 | 127,242 | 127,383 | -0.11% | ✅ | — |
| `nyc_311_complaints` | 2 | 17,859,506 | 20,997,024 | **-14.94%** | ❌ | Missing all of 2020 (~3.1M rows); never synced |
| `personal_property_master` | 2 | 4,515,861 | 4,523,303 | -0.16% | ✅ | `assessmentdate` 87% NULL but column not in Socrata |
| `hpd_registrations` | 3 | 192,998 | 203,043 | -4.95% | ⚠️ | 1 month behind; will close on next tier-3 cron run |
| `fdny_incidents` | 3 | 4,509,303 | 11,819,520 | **-61.85%** | ❌ | Local: 2018–2025 only. Socrata: 2005–2026. Missing ~7.3M rows |
| `nypd_crime_complaints` | 3 | 579,561 | 10,071,507 | **-94.25%** | ❌❌ | Local has ONLY 2025. Socrata: 2006–2025. Missing ~9.5M rows |
| `real_property_legals` | 3 | 22,581,578 | 22,581,852 | -0.00% | ✅ | — |
| `real_property_parties` | 3 | 46,200,163 | 46,230,300 | -0.07% | ✅ | — |
| `real_property_references` | 3 | 8,606,267 | 8,606,485 | -0.00% | ✅ | — |
| `real_property_remarks` | 3 | 5,727,541 | 5,727,609 | -0.00% | ✅ | — |
| `personal_property_legals` | 3 | 3,952,119 | 3,952,220 | -0.00% | ✅ | — |
| `personal_property_parties` | 3 | 10,971,372 | 10,971,401 | -0.00% | ✅ | — |
| `personal_property_references` | 3 | 7,709,731 | 7,709,734 | -0.00% | ✅ | — |
| `personal_property_remarks` | 3 | 492,963 | 492,964 | -0.00% | ✅ | — |

**True scoreboard: 15 fully healthy / 23. The 17 number from the count-only audit is wrong.**

---

## 🚨 Critical: 3 NEVER_SYNCED datasets with massive gaps

Confirmed against Railway prod. These three have `last_run_at = NULL` in `sync_state` because no tier-3 cron service exists yet. Their `last_success_at` was set by a manual `UPDATE sync_state` during bootstrap, not via `write_state()`.

**Important**: even when the tier-3 cron is created, these will only fetch new data from the existing cursor forward. To get the historical data, each needs a `--reset` backfill.

### `nypd_crime_complaints` — 94.2% missing

Year breakdown of local table (Railway):

| Year | Local rows |
|---:|---:|
| 2025 | 579,561 |

That's it. **Only the year 2025.** Socrata covers 2006-01-01 → 2025-12-31, 10,071,507 total rows. Missing ~9.5M rows spanning **19 years (2006–2024)**.

### `fdny_incidents` — 61.8% missing

| Year | Local rows |
|---:|---:|
| 2025 | 212,352 (partial) |
| 2024 | 696,697 |
| 2023 | 670,373 |
| 2022 | 653,238 |
| 2021 | 607,402 |
| 2020 | 534,561 |
| 2019 | 515,308 |
| 2018 | 619,372 |

Local: 2018-01-01 → 2025-04-30. Socrata: 2005-01-01 → 2026-03-31. Missing 13 years of history (2005–2017) plus the most recent 11 months. ~7.3M rows.

### `nyc_311_complaints` — 14.9% missing

| Year | Local rows |
|---:|---:|
| 2026 | 1,132,217 |
| 2025 | 3,654,955 |
| 2024 | 3,456,770 |
| 2023 | 3,224,722 |
| 2022 | 3,169,960 |
| 2021 | 3,220,882 |

Local starts 2021-01-01, Socrata starts 2020-01-01. Missing all of 2020 (~3M rows). Smaller backfill than the other two but still ~60 Socrata pages.

---

## 🐛 Critical bug: `_coerce()` silently NULLs M/D/YYYY date strings

`scripts/sync_delta.py:519-546` only handles ISO 8601 dates. Three datasets serve M/D/YYYY:

| Socrata ID | Dataset | Sample value | Format |
|---|---|---|---|
| `wvxf-dwi5` | hpd_violations | `2014-01-06T00:00:00.000` | ISO ✅ |
| `eabe-havv` | dob_complaints | `01/22/1996` | M/D/YYYY ❌ |
| `ic3t-wcy2` | dobjobs | `06/23/2023` | M/D/YYYY ❌ |
| `3h2n-5cm9` | dob_violations | `19881031` | YYYYMMDD ❌ (but bulk load loaded these via different path; only 0.001% NULL) |

`date.fromisoformat("01/22/1996")` raises ValueError, caught by the bare `except (ValueError, TypeError)` in `_coerce`, which returns `None`.

### Confirmed impact (Railway prod, 2026-05-02)

**dob_complaints** (3,081,172 rows total):

| Column | NULL count | NULL % |
|---|---:|---:|
| `dateentered` | 3,080,742 | **100.0%** |
| `dispositiondate` | 3,081,124 | **100.0%** |
| `inspectiondate` | 3,081,124 | **100.0%** |
| `dobrundate` | 3,081,172 | **100.0%** |

**dobjobs** (1,813,200 rows total):

| Column | NULL count | NULL % |
|---|---:|---:|
| `latestactiondate` | 1,009,207 | 55.7% |
| `prefilingdate` | 1,009,126 | 55.7% |
| `paid` | 1,012,654 | 55.8% |
| `fullypaid` | 1,011,885 | 55.8% |
| `assigned` | 1,269,746 | 70.0% |
| `approved` | 1,157,611 | 63.8% |
| `fullypermitted` | 1,218,628 | 67.2% |
| `dobrundate` | 1,813,200 | **100.0%** |
| `signoffdate` | 1,307,689 | 72.1% |
| `specialactiondate` | 1,721,902 | 95.0% (specialactionstatus is rare anyway, so partly real source NULL) |

The variable rates on dobjobs reflect overlap with the original NYCDB bulk load — rows synced from Socrata had their dates nulled, NYCDB-only rows kept theirs (~45% of the table is NYCDB-only post-2020 augmentation that the cursor never reached).

`dob_complaints` is uniformly 100% because the entire table got UPSERTed by the daily sync since deploy.

### Why the cursor advances correctly despite this

The cursor in `sync_state.cursor_value` is taken from the raw string before coercion (in `sync_delta.py:780-820`), so cursor tracking works. Only the persisted column gets nulled. The `dashboard.py` `_to_iso_date()` helper added in commit `68c0f74` already has the correct logic — just needs to be merged into `_coerce()`.

### Mitigation order

1. **Fix `_coerce()` first** — without this, any backfill re-corrupts dates.
2. **Re-pull dob_complaints**: clear cursor, full re-sync. UPSERT will repopulate dates on existing rows.
3. **Re-pull dobjobs**: same — also closes the FROZEN_SOURCE historical gap.

---

## 🩹 Harmless schema drift (no action needed)

Three columns exist locally but are not present in Socrata's API response. They were populated by the original NYCDB bulk load and just never get touched by Socrata syncs (since the field name doesn't exist in source rows).

| Local column | Local NULL % | Socrata field | Notes |
|---|---:|---|---|
| `hpd_litigations.findingdate` | 100.0% | (not in Socrata) | NYCDB-derived enrichment |
| `personal_property_master.assessmentdate` | 87.1% | (not in Socrata) | Field appears removed from source |
| `real_property_master.docdate` | 28.4% | (not in Socrata) | Some rows have it from bulk load |

These are not bugs. They're just dead-ish columns. Decide later whether to drop them.

---

## 🧊 FROZEN_SOURCE: `dobjobs`

Socrata `ic3t-wcy2` ("DOB Job Application Filings") is frozen at 2020-05-21. NYC DOB has migrated active filings to "DOB NOW" (which is `dob_now_jobs` in our schema, healthy). The 901K missing rows are pre-2020 history that the initial bulk load never fetched. Already documented in the cron-crash investigation; the perpetual drift warning was de-fanged by removing the non-zero exit in `sync_all.py` (commit `f4d88dc`).

A `--reset` would close the gap by pulling all 2.7M historical rows from Socrata.

---

## Recommended action plan (in priority order)

### 1. Fix `_coerce()` date parsing (CRITICAL — blocks downstream fixes)

`scripts/sync_delta.py:535-540`. Port the `_to_iso_date()` logic from `scripts/dashboard.py` (which already handles M/D/YYYY, YYYYMMDD, and ISO). Without this, any reset/backfill re-introduces the corruption.

### 2. Backfill the corrupted datasets

```bash
railway run --service nyc-property-intel-cron sh -c \
  'cd /app && uv run python scripts/sync_delta.py dob_complaints --reset'
railway run --service nyc-property-intel-cron sh -c \
  'cd /app && uv run python scripts/sync_delta.py dobjobs --reset'
```

Approx 30 min and 60 min respectively. UPSERT will repopulate dates on existing rows; INSERT will fill the dobjobs historical gap.

### 3. Set up tier-3 monthly cron service on Railway

Create `nyc-property-intel-cron-monthly` matching the existing `-weekly` pattern: same image, `SYNC_TIER=3`, monthly cron schedule (e.g. `0 2 1 * *`). This unblocks `hpd_registrations`, `fdny_incidents`, `nypd_crime_complaints`, and ongoing ACRIS sub-table sync.

### 4. Backfill the 3 NEVER_SYNCED datasets

`--reset` is required because incremental from the existing cursor would skip the historical gap entirely. Largest first; run from Railway VPC so the connection stays internal:

```bash
railway run --service nyc-property-intel-cron-monthly sh -c \
  'cd /app && uv run python scripts/sync_delta.py nypd_crime_complaints --reset'   # ~9.5M rows, ~3 hr
railway run --service nyc-property-intel-cron-monthly sh -c \
  'cd /app && uv run python scripts/sync_delta.py fdny_incidents --reset'          # ~7.3M rows, ~2.5 hr
railway run --service nyc-property-intel-cron-monthly sh -c \
  'cd /app && uv run python scripts/sync_delta.py nyc_311_complaints --reset'      # ~3.1M rows, ~1 hr
```

Best done one at a time during off-hours.

### 5. `uv add resend` so drift alerts actually email

Every cron run currently logs `WARNING resend package not installed`. Without it, the alerting code in `scripts/alerting.py` is a no-op — you have no early warning system for future drift.

---

## Reproducibility

```bash
# Connect via Railway external proxy (or RAILWAY_DB env var pre-set)
export RAILWAY_DB="postgresql://postgres:***@switchback.proxy.rlwy.net:33576/railway"

# Count + cursor comparison vs Socrata
uv run python scripts/coverage_audit.py
# → JSON-lines on stdout, markdown report to docs/data-coverage-audit-{date}.md

# Per-column NULL rate audit (catches column-level corruption)
uv run python scripts/column_null_audit.py
# → table on stdout, flags >50% NULL date/timestamp columns
```

Both scripts are committed and reproducible. Re-run after backfills to verify.

---

## Appendix: data-quality oddities (informational)

- **`hpd_litigations`** has rows with `caseopendate` = `0204-03-28` (DOB data-entry typo for 2004) and forward to `2030-10-05` (pre-scheduled future court dates). Same garbage in our DB and Socrata.
- **`dob_violations`** uses `Y9990120` as a Socrata sentinel "no expiration"; our local has the parsed value `2026-04-30` (most recent real date).
- **`real_property_master`** + ACRIS sub-tables share `goodthroughdate` cursor — a batch-update marker, not a per-row timestamp. Sync uses `refresh_by_documentid` mode for ACRIS which deletes/reinserts whole document batches.
- **`hpd_violations`** has 99.1% NULL on `newcertifybydate`/`newcorrectbydate` and 64.5% NULL on `certifieddate` — these are real source NULLs (these fields are only populated when DOB issues a re-certification order, which is rare).
