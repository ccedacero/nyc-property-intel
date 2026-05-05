# hpd_litigations.findingdate — Silent Data Loss Investigation

**Date:** 2026-05-03
**Branch:** `fix/hpd-litigations-columns`
**Status:** Real bug. Three columns 100% NULL. Two distinct root causes (one per fix; one needs a backfill).

---

## Verdict

**Real bug — and it's bigger than the reporter realized.** Three columns on `hpd_litigations` are 100% NULL despite Socrata having populated values. Two distinct root causes, both fixable.

| Local column | Source field | Local count nonnull | Source count nonnull | Root cause |
|---|---|---|---|---|
| `boro` | `boroid` | **0 / 237,369** | 237,860 | Name mismatch — no `column_map` in `DatasetCfg` |
| `openjudgement` | `casejudgement` | **0 / 237,369** | 237,860 | Name mismatch — no `column_map` in `DatasetCfg` |
| `findingdate` | `findingdate` | **0 / 237,369** | 322 | Pre-fix `_coerce` corruption; cursor never re-fetches affected rows |

**Auditor was right that `findingdate` is silently broken; was wrong about which ~322 rows are losing data — they're real, and the historical NULL is also real.**

---

## Concrete field-name evidence

**Socrata source field name:** `findingdate` (no underscore). Format: `M/D/YYYY HH:MM:SS`, e.g. `"01/02/2025 00:00:00"`.

**Local Postgres column name:** `findingdate`, type `timestamp without time zone`.

The names match exactly after `_normalize_socrata_keys` (no underscore to strip, no remap needed). Probed via:

```bash
$ curl 'https://data.cityofnewyork.us/resource/59kj-x8nc.json?$limit=3&$where=findingdate IS NOT NULL'
[{"findingdate":"01/02/2025 00:00:00", ...}, ...]

$ psql "$RAILWAY_DB" -c "\d hpd_litigations" | grep findingdate
 findingdate         | timestamp without time zone |
```

```bash
$ curl 'https://data.cityofnewyork.us/resource/59kj-x8nc.json?$select=count(litigationid)&$where=findingdate IS NOT NULL'
[{"count_litigationid":"322"}]

$ psql "$RAILWAY_DB" -c "SELECT COUNT(findingdate) FROM hpd_litigations;"
 0
```

---

## Root cause #1 — `boro` and `openjudgement` (name mismatch)

The dataset config in `scripts/sync_delta.py:146-149` (pre-fix) has **no `column_map`**:

```python
"hpd_litigations": DatasetCfg(
    key="hpd_litigations", socrata_id="59kj-x8nc", table="hpd_litigations",
    cursor_col="caseopendate", pk_cols=("litigationid",), tier=1,
),
```

`_normalize_socrata_keys` (line 44-57) strips underscores from source keys. With no map:

| Source raw | After strip | Local column? |
|---|---|---|
| `boroid` | `boroid` | **No** (local is `boro`) — silently dropped |
| `casejudgement` | `casejudgement` | **No** (local is `openjudgement`) — silently dropped |

`upsert_page` (line 707-717) projects rows tuple-by-tuple from `target_cols`; any field name not in `target_cols` falls out at `row.get(c)` returning `None`. No error, no warning. Pure data loss.

**Both columns are 100% NULL across all 237,369 rows.** Confirmed:

```sql
SELECT COUNT(boro), COUNT(openjudgement), COUNT(*) FROM hpd_litigations;
 count | count | count
-------+-------+--------
     0 |     0 | 237369
```

Same exact failure mode previously discovered for `nyc_311_complaints`, `fdny_incidents`, and `nypd_crime_complaints` (commit `b9680c8`, "Fix nyc_311 / fdny / nypd: column_map for underscore-bearing PKs"). This dataset was missed.

---

## Root cause #2 — `findingdate` (pre-fix corruption + cursor lock-in)

Field name matches; `_coerce` (post-fix at commit `962b690`) parses the value correctly. Verified:

```python
>>> _coerce("01/02/2025 00:00:00", "timestamp without time zone")
datetime.datetime(2025, 1, 2, 0, 0)
```

So why is `findingdate` 100% NULL?

**Two-step failure:**

1. Initial backfill ran **before** the `_coerce` fix on 2026-05-03 (commit `962b690`). The pre-fix `_coerce` only handled ISO 8601, so any `M/D/YYYY HH:MM:SS` value silently fell into the `except ValueError: return None` branch. All 322 historical `findingdate` values became `NULL` on insert.
2. Post-fix incremental syncs **never re-fetch the affected rows** because:
   - The cursor is `caseopendate` (case open date).
   - All 322 rows with a `findingdate` have `caseopendate < 2026-04-30` (the saved cursor as of `2026-05-04 15:41 UTC`).
   - The source updates `findingdate` *after* a harassment ruling, which can be years after `caseopendate`. Source rows get a `findingdate` change without any change to `caseopendate`, so the `caseopendate > $cursor` filter excludes them forever.

This is a classic **cursor staleness** issue: the cursor column doesn't track all source mutations, so updates to non-cursor columns on already-synced rows are invisible to incremental sync.

Cross-check with `findingofharassment` (TEXT, not date): 8014 of 8101 source rows are populated locally — those got through during initial backfill because TEXT doesn't go through `_coerce`'s buggy date branch. Same row population reached the DB; only the `findingdate` *value* was corrupted.

The 322 source rows with `findingdate` correspond exactly to harassment findings (`findingofharassment IN ('After Inquest', 'After Trial')`):

| `findingofharassment` | Source count | Local count | Local with `findingdate` |
|---|---|---|---|
| After Inquest | 247 | 244 | 0 |
| After Trial | 77 | 76 | 0 |
| No Harassment | 7777 | 7694 | 0 |
| (NULL) | ~229,659 | ~229,355 | 0 |

The harassment-finding rows are mostly present (320/322); they just have NULL `findingdate`.

---

## Recommended fix

### Code fix (lands in this PR)

`scripts/sync_delta.py:146-149` — add a `column_map`:

```python
"hpd_litigations": DatasetCfg(
    key="hpd_litigations", socrata_id="59kj-x8nc", table="hpd_litigations",
    cursor_col="caseopendate", pk_cols=("litigationid",), tier=1,
    column_map={
        "boroid":        "boro",
        "casejudgement": "openjudgement",
    },
),
```

### Tests added

`tests/test_coerce.py::TestNormalizeSocrataKeys` — five new cases:
1. Strip-only mode (regression guard for `_normalize_socrata_keys`).
2. Drop Socrata `:system_fields`.
3. `column_map` overrides stripped name.
4. Combined strip-then-remap (covers `nyc_311_complaints` style).
5. **`hpd_litigations` config-pinning test** — fails if anyone removes the map again.

All 64 tests in `test_coerce.py` pass post-fix.

### Operational follow-up (NOT in this PR — manual one-shot)

After this PR merges and Railway redeploys, run a **one-shot reset backfill** of `hpd_litigations` to fix the 100% NULL on all three columns:

```bash
# On Railway via the existing nyc-property-intel-backfill service:
DATABASE_URL=$DATABASE_URL SOCRATA_APP_TOKEN=$SOCRATA_APP_TOKEN \
  uv run python scripts/sync_delta.py hpd_litigations --reset
```

The dataset is small (~237K rows, ~5 pages at PAGE_SIZE=50K). Estimated runtime: 2–3 minutes.

`--reset` clears `sync_state.cursor_value`, the next run does a full backfill, every row goes through the post-fix `_coerce` and the new `column_map`. ON CONFLICT DO UPDATE replaces NULLs with the parsed values.

### Acceptance criteria (SQL)

```sql
-- All three should jump from 0 to ~populated counts after reset backfill:
SELECT
  COUNT(boro)              AS boro_nonnull,         -- expect ~237369 (~100%)
  COUNT(openjudgement)     AS openjudgement_nonnull,-- expect ~237369 (~100%)
  COUNT(findingdate)       AS findingdate_nonnull,  -- expect ~320 (matches harassment-finding rows)
  COUNT(*) FILTER (
    WHERE findingofharassment IN ('After Inquest', 'After Trial')
      AND findingdate IS NOT NULL
  ) AS harassment_with_date  -- expect ~320 (every harassment finding should have a date)
FROM hpd_litigations;
```

If `findingdate_nonnull = 0` after the reset, the parsing pipeline is still broken — escalate. If `boro_nonnull = 0`, the column_map didn't take — check the deployed branch.

---

## Why this slipped past prior audits

1. **The 2026-05-02 column-null audit** (`docs/data-coverage-audit-2026-05-02.md`) classified `hpd_litigations.findingdate` as "harmless schema drift — column exists locally from NYCDB bulk load but not in Socrata's API." That assessment used a sample row that lacked a finding (which is most rows) and concluded the field was missing entirely. It is in fact present on 322 of 237,860 rows — the long tail.
2. The `boro` and `openjudgement` mismatches were **never flagged by any audit**. They don't appear in any prior doc. This is a previously unreported bug.
3. The `_coerce` fix at commit `962b690` is correct in isolation, but its acceptance test was "tier-1 syncs run without errors." It didn't include row-level value verification for non-incremental columns on already-synced rows.

---

## Related issues (other datasets) — DO NOT FIX HERE, but log

The same `_normalize_socrata_keys` strip-only failure mode silently drops fields whenever the local schema diverges from Socrata's short names. The full list of datasets without `column_map`:

```
hpd_violations          (wvxf-dwi5)  — needs audit
hpd_litigations         (59kj-x8nc)  ← this PR
hpd_registrations       (tesw-yqqr)  — comment says "matches", not verified
ACRIS sub-tables × 5    (refresh_by_documentid mode, may also miss columns)
```

Recommended follow-up: extend `scripts/column_null_audit.py` to compare every Socrata source field name (stripped + mapped) against `information_schema.columns` for the target table, and flag any source field that doesn't land in a target column. That would have caught all three failures here automatically.

---

## File:line evidence summary

- `scripts/sync_delta.py:44-57` — `_normalize_socrata_keys` (drops unmapped names silently).
- `scripts/sync_delta.py:146-149` (pre-fix) — missing `column_map` for `hpd_litigations`.
- `scripts/sync_delta.py:707-717` — `upsert_page` projects only `target_cols`; unmapped fields fall out.
- `scripts/sync_delta.py:645-672` (post-fix at `962b690`) — `_coerce` + `_parse_flexible_datetime` correctly parse `M/D/YYYY HH:MM:SS`.
- `scripts/sync_delta.py:881-883` — cursor advances per page on `caseopendate`; never revisits older rows where `findingdate` later changes.
- Source fields, observed 2026-05-03: `boroid`, `casejudgement`, `findingdate`, `findingofharassment` (all populated where applicable).
- Local schema, queried Railway prod 2026-05-03: `boro`, `openjudgement`, `findingdate`, `findingofharassment` (first two are 100% NULL).
