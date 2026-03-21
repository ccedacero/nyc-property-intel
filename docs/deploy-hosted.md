# Hosted Deployment Guide

Deploy NYC Property Intel as a publicly accessible MCP server so anyone can add it to Claude Desktop or Claude Code without running their own database.

**Stack: Neon (Postgres) + Railway (MCP server) + Cloudflare Pages (landing page)**
**Estimated cost: ~$5/mo (Railway hobby) + Neon free tier + Cloudflare free tier**

---

## Part 1 — Hosted Database (Neon)

### 1. Create a Neon account

Go to [neon.tech](https://neon.tech) → Sign up (free) → Create a project.

- Project name: `nyc-property-intel`
- Region: `us-east-2` (or closest to your Railway region)
- Postgres version: 16

### 2. Get your connection string

In the Neon dashboard → Connection Details → copy the connection string:

```
postgresql://nycdb:<password>@ep-xxx.us-east-2.aws.neon.tech/nycdb?sslmode=require
```

### 3. Restore the database dump

From your local machine (where `data/nycdb.dump` lives):

```bash
pg_restore \
  --no-owner \
  --jobs=4 \
  -d "postgresql://nycdb:<password>@ep-xxx.us-east-2.aws.neon.tech/nycdb?sslmode=require" \
  data/nycdb.dump
```

This takes 15–30 minutes over a network connection. The 938MB compressed dump expands to ~8GB in Neon (within the free 10GB tier).

After restore, create indexes and materialized views:

```bash
psql "postgresql://nycdb:<password>@ep-xxx.us-east-2.aws.neon.tech/nycdb?sslmode=require" \
  -f scripts/create_indexes.sql

psql "postgresql://nycdb:<password>@ep-xxx.us-east-2.aws.neon.tech/nycdb?sslmode=require" \
  -f scripts/create_views.sql
```

---

## Part 2 — MCP Server (Railway)

### 1. Create a Railway account

Go to [railway.app](https://railway.app) → Sign up → Create a project.

### 2. Deploy from GitHub

- New Project → Deploy from GitHub repo → select `nycpropertyintel/nyc-property-intel`
- Railway auto-detects Python via nixpacks and runs `uv run nyc-property-intel`

### 3. Set environment variables

In Railway → your service → Variables → add:

| Variable | Value |
|----------|-------|
| `DATABASE_URL` | Your Neon connection string (with `?sslmode=require`) |
| `MCP_TRANSPORT` | `sse` |
| `LOG_LEVEL` | `INFO` |
| `NYC_GEOCLIENT_SUBSCRIPTION_KEY` | Your GeoClient API key (optional but recommended) |

`PORT` is set automatically by Railway — do not set it manually.

### 4. Get your public URL

Railway → your service → Settings → Networking → Generate Domain.

Your MCP server will be live at:
```
https://nyc-property-intel-production.up.railway.app/sse
```

### 5. Test the SSE endpoint

```bash
curl -N https://nyc-property-intel-production.up.railway.app/sse
```

You should see an SSE stream open (no error, no 404).

---

## Part 3 — Connect Claude to the Hosted Server

### Claude Desktop

Add to `claude_desktop_config.json` (Settings → Developer → Edit Config):

```json
{
  "mcpServers": {
    "nyc-property-intel": {
      "url": "https://nyc-property-intel-production.up.railway.app/sse"
    }
  }
}
```

### Claude Code

```bash
claude mcp add --transport sse nyc-property-intel \
  https://nyc-property-intel-production.up.railway.app/sse
```

Or add to `~/.claude.json`:

```json
{
  "mcpServers": {
    "nyc-property-intel": {
      "url": "https://nyc-property-intel-production.up.railway.app/sse"
    }
  }
}
```

---

## Part 4 — Landing Page (Cloudflare Pages)

### 1. Push to GitHub first

Make sure your repo is public at `github.com/nycpropertyintel/nyc-property-intel`.

### 2. Connect to Cloudflare Pages

1. [pages.cloudflare.com](https://pages.cloudflare.com) → Create a project → Connect to Git
2. Select your repo
3. Build settings:
   - **Framework preset**: None
   - **Build command**: *(leave empty)*
   - **Build output directory**: `site`
4. Deploy

### 3. Add your custom domain

Cloudflare Pages → your project → Custom Domains → add `nycpropertyintel.com`.

If your domain is already on Cloudflare DNS, this is automatic. Otherwise add a CNAME record pointing to `<your-project>.pages.dev`.

---

## Updating the Landing Page with Your Live URL

Once Railway gives you a URL, update `site/index.html` — add a third tab to the install section showing the hosted connection config. Search for `tab-claude-code` and add alongside it.

---

## Monitoring

- **Railway logs**: Railway dashboard → your service → Logs
- **Neon metrics**: Neon dashboard → Monitoring (query count, connection count, storage)
- **Uptime**: Railway restarts automatically on crash (configured in `railway.toml`)

---

## Cost breakdown

| Service | Plan | Cost |
|---------|------|------|
| Neon | Free tier | $0/mo |
| Railway | Hobby | ~$5/mo |
| Cloudflare Pages | Free | $0/mo |
| **Total** | | **~$5/mo** |

Neon free tier includes 10GB storage and 100 compute hours/month. The MCP server is read-only and low-traffic so compute hours will not be a concern.
