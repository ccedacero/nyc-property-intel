# r/ClaudeAI post draft

**Where:** https://reddit.com/r/ClaudeAI
**Audience:** Claude users — devs, AI tinkerers, power-users
**Format:** Show-and-tell. They like working demos.
**Why this works:** They already have Claude Desktop / Code installed. Zero-install demo via /chat removes friction.

## Title

> I built an MCP server that queries 22+ NYC public databases — Claude does NYC real estate due diligence in one query

Alternate:
> Show-off: my MCP server lets Claude pull from 22 NYC city databases (try it without installing)

## Body

```
TL;DR: open-source MCP server, hosted version free, try it in your browser
without installing anything: https://nycpropertyintel.com/chat

I've been working on NYC Property Intel for a few months — an MCP server that
gives Claude (Desktop, Code, or any MCP client) access to 22+ NYC public
record databases:

  - DOB violations and permits
  - HPD complaints and violations
  - ECB violations
  - ACRIS deeds, mortgages, and liens
  - DOF sales records (rolling + annual + assessments)
  - 311 complaints
  - FDNY fire incidents
  - NYPD complaint data (geospatial)
  - Marshal eviction records
  - PLUTO (property profiles + zoning)
  - Rent stabilization registry
  - …and more

Real estate due diligence in NYC normally means visiting 8–10 city websites
with different UIs and search syntax. With this server, you ask Claude:

  "Full due diligence on 123 Atlantic Ave, Brooklyn"

…and it walks the relevant tools, pulls the data, and returns a Markdown
report with violations, sales, ownership, permits, complaints, evictions.

If you have Claude Code:

  claude mcp add --transport http nyc-property-intel \
    "https://nyc-property-intel-production.up.railway.app/mcp" \
    --header "Authorization: Bearer YOUR_TOKEN" \
    --scope user

If you have Claude Desktop, the JSON config is in the README.

Free trial is 10 queries/day for 30 days (with up to 5 of those being full
due-diligence reports). Self-host is free under MIT.

Repo: https://github.com/ccedacero/nyc-property-intel

Happy to answer questions about how I built it, the data pipeline (~19M rows
in Postgres), or what I'd change next time.
```

## Tone notes

- This sub is friendly to "look what I built" but allergic to spam. Don't post here if you've posted in 5 other Claude subs in the last week.
- They WILL ask for the system prompt / tool schemas — be ready to paste a snippet. (Or link to `src/nyc_property_intel/tools/`.)
- They like posts that disclose limitations honestly — your `KNOWN_FROZEN_SOURCE` doc is gold here, mention it.

## Things to NOT do

- Don't post identical content to r/mcp same day (use the r/mcp variant)
- Don't pretend it's a side-project for fun if you're trying to monetize. They smell that. Be honest: "free trial, paid tiers coming for higher rate limits"
