# Show HN draft

**Where:** https://news.ycombinator.com/show
**When:** Tue/Wed/Thu, 8–10am ET (best traffic window)
**Notes:**
- Lead with the *user benefit*, not "MCP server" — MCP is jargon for HN even though they know it.
- One-shot: be ready to respond to comments for 4–6 hours. Most HN threads are won or lost in the first hour of comments, not the post.
- Don't include the demo URL twice. Once in the body is enough.

## Title (pick one)

Best:
> Show HN: I replaced 8 hours of NYC public-record lookups with one chat message

Alternates:
> Show HN: Ask Claude about any NYC property — 22 city databases, one query
> Show HN: NYC Property Intel — public-record due diligence as an MCP server

## Body

```
For NYC real estate, due diligence on a single building means visiting 8–10 city
websites — DOB violations, HPD complaints, ECB violations, ACRIS deeds, 311
complaints, DOF sales, NYPD precinct data, FDNY incident history, the rent
stabilization registry, marshal evictions, etc. Each one has its own UI, its
own search syntax, and a different definition of "this address."

I built NYC Property Intel because I got tired of doing this manually: an MCP
server that lets you ask Claude (Desktop, Code, or any MCP client) about any
NYC property in plain English, and it queries 22+ NYC public databases via a
single conversational interface.

Try it without installing anything: https://nycpropertyintel.com/chat
(3 free queries, no signup)

Examples it handles:
  "Any open violations on 350 5th Ave?"
  "Full due diligence on 123 Atlantic Ave, Brooklyn"
  "Show me eviction history at this BBL"
  "What 311 complaints have been filed near 170 W 85th in the last year?"

Stack: Python MCP server, asyncpg → Postgres, ~19M rows loaded from NYC Open
Data via the open-source nycdb project. Hosted version on Railway. Free trial
of 10 queries/day for 30 days; self-host is fully supported under MIT.

A few honest caveats up front:
- This is a due-diligence tool, not an appraisal. It surfaces the data —
  decisions are still on you.
- DOB job filings have a known coverage gap pre-2020 (Socrata API limitation,
  not a sync bug — documented in the repo).
- "Daily" sync for tier-1 datasets, weekly/monthly for slower-moving ones.

Open to feedback, especially from anyone who's done NYC due diligence the
hard way. Source: https://github.com/ccedacero/nyc-property-intel
```

## Comment-prep cheatsheet

Anticipate these questions; have answers ready in your draft replies:

1. **"How is this different from PropertyShark?"**
   PropertyShark is dashboard-style and paid. This is an MCP server — it lets Claude *reason* over the data, not just display it. Cheaper (free trial → tiered paid). And it's open-source if you'd rather self-host.

2. **"Where's the data from? How fresh?"**
   NYC Open Data via nycdb. Tier-1 (DOB violations, 311, HPD violations, DOF) refreshes daily. Tier-2 (ACRIS, NYPD, FDNY) weekly. Tier-3 (eviction records, personal property) monthly. Specific freshness for each dataset is in the repo's docs/known-issues.md.

3. **"Tenant screening?"**
   Explicitly disallowed in the ToS and refused at the software layer. Building data only; no demographic, no tenant info. Fair Housing compliance statement on the legal page references the specific NYS and NYC statutes.

4. **"What about ZoLa / NYC Scope / DOBGuard?"**
   ZoLa is a map; great for what it does. NYC Scope is the nearest competitor — dashboard UI, focused on violations. The MCP angle (Claude can chain queries, write a Markdown report, compare 5 buildings) is what makes this different.

5. **"Why would I pay for this?"**
   Self-host is free under MIT. Hosted is free for 10 queries/day for 30 days. Paid tiers in development for higher rate limits + priority data refresh — pricing not yet announced.

6. **"What stops bots from spamming the free trial?"**
   3-layer signup defense (disposable email reject, MX check, brand-prefix heuristic), IP rate limit, body size limit, CSRF protection. Honest answer: it's been tested in production — most bot signups now silent-reject.

7. **"Privacy?"**
   Email is the only PII collected. Token usage is logged by hashed token, not email. Full privacy policy on the site. Self-host has zero telemetry.

8. **"What's next?"**
   Tighten the chat experience for non-technical users (right now installing into Claude Desktop has a setup step). Pricing tiers. Maybe Boston / Chicago next if there's demand.

## Things to NOT do

- Don't lead with "MCP" in the title (HN audience knows it but it sounds wonky)
- Don't claim "all" or "complete" coverage — there are documented gaps; honesty wins on HN
- Don't argue with detractors. Acknowledge → offer detail → move on
- Don't post and disappear. Be present for at least 4 hours after posting
