# Comprehensive Review — 2026-05-17

**Two days post-Show-HN (2026-05-15). Seven Opus subagents reviewed the live site, repo, and launch plan in parallel.**

This document synthesizes their findings. Companion PR ships a scoped set of safe, reversible improvements from this review.

## Table of Contents

1. [Headline findings](#headline-findings)
2. [Security review](#security-review)
3. [QA / functional bugs](#qa--functional-bugs)
4. [UX/UI/SEO](#uxuiseo)
5. [Copy and messaging](#copy-and-messaging)
6. [Product roadmap / gaps](#product-roadmap--gaps)
7. [Launch plan critique](#launch-plan-critique)
8. [Monetization roadmap](#monetization-roadmap)
9. [What this PR ships](#what-this-pr-ships)
10. [What this PR explicitly does NOT ship](#what-this-pr-explicitly-does-not-ship)

---

## Headline findings

1. **The site is in good shape post-launch.** Security headers solid (CSP no `unsafe-inline`, HSTS preload, X-Frame DENY, Permissions-Policy locked), SQL fully parameterized, magic-link activation atomic and race-free, no secrets in git history. The 2026-04-12 incident did not regress.
2. **One real exploitable security issue:** the Railway hostname is publicly reachable without Cloudflare in front, and `_get_client_ip` (`src/nyc_property_intel/chat.py:146-190`) trusts `Fastly-Client-IP` / `CF-Connecting-IP` / `X-Real-IP` from any caller. Security agent verified end-to-end by curling Railway directly with a spoofed `Fastly-Client-IP: 1.2.3.4` and getting through. This defeats both the anon-IP gate and the signup rate limit. Operationally severe but **out of scope for this PR** — needs DNS + Cloudflare proxy work, not code-only fix.
3. **`/api/signup` accepts reserved TLDs.** QA agent sent `qa-test+probe@nycpropertyintel-qa.invalid` and got `{"ok":true}` — a token was provisioned. This is fixable in code right now and is in this PR.
4. **The hero is text-only.** No screenshot, no Loom, no GIF. Visitors landing from HN have nothing to anchor on in 3 seconds. OG card is the only image on the entire page.
5. **Meta description (200 chars) exceeds Google's truncation limit** (~155 chars). The tail "Open source (MIT)" gets cut on every SERP impression. One-line fix, in this PR.
6. **Sitemap is stale** (`lastmod 2026-05-12` — pre-launch). Crawlers won't recrawl as fast as they should. In this PR.
7. **`analyze_property` still ships "Phase B / Phase C not loaded" dev-copy to users.** Two months post-launch, this reads as "we haven't built this yet" when in fact those phases are loaded. Credibility hit. In this PR.
8. **The activation gap is the real product problem.** Per `docs/pending-todos-2026-05-05.md`: 1.9% activation across ~54 signups, almost all zero-query. The site is "Google for NYC property data" when investors want "Bloomberg Terminal for NYC property data." Roadmap section below proposes the fix (PDF/CSV export → saved reports → watchlists).

---

## Security review

Security agent (Opus). No critical vulnerabilities. One High, five Medium, six Low/Info findings worth documenting.

| ID | Sev | Issue | Location | Action |
|---|---|---|---|---|
| F1 | **High** | Railway hostname public, `Fastly-Client-IP` trusted from any peer — verified bypass | `chat.py:146-190` | **Out of scope for this PR.** Requires Cloudflare proxy + Fastly IP allowlist. File operational ticket. |
| F2 | Medium | Inline `<script>` in `index.html` blocked by CSP — `<details>` auto-expand on hash links doesn't fire on prod | `index.html:989-1023` | **In this PR.** Move IIFE to `js/main.js`. |
| F3 | Medium | Anonymous chat costs Anthropic spend; combined with F1, attacker can drain | `chat.py:893-945` | **Out of scope for this PR.** Wire Turnstile (plumbing exists at `config.py:108`). |
| F4 | Medium | PostHog receives raw email as `distinct_id` — PII / GDPR exposure | 20+ call sites in `chat.py`, `loops_webhook.py` | **Out of scope for this PR.** Needs privacy-policy update first. |
| F5 | Medium | Rate-limit DB check fails OPEN on exception | `auth.py:225-259` | **Out of scope for this PR.** Needs circuit breaker design. |
| F6 | Medium | Anon-rate-limit fails OPEN on DB error | `chat.py:919-935` | **Out of scope for this PR.** |
| F7 | Low | Activation URL (magic-link UUID) logged at WARNING when `LOOPS_CHAT_TRANSACTIONAL_ID` is unset | `chat.py:281-288` | **Out of scope for this PR.** Latent — env var is currently set. |
| F8 | Low | In-process rate-limit buckets per-worker — fine at workers=1 | `chat.py:76-77` | Defer. |
| F9 | Low | `forwarded_allow_ips="*"` compounds F1 | `server.py:553-554` | Defer (fix together with F1). |
| F10 | Low | Turnstile fields accepted but not validated by default | `chat.py:1100-1133` | Defer — pairs with F3. |
| F11 | Low | `ANON_IP_HASH_SECRET` may not be enforced in Railway env | `chat.py:449-470` | **Operational check** — verify Railway env. |
| F12-F20 | Info | All other audited surfaces clean: SQL parameterization, DOMPurify+marked piping, HMAC verification, no shell/exec/eval, .env clean in history | Various | None — confirmed safe. |

**Verdict:** infrastructure-level work (F1 + F9 + F3 + F10) is the next security sprint. Single coordinated change. **Not auto-merge material** — coordinate with DNS.

---

## QA / functional bugs

QA agent (Opus). 20 bugs found, ranked by severity. The most exploitable are in the security findings above; the most user-facing are below.

| ID | Sev | Bug | In this PR? |
|---|---|---|---|
| B1 | Critical | Bare Vercel 404 page (plaintext, no nav home) | ✅ Yes |
| B2 | Critical | `/api/signup` provisions tokens for `.invalid` / `.test` / `.example` / `.localhost` TLDs | ✅ Yes |
| B3 | Critical | BreadcrumbList JSON-LD has 1 item (Google requires ≥2) — Rich Results fails | ✅ Yes |
| B4 | Major | `/chat` has no footer (just 11px muted disclaimer) | ✅ Yes |
| B5 | Major | Twitter card meta tags missing from `/chat` and `/legal` | ✅ Yes |
| B6 | Major | `analyze_property` ships "Phase B / Phase C not loaded" dev-copy to users | ✅ Yes |
| B7 | Major | `backdrop-filter` missing `-webkit-` prefix — broken on iOS Safari | ✅ Yes |
| B8 | Major | PostHog script not deferred, blocks render | ✅ Yes |
| B9 | Major | `search_comps` returns empty array with no actionable hint when zip has no comps | ✅ Yes |
| B10 | Major | `auth-label` hardcodes "3 free queries · No account needed" in HTML before JS runs | ✅ Yes |
| B11 | Minor | Footer disclaimers diverge between `/` and `/legal` | Defer |
| B12 | Minor | "Rent Stabilization 2007–2017" coverage gap not flagged as a known limitation | Defer |
| B13-B17 | Minor | Various polish (og:image on legal, `<details>` find-in-page, activation email vars) | Defer |
| B18-B20 | Polish | Emoji consistency, light-mode `theme-color`, sitemap automation | Defer |

---

## UX/UI/SEO

UX/UI/SEO agent (Opus). 35 findings. The 10 highest-leverage:

| Rank | Issue | In this PR? |
|---|---|---|
| 1 | Trim meta description to ≤155 chars | ✅ |
| 2 | Bump sitemap `lastmod` to today | ✅ |
| 3 | Defer PostHog + add preconnects | ✅ |
| 4 | Trust strip below hero CTA (HN link, GH stars, MIT) | ✅ |
| 5 | Hero screenshot / animated SVG | **Defer** — needs Loom/screenshot capture |
| 6 | Chat empty-state "8–15s / 30–60s" expectation hint | ✅ |
| 7 | Pricing as 3 cards between Tools and Data Sources | **Defer** — bigger HTML change, save for copy pass |
| 8 | "Still pulling" stale-message after 20s in chat | **Defer** — JS change in `chat.js`, separate PR |
| 9 | Mobile sidebar pills → horizontal-scroll chips above input | **Defer** — mobile UX overhaul, separate PR |
| 10 | Tools grid `auto-fill minmax(280px, 1fr)` responsive | ✅ |

---

## Copy and messaging

Copy agent (Opus). The launch-copy voice in `docs/launch-copy/` is sharper, more first-person, and more confident than what made it onto the live site. **Migrate it inward.**

Top recommendations summarized — full before/after table is in the agent transcript.

| Issue | Recommendation |
|---|---|
| Hero H1 "Know the building before you sign, offer, or file" is poetic but vague | Replace with **"NYC due diligence in 90 seconds instead of an afternoon."** (names the time saved). **Deferred to a copy-pass PR** — opinionated change, founder should review. |
| Site says "20+ datasets / 18 tools," launch copy says "22+ databases" | Pick one number. Launch-copy wins (more honest, matches dataset count once derived integrations are counted). **In this PR** — site normalized. |
| Tools section is feature inventory, not benefit story | Each tool card: lead with In/Out + outcome, not technical capability. **Deferred** — full tools-section rewrite. |
| Hero CTA "Try It Now — Free" lacks job-to-be-done framing | "Look up a building →" is the actual JTBD. **Deferred.** |
| Final CTA repeats hero ("Look up a building.") wasting real estate | Escalate to "Run your next deal through it." **Deferred.** |
| No social proof anywhere | Add a strip with: HN link, GitHub stars, "Used by NYC investors closing X deals" (once X > 0). **In this PR** — trust strip with HN + GH stars + license. |
| Missing FAQ entry: "How is this different from PropertyShark?" | Highest-frequency objection from target persona. **Deferred** — needs founder voice. |
| "Phase B / Phase C" copy in `analyze_property` reads as dev-leak to users | Replace with user-facing language ("ACRIS deed records did not return for this BBL"). **In this PR.** |

---

## Product roadmap / gaps

Roadmap agent (Opus). **The 1.9% activation rate is the real problem.** People sign up, do zero queries, leave. The data isn't broken — the product is "lookup-only" when investors want "track + share + decide."

### 30-day roadmap (post-launch consolidation, through ~2026-06-16)

Theme: **make the existing trial tokens generate artifacts they want to share.**

1. PDF + CSV export of `analyze_property` output — branded report investors can forward to their partner / attorney.
2. Saved reports + report history per token (no real dashboard yet, just a list).
3. Fix the 3 partial tools (`search_comps`, `search_neighborhood_stats`, `analyze_property` data-gap copy).
4. "Find all properties owned by X" — uses ACRIS parties data already loaded.
5. Deal-scoring summary at top of `analyze_property` (0-100, 3 red / 3 green flags).

### 60-day (early growth, through ~2026-07-16)

1. Watchlist / portfolio mode.
2. Daily alert digest email on watchlist BBLs (uses existing Loops + cron).
3. Batch lookup (paste 10–50 addresses → CSV out).
4. **Stripe Pro tier ($79/mo) — actually charge people.** PLAN_LIMITS stub already in `auth.py:40-49`.
5. Add Lis Pendens, C of O, DOB ECB to atomic tool surface.

### 90-day (defensibility, through ~2026-08-16)

1. Ownership-network graph (LLC → registered agent → other LLCs → other buildings).
2. Dashboard surface (`/app`) — read-only watchlist + recent reports.
3. RGB / DHCR rent stabilization deeper data + MIH affordability layer.
4. Sentry + read replica + Redis caching layer (1k DAU readiness).
5. Excel add-in + Google Sheets formula (`=NYCPROP(A1, "violations")`).

### Moonshots (don't build now)

1. AI-drafted LOI conditional on findings (Itkowitz-style workflow).
2. White-label tier for NYC RE attorney firms ($499-$999/mo).
3. Predictive distressed-property detection (violations + arrears + LPND + dormant permits).

### Explicitly DO NOT build

1. Multi-city (LA/Chicago/Boston) — kills the depth moat.
2. Listings / "find a property for sale" — wrong ICP.
3. Custom appraisals / AVMs — explicit positioning is due diligence, not appraisal.

---

## Launch plan critique

Launch-plan agent (Opus). The launch copy is **above-average for a solo technical founder** but leaves three big levers unpulled.

### What worked

- Concrete, quantified title ("replaced 8 hours of NYC public-record lookups with one chat message").
- Honest caveats up front (`dobjobs` disclosure) — HN respects this.
- Example queries listed — gives readers a mental model in 5 seconds.

### What was missed

1. **No founder story.** The launch copy says "I built this because I got tired of doing this manually" — that's barely a story. Best Show HNs have a specific origin moment. Layer this in for press / LinkedIn / podcast outreach.
2. **Pricing dodged.** "Paid tiers in development — pricing not yet announced." HN downvotes this. Pre-commit to a number even if tentative.
3. **Attorneys deferred to Sunday onward.** They're the highest-LTV channel. Should have been Day 0/1 outreach.

### Channel ROI

| Channel | Conviction | Why |
|---|---|---|
| **NYC RE attorney LinkedIn DMs** | **Highest LTV** | 1-2 paying users per 20 DMs. Best $/hour. |
| Show HN | High (one-shot) | Done; sustain via comment replies for 48h. |
| The Real Deal / SPONY / Bisnow | High if any lands | 1 mention = 5-10k visitors + permanent backlink. |
| r/mcp | High (small but perfect-fit) | 50-200 visitors, 5-10 self-host installs. |
| BiggerPockets NYC | High (long tail) | Slow burn over 30 days. |
| r/ClaudeAI | Med | Tinkerers, low buying intent. |
| LinkedIn personal post | Med | Authentic > polished. |
| Twitter/X | Low without following | Skip as primary. |

### Missing channels to add

- NYC RE Slack/Discord (NYC PropTech Founders, NY YIMBY, Tech:NYC).
- Brokerage offices (eXp NYC, Compass, Corcoran) — direct sales motion.
- "Ask HN" follow-up at week 3 ("here's what I learned and shipped").
- ProductHunt — scheduled launch in week 3.

### 2-week sustainment plan (starting 2026-05-17)

Full plan in launch-plan agent transcript. Highlights:
- **Today**: HN/Reddit/PostHog metrics pull. 4 attorney LinkedIn DMs. Email Bashir for testimonial.
- **Tomorrow**: Send TRD + Bisnow follow-ups. Personal LinkedIn retro post. 4 more attorney DMs. Cold-email 5 NYC RE brokerages.
- **Tue 5/19**: Record 60-sec Loom (stop deferring). Begin filming **YouTube Video 1**.
- **Fri 5/22**: Publish YouTube V1.
- **Thu 5/29**: ProductHunt launch.
- **Throughout**: 4 attorney DMs/day. Weekly Beehiiv newsletter. Daily PostHog dashboard check.

### Outreach scripts ready to paste

5 cold-DM templates are in the launch-plan agent transcript: attorney LinkedIn DM, small landlord cold email, journalist pitch, brokerage cold email, podcaster DM.

---

## Monetization roadmap

Monetization agent (Opus). Current state: **no revenue mechanism is wired up.** `PLAN_LIMITS` stub exists in `auth.py:40-49` (trial=10/day, pro=500/day, team=2000/day, dev=999999) but no Stripe code, no `/pricing` page, no checkout.

### Recommended tiers

| Tier | Price | Daily queries | Reports/day | Key gates | Target |
|---|---|---|---|---|---|
| **Free (Trial)** | $0 | 10/day × 30 days | 5/day | Watermarked PDF, no CSV, no API | Tire-kickers, students |
| **Pro** | **$79/mo** ($790/yr = 2 mo free) | 500/day | Unlimited | CSV/PDF clean, query history, 1 saved-search alert | Solo investor doing 5-20 deals/mo, broker, appraiser |
| **Team** | **$299/mo** + $89/seat | 2,000/day pooled | Unlimited | Shared workspace, branded PDF, audit log | Attorney firms (2-5 lawyers), broker teams |
| **Enterprise / Private** | **$1.5K–$5K/mo** custom | Custom | Unlimited | Single-tenant, SSO, SLA, custom data | Title cos, mid-firm law, REITs |
| **API / Dev** | **$0.25/call** after $200/mo min | metered | metered | Raw MCP/REST | Proptech devs |

Pro at $79/mo: lands below the "I need approval" psychological threshold ($100) while 6× trial conversion potential. PropertyShark Pro ($60-$170) and CoStar ($400+) are the benchmarks; $79 is positioned on AI synthesis + NYC depth, not raw data volume.

### Introduction timeline

- **Week 1 (now)**: Ship "Pro coming — $79/mo founding price, lock in $59 lifetime for first 100" capture form. No checkout yet. Build email list.
- **Week 2**: Wire Stripe Checkout (hosted) + `/pricing` + `/billing` portal. Single product first.
- **Week 3 (5/31)**: Launch Pro publicly. Email all 30-day trial users with founder discount + expiration warning. Intercept the trial-cohort-1 churn cliff.
- **Week 5-6**: Add Team tier (3-seat $299). Outbound to attorney firms.
- **Week 8-12**: API tier with metered billing on r/MCP + ProductHunt.

### First $1k MRR — 12 Pro subs at $79, by week 8

1. Email existing trial users (founder $59 lifetime offer) → 5-8 converts from first ~200 signups.
2. BiggerPockets NYC case study ("$400K Brooklyn 8-unit save") → 2-3 conversions.
3. r/nycrealestate same play → 1-2.
4. 50 attorney LinkedIn DMs / week → 1-2 Pro subs + 1 Team conversation.
5. YouTube channel intro video → 1-2 trickle over 60 days.

### Key risks

- **Free tier may be too generous** (10/day × 30 days = 300 queries). Counter: shorten trial to 14 days when Pro ships, grandfather 30-day for existing users.
- **NYC RE investors may prefer per-report ($9) over subscription.** Run experiment 3.
- **$79 may underprice the attorney segment.** Validate with segment-priced `/pricing/attorneys` page later.
- **MIT self-host cannibalization** — sophisticated buyers will self-host. Counter: data pipeline is the real moat, not the code. Most won't run their own Postgres + Anthropic key + nightly data refreshes.

---

## What this PR ships

Scoped, reversible improvements only. No auth changes, no rate-limit changes, no operational/DNS changes, no Stripe wiring.

**Frontend (`site/`):**
- `index.html`: trim meta description to ≤155 chars; add preconnects + defer PostHog; fix BreadcrumbList JSON-LD; move inline IIFE to `js/main.js` (CSP compliance); normalize "20+ / 18" → "22+" datasets count.
- `chat.html`: add Twitter card meta + og:image; defer PostHog; add empty-state expectation hint; clear hardcoded `auth-label`; add slim footer.
- `legal.html`: add Twitter card + og:image.
- `css/style.css`: tools-grid `auto-fill minmax(280px, 1fr)` responsive; `-webkit-backdrop-filter` prefix; fix `--bg-alt` reference.
- `css/chat.css`: bump `.chat-disclaimer` font-size from 0.7rem to 0.8125rem.
- `sitemap.xml`: bump all `lastmod` to 2026-05-17.
- **New file**: `site/404.html` — branded 404 page with home link.
- `js/main.js`: add the IIFE moved from `index.html` (auto-expand `<details>` on hash links).

**Backend (`src/`) — copy/validation only, no flow changes:**
- `tools/analysis.py`: replace "Phase B / Phase C" stale dev copy with user-facing language.
- `tools/comps.py`: add actionable `comps_note` when empty result set is returned.
- `loops_webhook.py`: hard-reject reserved TLDs (`.invalid`, `.test`, `.example`, `.localhost`) before MX lookup.

## What this PR explicitly does NOT ship

These need separate follow-ups, founder review, or operational coordination:

- **F1/F3/F9 Cloudflare proxy + Fastly IP allowlist** — requires DNS + Cloudflare config.
- **F4 PostHog email hashing** — needs privacy-policy update first.
- **F5/F6 rate-limit circuit breaker** — design decision.
- **Hero copy rewrite** — opinionated voice change, founder should approve.
- **Pricing-as-3-cards section** — needs the actual pricing decisions locked in.
- **Hero screenshot / Loom** — requires asset creation.
- **PDF/CSV export, watchlists, batch lookup** — 30-day roadmap items.
- **Stripe wiring** — week 2-3 sprint.
- **YouTube video filming** — founder action.
- **Attorney LinkedIn DMs** — founder action (5 ready-to-paste scripts in `docs/launch-copy/` once added).

---

## Cross-references

- Pre-launch QA: `docs/technical-review-findings.md`
- Operational state: `docs/operational-changes-log.md`
- Pre-launch pending items: `docs/pending-todos-2026-05-05.md`
- Launch copy library: `docs/launch-copy/`
- Market validation: `docs/market-validation.md`
- Project memory: `/Users/devtzi/.claude/projects/-Users-devtzi/memory/project_nyc_property_intel.md`
- Launch checklist: `/Users/devtzi/.claude/projects/-Users-devtzi/memory/project_nyc_property_intel_launch_checklist.md`
