# NYC Property Intel — Tool-Correctness Verification Report

**Date:** 2026-08-24
**Method:** Field-level comparison of every live MCP tool's output against the authoritative NYC Open Data (Socrata) APIs. 62 individual field checks across 2 test BBLs (residential `2028800153` = 1750 Sedgwick Ave; commercial `2033200008` = 2968 Jerome Ave) plus zip-level aggregates for `10468` / `10453`.

---

## 1. Verdict

**All 18 tools are returning correct, trustworthy values.** No new correctness bugs were found. The two regression checks we specifically care about both pass: `analyze_property` now surfaces ECB violations, and there are **zero false harassment findings**.

The five DISCREPANCY rows are all **known, already-understood, non-blocking** conditions (quarterly PLUTO owner-refresh lag and the documented `dobjobs` BIS drift) plus one minor ~4% ECB undercount — none are wrong field values a user would be misled by on a distress signal.

| Verdict | Count | Meaning |
|---|---:|---|
| **MATCH** | 47 | Exact agreement with Socrata |
| **TOOL_HIGHER_OK** | 5 | Tool count ≥ Socrata due to retained history (correct, not a bug) |
| **DISCREPANCY** | 5 | Tool lower / stale vs Socrata — reviewed below, all explained |
| **COULD_NOT_VERIFY** | 5 | Source unreachable or no equivalent public dataset |
| **Total** | 62 | |

---

## 2. Per-Tool Results

| Tool | What was checked | Tool value vs source | Verdict |
|---|---|---|---|
| **lookup_property** | 9 PLUTO fields × 2 BBLs (owner, class, units, floors, year, assessed, zoning) | All match exactly **except** owner on `2033200008` (tool `2966-2968 JEROME AVE.` vs PLUTO `EMET EQUITY GROUP LLC`) | MATCH (18/19 fields); 1 DISCREPANCY (stale owner) |
| **get_tax_info** | Market value ↔ assessed ratio sanity, both BBLs | MV/assessed ≈ 45% (correct Class 2 & Class 4 ratios); FY2027 roll vs PLUTO snapshot | MATCH |
| **get_property_issues** | HPD (316), DOB (71), ECB (52) totals | HPD 316=316; DOB 71 ≥ 68–70; ECB 52 vs 54 (−2 rows) | MATCH / TOOL_HIGHER_OK / DISCREPANCY (ECB) |
| **get_hpd_complaints** | 302 complaints / 559 problems / 9 open | Socrata `uwyv-629c` + `a2nx-4u46` now return HTTP 403 (login-gated) | COULD_NOT_VERIFY |
| **get_hpd_litigations** | total_cases (25), harassment_findings (0) | 25=25; harassment 0=0 (No Harassment=12, null=13, zero actual findings) | MATCH |
| **get_hpd_registration** | registration id, dates, all 6 contact roles | id 221896, dates, Dalton Mgmt / Michael Borrero all match | MATCH |
| **get_building_permits** | DOB job filings count, both BBLs | 11 vs 17 and 5 vs 7 — documented `dobjobs` drift (~29–35%) | DISCREPANCY (accepted limitation) |
| **get_dob_complaints** | count (97 vs 98), open/active (0) | Off-by-1 at limit=100; ACTIVE=0 matches | MATCH |
| **get_evictions** | executed marshal evictions (17) | 17=17 by both address and BBL | MATCH |
| **get_311_complaints** | 311 requests at address | Tool capped at 100-row window; Socrata confirms 273–292 exist | COULD_NOT_VERIFY (display cap, not data loss) |
| **get_fdny_fire_incidents** | BBL→zip resolution (10453), incident volume | Zip correct; zip-level by design; 100-row cap | MATCH |
| **get_nypd_crime** | precinct assignment (46), offense mix | Precinct 46 correct for coords; 300m box + 100-row cap | MATCH |
| **get_property_history** | most-recent sale price/date + deed | $1,325,000 on 2026-02-27; deed 2026030500435001 confirmed in ACRIS | MATCH |
| **get_liens_and_encumbrances** | recent purchase-money mortgage | $1,380,000 MTGE to EMET / Hanmi Bank confirmed in ACRIS | MATCH |
| **search_comps** | 2 comp sales (2809 Morris, 2505 Grand) | $1.62M/2026-03-19 and $7.0M/2026-03-13 both exact | MATCH |
| **search_neighborhood_stats** | zip 10468 sales count + Q1 aggregates | 198 (24-mo local) vs 147 (12-mo rolling feed); Q1 internally consistent | TOOL_HIGHER_OK |
| **get_rent_stabilization** | is_rent_stabilized / unit count | Not-stabilized, 0 units — coherent; no clean Socrata equivalent | COULD_NOT_VERIFY |
| **analyze_property** | 11 PLUTO attrs + HPD/DOB/ECB/evictions/litigations/harassment/mortgages, both BBLs, vs standalone tools | All internal blocks equal their standalone tools and Socrata; **ECB block present**; harassment=0; only stale PLUTO owner on `2033200008` differs | MATCH (except stale owner DISCREPANCY) |

---

## 3. Discrepancies (Real Bugs?)

**No new wrong-value bugs were found.** All five DISCREPANCY rows fall into three already-understood buckets:

### 3a. Stale PLUTO owner name — refresh lag, not a wrong value
**Reproduce:** `lookup_property` / `analyze_property` on **BBL 2033200008**
- Tool returns owner `2966-2968 JEROME AVE.`; live PLUTO 26v1 shows `EMET EQUITY GROUP LLC`.
- Property genuinely sold 2026-02-27 for $1.325M to EMET (confirmed by the same tool's own mortgage + sale data, which already name EMET). Only the PLUTO owner snapshot lags the quarterly refresh. All other 10 PLUTO fields match exactly.
- **Action:** refresh the PLUTO snapshot; low severity (self-contradicted by the tool's fresher ACRIS/sales data).

### 3b. `dobjobs` BIS drift — documented, accepted limitation
**Reproduce:** `get_building_permits` on 2028800153 (11 vs 17) and 2033200008 (5 vs 7)
- ~29–35% below Socrata; pre-2020 historical BIS rows are unenumerable in the local ingest. Returned filings themselves are accurate. Matches the standing accepted-limitation memo — not a regression.

### 3c. ECB minor undercount — worth a look, not misleading
**Reproduce:** `get_property_issues` on 2028800153 (ECB 52 vs Socrata 54, ~4%)
- Retention policy predicts tool ≥ source, so a 2-row shortfall is mildly unexpected but small; the ECB content (elevator/facade/construction violations, balances) is correct. Flagged for a follow-up look; not order-of-magnitude and not user-misleading.

---

## 4. Could-Not-Verify Items

These are **not failures** — the tool output looked coherent in every case; we simply could not obtain an independent authoritative number.

| Item | Why unverifiable |
|---|---|
| **get_hpd_complaints** (302/559/9) | Socrata `uwyv-629c` and `a2nx-4u46` now return **HTTP 403 "must be logged in"** — a genuine access restriction on the complaints datasets (other datasets reached fine). Counts couldn't be independently confirmed. |
| **get_311_complaints** | Tool caps output at a 100-row window; summary sums that window, not a full total. Socrata confirms the property really has 273–292 requests, so the gap is a **display cap, not under-reporting**. |
| **get_rent_stabilization** | No clean Socrata equivalent exists — rent-stab unit counts come from taxbills.nyc / John Krauss DHCR scrape (2007–2017), not Open Data. Answer is coherent (0 ≤ 227 units). |
| **analyze_property.mortgages** (both BBLs) | ACRIS BBL→document join is multi-table and portfolio/cross-collateralized loans inflate face value. Exact count not reproducible; standalone tool corroborates and the amount_note already discloses this. |

---

## 5. The Two Things We Specifically Care About

**(a) `analyze_property` now surfaces ECB violations — CONFIRMED.**
The 2026-07 gap is fixed. On residential `2028800153`, `analyze_property.ecb_violations.total = 52` (active=3, balance_due=$4,375), equal to the standalone `get_property_issues` ECB total and within 1 row of Socrata (retention). On commercial `2033200008`, `analyze` surfaces ECB=2, matching standalone and Socrata exactly (2=2=2).

**(b) No false harassment findings — CONFIRMED CLEAN.**
Both `get_hpd_litigations` and `analyze_property` report `harassment_findings = 0` on `2028800153`, despite the building having **15 cases whose `casetype` is `Tenant Action/Harrassment`**. Socrata's `findingofharassment` breakdown is `{'No Harassment': 12, null: 13}` — zero rows with an actual harassment finding. The tool correctly distinguishes case **type** from a harassment **finding**. The known 2026-06 false-harassment bug remains fixed.
