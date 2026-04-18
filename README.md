# NYC Property Intel

MCP server that gives Claude AI access to 20+ NYC public record datasets for real estate due diligence. Ask Claude about any NYC property in plain English — violations, liens, sales history, ownership, permits, rent stabilization, zoning, fire history, crime data, and more.

**This is a due diligence tool, not an appraisal tool.** It surfaces public record data only; it does not estimate property values.

## Quickstart — Hosted (no local setup)

Sign up at [nycpropertyintel.com](https://nycpropertyintel.com) to get a free trial token. Then:

**Claude Code** — run once in your terminal:

```bash
claude mcp add --transport http nyc-property-intel \
  "https://nyc-property-intel-production.up.railway.app/mcp" \
  --header "Authorization: Bearer YOUR_TOKEN" \
  --scope user
```

**Claude Desktop** — add to `claude_desktop_config.json` (Settings → Developer → Edit Config):

```json
{
  "mcpServers": {
    "nyc-property-intel": {
      "type": "http",
      "url": "https://nyc-property-intel-production.up.railway.app/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_TOKEN"
      }
    }
  }
}
```

Then ask Claude:

```
"Look up 350 5th Ave, Manhattan"
"What violations does 123 Atlantic Ave, Brooklyn have?"
"Full due diligence on 123 Atlantic Ave, Brooklyn"
```

---

## Self-Hosting

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- PostgreSQL 16+
- [nycdb](https://github.com/nycdb/nycdb) CLI (`pip install nycdb`)

### 1. Clone and install

```bash
git clone https://github.com/ccedacero/nyc-property-intel.git
cd nyc-property-intel
uv sync
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env:
#   DATABASE_URL=postgresql://nycdb:nycdb@localhost:5432/nycdb
#   NYC_GEOCLIENT_SUBSCRIPTION_KEY=your_key  (optional, improves address resolution)
#   SOCRATA_APP_TOKEN=your_token             (optional, higher rate limits for 311/FDNY/NYPD)
```

### 3. Set up the database

**Option A: Restore from dump (~10 min)**

```bash
createuser -s nycdb 2>/dev/null; createdb -O nycdb nycdb 2>/dev/null
pg_restore -U nycdb -d nycdb --no-owner --jobs=4 data/nycdb.dump
```

**Option B: Load from source (~2.5 hours)**

```bash
createuser -s nycdb 2>/dev/null; createdb -O nycdb nycdb 2>/dev/null
chmod +x scripts/seed_nycdb.sh
./scripts/seed_nycdb.sh              # all phases
# or incrementally:
./scripts/seed_nycdb.sh --phase A    # core data (~30 min)
./scripts/seed_nycdb.sh --phase B    # sales + DOB (~45 min)
./scripts/seed_nycdb.sh --phase C    # ACRIS + permits (~90 min)
```

Then create indexes and materialized views:

```bash
psql -U nycdb -d nycdb -f scripts/create_indexes.sql
psql -U nycdb -d nycdb -f scripts/create_views.sql
```

### 4. Add to Claude Desktop

```json
{
  "mcpServers": {
    "nyc-property-intel": {
      "command": "uv",
      "args": ["run", "nyc-property-intel"],
      "cwd": "/absolute/path/to/nyc-property-intel",
      "env": {
        "DATABASE_URL": "postgresql://nycdb:nycdb@localhost:5432/nycdb"
      }
    }
  }
}
```

### 5. Add to Claude Code

The `.mcp.json` in the project root auto-registers when you open this directory. Or add manually to `~/.claude.json`:

```json
{
  "mcpServers": {
    "nyc-property-intel": {
      "command": "uv",
      "args": ["run", "nyc-property-intel"],
      "cwd": "/absolute/path/to/nyc-property-intel"
    }
  }
}
```

---

## 18 Tools

| Tool | Description |
|------|-------------|
| `lookup_property` | Resolve any NYC address or BBL to a full property profile: owner, building class, zoning, FAR, assessed value, lot dimensions. **Always call this first.** |
| `get_property_issues` | HPD housing violations (Class A/B/C), DOB building code violations, and ECB/OATH monetary penalties. Filter by severity, status, and date. |
| `get_property_history` | DOF sales records and ACRIS deed transfers. Price trajectory, buyer/seller names, document types going back to 2003. |
| `get_hpd_complaints` | Tenant-reported HPD complaints — leading indicators of building distress before formal violations are issued. |
| `get_hpd_litigations` | HPD housing court cases, open judgements, and harassment findings against building owners. |
| `get_hpd_registration` | Legal owner, managing agent, and head officer registration records. |
| `get_building_permits` | DOB job filings: new buildings, alterations, demolitions, sign permits. Status, cost estimate, applicant name. |
| `get_liens_and_encumbrances` | DOF tax lien sale list entries and ACRIS mortgage records. Outstanding liens, lender names, amounts, satisfactions. |
| `get_tax_info` | Tax assessments, market value estimates, taxable value, and active exemptions (421a, J-51, STAR). |
| `get_rent_stabilization` | Rent-stabilized unit counts by year (2007–2017). Trend analysis for deregulation signal. |
| `search_comps` | Comparable sales by zip code. Filter by building class, price, date. Includes quarterly market stats. |
| `search_neighborhood_stats` | Area-level aggregates: property stock, median sale prices, violation rates, rent stabilization share. |
| `get_fdny_fire_incidents` | FDNY fire and emergency incident history. Fire type, alarm level, spread, casualties, duration. 2013–present, loaded from NYC Open Data. |
| `get_311_complaints` | 311 service requests at or near a property. Noise, rodents, heat, illegal dumping, and 200+ types. 2010–present, loaded from NYC Open Data. |
| `get_evictions` | Marshal-executed evictions by address. Residential and commercial. 2017–present, loaded from NYC Open Data. |
| `get_dob_complaints` | DOB complaints filed before formal violations — the earliest public signal of construction or safety issues. |
| `get_nypd_crime` | NYPD crime complaints within a configurable radius (default 300 m ≈ 3 blocks). Felony/misdemeanor breakdown, top offenses, year-over-year trend. |
| `analyze_property` | Full due diligence summary — runs all sub-queries concurrently. Property profile, FAR analysis, financials, risk factors, rent stabilization, comparable sales, and key observations. |

---

## Data Sources

Core data (~19 million rows) is loaded from [nycdb](https://github.com/nycdb/nycdb). All datasets are loaded into PostgreSQL. Socrata API used as fallback only.

| Dataset | Agency | Notes |
|---------|--------|-------|
| PLUTO | DCP | Property profiles, zoning, FAR, building class |
| PAD | DCP | Address-to-BBL resolution |
| HPD Violations | HPD | Housing code violations by class and status |
| HPD Complaints | HPD | Tenant complaint records |
| HPD Registrations | HPD | Owner/agent/officer registration |
| HPD Litigations | HPD | Housing court cases |
| DOB Violations | DOB | Building code violations |
| ECB Violations | OATH/ECB | Environmental Control Board penalties |
| DOF Rolling Sales | DOF | Recent property sales |
| DOF Annual Sales | DOF | Historical sales 2003–present |
| DOF Assessments | DOF | Tax valuations and assessment rolls |
| DOF Exemptions | DOF | Tax exemption records (421a, J-51, STAR) |
| DOF Tax Liens | DOF | Annual lien sale list |
| Rent Stabilization | HCR/RGB | Stabilized unit counts by building |
| ACRIS | DOF | Deeds, mortgages, liens, satisfactions, UCC filings |
| FDNY Fire Incidents | FDNY | 2013–present |
| 311 Service Requests | 311/DOITT | 2010–present |
| Marshal Evictions | DOI | 2017–present |
| DOB Complaints | DOB | Loaded from NYC Open Data |
| NYPD Crime Data | NYPD | 2006–present, geospatial radius |

---

## Architecture

```
src/nyc_property_intel/
  app.py              # FastMCP instance + system prompt
  server.py           # Entry point: lifespan, auth middleware, tool registration
  config.py           # pydantic-settings (reads .env / environment variables)
  db.py               # asyncpg connection pool + query helpers
  auth.py             # Token validation, rate limiting, usage logging
  analytics.py        # PostHog event capture (fire-and-forget)
  geoclient.py        # NYC GeoClient API + PAD fallback for address resolution
  socrata.py          # Socrata Open Data API client — fallback for FDNY, 311, NYPD, evictions
  loops_webhook.py    # Loops.so webhook → auto-provision trial tokens on signup
  tools/
    lookup.py         # lookup_property
    issues.py         # get_property_issues
    history.py        # get_property_history
    hpd_complaints.py # get_hpd_complaints
    hpd_litigations.py# get_hpd_litigations
    hpd_registration.py# get_hpd_registration
    permits.py        # get_building_permits
    liens.py          # get_liens_and_encumbrances
    tax.py            # get_tax_info
    rentstab.py       # get_rent_stabilization
    comps.py          # search_comps
    neighborhood.py   # search_neighborhood_stats
    fdny.py           # get_fdny_fire_incidents
    complaints_311.py # get_311_complaints
    evictions.py      # get_evictions
    dob_complaints.py # get_dob_complaints
    nypd_crime.py     # get_nypd_crime
    analysis.py       # analyze_property
scripts/
  seed_nycdb.sh         # Downloads and loads all nycdb datasets
  create_indexes.sql    # Performance indexes on critical columns
  create_views.sql      # Materialized views for fast property lookup
  manage_tokens.py      # CLI for provisioning and managing customer tokens
```

---

## Development

```bash
uv run pytest tests/test_utils.py -q          # unit tests (no DB needed)
uv run pytest tests/ -m integration -q        # integration tests (needs live DB)
uv run ruff check src/                         # lint
uv run nyc-property-intel                      # run server locally (stdio)
```

---

## Fair Housing

NYC Property Intel provides building and property data from public city records only. It does not provide demographic data, tenant screening, or any analysis based on protected characteristics. See [nycpropertyintel.com/#fair-housing](https://nycpropertyintel.com/#fair-housing) for the full policy.

## License

MIT
