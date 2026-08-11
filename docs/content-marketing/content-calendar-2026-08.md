# Content Calendar — 2026-08-10 (3-agent research synthesis)

Sources: news-wave law research (Fable, primary-source verified), competitor
keyword-gap crawl, money-page SERP/autocomplete validation with code-level data
audit. Forum-mining lens incomplete (session limit) — autocomplete evidence
substitutes. Volume proxy = live Google autocomplete (no volume tool available).

## Data prerequisites (small jobs that unlock pages)

1. **Widen `liens.py` ACRIS doctype filter** (currently mortgages-only:
   MTGE/AGMT/ASST/SAT/SMTG/AL&R/AALR) — prerequisite for the lien page to
   honestly be a "lien search." Tiny change.
2. **Ingest RGB rent-stabilized building lists** (free download, 2024
   registrations as of Nov 2025) — unlocks the rent-freeze page, Good Cause
   page, and tenant rent-stab lookup. Without it our 2007–2017 data loses to
   free 2024 competitors (mystabilizednyc, rsbl.nyc, amirentstabilized.nyc).
3. Later: DOB Certificates of Occupancy dataset (gates C-of-O page);
   nycourts.gov county auction PDFs (gates the foreclosure-list play);
   LL84/BEAM (gates LL97 calculator).

## Tier 1 — ship now (time-limited arbitrage + biggest wave of the year)

| Page | Why now | Data hook | ICP |
|---|---|---|---|
| `/propertyshark-alternative` | Their July price hike (Pro $59.95, Elite $79.95) — Capterra ("updated Jul 21") and every comparison site still quote old prices; #1 SERP result is a 2021 Reddit thread; nothing NYC-specific ranks. Target modifiers: free alternative / cost / worth it / vs costar. | Verified-today price table + free uncapped lookup | HIGH |
| Rent-freeze pages (RGB Order #58) | 0% freeze on BOTH lease terms, renewals start **Oct 1, 2026** — the year's biggest wave, ~7 weeks out. Everyone ends with "mail DHCR"; nobody has an address check. Companion: **LL86 of 2025** lobby-sign law (bilingual signs manufacture our exact query). Bilingual edge. | `get_rent_stabilization` signals (+ RGB ingest) | funnel |
| `/nyc-property-lien-search` (after liens.py fix) | Deepest autocomplete cluster; RegWatch holds #1-2 but gates at 5 views/day + $15/mo; PropertyShark's asset is from 2014. Fold LL153 ECB-lien content in as H2 (its own page: zero autocomplete — killed). | ACRIS + tax-lien-sale list + ECB balancedue in one query; explicit "not a title search" | HIGH |
| `/nyc-tax-lien-sale-list` | City ships an unusable PDF; we hold the table (`dof_tax_lien_sale_list`). All 10 autocomplete slots verified. Annual seasonal spike. Verify 2026 sale authorization before publishing. | zero build — dataset ingested | HIGH |
| Pied-à-terre page maintenance | Add TC107 appeal dates (Mar 1/15, 2027) as the Sept 18 deadline passes — second wave on the existing page. | done page | — |

## Tier 2 — Q4 deadline pages (build Sept–Oct)

| Page | Deadline/wave | Data hook |
|---|---|---|
| FISP/LL11 Cycle 10 deadline lookup | Oct 1, 2026 rule change (LL49: 5yr → 6-12yr) + 10A deadline Feb 21, 2027. SERP = 100% restoration vendors; nobody computes address → sub-cycle. | Pure BBL math (block last digit + PLUTO stories) + FISP violation types already ingested (FISPNRF 17,695 etc.). Never infer delinquency from absence. |
| LL152 gas-piping CD deadlines | CDs 4,6,8,9,16 due **Dec 31, 2026**; penalty $1,500/$5,000 (the "$10k" everyone repeats is wrong per 1 RCNY 103-10). | PLUTO community district per BBL. "No violation on record," never "compliant." |
| `/buying-a-rent-stabilized-building-nyc` | Investor cluster verified + essentially unserved (one non-tenant page ranks, total). | The 2007→2017 registration time series as deregulation trajectory — the one framing where our vintage is a moat. |
| Deed-theft 60-second deed check | No deadline; highest conversion intent found — AG's own advice is "check ACRIS yearly." Direct on-ramp to Watch $19 (deed/lien monitoring). | `get_property_history` + liens. Show records, never assert fraud; link OAG/HOPP. |
| `/nyc-bbl-lookup` + glossary quick-wins | HPD classes A/B/C (zero gov presence in SERP), ECB vs DOB, ACRIS document decoder (an Indonesian school subdomain ranks page 1), OATH hearing, lis pendens NYC explainer (seeds the moat topic pre-ingestion). | instant answers |

## Tier 3 — armed & waiting / seasonal / bigger builds

- **Basement legalization (LL126/127 pilot, 15 CDs)** — DOB rules NOT yet
  promulgated; build bilingual pages NOW so they're ranking the day the rule
  drops. Address → in/out of pilot (PLUTO CD) + illegal-conversion history.
- **Good Cause 8.38% page** — DHCR republishes numbers every Aug 1 → guaranteed
  annual refresh wave; auto-fill 3 of 5 exemption tests from data.
- **J-51** — owner deadline ~late Oct 2026 (short-fuse piece) + evergreen renter
  "did your building get J-51 = stabilization evidence" (DOF benefit data).
- **Property-tax assessment history page** — publish December, ride the Jan 15
  NOPV → Mar 1 appeal wave. `dof_property_valuation_and_assessments` 10-yr
  history + comps = product-ready today.
- **DOB/ECB fine calculator (tool)** — ViolationWatch's "calculator" page admits
  none exists. Highest link-earning asset identified.
- **Programmatic: violation-code decoder pages** (~400-2,000 pages; per-code
  frequency + median days-to-cure stats need our full mirror — uncopyable) and
  **per-zip building-conditions pages** (~300-430; beat 311tracker's 50-word
  stubs). Per-building pages: only with quality gates, later.
- **Tax class 2a/2b/2c explainer** — the autocomplete is sub-classes, not
  "1 vs 2"; 2a/2b = ICP's 4-10-unit asset class.
- **NY LLC Transparency Act contrarian piece** — "the state sealed LLC
  ownership; here's what public records still show." Reconcile scope on the DOS
  page first (agents conflicted).

## Killed (don't build) — with reasons

- `/ecb-violations-liens-buying-nyc` — zero autocomplete; already covered on
  /dob-violations; fold into lien page.
- `/nyc-certificate-of-occupancy-check` — no C-of-O data; RegWatch shipped
  July 2026 guide + free tool. Revisit after ingestion; the good angle later is
  "letter of no objection nyc".
- `/nyc-foreclosure-auction-due-diligence` as SEO — the intent is "list pdf"
  (6/10 autocomplete slots), defended by auction.com/Zillow/PropertyShark;
  salvage = conversion tool ("paste an address off any auction list") seeded in
  forums, or ingest nycourts auction calendars (≠ lis pendens).
- "Buying a building with tenants nyc", "why is assessed value so low",
  "tax class 1 vs 2" — zero autocomplete (reframes noted above).
- Any per-landlord scoring/grades/rankings — defamation shape (311tracker and
  openigloo both do it; accept the page-count deficit).
- Fair Chance for Housing, flood disclosure, LL157, Safe Hotels — no hook.

## Cross-cutting

- **AI-crawler moat**: 4 of 10 SEO competitors are blocked or client-rendered
  to AI crawlers; we are SSR + open + llms.txt. Keep it that way (daily GH
  Action now guards it).
- **Absence-of-record trap**: never infer non-compliance from a missing record
  (the false-harassment bug's failure mode; parapets have no filing at all).
- **FCRA line**: building/landlord-level framing only; consumer traffic stays
  free-funnel.
- **Pillar→spoke links before more spokes** (partially fixed 8/9; keep going).
- **Memory corrections captured**: RegWatch = regwatch.nyc, no foreclosure
  data, ~1,031 URLs. New competitor cohort: dwellcheck.io, hpdsigns.nyc,
  mystabilizednyc, rsbl.nyc, amirentstabilized.nyc, leaseswap.nyc, reasier,
  rentsure.
- **Verify before writing**: LL86 Rent Transparency Act primary source; 2026
  lien-sale authorization; LL49 exact rule text after Oct 1.
