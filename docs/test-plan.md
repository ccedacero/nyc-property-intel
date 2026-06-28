# NYC Property Intel — Test Plan (all features)

**Maintained at HEAD:** `0fba453` · **Last updated:** 2026-06-28

A living checklist for validating every feature surface. Test types:
**U** = unit (pytest, no DB) · **I** = integration (pytest `-m integration`, live local Postgres) · **L** = live-prod (call prod MCP/HTTP) · **M** = manual (browser/human).

How to run automated tiers:
```bash
.venv/bin/python -m pytest tests/ -q -m "not integration"     # U  (489 tests, ~3min)
.venv/bin/python -m pytest tests/ -q -m integration           # I  (needs local Postgres w/ NYC data)
```

---

## A. MCP tools (18) — `src/nyc_property_intel/tools/`

Reference test BBLs: `2028800153` (1750 Sedgwick, rich litigation), `1003980015` (170 E 3rd St). Zips `10009`/`10453`.

| ID | Feature | Cases | Type |
|---|---|---|---|
| A1 | `lookup_property` | address→BBL; BBL→profile; fuzzy-match disclaimer; invalid address → graceful; invalid BBL → ToolError | U + L |
| A2 | `analyze_property` | full 14-source aggregate returns; no false/alarmist claims; mortgage cross-collateralization caveat present; partial-failure of one sub-tool doesn't crash report | U + L |
| A3 | `get_property_issues` | HPD/DOB/ECB counts + open breakdown; ECB balance reconciles | U + L |
| A4 | `get_property_history` | sales + ACRIS deeds; $0 transfer flagged non-arm's-length; `most_recent_sale_price` null when only $0 | U + L |
| A5 | `get_hpd_complaints` | counts, open subset, top categories | U + L |
| A6 | `get_hpd_litigations` | **REGRESSION: harassment_findings counts only `After Inquest`/`After Trial`; NO `harassment_warning` on a "No Harassment"-only building** | U + L |
| A7 | `get_hpd_registration` | owner/agent/head-officer contacts; valid registration window | U + L |
| A8 | `get_dob_complaints` | BIN resolution; category map; status | U + L |
| A9 | `get_building_permits` | filings list; `initialcost` null tolerated (not shown as $0) | U + L |
| A10 | `get_liens_and_encumbrances` | tax liens + ACRIS mortgages w/ borrower/lender parties; doctype filter (`MTGE/AGMT/ASST/SAT/SMTG/AL&R/AALR`); **known gap: no lis pendens** | U + L |
| A11 | `get_tax_info` | assessment, market/taxable value, exemptions | U + L |
| A12 | `get_rent_stabilization` | unit counts 2007–2017; hedged note when not DHCR-registered | U + L |
| A13 | `get_evictions` | **REGRESSION: res/com match on first letter (`R`/`Residential`/`RESIDENTIAL`)**; 2017+ scope note | U + L |
| A14 | `get_311_complaints` | **REGRESSION: open = `status != CLOSED` (Open/In Progress/Pending/Assigned/Started), not `== OPEN`** (`complaints_311.py::_summarize`) | U + L |
| A15 | `get_nypd_crime` | 300m radius; felony/misd/violation split | U + L |
| A16 | `get_fdny_fire_incidents` | returns; **known issue: zip-level + single-day clustering** — assert disclosure note present | U + L |
| A17 | `search_comps` | comps by zip/class/price/date; quarterly stats; N/A note for sqft-less rows | U + L |
| A18 | `search_neighborhood_stats` | zip aggregate (stock, sales, rent-stab %, violations) | U + L |
| A19 | Cross-tool consistency | 311 count in `get_311_complaints` (windowed) vs `analyze_property` (total) — document expected difference, don't regress to identical | L |
| A20 | Auth + rate limiting | `server.py`: missing token → 401; invalid token → 401; over daily limit → `rate_limit_hit`; anon cost cap (PR #83) graceful-degrades, no blackout | I + L |

---

## B. HTTP / API endpoints — `server.py`, `chat.py`, `watch.py`, `loops_webhook.py`

| ID | Endpoint | Cases | Type |
|---|---|---|---|
| B1 | `POST /api/signup` | valid email → token provisioned via Loops; empty/malformed → **400** (not 500); MX-record check; duplicate email handling | I + L |
| B2 | `POST /api/watch` | valid → double-opt-in email; empty → **400**; IP rate-limit; per-email cap; looser email regex than chat (known) | I + L |
| B3 | `/watch-confirm` | confirm token → activates watch; bad/expired token → graceful | I + M |
| B4 | `POST /mcp` | no auth → **401**; valid token → tool dispatch | L |
| B5 | Chat endpoint | anon 3 queries/day enforced; 4th → limit message; query produces report + persists `shared_reports` row | I + L |
| B6 | `POST /api/loops-webhook` | valid signature → provision; bad signature → log + 200 (no provision); malformed → `signup_rejected_malformed` event | U + I |
| B7 | Report permalink `/r/<id>` | real id → renders report; bogus id → graceful (200 shell, client handles) | L + M |
| B8 | Coverage endpoint (if exposed) | returns coverage summary | L |

---

## C. Web frontend — `site/`

| ID | Feature | Cases | Type |
|---|---|---|---|
| C1 | Static pages load | all pages 200: `/`, `/chat`, `/hpd-violations`, `/dob-violations`, `/property-owner-lookup`, `/nyc-eviction-history-search`, `/nyc-property-due-diligence`, `/legal`, `/reports`, `/watch-confirm` | L |
| C2 | Chat UI | submit address → streamed report; anon counter decrements; limit reached → signup CTA | M |
| C3 | Signup flow | email submit → confirmation; token email arrives (Loops) | M |
| C4 | Watch-this-building | set watch → opt-in email → confirm → appears as watched; weekly alert fires | M |
| C5 | Report generation + permalink | generate → shareable `/r/<id>` → opens for logged-out user | M |
| C6 | Lookup landing pages | `hpd-violations` etc. → entering address runs query | M |
| C7 | Painted door: $19 Download/Print | button fires `report_download_print` PostHog event; HTML5 print-to-PDF works | M |
| C8 | Painted door: $19/mo Pro-monitoring | modal after watch fires `pro_monitoring_interest`; **no real charge** | M |
| C9 | Demo counter / trust + fair-housing blocks | render on homepage | M |
| C10 | 404 page | bogus path → 404 page | L |

---

## D. Security & headers

| ID | Check | Expected | Type |
|---|---|---|---|
| D1 | CSP header | locked-down allowlist (self + posthog + loops + cloudflare) | L |
| D2 | HSTS | `max-age=63072000; includeSubDomains; preload` | L |
| D3 | Clickjacking | `X-Frame-Options: DENY`, `frame-ancestors 'none'` | L |
| D4 | MIME / referrer / permissions | `nosniff`, `strict-origin-when-cross-origin`, camera/mic/geo denied | L |
| D5 | CORS allowlist | only expected origins on API | L |
| D6 | Token storage | tokens SHA-256 hashed at rest; `nyprop_*` prefix; 60s TTL cache | U + I |
| D7 | Injection defense | Loops webhook XML-escapes; SQL params bound (no string interpolation) | U |

---

## E. Background jobs / cron — `railway.toml`, `scripts/`

| ID | Job | Cases | Type |
|---|---|---|---|
| E1 | Data-sync cron | tier-1 daily / tier-2 weekly / tier-3 monthly dispatch; exit 0 convention | I + M |
| E2 | Cleanup cron | consolidated weekly cleanup runs | M |
| E3 | **Coverage-audit cron (PR #85)** | `scripts/coverage_audit.py` prints `[coverage-audit] … RED_FLAGS:`; runs only if a Railway service named `*audit*` (not `*cron*`) exists, schedule `0 8 * * 1`, env `RAILWAY_DB`/`DATABASE_URL` + `SOCRATA_APP_TOKEN`. **Verify service exists & last run green.** | M |

---

## F. Data layer & integrity

| ID | Check | Cases | Type |
|---|---|---|---|
| F1 | Migrations apply cleanly | `scripts/migrations/` 001–018 idempotent; **018 (renamed from 016) no collision with 016_watched_buildings / 017_watch_confirmation** | I |
| F2 | Core tables present | `mcp_tokens`, `mcp_usage_log`, `shared_reports`, `watched_buildings` + NYC datasets | I + L |
| F3 | Data-vs-source integrity | spot-check N records vs NYC Open Data (the coverage_audit job automates this) | M + L |
| F4 | Accepted limitations documented | dobjobs ~33% drift; FDNY lag; evictions 2017+; rent-stab 2007–2017 — surfaced in tool notes | U |
| F5 | Freshness notes | each tool emits `data_as_of` / freshness note | U + L |

---

## G. Auth / billing

| ID | Check | Cases | Type |
|---|---|---|---|
| G1 | Token provisioning | Loops webhook → `nyprop_*` token; plan-based daily limit (trial 10/day 30-day) | I |
| G2 | Plan enforcement | trial vs pro vs team daily limits honored | I + L |
| G3 | Anon cost cap (PR #83) | daily Anthropic spend ceiling → graceful degrade, site stays up | I + L |
| G4 | Billing | **No Stripe** — confirm painted doors do NOT charge; only fire intent events | M |

---

## H. Regression-critical (run before every lender-facing demo)

1. **A6** — no false harassment warning on BBL 2028800153 (live).
2. **A14** — 311 open-count not undercounting.
3. **A2** — `analyze_property` makes no false scary claims on a real building.
4. **A10** — liens report does NOT imply foreclosure/lis-pendens coverage we don't have.
5. **B1/B2/B4** — signup/watch reject malformed (400), `/mcp` enforces auth (401).
6. **Full suite** — `pytest -m "not integration"` green.

---

## Outstanding before "all green"

- [ ] Ship the **311 standalone fix** (`complaints_311.py`) — branch + PR (currently uncommitted/undeployed).
- [ ] Create the **Railway coverage-audit service** (E3) so the weekly-audit claim is true.
- [ ] (Project) Decide on **lis-pendens/foreclosure ingestion** (A10) — the real lender gap; not a quick fix.
- [ ] (Cosmetic) FDNY ordering (A16); cross-tool 311 drift note (A19).
