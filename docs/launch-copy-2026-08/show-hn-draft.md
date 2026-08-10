# Show HN draft (2026-08-10)

## Title (pick one, ≤80 chars)

1. `Show HN: Ask about any NYC building – violations, liens, evictions, in one query`
2. `Show HN: I wired 20+ NYC property datasets into one plain-English lookup`
3. `Show HN: Free NYC building due-diligence – 20+ city datasets, one question`

**URL:** https://nycpropertyintel.com/chat  (the tool, not the homepage — HN wants the thing itself)

## First comment (post immediately after submitting)

Hi HN — I built this after watching NYC buyers and renters get burned by
records that were public the whole time, just scattered across a dozen city
systems that don't talk to each other (DOB BIS *and* DOB NOW, ECB/OATH, HPD
Online, ACRIS, DOF, 311, marshal evictions…).

It mirrors 20+ official NYC Open Data datasets across 9 city agencies into
Postgres (via the nycdb project), resolves any address to its canonical
BBL (borough-block-lot), and lets you ask questions in plain English —
"any red flags on 123 Atlantic Ave?" — through a chat UI or as an MCP
server inside Claude/other agents. 18 tools cover violations, liens,
sales history, ownership behind LLCs, rent-stabilization signals,
evictions, permits, fire incidents, and 311.

Things I care about that similar tools skip:

- **Honest data vintage.** Every answer is stamped with when the dataset
  last synced. Public data lags reality and pretending otherwise is how
  people get hurt.
- **Honest boundaries.** A clean ACRIS read is not a clean title —
  foreclosure lis pendens live in the court system (NYSCEF), not ACRIS,
  and the tool says so instead of quietly overclaiming. Same for the
  DHCR rent-stabilization data (2007–2017 vintage, disclosed).
- **Not a person-lookup.** Everything is keyed on buildings, not people.
  It deliberately can't do tenant screening; the eviction data is
  address-level marshal executions with no names.

Free tier: 3 queries with no signup, 10/day with a free account. The whole
thing is MIT-licensed if you'd rather self-host — the repo has the schema,
sync pipeline, and the same MCP server the hosted version runs:
https://github.com/ccedacero/nyc-property-intel

Happy to answer anything about wrangling NYC's data (the address-matching
problems alone could fill a blog series).

## Timing & mechanics

- Post Tue–Thu, 8–10am ET. Don't post Friday/weekend.
- Do NOT ask anyone to upvote (HN detects rings). Just be present in the
  thread for 3–4 hours answering everything.
- Expect the "it's just Open Data" comment — the honest answer is yes,
  and the value is the join + address resolution + honesty layer. Lean in.
- Have the demo BBLs ready (docs/demo-bbls.md) for skeptics who test live.
