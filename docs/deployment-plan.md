# NYC Property Intel — Deployment Plan

> Last updated: 2026-04-10
> Stack: Railway (Python MCP server + PostgreSQL) + Vercel (static site)

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│  End-user machine                                                   │
│                                                                     │
│  Claude Desktop / Claude Code                                       │
│       │                                                             │
│       │  MCP over SSE (HTTPS)                                       │
│       │  Authorization: Bearer <MCP_SERVER_TOKEN>                   │
│       ▼                                                             │
│  https://<project>.up.railway.app/sse                               │
└─────────────────────────────────────────────────────────────────────┘
                │
                │  Private networking (Railway internal)
                ▼
┌────────────────────────────────────────┐
│  Railway Project                       │
│                                        │
│  ┌──────────────────────────────────┐  │
│  │  Service: nyc-property-intel     │  │
│  │  Python 3.12 / uv                │  │
│  │  FastMCP + uvicorn (SSE mode)    │  │
│  │  Connection pool: min=1 max=10   │  │
│  └────────────────┬─────────────────┘  │
│                   │  asyncpg           │
│  ┌────────────────▼─────────────────┐  │
│  │  Plugin: PostgreSQL              │  │
│  │  ~19M rows / ~15 GB             │  │
│  │  nycdb datasets (PLUTO, HPD,     │  │
│  │  DOF, ACRIS, DOB, etc.)          │  │
│  └──────────────────────────────────┘  │
└────────────────────────────────────────┘

┌──────────────────────────────────────────┐
│  Vercel                                  │
│                                          │
│  https://nycpropertyintel.com            │
│  Static HTML/CSS/JS  (site/ directory)   │
│  No server-side code                     │
└──────────────────────────────────────────┘
```

---

## 2. Railway Backend Setup

### 2a. Database Setup

#### Choose the right Railway PostgreSQL plan

| Plan | Storage | RAM | Notes |
|------|---------|-----|-------|
| Hobby | 1 GB | shared | Too small — data alone is ~15 GB |
| Pro (pay-per-use) | Unlimited | dedicated | **Use this** |

Railway PostgreSQL is provisioned on the Pro plan with per-GB storage billing
(roughly $0.25/GB-month as of 2026). At 15 GB of data the monthly database
cost is ~$3–5 on top of the base compute.

#### Create the PostgreSQL plugin

1. Open your Railway project dashboard.
2. Click **+ New** → **Database** → **Add PostgreSQL**.
3. Railway provisions the instance and surfaces a `DATABASE_URL` environment
   variable automatically on the plugin's **Variables** tab.
4. Copy that value — it looks like:
   ```
   postgresql://postgres:<password>@<host>.railway.internal:5432/railway
   ```
   Railway also exposes a public URL (with a different port) for external
   access during data loading. Find it under **Connect** → **Public Network**.

#### Load the nycdb data

The recommended path is a `pg_restore` from a local dump you produce with
`nycdb`. Estimate: a ~15 GB custom-format dump restores in 30–60 minutes over
a good connection.

```bash
# 1. Produce the dump locally (if you haven't already)
pg_dump -Fc -d postgresql://nycdb:nycdb@localhost:5432/nycdb \
  --no-owner --no-acl \
  -f data/nycdb.dump

# 2. Get the Railway PUBLIC connection URL from the plugin's Connect tab.
#    It looks like: postgresql://postgres:<pw>@<host>.railway.app:<port>/railway
export RAILWAY_PUBLIC_URL="postgresql://postgres:<pw>@<host>.railway.app:<port>/railway"

# 3. Restore — use --jobs=4 to parallelise; adjust to your CPU count
pg_restore \
  -d "$RAILWAY_PUBLIC_URL" \
  --no-owner \
  --no-acl \
  --jobs=4 \
  data/nycdb.dump

# 4. Create indexes (idempotent — safe to re-run)
psql "$RAILWAY_PUBLIC_URL" -f scripts/create_indexes.sql

# 5. Create materialized views
#    mv_property_profile:  ~2 min
#    mv_violation_summary: ~5 min
#    mv_current_ownership: ~10 min  (scans all of ACRIS)
psql "$RAILWAY_PUBLIC_URL" -f scripts/create_views.sql
```

The required objects after restore + post-processing:

**Tables (from nycdb):**
- `pluto_latest`, `pad_adr`, `pad_bbl`
- `hpd_violations`, `hpd_complaints`, `hpd_registrations`, `hpd_contacts`, `hpd_litigations`
- `dof_sales`, `dof_annual_sales`
- `dof_property_valuation_and_assessments`, `dof_exemptions`, `dof_tax_lien_sale_list`
- `dob_violations`, `dobjobs`, `dob_now_jobs`
- `rentstab`, `ecb_violations`
- `real_property_legals`, `real_property_master`, `real_property_parties`
- `real_property_references`, `real_property_remarks`
- `personal_property_legals`, `personal_property_master`, `personal_property_parties`
- `acris_document_control_codes`

**Materialized views (created by `scripts/create_views.sql`):**
- `mv_property_profile` — denormalized PLUTO view, primary lookup target
- `mv_violation_summary` — per-BBL HPD + DOB violation counts
- `mv_current_ownership` — most recent deed per BBL from ACRIS

**Indexes:** see `scripts/create_indexes.sql` — ~40 indexes covering every
BBL column, composite comps queries, and GIN full-text indexes on address
fields. Estimated index build time: ~18 min total.

**Timing notes:**
- `pg_restore --jobs=4`: 30–60 min depending on connection speed
- `create_indexes.sql`: Phase A ~5 min, Phase B ~3 min, Phase C ~10 min
- `create_views.sql`: mv_property_profile ~2 min, mv_violation_summary ~5 min,
  mv_current_ownership ~10 min

Once the public URL is no longer needed you can disable public access from the
Railway plugin settings to reduce attack surface.

---

### 2b. MCP Server Service

#### Create the service

1. In your Railway project, click **+ New** → **GitHub Repo**.
2. Select the `nyc-property-intel` repository and the `main` branch.
3. Railway will detect `railway.toml` and use nixpacks automatically.

#### Environment variables

Set these on the service's **Variables** tab:

| Variable | Value | Notes |
|----------|-------|-------|
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` | Railway reference syntax — auto-updates if plugin credentials rotate |
| `MCP_TRANSPORT` | `sse` | Switches server from stdio to uvicorn/SSE mode |
| `MCP_SERVER_TOKEN` | _(generate below)_ | Required for production auth |
| `LOG_LEVEL` | `INFO` | Use `DEBUG` only while troubleshooting |
| `PORT` | _(leave unset)_ | Railway injects this automatically |

Optional variables (enable for better geocoding accuracy):

| Variable | Value | Notes |
|----------|-------|-------|
| `NYC_GEOCLIENT_SUBSCRIPTION_KEY` | your key | Preferred auth method — register at api-portal.nyc.gov |
| `NYC_GEOCLIENT_APP_ID` | your app ID | Legacy auth — only if subscription key unavailable |
| `NYC_GEOCLIENT_APP_KEY` | your app key | Legacy auth — only if subscription key unavailable |
| `SOCRATA_APP_TOKEN` | your token | Raises Socrata rate limit from 1000 → 5000 req/hr |

Generate a secure token:
```bash
openssl rand -hex 32
# Example output: a3f8c2e1d9b4a7f6e5c8d2b1a4e7f3c6d9b2a5e8f1c4d7b3a6e9f2c5d8b1a4
```

#### Railway URL and custom domain

After the first deploy, Railway assigns a URL in the format:
```
https://<project-name>-production.up.railway.app
```

The SSE endpoint is at the path `/sse`:
```
https://<project-name>-production.up.railway.app/sse
```

The site's `index.html` already hardcodes
`https://nyc-property-intel-production.up.railway.app/sse`. If Railway assigns
a different slug, update the two occurrences in `site/index.html` (lines 513
and 519).

To set a custom domain (e.g. `api.nycpropertyintel.com`):
1. Railway service → **Settings** → **Networking** → **Custom Domain**.
2. Add the domain and follow the CNAME instructions.
3. Update the SSE URL in `site/index.html` accordingly.

#### Health checks

FastMCP's SSE server does not expose a dedicated `/health` or `/ping` endpoint
out of the box. Railway's built-in TCP health check (port liveness) is
sufficient. To verify the service is up manually:

```bash
# Should return HTTP 401 (auth enforced = server is alive)
curl -i https://<project>.up.railway.app/sse

# Should return HTTP 200 with text/event-stream Content-Type
curl -i \
  -H "Authorization: Bearer <MCP_SERVER_TOKEN>" \
  https://<project>.up.railway.app/sse
```

---

### 2c. Nixpacks Build

Railway uses **nixpacks** to auto-detect the build strategy. As of early 2026,
nixpacks recognises `pyproject.toml` with a `[tool.uv]` section and installs
`uv` automatically. However, this detection is fragile across nixpacks versions.

A `nixpacks.toml` is included at the project root to pin the behaviour
explicitly:

```toml
# nixpacks.toml
[phases.setup]
nixPkgs = ["python312", "uv"]

[phases.install]
cmds = ["uv sync --frozen --no-dev"]

[start]
cmd = "uv run nyc-property-intel"
```

(This file is created in section 2c below — see the note at the end of this
document.)

The `start` command in `nixpacks.toml` is redundant with `railway.toml`'s
`startCommand`; having both is harmless and provides a fallback.

**Python version:** `pyproject.toml` declares `requires-python = ">=3.12"`,
so nixpacks is pinned to `python312`.

**Lock file:** `uv sync --frozen` requires a committed `uv.lock`. Verify it is
present and up-to-date:
```bash
uv lock
git add uv.lock
git commit -m "chore: commit uv.lock for Railway deployment"
```

---

### 2d. Scaling and Cost

#### Railway Hobby vs Pro

| | Hobby | Pro |
|---|---|---|
| Always-on services | No (sleeps after inactivity) | Yes |
| Outbound egress | 100 GB/month | 500 GB/month |
| PostgreSQL storage | 1 GB | Unlimited (billed per GB) |
| Price | $5/month base | $20/month base + usage |

**Use Pro.** The 15 GB PostgreSQL dataset alone exceeds Hobby's 1 GB limit.
An always-on SSE server also cannot be on Hobby (services sleep after ~5 min
of inactivity, which would terminate open SSE connections).

#### Estimated monthly cost (Pro plan)

| Resource | Est. cost |
|----------|-----------|
| MCP server compute (~0.5 vCPU, ~256 MB RAM) | ~$3–5/mo |
| PostgreSQL storage (15 GB) | ~$4/mo |
| Egress (low for an MCP server — mostly small JSON) | <$1/mo |
| **Total** | **~$8–10/mo** |

#### Connection pool

`db.py` creates the pool with `min_size=1, max_size=10`. This is correct for
Railway hobby/pro single-instance deployments. Each MCP session holds at most
one acquired connection at a time; the pool handles concurrent tool calls
within a session cleanly. No changes needed.

---

## 3. Vercel Frontend Setup

### 3a. Project Setup

1. Go to [vercel.com/new](https://vercel.com/new) and import the
   `nyc-property-intel` GitHub repository.
2. In the **Configure Project** screen:
   - **Root Directory**: `site`
   - **Framework Preset**: Other
   - **Build Command**: _(leave blank, or enter `echo "static"`)_
   - **Output Directory**: `.`
3. Click **Deploy**.

Vercel will serve `site/index.html` as the root. All assets under `site/assets/`,
`site/css/`, and `site/js/` will be served relative to root.

### 3b. Custom Domain

1. Vercel project → **Settings** → **Domains** → add `nycpropertyintel.com`.
2. Vercel shows required DNS records. Add them at your registrar:
   - **A record**: `@` → `76.76.21.21` (Vercel's IP)
   - **CNAME**: `www` → `cname.vercel-dns.com`
3. SSL is provisioned automatically via Let's Encrypt (usually within 60 seconds).
4. To redirect `www` → apex (or vice versa), add both domains in Vercel's
   domain settings and set one as the redirect target.

### 3c. Environment and Configuration

No environment variables are needed — this is a fully static site.

Verify `site/sitemap.xml` references `https://nycpropertyintel.com` (not
`localhost` or a staging URL) before the first deploy.

The `site/robots.txt` and `site/sitemap.xml` files are served automatically
because Vercel serves the entire `site/` directory.

### 3d. vercel.json

A `site/vercel.json` is included in this repo (created alongside this document)
with:
- Long-lived cache headers for versioned assets (`/assets/`, `/css/`, `/js/`)
- `no-cache` for `index.html` so updates deploy immediately
- `www` → apex redirect
- Security headers: `X-Frame-Options`, `X-Content-Type-Options`,
  `Referrer-Policy`, `Permissions-Policy`, `Content-Security-Policy`

---

## 4. Post-Deployment Checklist

### Database
- [ ] Railway PostgreSQL plugin created (Pro plan)
- [ ] `pg_restore` completed without fatal errors
- [ ] `scripts/create_indexes.sql` applied
- [ ] `scripts/create_views.sql` applied — all three materialized views present
- [ ] Spot-check: `SELECT COUNT(*) FROM mv_property_profile;` returns ~870K rows
- [ ] Spot-check: `SELECT COUNT(*) FROM mv_current_ownership;` returns >0 rows

### Railway MCP Service
- [ ] `uv.lock` committed to repository
- [ ] `nixpacks.toml` committed to repository root
- [ ] All required environment variables set (see table in §2b)
- [ ] `MCP_SERVER_TOKEN` set to a 64-character hex value
- [ ] `MCP_TRANSPORT=sse` set
- [ ] `DATABASE_URL` set using Railway's reference syntax `${{Postgres.DATABASE_URL}}`
- [ ] Service deployed and showing **Active** in Railway dashboard
- [ ] Logs show: `Starting NYC Property Intel MCP server v0.1.0 (SSE transport on port …)`
- [ ] Logs show: `Connection pool ready (min=1, max=10)`
- [ ] Logs show: `SSE transport: bearer token auth enabled`

### Smoke tests
```bash
# 1. Unauthenticated request → must return 401
curl -i https://<project>.up.railway.app/sse
# Expected: HTTP/2 401, {"error":"Unauthorized"}

# 2. Authenticated request → must return 200 with SSE stream
curl -i \
  -H "Authorization: Bearer <YOUR_MCP_SERVER_TOKEN>" \
  https://<project>.up.railway.app/sse
# Expected: HTTP/2 200, content-type: text/event-stream

# 3. Test from Claude Code (see §5)
claude mcp add --transport sse nyc-property-intel \
  --header "Authorization: Bearer <YOUR_MCP_SERVER_TOKEN>" \
  https://<project>.up.railway.app/sse
```

- [ ] All 13 tools respond correctly via live Railway deployment:
  - [ ] `lookup_property` — returns full PLUTO profile
  - [ ] `get_hpd_violations` — returns violation list
  - [ ] `get_hpd_complaints` — returns complaint list
  - [ ] `get_hpd_registration` — returns ownership/contact info
  - [ ] `get_hpd_litigations` — returns litigation list
  - [ ] `get_dob_permits` — returns permit/job list
  - [ ] `get_sales_history` — returns sales records
  - [ ] `get_comparable_sales` — returns comps
  - [ ] `get_liens_and_encumbrances` — returns lien/mortgage data
  - [ ] `get_tax_and_assessments` — returns DOF assessment data
  - [ ] `get_rent_stabilization` — returns rentstab data
  - [ ] `get_neighborhood_context` — returns neighborhood stats
  - [ ] `analyze_property` — returns aggregated analysis

### Vercel / Frontend
- [ ] `site/vercel.json` committed
- [ ] Vercel project created, root directory set to `site`
- [ ] First deploy successful — `nycpropertyintel.com` loads
- [ ] Custom domain configured, SSL active (green padlock)
- [ ] `www.nycpropertyintel.com` redirects to `nycpropertyintel.com`
- [ ] `site/index.html` SSE URL matches the actual Railway deployment URL
  (check lines 513 and 519 in `site/index.html`)
- [ ] `robots.txt` accessible at `https://nycpropertyintel.com/robots.txt`
- [ ] `sitemap.xml` accessible at `https://nycpropertyintel.com/sitemap.xml`
- [ ] Sitemap submitted to [Google Search Console](https://search.google.com/search-console)

---

## 5. Connecting Claude to the Hosted Server

### Claude Code (CLI)

```bash
# Add the MCP server (one-time setup)
claude mcp add --transport sse nyc-property-intel \
  --header "Authorization: Bearer <YOUR_MCP_SERVER_TOKEN>" \
  https://nyc-property-intel-production.up.railway.app/sse

# Verify it was added
claude mcp list
```

Replace `nyc-property-intel-production.up.railway.app` with your actual Railway
URL if it differs.

### Claude Desktop (`claude_desktop_config.json`)

Location: `~/Library/Application Support/Claude/claude_desktop_config.json`
(macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows).

```json
{
  "mcpServers": {
    "nyc-property-intel": {
      "transport": "sse",
      "url": "https://nyc-property-intel-production.up.railway.app/sse",
      "headers": {
        "Authorization": "Bearer <YOUR_MCP_SERVER_TOKEN>"
      }
    }
  }
}
```

Restart Claude Desktop after editing. The `nyc-property-intel` server should
appear in the tool picker within a few seconds.

### Without a token (development only)

If `MCP_SERVER_TOKEN` is intentionally left empty (not recommended for
production), omit the `--header` flag and the `"headers"` key respectively.
The server logs a warning at startup in this configuration.

---

## 6. Monitoring and Ops

### Viewing Railway logs

```bash
# Install the Railway CLI if you haven't
npm install -g @railway/cli
railway login

# Tail live logs for the MCP service
railway logs --service nyc-property-intel --follow
```

Or from the dashboard: select your project → select the service → **Logs** tab.

### Key log lines to watch for

| Log message | Meaning |
|-------------|---------|
| `Connection pool ready (min=1, max=10)` | Startup OK, DB reachable |
| `SSE transport: bearer token auth enabled` | Auth middleware active |
| `Connection pool exhausted` | All 10 connections busy — investigate slow queries |
| `Database connection error` | PostgreSQL unreachable — check Railway DB plugin |
| `Lost connection to the property database` | Transient; pool recreates automatically |

### Metrics

Railway Pro exposes CPU, memory, and network charts per service. For this
workload watch:

- **Memory**: the server itself is lightweight (~60–100 MB). Spikes above
  300 MB indicate a query returning an unexpectedly large result set.
- **CPU**: should be low between requests. Sustained CPU during idle indicates
  a background task or runaway query.
- **PostgreSQL connections**: `SELECT COUNT(*) FROM pg_stat_activity;` via
  Railway's query console should stay well under 10 during normal operation.

### If Railway restarts the service

The `server_lifespan` context manager in `server.py` runs `db_lifespan` on
every startup, which calls `get_pool()` to create a fresh asyncpg pool. There
is no manual action required — the pool recreates itself on the next request
if it was closed.

`railway.toml` is configured with `restartPolicyType = "on-failure"` and
`restartPolicyMaxRetries = 3`, so transient crashes auto-recover.

### Alerting

In the Railway dashboard under **Project Settings** → **Notifications**, you
can configure:
- Email or Slack webhook on service deployment failure
- Email or Slack webhook on service crash / restart

Set up at least one notification channel before going live.

### Refreshing materialized views

The materialized views (`mv_property_profile`, `mv_violation_summary`,
`mv_current_ownership`) are static snapshots. Refresh them after each nycdb
data update:

```sql
-- Run via psql against the Railway public URL or Railway query console
REFRESH MATERIALIZED VIEW CONCURRENTLY mv_property_profile;
REFRESH MATERIALIZED VIEW CONCURRENTLY mv_violation_summary;
REFRESH MATERIALIZED VIEW CONCURRENTLY mv_current_ownership;  -- ~10 min
```

`CONCURRENTLY` requires the unique indexes that `create_views.sql` creates, and
keeps the view readable during refresh.

---

## 7. Security Notes

### Token generation

```bash
# Generate a cryptographically random 64-character hex token
openssl rand -hex 32
```

Store this value only in:
- Railway's environment variables UI (never in the repository)
- Your local password manager
- The `headers` field of your Claude Desktop / Claude Code config

### How the token is validated

`server.py` wraps the FastMCP ASGI app in `_BearerTokenMiddleware`. Every HTTP
and WebSocket request must carry:

```
Authorization: Bearer <MCP_SERVER_TOKEN>
```

Requests without a valid header receive `HTTP 401` with body
`{"error":"Unauthorized"}` before any MCP processing occurs. The middleware is
a pure ASGI class (not Starlette's `BaseHTTPMiddleware`) so it does not buffer
SSE streams — safe for long-lived connections.

### Database exposure

Railway PostgreSQL is only reachable from within the Railway project's private
network by default. The MCP server connects using the internal
`railway.internal` hostname. The public URL you used for `pg_restore` should
be disabled after data loading (Railway plugin → **Settings** → disable public
networking) to eliminate the external attack surface.

Never commit a `DATABASE_URL` containing credentials. Use Railway's reference
variable syntax `${{Postgres.DATABASE_URL}}` in the service's variable
configuration.

### Secrets rotation

If `MCP_SERVER_TOKEN` is compromised:
1. Generate a new token: `openssl rand -hex 32`
2. Update the Railway service variable — Railway redeploys automatically
3. Update the token in your Claude Desktop config and/or re-run `claude mcp add`

---

## Appendix: Files Created Alongside This Document

| File | Purpose |
|------|---------|
| `site/vercel.json` | Cache, security, and redirect rules for Vercel |
| `nixpacks.toml` | Pins Python 3.12 + uv for Railway nixpacks build |
