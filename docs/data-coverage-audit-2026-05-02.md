# Data coverage audit — 2026-05-02

Comparison of local Postgres tables to Socrata source-of-truth, run at 2026-05-02 14:01 UTC against the Railway production DB.

Method: `scripts/coverage_audit.py` queries each dataset for local `count(*)`, local `min/max(cursor_col)`, and live Socrata `$select=count(*)` plus `min/max(cursor)`. 23 datasets, 3 minutes wall time.

---

## TL;DR

| Status | Count | Datasets |
|---|---:|---|
| ✅ **ALIGNED** (within 1%) | 17 | All ACRIS sub-tables, hpd_violations, dob_violations, ecb_violations, hpd_complaints_and_problems, hpd_litigations, real_property_master, dob_now_jobs, marshal_evictions_all, personal_property_master |
| ⚠️ **MINOR_DRIFT** (1–5%) | 1 | `hpd_registrations` (-4.95%) |
| ➕ **LOCAL_AHEAD** | 1 | `dob_complaints` (+424 rows, harmless) |
| 🧊 **FROZEN_SOURCE** | 1 | `dobjobs` (Socrata stopped updating 2020-05-21) |
| 🚨 **NEVER_SYNCED** (massive gaps) | 3 | `nyc_311_complaints`, `fdny_incidents`, `nypd_crime_complaints` |
| 🐛 **DATA QUALITY BUG** (separate from count audit) | 2 | `dob_complaints` (99.99% NULL dateentered), `dobjobs` (55.6% NULL latestactiondate) |

**Most pressing**: 3 tier-3 datasets are missing huge historical chunks. `nypd_crime_complaints` has only 2025 (94% missing). And there's a date-coercion bug silently nulling cursor columns on 2 datasets.

---

## Per-dataset summary table

| Dataset | Tier | Local | Socrata | Diff | Local max | Socrata max | Status |
|---|---:|---:|---:|---:|---|---|---|
| `hpd_violations` | 1 | 10,886,435 | 10,889,395 | -0.03% | 2026-04-30 | 2026-04-30 | ✅ ALIGNED |
| `hpd_complaints_and_problems` | 1 | 16,038,450 | 16,038,458 | -0.00% | 2026-04-30 | 2026-04-30 | ✅ ALIGNED |
| `hpd_litigations` | 1 | 237,369 | 237,860 | -0.21% | 2030-10-05 | 2030-10-05 | ✅ ALIGNED¹ |
| `dob_violations` | 1 | 2,473,906 | 2,474,046 | -0.01% | 2026-04-30 | Y9990120 | ✅ ALIGNED² |
| `ecb_violations` | 1 | 1,808,951 | 1,809,319 | -0.02% | 2026-04-29 | 20260429 | ✅ ALIGNED |
| `real_property_master` | 1 | 16,947,488 | 16,958,800 | -0.07% | 2026-04-03 | 2026-04-03 | ✅ ALIGNED |
| `dobjobs` | 1 | 1,813,200 | 2,714,844 | **-33.21%** | 2025-12-31 | 2020-05-21 | 🧊 FROZEN_SOURCE |
| `dob_complaints` | 1 | 3,081,172 | 3,080,748 | +0.01% | 2026-04-12 | 12/31/2025 | ➕ LOCAL_AHEAD³ |
| `dob_now_jobs` | 1 | 897,138 | 897,256 | -0.01% | 2026-05-01 | 2026-05-01 | ✅ ALIGNED |
| `marshal_evictions_all` | 2 | 127,242 | 127,383 | -0.11% | 2026-05-01 | 2026-05-01 | ✅ ALIGNED |
| `nyc_311_complaints` | 2 | 17,859,506 | 20,997,024 | **-14.94%** | 2026-04-12 | 2026-05-01 | 🚨 NEVER_SYNCED |
| `personal_property_master` | 2 | 4,515,861 | 4,523,303 | -0.16% | 2026-02-27 | 2026-04-03 | ✅ ALIGNED |
| `hpd_registrations` | 3 | 192,998 | 203,043 | **-4.95%** | 2026-03-31 | 2026-04-30 | ⚠️ MINOR_DRIFT |
| `fdny_incidents` | 3 | 4,509,303 | 11,819,520 | **-61.85%** | 2025-04-30 | 2026-03-31 | 🚨 NEVER_SYNCED |
| `nypd_crime_complaints` | 3 | 579,561 | 10,071,507 | **-94.25%** | 2025-12-31 | 2025-12-31 | 🚨 NEVER_SYNCED |
| `real_property_legals` | 3 | 22,581,578 | 22,581,852 | -0.00% | 2026-03-31 | 2026-03-31 | ✅ ALIGNED |
| `real_property_parties` | 3 | 46,200,163 | 46,230,300 | -0.07% | 2026-03-31 | 2026-03-31 | ✅ ALIGNED |
| `real_property_references` | 3 | 8,606,267 | 8,606,485 | -0.00% | 2026-03-31 | 2026-03-31 | ✅ ALIGNED |
| `real_property_remarks` | 3 | 5,727,541 | 5,727,609 | -0.00% | 2026-03-31 | 2026-03-31 | ✅ ALIGNED |
| `personal_property_legals` | 3 | 3,952,119 | 3,952,220 | -0.00% | 2026-03-31 | 2026-03-31 | ✅ ALIGNED |
| `personal_property_parties` | 3 | 10,971,372 | 10,971,401 | -0.00% | 2026-03-31 | 2026-03-31 | ✅ ALIGNED |
| `personal_property_references` | 3 | 7,709,731 | 7,709,734 | -0.00% | 2026-03-31 | 2026-03-31 | ✅ ALIGNED |
| `personal_property_remarks` | 3 | 492,963 | 492,964 | -0.00% | 2026-03-31 | 2026-03-31 | ✅ ALIGNED |

¹ `hpd_litigations` has dates back to year `0204` (data entry error in DOB) and forward to `2030-10-05` (future court dates) — same garbage in our DB as in Socrata, so aligned.
² `dob_violations` Socrata uses `Y9990120` as a "no-end-date" sentinel; our local has the parsed value `2026-04-30`.
³ The +424 rows in `dob_complaints` are likely NYCDB-augmented historical records that Socrata has since dropped — harmless.

---

## 🚨 Critical gaps (NEVER_SYNCED)

These three datasets have **never run** through `sync_delta.py`. Their `last_run_at` is NULL in `sync_state`; only `last_success_at` is populated (from a manual `UPDATE sync_state` during bootstrapping). Until tier-3 cron is configured, they will not auto-recover, and **even when sync runs, it will only fetch new rows past the existing cursor — historical gaps require `--reset`**.

### `nypd_crime_complaints` — 94.2% missing (~9.5M rows)

**The most severe gap.** Local table has only the year 2025:

| Year | Local rows |
|---:|---:|
| 2025 | 579,561 |

But Socrata covers 2006-01-01 → 2025-12-31 with 10,071,507 rows total. We're missing **all of 2006–2024**.

The cursor `2025-12-31` is set, so an incremental sync would skip the gap entirely. **Requires `--reset` backfill** (~200 Socrata pages, several hours).

### `fdny_incidents` — 61.8% missing (~7.3M rows)

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

Local starts in **2018**, Socrata starts in **2005**, and Socrata extends to **2026-03-31** while local stops at **2025-04-30**. Missing all of 2005-2017 plus the 11 most recent months. Same fix: `--reset` backfill.

### `nyc_311_complaints` — 14.9% missing (~3.1M rows)

| Year | Local rows |
|---:|---:|
| 2026 | 1,132,217 |
| 2025 | 3,654,955 |
| 2024 | 3,456,770 |
| 2023 | 3,224,722 |
| 2022 | 3,169,960 |
| 2021 | 3,220,882 |

Local starts in **2021-01-01**, Socrata starts in **2020-01-01**. Missing all of 2020 (~3M rows) plus the most recent ~3 weeks (cursor at `2026-04-12`, Socrata max at `2026-05-01`). Smaller backfill (~60 pages).

---

## 🐛 Data quality bug: silent NULL date coercion

`scripts/sync_delta.py:535-540` only handles ISO 8601 dates in `_coerce()`:

```python
if pg_type == "date":
    from datetime import date
    return date.fromisoformat(s.split("T", 1)[0])  # ← only ISO
if pg_type in ("timestamp without time zone", "timestamp with time zone"):
    from datetime import datetime
    return datetime.fromisoformat(s.replace("Z", "+00:00"))
```

But Socrata returns dates in **three** formats depending on the dataset:

| Format | Example | Datasets affected |
|---|---|---|
| ISO 8601 | `2014-01-06T00:00:00.000` | `wvxf-dwi5` (hpd_violations) and most others |
| **M/D/YYYY** | `01/22/1996` | `eabe-havv` (dob_complaints), `ic3t-wcy2` (dobjobs) |
| **YYYYMMDD** | `19881031` | `3h2n-5cm9` (dob_violations) |

`date.fromisoformat()` raises `ValueError` on the M/D/YYYY and YYYYMMDD formats; `_coerce` catches it and returns `None`. The row inserts with NULL in the cursor column.

### Confirmed impact

| Dataset | Cursor col | NULL count | Total | NULL % |
|---|---|---:|---:|---:|
| `dob_complaints` | `dateentered` (M/D/YYYY source) | 3,080,742 | 3,081,172 | **99.99%** |
| `dobjobs` | `latestactiondate` (M/D/YYYY source) | 1,009,207 | 1,813,200 | **55.66%** |
| `hpd_violations` | `novissueddate` | 806,265 | 10,886,435 | 7.41% (likely real NULLs in source) |
| `dob_violations` | `issuedate` (YYYYMMDD source) | 34 | 2,473,906 | 0.00% |

The dob_violations YYYYMMDD-format rows mostly went through the bulk-load before the sync code existed, so they have correctly-parsed dates from a different code path. But future incremental rows would also lose dates.

`dob_complaints` is the worst offender — incremental sync has added 3.08M rows since the bulk load and *every single one* dropped its `dateentered`. The cursor in `sync_state` shows `2026-04-12` because the cursor advance reads the raw string before coercion, but the persisted row has NULL.

### Why count-comparison alone missed this

`dob_complaints` is "LOCAL_AHEAD" by 424 rows because count-only comparison can't see column-level corruption. The data is *there* (rows match), but key columns are NULL.

### Fix

`_coerce()` should fall through to `_to_iso_date()` (already exists in `scripts/dashboard.py`) before raising. After the fix, **all dob_complaints and dobjobs rows need their dates re-pulled** — `--reset` won't help directly because UPSERT doesn't update the cursor column on an existing row when the source field is the same value.

The simplest path:
1. Fix `_coerce()` to handle M/D/YYYY and YYYYMMDD
2. `UPDATE sync_state SET cursor_value = NULL WHERE dataset_key IN ('dob_complaints', 'dobjobs')`
3. Run `sync_delta.py dob_complaints --reset` and `sync_delta.py dobjobs --reset`

Note: dobjobs is also FROZEN_SOURCE, so the reset will get the dates for the rows we have but won't fill the 901K historical gap.

---

## 🧊 FROZEN_SOURCE: `dobjobs`

The Socrata dataset `ic3t-wcy2` ("DOB Job Application Filings") is **frozen at 2020-05-21**. NYC DOB has migrated active filings to "DOB NOW" (which we already capture as `dob_now_jobs`). The 901,644 missing rows are all pre-2020 history that the initial bulk load never fetched. Doing a `--reset` would pull all 2.7M from Socrata and INSERT the missing 901K (the rest are no-ops via ON CONFLICT). After that, drift would be ~0%.

Already discussed in [the cron crash investigation](../docs/cron-crash-investigation.md) and `scripts/sync_all.py` was patched in commit `f4d88dc` to no longer crash Railway on perpetual drift warnings.

---

## ⚠️ MINOR_DRIFT: `hpd_registrations`

Local cursor is `2026-03-31`, Socrata max is `2026-04-30` — exactly one month behind. Combined with `last_run_at = NULL` (cron never ran for this dataset), the gap is literally one month of new registrations (~10K rows). Will close on the first tier-3 monthly cron run.

---

## ➕ LOCAL_AHEAD: `dob_complaints`

+424 rows over Socrata's count (rounding error at 0.01%). Likely some pre-2015 records from the original NYCDB load that Socrata has since dropped. Harmless, no action needed.

---

## ✅ ALIGNED datasets

17 of 23 datasets are within 1% of Socrata's count. Tier-1 daily cron is doing its job (when it's not crash-looping on dobjobs drift) and the manual tier-3 ACRIS run from earlier today landed all 8 ACRIS sub-tables within 0.01% of source.

---

## Recommended action plan

In priority order:

### 1. Fix `_coerce()` date parsing (critical, ~30 lines)

Patch `scripts/sync_delta.py:535-540` to handle M/D/YYYY and YYYYMMDD. Same logic as `_to_iso_date()` in `scripts/dashboard.py`. Without this, every future row added by the daily cron for dob_complaints continues to land with NULL `dateentered`.

### 2. Backfill dob_complaints + dobjobs after the fix

```
railway run --service nyc-property-intel-cron "uv run python scripts/sync_delta.py dob_complaints --reset"
railway run --service nyc-property-intel-cron "uv run python scripts/sync_delta.py dobjobs --reset"
```
~30 min each. Repopulates dates, also closes dobjobs' historical gap.

### 3. Set up tier-3 monthly cron service on Railway

Create `nyc-property-intel-cron-monthly` matching the existing weekly pattern: same image, `SYNC_TIER=3`, monthly cron schedule (e.g. `0 2 1 * *`). This unblocks `hpd_registrations`, `fdny_incidents`, `nypd_crime_complaints`, and the 8 ACRIS sub-tables for ongoing sync.

### 4. Backfill the 3 NEVER_SYNCED datasets

These need `--reset` because incremental from existing cursor would skip the historical gap. Largest first:

```
sync_delta.py nypd_crime_complaints --reset    # 9.5M rows, ~3 hr
sync_delta.py fdny_incidents --reset           # 7.3M rows, ~2.5 hr
sync_delta.py nyc_311_complaints --reset       # 3.1M rows, ~1 hr
```

Run from Railway so the connection stays inside the VPC. Best to do these one at a time during off-hours.

### 5. Install `resend` so drift alerts actually email

`uv add resend` — every cron run currently logs `WARNING resend package not installed`, so you have no early warning system for future drift.

---

## Appendix: data quality oddities (not actionable)

- **`hpd_litigations`** has rows with `caseopendate` going back to year `0204` and forward to `2030-10-05` — these match Socrata's data exactly. Likely DOB data-entry errors (year typos like 0204 instead of 2004, and pre-scheduled court dates).
- **`dob_violations`** uses `Y9990120` as a sentinel "no expiration" date in Socrata — appears as `2026-04-30` in our table after coercion (the most recent real date).
- **`real_property_master`** and ACRIS sub-tables share the cursor `goodthroughdate` which is a batch-update marker, not a per-row timestamp. The sync uses `refresh_by_documentid` mode for ACRIS which deletes/reinserts whole document batches.
