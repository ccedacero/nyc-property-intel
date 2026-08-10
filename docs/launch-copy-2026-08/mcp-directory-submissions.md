# MCP directory submissions + copy corrections (2026-08-10)

Canonical description (use verbatim everywhere — never "22+", never "databases"):

> NYC Property Intel gives AI agents access to 20+ official NYC public-record
> datasets across 9 city agencies through 18 tools — violations (HPD/DOB/ECB),
> liens, sales history, ownership behind LLCs, rent-stabilization signals,
> marshal evictions, permits, fire incidents, and full due-diligence reports.
> Free trial token by email; MIT-licensed and self-hostable.

**Endpoint:** `https://nyc-property-intel-production.up.railway.app/mcp`
(HTTP transport, `Authorization: Bearer <token>`)
**Repo:** https://github.com/ccedacero/nyc-property-intel
**Site:** https://nycpropertyintel.com

## Submit to (in order)

1. **mcp.so** — https://mcp.so/ → Submit. Category: Data/Real Estate.
2. **Smithery** — https://smithery.ai/ → submit via GitHub repo link.
3. **cursor.directory** — https://cursor.directory/ → MCP section → submit.
4. **PulseMCP** — https://www.pulsemcp.com/ → submit server.
5. **awesome-mcp-servers PRs** (two biggest lists):
   - https://github.com/punkpeye/awesome-mcp-servers → add under
     "Location Services" or "Data" as:
     `- [nyc-property-intel](https://github.com/ccedacero/nyc-property-intel) - NYC property due diligence from 20+ official city datasets: violations, liens, evictions, ownership, rent stabilization.`
   - https://github.com/wong2/awesome-mcp-servers → same line.

## Corrections to existing listings (the "22+ databases" cleanup)

The stale copy came from an old README; both caches predate the fix. It's
the only off-site quantitative claim about the product, so AI answers repeat it.

- **Glama** — https://glama.ai/mcp/servers → find nyc-property-intel →
  "request re-scrape" / claim the server with the GitHub account. The
  README is already canonical, so a re-index fixes it automatically.
- **mcpmarket.com** — listing still says "22+ NYC public-record databases".
  Use their contact/claim flow; paste the canonical description above.

## After each submission

Add the live listing URL to a "Listings" row in docs/operational-changes-log.md
so the next copy change knows every cache to invalidate.
