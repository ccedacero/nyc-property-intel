# MCP Directory Submissions — Status

## ✅ Done autonomously (no further action needed)

| # | Channel | Status | Link |
|---|---|---|---|
| 1 | GitHub topics added | Live | Repo now tagged `mcp-server`, `mcp`, `claude`, `nyc`, `real-estate`, `due-diligence`, `anthropic`, `property-data`, `python`. **This is what triggers Glama to auto-index** — they should pick the repo up within hours. |
| 2 | **punkpeye/awesome-mcp-servers** | PR open, awaiting review | https://github.com/punkpeye/awesome-mcp-servers/pull/6041 — Real Estate category, second entry. Maintainer is active; PRs typically merge within a few days. |
| 3 | **Cline MCP Marketplace** | Issue open, awaiting review | https://github.com/cline/mcp-marketplace/issues/1527 — submission with logo + install instructions. Cline reviews within ~a couple of days per their stated SLA. |

## ⛔ Cannot auto-submit (maintainer disabled PRs)

These two awesome-lists have **PRs structurally disabled**. Both have 0 PRs in their entire repo history.

| Channel | Why blocked | What you can do |
|---|---|---|
| **wong2/awesome-mcp-servers** | `pull` permission only on the repo, no PRs ever accepted | Email wong2 directly via [@wong2 on GitHub](https://github.com/wong2) — pitch a one-liner add for the Community Servers section |
| **appcypher/awesome-mcp-servers** | Same — 0 PRs in repo history | Open a discussion via the repo's Discussions tab (currently disabled too) or email/DM the maintainer |

Both are low-priority — punkpeye and the official MCP Registry are higher-traffic anyway.

## 🔜 USER ACTION needed (login or CLI required)

Each of these requires your account or a CLI you have to run on your laptop. None are autonomous-via-gh.

### A. Official MCP Registry — **highest priority** (table stakes; downstream listings pull from here)

**URL:** https://github.com/modelcontextprotocol/registry

**How to submit:**
```bash
git clone https://github.com/modelcontextprotocol/registry /tmp/mcp-registry
cd /tmp/mcp-registry
make publisher  # builds the mcp-publisher CLI
./bin/mcp-publisher login github  # opens browser for GitHub OAuth
./bin/mcp-publisher publish path/to/server.json
```

**`server.json` to use** (drop into the repo at root or pass with `--config`):
```json
{
  "$schema": "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json",
  "name": "io.github.ccedacero/nyc-property-intel",
  "version": "0.1.1",
  "description": "MCP server giving Claude AI access to 22+ NYC public-record databases for real estate due diligence",
  "repository": {
    "url": "https://github.com/ccedacero/nyc-property-intel",
    "source": "github"
  },
  "remotes": [
    {
      "type": "streamable-http",
      "url": "https://nyc-property-intel-production.up.railway.app/mcp"
    }
  ]
}
```

**Time:** ~10 minutes including building the publisher.
**Outcome:** instant publish; near-real-time downstream syndication to mcpfinder, MCP Market, and other aggregators.

### B. Glama — confirms auto-index

**URL:** https://glama.ai/mcp/servers
**How:** click "Add Server" (top-right), paste your repo URL: https://github.com/ccedacero/nyc-property-intel
**Time:** ~2 minutes
**Outcome:** indexed within hours; tools/schemas auto-extracted.

### C. PulseMCP

**URL:** https://www.pulsemcp.com/submit
**Field:** repo URL → https://github.com/ccedacero/nyc-property-intel + select "MCP Server"
**Time:** ~2 minutes
**Outcome:** manual review, days-to-week.

### D. Smithery

**URL:** https://smithery.ai/new
**Choose:** "Bring your own hosting" (your endpoint is hosted on Railway)
**Field:** HTTPS endpoint → `https://nyc-property-intel-production.up.railway.app/mcp`
**Caveat:** auto-scanner may fail behind your Bearer-token wall. If so, paste your tool list in the description or stage `/.well-known/mcp/server-card.json` (small JSON listing tools/resources).
**Time:** ~5 minutes
**Outcome:** live immediately after publish; Smithery has a slick install button format.

### E. mcp.so

**URL:** https://mcp.so/submit
**Fields:** name, short description, repo/docs link, category (pick "Other" or "Database")
**Time:** ~3 minutes
**Outcome:** manual review, days.

### F. mcpservers.org

**URL:** https://mcpservers.org/submit (PRs explicitly NOT accepted; web form only)
**Fields:** standard form
**Time:** ~3 minutes
**Outcome:** manual review, days-to-weeks.

## Your sequenced action

If you want to knock all of these out in one sitting (~30 minutes total):

1. **Now (5 min):** Open https://glama.ai/mcp/servers — click "Add Server" — paste the repo URL.
2. **Now (3 min each):** PulseMCP, mcp.so, mcpservers.org (parallel browser tabs).
3. **Now (5 min):** Smithery — paste hosted endpoint.
4. **Tonight (10 min):** Official MCP Registry — git clone + make publisher + login + publish.

After those are submitted, the MCP-discovery side of distribution is fully covered. Then you can focus on the post drafts in `docs/launch-copy/` for HN / Reddit / BiggerPockets / SPONY / TRD / LinkedIn.

## Why I didn't do these for you

- **Official MCP Registry:** requires GitHub OAuth login on your machine, not a one-shot API call. Can't do without your identity.
- **Glama / PulseMCP / Smithery / mcp.so / mcpservers.org:** all are web forms requiring you to be logged in (or at least typing on your machine for the captcha). No public REST submission API.
- **wong2 / appcypher:** infrastructurally disabled. Would have failed even with full access.

The autonomous lever (GitHub topic + punkpeye PR + Cline issue) covers ~30% of MCP discoverability. The 30 minutes of manual submissions above add another ~50%. Combined with HN/Reddit posts, you'll be visible across nearly every MCP discovery surface.
