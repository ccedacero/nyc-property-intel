# NYC Property Intel — SEO / GEO Review

**Date:** 2026-07-29
**Method:** 5 review lenses — Technical SEO & Indexation, Content/Keyword/Competitor, GEO/AI-search, Conversion & ICP alignment, and Off-page/Authority/Distribution.
**Caveat:** the shared WebSearch/live-SERP budget was exhausted during the review, so competitor-ranking and live-mention claims rest on source reads, registry/GitHub/HN-Algolia fetches, and project memory rather than fresh SERP scrapes. Flagged inline where relevant.

---

## Executive summary

**The on-page and technical foundation is genuinely strong — SEO mechanics are not the bottleneck.** All 8 target pages return 200 with unique titles, single H1s, self-referencing canonicals, clean redirect hygiene (no duplicate-content exposure), and above-average structured data (Organization, WebSite, SoftwareApplication, FAQPage, plus Article/HowTo/BreadcrumbList on guides). The llms.txt is one of the better examples in the wild, robots.txt correctly allows AI crawlers (GPTBot/PerplexityBot verified live 200), and the 8,340-word due-diligence pillar out-depths anything ranking for its query. This site is already **AI-citation-grade**.

**The real problems are three, and none is "fix a meta tag":**

1. **Off-site authority is at the floor.** Near-zero earned backlinks, no Show HN (HN Algolia = 0 hits), no Product Hunt, GitHub at 5 stars, and the *few* directory citations that exist ship **forbidden off-brand copy** ("22+ NYC public-record databases," violating the canonical `20+ datasets · 9 agencies · 18 tools` rule). For a domain with ~0 organic authority, LLMs and Google alike have nothing to triangulate against.

2. **Content targets the wrong audience.** Every money page speaks to renters/buyers/attorneys; the validated ICP is **hard-money/private lenders**, and the word "lender" never appears as an audience anywhere. The single query that unites GEO reach and ICP fit — **foreclosure / lis-pendens lookup** — has no dedicated page (and the data isn't ingested yet, so today's copy *overclaims* it).

3. **SEO isn't the leaky bucket — retention is.** Per project memory the site had ~1 real user and ~0% return rate. More traffic into a funnel with no watchlist/alert and an invisible account value-prop just leaks faster. Fix retention in parallel or SEO wins are wasted.

### Single highest-upside move

**Ship a dedicated "NYC Foreclosure & Lis-Pendens Lookup by Address" page — but only after the NYSCEF/County-Clerk data is ingested.** It is the rare play that maximizes GEO citability *and* ICP fit *and* the product's true differentiator (court data not in ACRIS) at once. Until the data ships, the correct interim move is to **remove the lis-pendens overclaim from the due-diligence page** to protect trust with the exact audience you're selling to.

---

## Findings by lens (sorted by severity within each)

### Lens 1 — Technical SEO & Indexation
*Solid base; work here is optimization, not repair.*

| Sev | Finding | Evidence | Recommendation |
|---|---|---|---|
| MEDIUM | **Broken hub-and-spoke:** the due-diligence pillar links to `/chat` 13× but **0×** to the three tool pages (`/hpd-violations`, `/dob-violations`, `/property-owner-lookup`). Spokes link up; pillar doesn't link down. | grep of pillar HTML: 13× `/chat`, 0 links to tool pages | Add contextual in-body links from the pillar to each tool page at the relevant checklist step; add a shared footer linking all 6 indexable landing pages. Make the cluster bidirectional. |
| MEDIUM | **Sitemap drift.** Hand-maintained; lastmod tops out 2026-06-12 while source files changed 2026-06-28. Homepage `<url>` lacks changefreq/priority the others have. | Live sitemap lastmod set {05-31, 06-06, 06-12}; index.html mtime Jun 28 | Generate sitemap at deploy time from git mtime; apply consistent changefreq/priority. |
| LOW | Tool-page titles run 62–70 chars (truncate ~60px) due to `| NYC Property Intel` suffix; guides (no suffix) are cleaner — inconsistent. | index=70, dob-violations=68, hpd/owner=62 | Trim tool-page titles below 60 chars; front-load the keyword, drop/shorten the brand suffix. |
| LOW | `www→apex` is a 307 (temporary), while http→https is a correct 308. | curl: www → 307 | Make www→apex a permanent 308/301. |
| LOW | WebSite JSON-LD has no `SearchAction` despite the product being a search tool. | schema grep: 0 potentialAction | Add a SearchAction pointing at the `/chat` query flow — sitelinks searchbox eligibility + cleaner AI action. |
| LOW | robots.txt correct today but fragile: enabling Cloudflare "Block AI Scrapers" silently rewrites it at the edge and ignores the allow-list. No monitoring. | file lines 8-11 self-document the risk; GPTBot fetch verified un-rewritten | Add a scheduled GPTBot/PerplexityBot 200-check; document "keep AI-scraper block OFF" in the ops runbook. |

### Lens 2 — Content / Keyword / Competitor
*Content craft is excellent; keyword strategy has a structural ceiling and an ICP mismatch.*

| Sev | Finding | Evidence | Recommendation |
|---|---|---|---|
| HIGH | **No programmatic per-building layer.** JustFix "Who Owns What" and PropertyShark win transactional "[address] violations/owner" long-tail via millions of indexable per-BBL URLs. NYCPI has ~6 static pages and 0 per-building pages, so the majority of address-search volume is uncapturable. | sitemap = 8 static URLs; no per-building pattern | Build a *curated, differentiated* programmatic layer (most-distressed buildings, neighborhood/ZIP distress roll-ups with real narrative) — not stub-per-BBL templates that become index bloat. |
| HIGH | **Content–ICP divergence.** Money pages target renters/buyers/attorneys; ICP is hard-money lenders. No page targets "hard money," "lien search," "pre-funding," "foreclosure due diligence." | HPD FAQ #6 renter-framed; owner page = consumer intent | Add a lender-facing money page + a lien-search page; reframe at least one page's FAQs to investor/lender intent. Sequence honestly — don't publish foreclosure pages until the data ships. |
| HIGH | **Underexploited winnable asset:** "NYC property due diligence checklist" has a fragmented law-firm/blog SERP with no dominant tool, and the pillar already out-depths it — yet it's treated as one page among equals. | pillar: 14 H2s, ~8,340 words, 23 schema entries | Make it the hub; earn backlinks *specifically* to it; spin its LL97/LL11, rent-stab, and C-of-O sections into standalone spokes that link back. Best ROI on the site. |
| HIGH | **Missing top-of-funnel pages:** no "BBL lookup / what is a BBL" (gates every other lookup) and no ACRIS/lien-search page (the lender differentiator). | no bbl*/acris*/lien* files | Ship a BBL explainer + instant lookup and an ACRIS/lien-search page; both slot as pillar spokes. |
| MEDIUM | `property-owner-lookup` targets the head term "who owns this building nyc" that JustFix owns by brand + authority; the page is also thin (4 H2s). | 4 H2s, head-term keywords | Reposition to "find the real owner behind an LLC" (HPD-registration-contact angle JustFix under-serves); add LLC-piercing / shell-pattern / deed-red-flag depth. |
| MEDIUM | `dob-violations` and `property-owner-lookup` (4 H2s each) are thinner than the HPD page and pillar. | H2 counts | Deepen both to the HPD pattern (real-BBL worked example, expanded FAQ, ECB-penalty/Local-Law-153-lien depth for DOB). |

### Lens 3 — GEO / AI-search
*On-page GEO is above-average; the ceiling is off-site corroboration and topical coverage.*

| Sev | Finding | Evidence | Recommendation |
|---|---|---|---|
| HIGH | **Near-zero off-site corroboration.** LLMs cite brands they can triangulate. The product is an MCP server yet appears absent from the registries LLMs retrieve from (mcp.so, Glama, Smithery, PulseMCP, awesome-mcp-servers). | ~1 real user (memory); only 1 GitHub link found | Treat off-site seeding as the #1 GEO lever: submit to MCP directories, PR awesome-mcp-servers, enrich the GitHub README with quotable facts, seed authentic HN/Reddit/BiggerPockets mentions, build "PropertyShark/RegWatch alternative" pages. |
| HIGH | **No dedicated foreclosure/lis-pendens page** despite it being the ICP's #1 query *and* the product's court-data differentiator. Buried in a 31-min guide won't get cited. | grep: lis-pendens only inside the pillar | Ship a dedicated foreclosure/lis-pendens page (TL;DR + Quick-Answer + FAQPage + HowTo) — **after** the data ships. Highest GEO×ICP overlap. |
| MEDIUM | **Content staleness.** For a live-data product, everything is ~6-7 weeks old (dateModified 06-12/13). AI weights recency; reads as dormant. | llms.txt "Updated 2026-06-13" | Monthly refresh cadence: bump dateModified + llms.txt + sitemap lastmod, add a dated "what's new in the data" note per guide. |
| MEDIUM | AI-crawler access is one Cloudflare toggle from silent death. | robots.txt lines 8-11; live 200 today | Scheduled curl-as-AI-bot 200 check + confirm no AI-scraper managed rule. |
| LOW | No `llms-full.txt` (404). | live GET → 404 | Generate one inlining the due-diligence + eviction guide text. |
| LOW | Canonical stat not stated as one atomic sentence; count phrasing drifts (llms.txt "20"/"18" vs home "20+"). | "9 city agencies" appears once | Add one bolded sentence everywhere: "NYC Property Intel covers 20+ official datasets across 9 NYC city agencies via 18 AI tools." Normalize all counts. |

### Lens 4 — Conversion & ICP alignment
*The funnel converts the wrong visitor and gives the right one no reason to return.*

| Sev | Finding | Evidence | Recommendation |
|---|---|---|---|
| **CRITICAL** | **ICP invisible in the hero.** H1 "Know the building before you sign, offer, or file" maps to renter/buyer/attorney — not lender. Audience cards = Investors/Attorneys/Brokers/Developers/Renters; **no Lender card.** "lender" appears only as a data field. | index.html H1 + audience grid; grep "lender" = data fields only | Add "fund" to the hero verb set and a "For Lenders / Hard-Money & Private" audience card speaking their job (open violations, senior liens, tax-lien exposure, distress signals before you wire). |
| HIGH | **No retention loop.** Only return surface is static `/reports`. **No watchlist/alert/monitoring** — the exact feature a lender returns for (new lien/violation/lis-pendens on a collateral building). Directly explains ~0% return. | reports.html + /chat: no alert markup | Build "Watch this building" (email/push on new lien, violation, ECB judgment, lis-pendens). Ship an email-capture waitlist button *now*, before the full feature. |
| HIGH | **Account value-prop invisible at moment of intent.** Auto-saved reports (the retention benefit) live on a noindex page reachable only after signup. | reports.html noindex; /chat no signup prompt | After a user's first report in `/chat`, prompt "Create a free account to save this + get notified if anything changes." |
| HIGH | **Off-ICP traffic.** Two of five SEO pages (owner-lookup, eviction-history) + the "For Renters" card court low-WTP, high-churn curiosity traffic. | page titles + audience card | Keep them for links, but demote from the funnel top; build a lender-intent cluster and route buyer/investor traffic as the money path. |
| HIGH | **ICP-critical overclaim.** Due-diligence page claims the lien check covers "ACRIS mortgages and **lis pendens**" — but lis pendens is NYSCEF court data, **not ingested**. A lender trusting "no lis pendens on record" and funding against a property already in foreclosure is the worst-case trust failure. | pillar lines 89, 253 | Remove/qualify the lis-pendens claim until NYSCEF ingestion ships; then prioritize ingesting it. |
| MEDIUM | **No lender monetization/capture.** Paid tiers are "in development — sign up and we'll reach out." No lender plan, no "talk to us." | index.html FAQ | Add a "For lending teams — API + monitoring" capture (Calendly/waitlist) so high-WTP lenders self-identify at the moment a report impresses them. |
| MEDIUM | Meta descriptions are feature lists with no ICP/decision hook. | index.html meta | Lead with the decision moment: "Screen any NYC building before you fund, offer, or file…" |

### Lens 5 — Off-page / Authority / Distribution
*The highest-leverage, lowest-cost surface — almost entirely unexecuted.*

| Sev | Finding | Evidence | Recommendation |
|---|---|---|---|
| HIGH | **Effectively zero earned backlinks.** Only confirmable referrers are auto-ingested MCP directories. | GitHub 5★/1 fork; HN Algolia 0 hits | Make earned links the #1 off-page project: awesome-list PRs → manual directory submissions → coordinated launch → 1-2 guest posts. Target 8-12 distinct referring domains in 30 days. |
| HIGH | **The few citations that exist ship forbidden copy.** Official MCP registry (v0.1.1) and Glama describe it as "22+ NYC public-record **databases**" — violates the canonical `20+ datasets · 9 agencies · 18 tools`. | registry + Glama = "22+ databases"; GitHub About is correct | Publish a new server.json with the canonical string; claim/edit Glama + PulseMCP to match. One manifest fix propagates to ingesters. |
| HIGH | **No Show HN / Product Hunt ever.** The two canonical OSS-dev-tool authority events, both unexecuted. | HN Algolia nbHits=0 both queries | Run ONE coordinated launch day, ICP-framed ("Show HN: MCP server for NYC property due diligence"), pre-seed a few stars. |
| MEDIUM | **MCP directory gaps:** absent (or unconfirmed 403/429) on mcp.so, Smithery, cursor.directory, mcpservers.org, awesome-mcp-servers lists. | confirmed only registry/Glama/PulseMCP | Submit to each; PR the two big awesome-mcp-servers repos (real-estate/data category). |
| MEDIUM | **GitHub repo under-leveraged** (5★, no reciprocal directory links, no "who it's for" section). | repo metadata | Add "Listed on" + badges, pin on profile, add "who it's for (NYC lenders & investors)," ask early users to star. |
| LOW | Dormant `@nycpropertyintel` X handle in schema sameAs adds no authority. | index.html sameAs | Make it minimally active (post the launch) or drop it. |

---

## Prioritized action list

### Quick wins (< 1 day each)
1. **Fix the "22+ databases" copy on the MCP registry, Glama, and PulseMCP** → canonical `20+ datasets · 9 agencies · 18 tools`. One manifest fix propagates; stops the forbidden number reaching every LLM/directory browser. *(Off-page; also a MEMORY copy-rule violation.)*
2. **Remove/qualify the lis-pendens overclaim** on the due-diligence page (lines 89, 253). *(Conversion — protects trust with the paying ICP. Do this even before the SEO work.)*
3. **Close the pillar→spoke gap:** add contextual links from the due-diligence pillar to the three tool pages + a shared footer across all 6 landing pages. *(Technical — bidirectional link equity in one edit.)*
4. **Add a "For Lenders / Hard-Money" audience card + "fund" to the hero.** *(Conversion — ICP legibility in 5 seconds.)*
5. **Add a "Watch this building" email-capture button** in `/chat` and `/reports` (waitlist now, feature later). *(Conversion — the retention primitive; turns single sessions into a lead list.)*
6. **Submit the MCP server to mcp.so, Smithery, cursor.directory, mcpservers.org + PR awesome-mcp-servers.** *(Off-page/GEO — free, durable, on-brand referring domains.)*
7. **Bump freshness signals** (dateModified, llms.txt "Updated," sitemap lastmod) + add an atomic quotable stat sentence everywhere. *(GEO.)*
8. **Scheduled curl-as-GPTBot/PerplexityBot 200 check** so a Cloudflare toggle can't silently kill AI access. *(GEO/Technical.)*

### Strategic bets
1. **Ingest NYSCEF/County-Clerk lis-pendens + foreclosure, then ship a dedicated "NYC Foreclosure & Lis-Pendens Lookup by Address" page.** The one move that maximizes GEO citability, ICP fit, and the product's court-data differentiator simultaneously. *(This is the answer to "the single highest-upside move.")*
2. **Build the watchlist/alert engine** (new lien/violation/ECB/lis-pendens on a saved BBL). The missing retention primitive — the actual fix for the ~0% return leaky bucket, and the feature lenders will pay a subscription for.
3. **Run ONE coordinated launch day (Show HN + Product Hunt), ICP-framed,** plus a focused backlink push at the due-diligence pillar (RE-attorney/investor guest posts, brokerage newsletters).
4. **Stand up a lender-intent SEO + GEO cluster** ("collateral due diligence for hard-money lenders," "check a borrower's building before you fund," "NYC lien search") written as citable, question-shaped passages — lean GEO toward the audience that pays, not the renter queries the site currently ranks for.
5. **Decide deliberately on a curated programmatic per-building layer** (most-distressed buildings, neighborhood distress roll-ups with real narrative) — the only route to the long-tail address volume, scoped to avoid thin-content index bloat.

---

## Honest bottom line

**SEO is not the bottleneck — the mechanics are already good and, per project memory, the product's real problem was retention (leaky bucket), not traffic.** Pouring more search/AI traffic into a funnel that (a) speaks to renters instead of lenders, (b) overclaims the lender's #1 data point, and (c) has no return loop will just leak faster. The correct sequence is: **fix the copy accuracy and ICP legibility (days) → add the retention primitive and ingest lis-pendens (weeks) → *then* turn on the GEO/off-page distribution flywheel** so the traffic that arrives lands on an ICP-aligned funnel it will actually stick to. The GEO/AI-citation channel is the right long-term bet for a low-authority domain — but it pays off only once the off-site corroboration exists and the funnel is worth keeping.
