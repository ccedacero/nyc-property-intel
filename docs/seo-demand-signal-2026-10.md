# SEO Demand Signal — Oct 2026 (parked finding)

**Source:** Google Search Console export, last 28 days (Sep 16 – Oct 13 2026).
**Status:** PARKED-WITH-GATE. Captured here so it isn't lost; **build nothing until the gate below is met.**
**Panel:** 3-Opus review (demand / revenue / discipline), 2026-10-15 — unanimous.

## Headline numbers
239 clicks · 15,258 impressions · 1.57% CTR · avg position ~8.6 · 770 queries · 97% US.
SEO is working — content pages rank page-1 and pull real, growing traffic.

## The signal: we rank page-1 but capture ~0 clicks on high-intent ownership queries
| Query | Impr | Pos | Clicks | Intent guess |
|---|---|---|---|---|
| 175 w 76th st llc ownership ny dos acris | 1,161 | 2.3 | 0 | title/legal/skip-trace — LLC-piercing |
| 175 west 76th street llc ownership manhattan ny acris | 1,154 | 6 | 0 | same (near-dup = likely one researcher/scraper) |
| 15 west 26th street llc owner ny dos acris | 378 | 9.2 | 0 | LLC-piercing |
| 18 west 27th street llc deed chain of title acris | 221 | 9.7 | 0 | chain-of-title (attorney/abstractor) |
| 324 east 86th street owner records dof acris bbl | 217 | 9.9 | 0 | entity resolution |
| hpd violation lookup | 454 | 8.5 | 0 | commodity/consumer |
| how to find who owns a home | 107 | 8.7 | 0 | mixed/consumer |

Top pages by clicks: `/nyc-eviction-record-check` (77), `/hpd-violations` (64), `/property-owner-lookup` (38 on 5,658 impr = 0.67% CTR — most impressions, worst CTR).

## What the data is telling us (panel synthesis)
1. **The real, recurring, monetizable intent is professional LLC-piercing / chain-of-title / entity-resolution** — title abstractors, RE/foreclosure attorneys, skip-tracers, lender diligence staff. NOT consumers (nobody types "dos acris"). This is a **more reachable, higher-frequency wedge than the current hard-money-lender ICP** — a finding for the Jan-31 fork, not an action now.
2. **Don't size off the 1,100-impression number** — one hyper-specific address string at that volume is almost certainly a single determined researcher or a scraper iterating query variants (note the near-duplicate twin). The *pattern* recurring across unrelated buildings is the real signal; the raw count is junk.
3. **Single-address lookup is a commodity trap** — the data is free/public and PropertyShark + RegWatch already rank. Per-address programmatic pages are a **free funnel, not a product.**
4. **The one payable, semi-defensible asset = the LLC / portfolio roll-up graph** — "every building this owner/principal touches," cross-indexed across ACRIS + HPD registration, ideally + lis-pendens (our known #1 gap, [[project_nycpi_lis_pendens_not_in_acris]]). That's what neither the city portal (30-sec free) nor a generic tool page delivers, and it's the one feature both lenders AND the searchers want.
5. **Refuse the skip-trace / owner-phone lane** — that's PropertyShark Platinum's moat (human-verified contact data, TCPA/DNC/data-ops exposure). Compete on graph + AI narration + MCP, not dialable numbers.
6. **Eviction / HPD-violation consumer traffic = free funnel ONLY** — highest clicks, lowest value, FCRA/CRA legal trap. Never productize ([[project_nycpi_pricing_mvp_2026_07]]).

## Revenue hypothesis (to test LATER, if gated)
Programmatic per-address "Who owns [address] / ACRIS chain of title" pages → partial answer above a gate → funnel into **$49 Pro**, whose payable value is the **portfolio/LLC-graph roll-up + AI narration + monitoring**, not the raw lookup.

## Why we're NOT building it now
SEO demand is **evergreen and non-decaying** — we already hold the rankings; nothing expires if we wait 6–8 weeks. ALETA's Nov-1 consumer launch window is **not** fungible across time. Building programmatic pages + a graph tool is a real engineering effort competing for exactly the attention ALETA needs two weeks out. A compelling new reason to work on the side-bet is what a comfort-loop produces.

## GATE (build only if ALL true)
1. ALETA is **launched and stable** (post Nov-1), AND
2. The **Oct-26 paid verdict** shows ≥1 paying customer or a warm pipeline.
Otherwise this stays a parked note and comes to the **Jan-31 fork** as a *candidate growth lever*, not a commitment.

## Shipped now (cheap, no focus cost)
- `/property-owner-lookup` title + meta rewritten to lead with "LLC owner, deed & ACRIS / chain-of-title" — a CTR fix on a ranking we already hold. No pipeline, no new surface.
