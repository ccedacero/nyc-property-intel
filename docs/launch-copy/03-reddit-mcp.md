# r/mcp post draft

**Where:** https://reddit.com/r/mcp
**Audience:** Devs building MCP servers + early adopters of MCP
**Format:** Technical show-and-tell — they want install commands, not marketing copy.

## Title

> NYC Property Intel — MCP server giving Claude access to 22 NYC public-record databases

Alternates:
> Built an MCP server for NYC real estate due diligence (22 datasets, ~19M rows)
> [MCP server] NYC public-records → Claude in one query

## Body

```
Open-source MCP server, MIT licensed.

What it does: lets Claude query 22+ NYC public databases (DOB, HPD, ECB, ACRIS,
DOF, 311, FDNY, NYPD, etc.) for real estate due diligence in plain English.

Source: https://github.com/ccedacero/nyc-property-intel
Try without install: https://nycpropertyintel.com/chat
Hosted MCP endpoint: https://nyc-property-intel-production.up.railway.app/mcp

Install (Claude Code):
  claude mcp add --transport http nyc-property-intel \
    "https://nyc-property-intel-production.up.railway.app/mcp" \
    --header "Authorization: Bearer YOUR_TOKEN" \
    --scope user

  (Get YOUR_TOKEN by signing up at nycpropertyintel.com — 10 queries/day for
  30 days free.)

Install (Claude Desktop): JSON in the README.

Self-host: clone repo, `uv sync`, load NYC Open Data into Postgres via the
nycdb project. Detailed instructions in docs/.

Tech:
  - Python MCP server (mcp-server SDK)
  - asyncpg → PostgreSQL
  - HTTP transport (also has STDIO for self-host)
  - Bearer token auth, hashed token in DB (SHA-256), token rotation supported
  - Per-token + per-IP rate limits
  - 18 tools exposed: lookup_property, get_property_issues, get_complaints,
    analyze_property (full report), search_comparable_sales, etc.
  - Full Fair Housing compliance enforcement at the software layer

Things I learned that might help others building MCP servers:
  - Tool descriptions matter more than function names — Claude routes from
    the description text, not the name
  - Returning Markdown (not JSON) made tool output way more usable
  - Streaming responses through MCP HTTP transport works but is finicky;
    most tool calls are synchronous req/res
  - Bearer-token auth in MCP is fine but the spec is still maturing —
    OAuth flow is on the horizon

Happy to talk shop about the data pipeline (~19M rows, mostly Socrata API
+ nycdb), the schema, or trade-offs in tool granularity.
```

## Tone notes

- r/mcp is small and technical. They want to see the tool list, the install command, the schema. The marketing pitch is unwelcome.
- If you can post a screenshot of Claude using the tools, do it. Visuals beat words.
- Mention what you LEARNED — this sub is collaborative, not a marketplace.

## Things to NOT do

- Don't reuse the r/ClaudeAI body verbatim — they overlap, you'll look spammy
- Don't oversell. The audience is small + technical + has a sharp BS detector
