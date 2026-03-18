# NYC Property Intel

MCP server that gives Claude access to NYC public property data for real estate due diligence. Consolidates 22+ city data sources into five AI-callable tools — property lookup, violation history, sales history, comparable sales, and comprehensive analysis.

**This is a due diligence tool, not an appraisal tool.** It surfaces public record data; it does not estimate property values.

## Tools

| Tool | Description |
|------|-------------|
| `lookup_property` | Look up a property by address or BBL. Returns profile, zoning, assessed values, owner. Always call this first. |
| `get_property_issues` | HPD housing violations + DOB building code violations. Filter by severity, status, date. |
| `get_property_history` | DOF sales history + ACRIS deed transfers. Shows price trajectory and ownership changes. |
| `search_comps` | Comparable sales in the same zip code. Filters by building class, date range, price. |
| `analyze_property` | Full due diligence summary — runs all sub-queries concurrently. Property profile, financials, development potential (FAR), risk factors, comps, and key observations. |

## Quick Start

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- PostgreSQL 16+ running locally
- [nycdb](https://github.com/nycdb/nycdb) CLI (`pip install nycdb`)

### 1. Clone and install

```bash
git clone https://github.com/YOUR_USERNAME/nyc-property-intel.git
cd nyc-property-intel
uv sync
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your credentials:
#   DATABASE_URL=postgresql://nycdb:nycdb@localhost:5432/nycdb
#   NYC_GEOCLIENT_SUBSCRIPTION_KEY=your_key  (optional, for address lookup)
```

### 3. Set up the database

**Option A: Load from dump (fast, ~10 min)**

If you have a `data/nycdb.dump` file:

```bash
createuser -s nycdb 2>/dev/null; createdb -O nycdb nycdb 2>/dev/null
pg_restore -U nycdb -d nycdb --no-owner --jobs=4 data/nycdb.dump
```

**Option B: Load from source (slow, ~2.5 hours)**

```bash
createuser -s nycdb 2>/dev/null; createdb -O nycdb nycdb 2>/dev/null
chmod +x scripts/seed_nycdb.sh
./scripts/seed_nycdb.sh              # All phases
# or load incrementally:
./scripts/seed_nycdb.sh --phase A    # Core data (~30 min)
./scripts/seed_nycdb.sh --phase B    # Sales + DOB (~45 min)
./scripts/seed_nycdb.sh --phase C    # ACRIS + permits (~90 min)
```

After loading, create indexes and materialized views:

```bash
psql -U nycdb -d nycdb -f scripts/create_indexes.sql
psql -U nycdb -d nycdb -f scripts/create_views.sql
```

### 4. Add to Claude Desktop

Add to your `claude_desktop_config.json` (Claude Desktop > Settings > Developer > Edit Config):

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

The `.mcp.json` file in the project root auto-registers the server when you open this directory in Claude Code.

Or add manually to `~/.claude.json`:

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

## Example Queries

Once connected, ask Claude:

- "Look up 350 5th Ave, Manhattan"
- "What violations does BBL 1012150061 have?"
- "Show me the sales history for 170 West 85th Street, Manhattan"
- "Find comparable sales near 11215"
- "Give me a full due diligence report on BBL 3010060055"

## Data Sources

All data comes from official NYC open data via [nycdb](https://github.com/nycdb/nycdb):

- **PLUTO** — Property Land Use Tax Lot Output (lot dimensions, zoning, building class)
- **PAD** — Property Address Directory (address-to-BBL resolution)
- **HPD Violations** — Housing Preservation & Development violation records
- **DOB Violations** — Department of Buildings violation records
- **DOF Sales** — Department of Finance rolling and annual property sales
- **ACRIS** — Automated City Register Information System (deeds, mortgages, liens)
- **Rent Stabilization** — Rent stabilized unit counts by building
- **DOF Assessments** — Tax assessments and exemptions
- Plus: HPD complaints, HPD registrations, HPD litigations, ECB violations, DOB jobs, tax liens

## Development

```bash
# Run tests
uv run pytest tests/test_utils.py -q              # Unit tests (no DB needed)
uv run pytest tests/ -m integration -q             # Integration tests (needs DB)

# Lint
uv run ruff check src/

# Run the server directly
uv run nyc-property-intel
```

## Architecture

```
src/nyc_property_intel/
  app.py          # FastMCP instance + system instructions
  server.py       # Entry point — wires lifespan, imports tools, starts server
  config.py       # pydantic-settings config (reads .env)
  db.py           # asyncpg connection pool + query helpers
  geoclient.py    # NYC GeoClient API + PAD fallback for address resolution
  utils.py        # BBL validation, borough mappings, formatting
  tools/
    lookup.py     # lookup_property
    issues.py     # get_property_issues
    history.py    # get_property_history
    comps.py      # search_comps
    analysis.py   # analyze_property
scripts/
  seed_nycdb.sh       # Data loader (downloads + loads all datasets)
  create_indexes.sql   # Performance indexes
  create_views.sql     # Materialized views for fast lookups
```

## License

MIT
