# NYC Property Intel — Comprehensive QA (2026-09-11)

**Method:** 5 parallel agents, each on a non-overlapping lens. Live MCP tool calls
against the hosted server, corroborated against the Railway prod DB and live NYC
Open Data (Socrata). Read-only; no emails sent; no prod writes. Covered all 19
tools, the underlying data, and the watch/property-change email-alert system.

**Overall verdict:** Data is valid and fresh; core tools are accurate and never
fabricate; all previously-fixed bugs remain fixed. Two **HIGH** issues found (one
correctness bug, one product gap), plus one medium-high accuracy bug and several
medium/low items.

---

## Regression checks — ALL PASS
- **ECB in `analyze_property`** (old null/zero bug): surfaces ECB, matches
  `get_property_issues` exactly (BBL 2028800153 → 52 total / 3 active). ✅
- **False-harassment claim** (old 6,172-building bug): keyed correctly on
  `findingofharassment IN ('After Trial','After Inquest')`, not the casetype
  string. BBL 2028800153 (25 cases, 15 "Tenant Action/Harrassment", all "No
  Harassment") → `harassment_findings: 0`, no warning. True positives (After
  Trial / After Inquest) still flag correctly. ✅
- **Lis-pendens / foreclosure over-claim**: no tool claims coverage;
  `get_liens_and_encumbrances` explicitly lists them as not covered. ✅
- **Fabrication guard**: every tool returns empty/graceful-error on nonexistent
  BBLs, malformed addresses, vacant lots, ambiguous boroughs, condo billing
  lots. No invented BBLs/owners/data anywhere. ✅
- **hpd_litigations column-map bug** (`boro`/`openjudgement` once 100% NULL):
  now 0.00% NULL. ✅
- **Data integrity**: MVs current (exact 3-BBL reconciliation), zero duplicate
  PKs, drift vs Socrata <2% (normal incremental lag). ✅

---

## Findings (ranked)

### 1. HIGH — `get_dob_complaints` returns false "0 complaints" for ~52,900 BBLs
**Correctness bug.** Repro: `get_dob_complaints(bbl="2031420001")` → resolves
placeholder `bin="2000000"` → `total_returned: 0` with an authoritative-looking
`data_note: "BIN-based exact match via PAD table"`. **Truth: 119 DOB complaints**
on that BBL's real BINs (2013284, 2092392); querying by address returns them.

Root cause — `src/nyc_property_intel/tools/dob_complaints.py:316-319`:
```python
raw_bin = str(pad_row["bin"]).strip() if pad_row.get("bin") else ""
bin_val = raw_bin if raw_bin and raw_bin not in ("0", "1000000") else None
```
The sentinel guard excludes only `"0"` and Manhattan's `"1000000"`. The other
four borough placeholder BINs (`2000000`,`3000000`,`4000000`,`5000000`) pass
through and match zero rows. ~52,900 BBLs carry one of these placeholders in PAD
(`pad_adr LIMIT 1` with no ORDER BY can return it). Manhattan is the only
protected borough.

**Fix:** exclude any BIN matching `^[1-5]000000$`; prefer a real BIN and/or
aggregate all BINs for the BBL (fall back to address/BBL match when only a
placeholder exists).

### 2. HIGH — Watch alerts miss the events the ICP cares about most
**Product/false-negative gap.** Change-detection (`watch.py`) watches only **4
aggregate counts**: HPD-open, DOB-open (via `dispositiondate IS NULL` proxy),
ECB-active, and HPD-litigation row count. It does **not** watch: new
sales/deed transfers, new liens/mortgages, foreclosure/lis-pendens, new
311/DOB complaints, or evictions. A hard-money lender "watching" a building will
get **silence through a sale or a new lien** — exactly the events a watch is for.
(lis-pendens isn't even ingested yet — separate known gap.)

**Fix (scoping):** add sale/ownership-change and new-lien signals to the watch
snapshot at minimum; complaints/evictions next. Decide product-side which
signals matter most for lenders.

### 3. MEDIUM-HIGH — `get_tax_info` reports prior-year values under the current year label
**Accuracy bug, systematic.** Tool returns `"year": <latest roll year>` but the
market/assessed/taxable dollars are the PRIOR year's figures (equals DOF
`pyacttot`). Understates current assessment ~6-8% and **contradicts
`lookup_property`/`analyze_property`** for the same BBL.

| BBL | get_tax_info (labeled 2027) | Actual FY2027 assessed total |
|---|---|---|
| 1008350041 (ESB) | $534,044,700 | **$577,440,900** |
| 2028800153 | $6,412,050 | **$6,806,250** |
| 4018900016 | $100,350 | **$101,250** |

**Fix:** select current-year (`curacttot`/`tenacttot`) columns, or correct the
year label to match the values returned.

### 4. MEDIUM — Capped-window summaries misrepresent building/area totals
`get_311_complaints`, `get_nypd_crime`, and `get_fdny_fire_incidents` compute
their summary stats over the LIMIT-capped result rows (max 100/50), not the true
total, and don't disclose the cap.
- **311** (BBL 1018510008): tool `total_returned:100`, `open:78`; DB truth
  **2,693 total / 212 not-closed**.
- **NYPD** (BBL 2031420001, 300m, since 2023): tool 50 / 19 felonies /
  `by_year:{2025:50}`; DB in-radius **3,880 / 1,221 felonies** — the `by_year`
  summary falsely implies zero crime in 2023-24.
- **FDNY**: zip-level only (145,682 incidents in zip 10457); scope disclosed in
  `data_note` but the tool description oversells building-specificity.

`get_hpd_complaints` does this **right** (true `summary.total_complaints`) — use
it as the pattern. **Fix:** expose true totals (separate COUNT) and/or disclose
the cap in `data_note`.

### 5. MEDIUM — Watch alert send path has never fired in production
`last_notified_at` is NULL for all 11 watches; every daily cron run logs
`alerted=0`. The pipeline **is** running daily (cron `nyc-property-intel-cron`,
`process_watches` post-tier-1-sync; logs confirm `checked:8` = the 8
confirmed+active watches), and the Loops template contract matches
(`[address,changes,reportUrl,unsubscribeUrl]` == `_send_watch_email` payload;
`LOOPS_API_KEY` + `LOOPS_WATCH_TRANSACTIONAL_ID` both SET). But no real send has
ever occurred (nothing tracked has changed), so **delivery is unproven
end-to-end**. `scripts/trigger_test_watch_alert.py` has **no dry-run flag** (always
sends), so it wasn't safe to exercise. **Fix:** add a `--dry-run`/test-address
flag to the trigger and do one controlled real send to an internal address.

### 6. MEDIUM — Watch false-positive risk (backfill & status wobble)
Detection is count-based. When a daily sync ingests *old* records retroactively
(esp. `litigations` = raw row count; `dob_open` disposition proxy; `ecb_active`
status string re-classification), counts rise and read as "new," which would
email customers about non-events. The 7-day per-watch cooldown caps volume, not
correctness. **Fix:** compare identity sets (new IDs) rather than counts, or
gate on record dates being recent.

### 7. LOW / cosmetic
- **Negative `ecb_balance_due_total`** (ESB → -3,060): real sum of overpayment
  rows; a negative "balance due" is meaningless to users. Floor at 0 / exclude
  negatives. (`get_property_issues` + `analyze_property`.)
- **`get_hpd_litigations` has no limit/pagination** — a 350-case building
  produced a 112KB response that overflowed the token cap. Add a cap.
- **`get_hpd_complaints` mixed granularity** — `total_complaints` counts distinct
  complaints (560) while `open`/`closed` count problem-rows (57/1189). Similarly-
  named fields, different denominators. Clarify labels.
- **`search_comps` ignores reference-BBL building class/size** — with only a
  `bbl` it defaults zip but not class/sqft, returning type-mismatched comps
  (condos as comps for a multifamily). Description overstates behavior. Default
  class from the reference BBL, or fix the description.
- **`dof_sales` stale tail** — data ends 2026-03-31; a "last 12 months" comp
  search silently has a ~5.5-month empty tail (freshest comp 5+ months old) with
  no staleness warning.
- **`search_neighborhood_stats` neighborhood-name mode** returns only sales +
  trends; omits property_stock/violations/rent-stab (those are zip/PLUTO-keyed).
  Zip mode is the full picture.
- **`analyze_property` data-gap wording** slightly overstates "no sales" when
  only non-arms-length/$0 sales exist. Low severity.
- **`sync_state.expected_rows` stale** for several tier-1 datasets — cosmetic
  metadata; live-count comparison is authoritative.

---

## Data health snapshot
- **Freshness:** all 11 tier-1 datasets synced within ~11h; tier-3 within the
  monthly window (last 2026-09-01); `personal_property_master` re-synced today
  (fixed). Zero `last_error` across 23 datasets.
- **Drift vs live Socrata:** <2% on all core datasets (normal incremental lag),
  except two documented accepted limitations: `dobjobs` (−33%, source frozen
  2020-05-21, pre-2020 unenumerable) and `dob_complaints` (−1.56%, legacy
  `date_entered` frozen 12/31/2025). No real undercount.
- **Materialized views** (`mv_violation_summary`, `mv_property_profile`,
  `mv_current_ownership`): current — exact GROUP-BY reconciliation on 3 BBLs.
- **Integrity:** no duplicate PKs; PLUTO owner/assessment NULL rates <1%;
  hpd_litigations column-map bug fixed.

## Security (watch/email)
No issues. Confirm + unsubscribe tokens are `secrets.token_urlsafe(8)` (64-bit,
unguessable), travel only in the subscriber's own email; unconfirmed/inactive
watches correctly excluded from alerting; unsubscribe requires an explicit click
(no scanner/prefetch auto-detonation); double opt-in prevents watch-bombing.
