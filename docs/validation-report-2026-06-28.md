# NYC Property Intel — Validation Report

**Date:** 2026-06-28
**HEAD:** `0fba453` (Merge PR #85 — outreach kit, migration 016→018 rename, weekly coverage-audit cron)
**Environment:** Production — frontend `nycpropertyintel.com` (Vercel), backend `nyc-property-intel-production.up.railway.app` (Railway), hosted MCP, Railway Postgres.
**Method:** Full test suite + live MCP tool calls against prod + live HTTP probes + prod DB inspection.

---

## 1. Executive summary

**Overall: PASS / production-healthy.** All 18 MCP tools work and return sane data; both previously-known data bugs (HPD false-harassment, 311 undercount) are confirmed fixed live; the full non-integration test suite is green (489/489); web + API surface is up with strong security headers and correct input validation.

**Caveats before lender outreach:**
- **Lis pendens / foreclosure data is NOT available** (not in ACRIS; needs a new dataset ingestion). Do not claim foreclosure coverage.
- The **weekly coverage-audit cron (PR #85) is not yet running** — it needs a Railway service created per `docs/ops-coverage-audit-cron.md`.
- The **311 standalone-tool fix is committed in code but uncommitted locally / not yet deployed** (see §6).
- A few cosmetic data-scoping items (FDNY zip-level/single-day, cross-tool 311 count drift).

---

## 2. Automated test suite

| Item | Result |
|---|---|
| `pytest -m "not integration"` | **489 passed, 0 failed** (50 integration tests deselected — require live local Postgres) |
| Runtime | 167s |

✅ No regressions on the merged tree.

---

## 3. MCP tools — live production validation (18/18)

Test properties: **1750 Sedgwick Ave, BBL `2028800153`** (heavy HPD litigation, all "No Harassment"); **170 E 3rd St, BBL `1003980015`**; zips `10009` / `10453` for market tools.

| Tool | Works | Data sane | Notes |
|---|---|---|---|
| lookup_property | ✅ | ✅ | address↔BBL both directions; fuzzy-match disclaimer present |
| analyze_property | ✅ | ✅ | 14-source aggregate coherent; mortgage total carries cross-collateralization caveat |
| get_property_issues | ✅ | ✅ | HPD/DOB/ECB class + open breakdowns reconcile |
| get_property_history | ✅ | ✅ | $0 transfer correctly flagged non-arm's-length |
| get_hpd_complaints | ✅ | ✅ | 302 complaints / 9 open |
| get_hpd_litigations | ✅ | ✅ **bug-fix PASS** | harassment_findings=0, no warning field (see §4) |
| get_hpd_registration | ✅ | ✅ | full contact set |
| get_dob_complaints | ✅ | ✅ | BIN-resolved, category map included |
| get_building_permits | ✅ | ✅ | `initialcost` null on older BIS jobs (data-source limit) |
| get_liens_and_encumbrances | ✅ | ✅ | 28 ACRIS instruments; coherent debt chain |
| get_tax_info | ✅ | ✅ | FY2027 assessment, exemptions |
| get_rent_stabilization | ✅ | ✅ | `false` with hedged note (HUD/HFA building) |
| get_evictions | ✅ | ✅ | 15 executed evictions 2017–2025 |
| get_311_complaints | ✅ | ✅ **bug-fix PASS** | open/closed split correct (see §4) |
| get_nypd_crime | ✅ | ✅ | 300m radius, felony/misd/violation split |
| get_fdny_fire_incidents | ✅ | ⚠️ | works, but zip-level + single-day clustering (see §7) |
| search_comps | ✅ | ✅ | quarterly stats, N/A note for sqft-less rows |
| search_neighborhood_stats | ✅ | ✅ | zip aggregate coherent |

**18/18 return without error; no tool returned empty where data should exist; no false/alarmist claims.**

---

## 4. Known-bug regression verification (the credibility gate)

| Bug | Verification | Result |
|---|---|---|
| **HPD false "harassment" warning** | Live `get_hpd_litigations` on BBL 2028800153 (25 cases, all "No Harassment"/null) → `harassment_findings: 0`, **no `harassment_warning` field**; `analyze_property` mirrors correctly with no harassment accusation | ✅ **PASS (live in prod)** |
| **311 open-count undercount** | `get_311_complaints` summary `open: 9 / closed: 21` reconciles with row-by-row recount; report path (`analysis.py`) uses `status != 'CLOSED'` | ✅ **PASS** |
| **Eviction res/com undercount** | Fixed in `analysis.py` (commit 261be7d); 15 evictions returned BBL-indexed | ✅ PASS |
| **No false scary claims** | All `key_observations` are literal sourced counts; mortgage caveat present | ✅ PASS |

---

## 5. Web + API surface — live probe

| Page / endpoint | Method | Expected | Actual | Result |
|---|---|---|---|---|
| `/`, `/chat`, `/hpd-violations`, `/dob-violations`, `/property-owner-lookup`, `/nyc-eviction-history-search`, `/nyc-property-due-diligence`, `/legal`, `/reports`, `/watch-confirm` | GET | 200 | 200 | ✅ |
| `/r/<bogus>` (permalink shell) | GET | 200 graceful | 200 | ✅ |
| bogus path | GET | 404 | 404 | ✅ |
| `/api/signup` (empty body) | POST | 4xx | **400** | ✅ validates |
| `/api/watch` (empty body) | POST | 4xx | **400** | ✅ validates |
| `/mcp` (no auth) | POST | 401 | **401** | ✅ auth enforced |

**Security headers (main site):** ✅ CSP (locked-down allowlist), HSTS `max-age=63072000; includeSubDomains; preload`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy` (camera/mic/geo denied).

---

## 6. PR #85 specific validation

| Item | Status |
|---|---|
| Migration rename `016 → 018_shared_reports_owner.sql` (collision fix) | ✅ Present; references updated; idempotent/no DB change; tests pass |
| Outreach kit (`docs/outreach-kit-2026-06-19.md`) | ✅ Present (docs only) |
| Weekly coverage-audit cron (`railway.toml` + `scripts/coverage_audit.py`) | ⚠️ **Code merged but NOT yet active** — requires a Railway service whose name contains `audit` (not `cron`), schedule `0 8 * * 1`, env `RAILWAY_DB`/`DATABASE_URL` + `SOCRATA_APP_TOKEN`, per `docs/ops-coverage-audit-cron.md`. **Until created, the "automated weekly audit" claim is still not automated.** |
| Standalone 311 fix (`complaints_311.py`) | ⚠️ **Committed in working tree but uncommitted to git / not deployed.** Needs branch + PR to ship to prod. |

---

## 7. Open issues / limitations (none blocking, but track)

1. **Lis pendens / foreclosure — DATA GAP.** All 95 ACRIS doctypes checked; no lis-pendens/notice-of-pendency/foreclosure code exists. NYC files these with the County Clerk / NYSCEF court system, not ACRIS. Closing this = new dataset ingestion, not a filter change. **This is the #1 true gap vs. competitor RegWatch for the lender ICP.**
2. **`acris_document_control_codes` lookup table is malformed** — `doctype`/`doctypedescription` columns loaded blank (only `recordtype`/`classcodedescription` populated). Unused today; would need a re-import if ever relied upon.
3. **FDNY tool is zip-level + single-day-clustered** — returns most-recent N rows of the zip, not address-level fire history. Misleading default ordering; cosmetic.
4. **Cross-tool 311 count drift** — `get_311_complaints` (windowed, ~30 rows) vs `analyze_property` (full COUNT, e.g. 289) differ for the same BBL. Both internally consistent; potential user confusion.
5. **`get_building_permits` `initialcost` null** on older BIS jobs — data-source limitation; don't read as $0.
6. **Accepted limitations** (documented): dobjobs ~33% Socrata drift; FDNY reporting lag; evictions 2017+; rent-stab 2007–2017.
7. **No billing wired** — Stripe absent; $19 download/print and $19/mo Pro-monitoring are painted doors firing PostHog events only.

---

## 8. Verdict

The product is **production-healthy and safe to put in front of a lender today**, provided the report does **not** claim foreclosure/lis-pendens coverage. The credibility gate (false-harassment) is verified closed in prod. Two follow-ups before the "automated weekly audit" and 311-standalone fix are truly live: ship the 311 fix (branch+PR) and create the Railway audit service.
