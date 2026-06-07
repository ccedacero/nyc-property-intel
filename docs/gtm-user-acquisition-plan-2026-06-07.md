# NYC Property Intel — State Assessment & User-Acquisition Plan

**Date:** 2026-06-07
**Author:** Synthesis of a 3-lens parallel review (demand/channels · activation/retention · named outreach targets) over verified production data.
**Branch context:** repo on `fix/suppress-warning-for-landmarks`; live site = Vercel (`site/`), backend/MCP = Railway.

---

## 0. TL;DR — the brutal truth (revised 2026-06-07 after scrubbing internal/QA/bot accounts)

**You have almost no proven organic traction yet, AND the funnel you'd pour traffic into has two self-inflicted holes. The "we have demand, we just can't convert it" story is NOT supported by the data — scrubbed, the data says barely any real prospect has ever arrived.**

- The product is **technically done and live**. That is not the bottleneck.
- The raw dashboard showed "103 signups, 1 REAL user." **After removing QA/test accounts (22), your own founder accounts (`ccedacero@`, `cristiancedacero@`, `devtzitest@`), the friend test (Bashir), and known disposable-domain bots (4): exactly 3 genuine external humans have EVER run a single query, and exactly 1 of them (`matigonzalez09@`, plausibly an acquaintance) ever came back for a second day.** The dashboard's headline "1 REAL user" was literally counting *your own* accounts. The single most active account in the whole system is your own test rig (`devtzitest@`, 32 calls over 13 days).
- 73 of the 78 external emails made **zero** queries — overwhelmingly a May 5–6 scripted CLI token-harvest wave, not real hand-raisers.
- Two self-inflicted holes are draining ~98% of everyone who arrives:
  1. **Delivery friction.** The headline path is "add an MCP server + bearer token to Claude Desktop/Code." Almost none of your ICP (investors, attorneys, brokers) uses Claude Code or will configure MCP. They get a token and hit a dead end. This is most of the 81 zero-query signups.
  2. **No retention surface.** The web `/chat` is a vending machine — ask, get an answer, and nothing persists. No saved report, no PDF, no watchlist, no reason to come back.
- Meanwhile the last month of effort drifted into a **solo SEO content play** — a 3–6 month lagging channel that does nothing about either hole.

**The sequence is non-negotiable:** fix the bucket (Phase 0, ~2–4 weeks, mostly copy + small builds) → then turn on high-LTV outreach (Phases 1–3) → wire Stripe (~day 75) so you can finally tell vanity from revenue. Pouring outreach into the current funnel would just manufacture more zero-query ghosts and burn the Anthropic spend cap.

---

## 1. Current state (verified 2026-06-07)

| Dimension | State | Evidence |
|---|---|---|
| Product / tech | **Solid, live.** 18 tools over 20+ NYC datasets; web `/chat` + MCP server; clean security; SEO blog published. | `git log`, live site HTTP 200, comprehensive review 2026-05-17 |
| Users (raw) | 104 distinct signup emails; dashboard reports "1 REAL, 9 LIGHT, 81 ZERO, 12 bots." | `scripts/signup_dashboard.py` (RAILWAY_DB prod) |
| **Users (scrubbed)** | **3 genuine external humans ever ran a query** (`matigonzalez09@`, `adam@adamaaronarchitect.com`, `svarughe@hotmail.com`); **1 ever returned a 2nd day**. 73/78 external emails = zero-query (bot/CLI harvest wave). Dashboard's "REAL/LIGHT" were inflated by founder + QA + a friend + disposable-domain bots. | direct prod query, this session |
| Internal noise | Founder accounts `ccedacero@` + `cristiancedacero@` (7 calls/4d) + test rig `devtzitest@` (32 calls/13d — most active acct in the system) + friend `bashiralhanshali@` (66 calls/1d, now excluded). | direct prod query |
| Launch | The May 15 "Show HN" effectively **never landed** — no traffic/signup spike. | dashboard time-series, launch checklist |
| Delivery | **MCP-first framing is the core friction.** Web `/chat` is the only ICP-viable path but sits behind signup/anon gates. | README install path, dashboard `cli`-source ZEROs |
| Monetization | **None wired.** No Stripe, no `/pricing`. `PLAN_LIMITS` is a stub in `auth.py:40-49`. | comprehensive review §monetization |
| Cost ceiling | Anthropic API spend cap is a real constraint on free/anonymous usage. | launch checklist blocker note |

---

## 2. Core diagnosis & sequencing

```
                 You have been here  ───────────────►  SEO content (3–6mo lag, fixes neither hole)
                                                         ✗ wrong end of the bucket

   REALITY:   [ traffic ]  →  ★HOLE 1: MCP delivery friction (activation)  →  first query
                          →  ★HOLE 2: no persistence (retention)           →  second session → habit → $$$

   CORRECT ORDER:
     Phase 0  Patch HOLE 1 (cheap: copy + flow)  →  Patch HOLE 2 (medium: reports/exports/watchlist)
     Phase 1  Turn on warm, high-LTV outreach (attorneys + warm network)
     Phase 2  Communities + one trade-press shot
     Phase 3  Wire Stripe; double down on what converted; prune the rest
```

**Gate rule:** do not scale any cold channel until ≥40% of *warm, hand-walked* users hit ≥3 queries. If they won't activate when you're holding their hand, no amount of traffic fixes it.

---

## 3. Phase 0 — Fix the bucket (Weeks 0–4) — **DO THIS FIRST**

### 3a. Activation fixes (mostly copy + small flow changes)

| # | Change | Type | Effort | Why |
|---|---|---|---|---|
| 1 | **Make `/chat` the single hero CTA sitewide.** Demote MCP to a "For Claude / developers" footnote. | copy | hours | Kills the #1 friction. ICP pastes an address into a chat box; it won't edit JSON config. |
| 2 | **Issue the trial token in-session** on email submit; magic link becomes a *backup/continuity* email, not a blocking mid-task gate. | flow | 0.5–1d | The mid-task email round-trip drops people the moment they're getting value. `chat.py` ~lines 1003–1045. |
| 3 | **Guarantee one full DD report before any wall** + loud empty-state primer ("Ask about any address — violations, liens, sales, ownership across 20+ city datasets in ~30s"). | copy/flow | hours | The full `analyze_property` report IS the aha moment. Make sure everyone reaches it. |
| 4 | **Targeted re-activation email — to the handful of *real* zero-query signups only** (e.g. `mark@aptsny.com`, `conrado@squareup.com`, `anask7304@`), NOT the bot wave. "We made it dead simple — no setup, just ask here →" + chat deep-link. | email | hours | ⚠️ Corrected: the "81 zero-query" are ~73 bots/CLI-harvest + a few reals. Hand-pick the ~5 genuine ones; the broadcast-to-81 idea was wrong (you'd be emailing bots). Low effort, small but real upside. |
| 5 | **Instrument the funnel** in PostHog: landing → first query → ≥3 queries → day-2 return; fire `first_dd_report_completed`. | build | hours | You're blind past "signup" today. Can't manage what you can't see. |

### 3b. Retention fixes (small/medium builds — turn the vending machine into a tool with memory)

| # | Change | Type | Effort | Why |
|---|---|---|---|---|
| 6 | **Saved Deal Reports** — persist each `analyze_property` as a record with a permanent link (`/r/<id>`) + a bare "Your reports" list. | build | 2–3d | The only reason a user returns is that their work now *lives* somewhere. |
| 7 | **PDF + CSV export** of the report. | build | 1–2d | The investor forwards the PDF to a partner/lender; the attorney attaches it to a file. **The share IS the retention + referral loop.** |
| 8 | **Deal-score header** (0–100, 3 red / 3 green flags) atop every report. | build | 1d | Turns raw data into a decision artifact — the thing they screenshot and share. |
| 9 | **Watchlist** on a BBL or owner/LLC (ACRIS parties data already loaded — competitors don't surface owner-level cleanly; this is your unfair advantage). | build | 2d | Sets up the founder-free retention engine below. |
| 10 | **Weekly digest email** (cron diff → Loops): "3 updates across your watched buildings — 12 Broad St picked up 2 new HPD violations; ABC Holdings LLC filed a deed on a new property." Sends only when there's a delta. | build | 2–3d | New violations/filings/sales are exactly what triggers a deal or a legal action. This is what converts a tool into a subscription — and the natural on-ramp to $79 Pro. |

### 3c. Define your metrics (instrument now)

- **Aha event:** first completed full DD report (`analyze_property`) on a real BBL. (A single `lookup_property` is *not* aha.)
- **Activated user:** ran ≥1 full DD report within 24h of first touch **AND** produced/returned to ≥1 artifact (export or report-link open).
- **Habit/retention user:** your existing "REAL" definition (≥3 calls over ≥2 days).

---

## 4. Lifecycle email sequence (Loops is already wired)

| # | Timing | Trigger | Purpose | CTA |
|---|---|---|---|---|
| 1 | Instant on signup | token issued | First DD report NOW, no setup | 3 copy-paste starter questions as chat deep-links. **No mention of MCP.** |
| 2 | +24h, only if no report yet | zero-query at 24h | Rescue the not-yet-activated | "Here's the 30-second version →" one-click example deal |
| 3 | After first report | `first_dd_report_completed` | Convert activation → retention surface | "Here's your PDF — forward it. Want alerts when this building changes? Watch it →" |
| 4 | Weekly, only if deltas | cron diff (§3b #10) | The core retention loop | "3 updates across your watched buildings" |
| 5 | Day ~25 of 30-day trial | trial nearing expiry | Intercept the churn cliff | "Trial ends in 5 days. Keep your watchlist + exports for $79/mo (founding $59)." |

---

## 5. Channel strategy (Phases 1–3) — ranked by revenue-bearing users per founder-hour

| # | Channel | ICP fit | Effort | Realistic 90-day outcome | Verdict |
|---|---|---|---|---|---|
| 1 | **NYC RE attorney 1:1 outreach** (LinkedIn DM + email) | Highest LTV | High | 3–8 activated, 1–2 paying convos | **OVER-INVEST HERE** |
| 2 | **Concierge "I'll run it for you" report-drops** in niche communities | High | Med | 5–15 activated, durable referrals | Removes 100% of install friction |
| 3 | **Trade press** (TRD / Bisnow / SPONY) — one good data-story pitch | High if it lands | Med | Binary: 0 or a real spike + backlink | One clean shot, then move on |
| 4 | **r/mcp + RE subreddits** (value-first) | Med | Low–Med | 3–10 activated trickle | Good long tail; r/mcp = config-willing devs |
| 5 | **Founder build-in-public** (LinkedIn/X teardowns) | Med | Med (ongoing) | Compounding proof; few direct early | Feeds channels 1–3 with social proof |
| 6 | **SEO pillar+spoke** (current drift) | Med | High (sunk) | ~0 in 90 days | **DEMOTE to ≤1–2h/wk background** |
| 7 | **Show HN (2nd)** | Med | — | Spent unless genuinely new hook | Only re-fire with "we removed the MCP requirement" |
| 8 | **Paid ads** | — | — | Avoid | $0 budget + leaky bucket = lighting money on fire |

**Stop / avoid:** SEO-as-primary (wrong tool for an activation problem — it's where a technical founder hides from scarier, higher-ROI attorney conversations), paid ads, a naked second Show HN, and treating r/mcp upvotes as ICP demand.

**Channel-enabling assets to build first (not product roadmap):** (a) a **60–90s Loom** of `/chat` answering one real DD question; (b) one **redacted before/after teardown** ("14 open violations on a $2.1M Crown Heights deal in 4 min vs. 4–6 hrs across 10+ sites"); (c) a **"I'll run one property free for you"** offer mechanic.

---

## 6. WHO to reach out to (named targets, tiered)

**Tier key:** P0 = this week · P1 = next 2–4 weeks · P2 = backlog.
**Carry into every message:** *"Due-diligence screening, not appraisal or legal advice. 20+ NYC datasets across ~10 city systems → one AI query. Manual version = 4–6 hrs/property; this = 30 seconds."* For high-LTV targets, lead with the **design-partner offer: free unlimited access in exchange for a testimonial + 2–3 feedback calls.**

### Segment 1 — NYC RE attorneys & title pros (highest LTV — START HERE)
- **Solo & 2–5 attorney NYC RE transactional firms** — they personally do the ACRIS/DOB grind; fastest yes. Find via LinkedIn title search "Real Estate Attorney" + "New York". **(P0)**
- **Authors of the DD blogs already in your distribution CSV** — they've self-identified as caring about this exact check: **Avenue Law Firm**, **Adam Leitman Bailey PC**, **Belkin Burden Goldman**, **Gartenberg Decker / GDB Law**, **Sishodia PLLC**, **Moshes Law**, **Scarinci Hollenbeck**. Hook: "You wrote the definitive piece on [open violations / ACRIS encumbrances / overcharge risk]; I built the AI that runs that check in one query — free, as a design partner." **(P0: Avenue, ALB, BBG / P1: rest)**
- **Title / abstract firms** (complement, not competitor — you screen pre-offer, they clear at closing): **Home Abstract Corp**, **ProTitleUSA**, **Real Title Services**, **ATG**. **(P1)**

### Segment 2 — Small/mid multifamily investor communities (value-first, never link-drop)
- **SPONY — Small Property Owners of NY** (info@spony.org) — member-benefit / lunch-and-learn. **(P0)**
- **BiggerPockets NYC subforum** (biggerpockets.com/forums/521) — build karma with 5 helpful answers, then one permitted "I built this" post. **(P0 karma / P1 post)**
- **CHIP** (chipnyc.org) and **RSA** (rsanyc.net, ~25k owners) — rent-stabilized owner audiences, perfect for `get_rent_stabilization`. **(P1)**
- **r/nycrealestate** (P0), **r/realestateinvesting** + **r/RealEstate** (remote-DD angle, P1), **r/AskNYC/r/nyc** (P2, anti-promo).
- **NYREIA & local REIA chapters**, **NYC RE Meetup.com groups**, **NYC landlord Facebook groups**, **REI Discord/Slack servers** — present/demo, ask admins for a permitted post. **(P1)**

### Segment 3 — Journalists / trade press (pitch a DATA story, not "I launched")
- **The Real Deal** (tips@therealdeal.com) **(P0)**, **Bisnow** (tips@bisnow.com) **(P0)**.
- **THE CITY**, **Gothamist** (data-map angle), **Crain's NY**, **Brick Underground**, **CRE Daily** newsletter, **City Limits / Brownstoner / City & State** **(P1–P2)**.
- Hook examples: *"X% of stabilized Bushwick buildings show warehousing signals,"* *"distressed/foreclosure trend from ACRIS+liens this quarter."* Offer the underlying city-sourced data exclusively.

### Segment 4 — PropTech / dev communities (for the MCP/API)
- **r/mcp** (P0), **r/ClaudeAI** (P0) — gated on the Anthropic spend cap being raised + chat re-verified.
- **BetaNYC Slack** (civic-tech, exact data domain — re-engage with an update), **Techqueria Slack** (Latino founder angle), **Tech:NYC**, **NYC PropTech Founders**. **(P1)**
- **Maintain MCP directory listings** (Official Registry, Glama, mcp.so, Smithery; reply to punkpeye PR #6041, Cline #1527). **(P1)**

### Segment 5 — Podcasts / YouTube + bilingual edge
- **BiggerPockets Podcast/blog**, **TRD/Bisnow podcasts & events** (P1).
- **Spanish-language NYC RE creators / Latino-investor podcasts** — founder's differentiated edge; underserved Spanish-speaking NYC investors; co-produce in Spanish. **(P1)**

### Segment 6 — Partnership channels (one org > 100 cold DMs)
- **1031 Qualified Intermediaries** — **1031 Specialists** (warmest; their content names skipped checks), **IPX1031**, **Deferred.com**. The 45/180-day clock = forced DD compression = your sharpest wedge. Co-marketing/referral. **(P0: 1031 Specialists / P1: rest)**
- **SPONY / CHIP / RSA** affinity deals (P1); **REBNY** (P2); **attorney bar assoc. / CLE** via a design-partner attorney (P2); **Ariel Property Advisors** research-team referral (P2).

### Segment 7 — Warm network (convert FIRST — nearly free yeses)
- **Your 3 confirmed real users — interview ALL of them within 48h (P0, #1 priority).** These are your entire real-demand signal: `matigonzalez09@gmail.com` (11 calls, multi-day — your only *retained* user, start here), `adam@adamaaronarchitect.com` (NYC architect — ICP-adjacent; zoning/permits/C-of-O are their daily work), `svarughe@hotmail.com` (3 calls). Email each personally: *why did you try it, what did you look up, what was missing, would you pay, can I quote you?* Offer free extended access as a design partner. This replaces the Bashir testimonial (he was a friend test — exclude from any social-proof claim). **(P0)**
- **eXp Referral Division / NY RE license network** — warm intros to NYC agents who do DD and refer investor clients. **(P1)**
- **Bilingual / first-gen Latino NYC RE community** — personal outreach in Spanish to meetups/WhatsApp groups: "AI DD tool, works in Spanish, built by one of us." **(P1)**
- **Your own LinkedIn + 1st/2nd-degree attorney connections** — DM the attorneys already in your network before cold-DMing strangers. **(P0)**

---

## 7. The first 10 outreach actions THIS WEEK (in order)

1. **Email your 3 confirmed real users** (`matigonzalez09@`, `adam@adamaaronarchitect.com`, `svarughe@hotmail.com`) — why they tried it, what they looked up, what was missing, would they pay, can you quote them. matigonzalez first (he came back). This is your first real validation + testimonial.
2. **Record a 60-second Loom** of one Brooklyn building walkthrough — the asset every message below links to.
3. **DM 4 NYC RE attorneys already in your LinkedIn network** with the design-partner offer.
4. **Email the authors of Avenue Law Firm's CRE DD checklist + Adam Leitman Bailey PC** with the "you wrote about this exact check" hook.
5. **Email 1031 Specialists** proposing a referral relationship around "don't skip lien/violation/stabilization checks under the 45-day clock."
6. **Email SPONY** (info@spony.org) — free member-benefit DD tool + lunch-and-learn.
7. **Post 5 genuinely helpful answers** on BiggerPockets NYC + r/nycrealestate DD threads (karma, zero promo).
8. **Pitch The Real Deal** (tips@therealdeal.com) one exclusive ACRIS+liens distressed-trend data nugget.
9. **Pitch Bisnow** (tips@bisnow.com) — "solo founder built an AI DD tool over 20+ NYC datasets."
10. **Publish a founder-voice LinkedIn + Twitter post** with the Loom, tagging @AnthropicAI / @claude_code.

*(Show HN / r/mcp / r/ClaudeAI are P0 in spirit but gated on the Anthropic spend cap being raised + live chat re-verified.)*

---

## 8. Reusable cold-message templates

**A) Attorney DM / email (design-partner)**
> Hi [Name] — I read your piece on [checking open violations / ACRIS encumbrances / overcharge risk]. I'm a solo NYC-based developer and I built a tool that runs that exact pre-contract check — ACRIS deeds & liens, HPD/DOB violations, DHCR stabilization signals, evictions — across 20+ city datasets in one AI query. The manual version takes 4–6 hours; this takes 30 seconds. It's due-diligence screening, not a title search or legal advice. I'm not selling anything — I want 3–5 NYC RE attorneys as design partners: free unlimited access for one 20-min feedback call and a short testimonial if it's useful. Worth a look? 60-sec demo: [Loom].

**B) Investor-community post (value-first)**
> A lot of NYC pre-offer questions here boil down to "how do I check violations / liens / rent-stabilization before I commit?" Here's the manual path across the city sites — [genuinely useful step-by-step]. I got tired of the 4–6 hr-per-property grind so I built a free tool that does the whole pull in one query (ACRIS, HPD, DOB, DHCR signals, evictions, taxes). It screens, it doesn't replace your attorney or title search. Happy to run a building for anyone in the comments — drop an address. [Link in profile.]

**C) Journalist data-story pitch**
> Hi [Reporter] — I run an AI tool aggregating 20+ NYC public-record datasets (ACRIS, HPD, DOB, DHCR, evictions, liens). I pulled a pattern your readers would want: [e.g., "X% of rent-stabilized buildings in [area] show warehousing signals"]. Happy to give you the underlying data exclusively and walk you through the methodology — all sourced from city records. Interested?

---

## 9. Metrics, kill-criteria, and the monetization gate

**Weekly leading metrics:** landing→first-query rate (target >25%); first-query→≥3-queries activation (1.9% → ≥15–20% on warm traffic); day-2 return; per-channel reply→demo→activated; cost per activated user (Anthropic $ ÷ activated).

**Kill-criteria:** attorney DMs — rework if <2 demos per 40 personalized DMs (fix the Loom/offer first); community drops — drop a community if 3 quality posts → 0 activated; trade press — one shot, shelve if no reply in 2 weeks; SEO — freeze to background-only if no high-intent spoke ranks top-10 or drives an activated user by day 90.

**Monetization gate (~day 75):** wire **Stripe Checkout + `/pricing`** once you have ≥1 user who'd pay. Recommended **Pro $79/mo** (founding $59), Team $299/mo, API metered. You cannot judge any channel's ROI while everything is free — free users are not a demand signal. Email all trial users with a founder discount + trial-expiry warning to intercept the churn cliff.

---

## 10. The one-paragraph version

You have ~3 genuine external humans who've ever used this and ~zero retention — so treat the next phase as **validation from near-zero, not optimization of existing demand.** Spend 2–4 weeks making `/chat` the front door (demote MCP), issuing tokens in-session instead of behind a mid-task email wall, guaranteeing one free DD report, and shipping saved reports + PDF/CSV export + a watchlist with a weekly "what changed" digest. Then put your hours into warm NYC RE attorney 1:1 outreach (design-partner offer) and concierge "I'll run it for you" report-drops in SPONY/BiggerPockets/CHIP — the goal is to get the **first 10 real ICP humans** through a frictionless path and watch whether they activate and return. One data-story pitch to The Real Deal/Bisnow. Wire Stripe by ~day 75 so you can finally measure revenue, not vanity. (Bashir was a friend test, not a user — exclude him from any testimonial/social-proof claim.)
