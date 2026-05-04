# Known issues — for future debug

Issues we deferred rather than fixed in the current pass. Each entry: what / why-it-happens / why-deferred / suggested fix.

---

## dobjobs: 901K-row historical gap not closed by `--reset` backfill (2026-05-03)

**Symptom**
- `dobjobs` table has 1,813,227 rows on Railway prod after a full `--reset` backfill.
- Socrata metadata for `ic3t-wcy2` reports `rowsCount = 2,714,871`.
- Drift gap: 33.21% missing — same as before the backfill (the backfill added only 27 new rows).

**What actually happens during a `--reset` backfill on this dataset**
- We paginate `https://data.cityofnewyork.us/resource/ic3t-wcy2.json` with `$order=latest_action_date+ASC&$offset=N&$limit=50000`.
- 55 pages get fetched (~1.87M total rows).
- Per-page UPSERT logs show 33,000–34,000 unique `(job, doc)` PKs after the in-page dedup at `scripts/sync_delta.py:570-577`.
- Almost every fetched PK matches an existing row → UPDATE (column-fix wins). Only ~27 are net new INSERTs.

**Root cause hypothesis**
Socrata's `$offset` pagination is unreliable when many rows tie on the cursor column. `dobjobs` has thousands of rows sharing the same `latest_action_date`. Page boundaries that fall mid-tie cause some rows to be skipped silently. Net effect: a sequential `$offset` sweep cannot enumerate the full ~2.7M-row dataset; it tops out around the ~1.87M unique PKs we observe.

**Why we deferred**
- The original audit framed this as `FROZEN_SOURCE` + a separate `_coerce` corruption bug. The corruption bug was the high-value fix and is now resolved (every date column on dobjobs went from 55–100% NULL to <1%).
- The missing 900K rows are mostly pre-2020 expired permits — low product value for due-diligence queries, which mostly need recent permits.
- Closing the gap requires non-trivial fetch-strategy work (see below).

**Suggested fix when revisited**
Three options, in order of effort:

1. **Paginate by `:id` instead of `latest_action_date`.** Socrata's internal `:id` field is unique and stable, so `$order=:id&$offset=N` enumerates all rows. Trade-off: we lose the cursor-friendly ordering, so this only makes sense in a one-off backfill mode (not steady-state incremental).

2. **Split the historical sweep into date-range buckets.** E.g. `$where=latest_action_date BETWEEN 'X' AND 'Y'` for tight enough buckets that no single bucket has >50K rows tied on the same date. Need to discover the right granularity (probably weekly).

3. **Cross-check with `:id` set difference.** Pull the full `:id` list from Socrata via `$select=:id` (compact, fast), diff against local PKs, fetch only the missing ones individually. Most precise; most code.

Recommend (1) for a one-off recovery; bake it into a `scripts/sync_delta.py --backfill-by-id <dataset>` mode, run once, throw away the cursor afterwards.

**Where to look**
- Pagination logic: `scripts/sync_delta.py:fetch_page` (around line 480–520).
- Dedup logic that masks the issue: `scripts/sync_delta.py:upsert_page` (around line 570–577).
- Drift detection: `scripts/sync_delta.py:915–925` (the warning that fired this time).

**Affected datasets to recheck after fix**
- `dobjobs` (confirmed)
- Any other dataset with high tie-density on its cursor column. Worth a sweep — e.g. ACRIS sub-tables share `goodthroughdate` across millions of rows but use `refresh_by_documentid` mode so likely immune. `nyc_311_complaints` and `fdny_incidents` use ISO-precision timestamps so tie density is low; probably fine.
