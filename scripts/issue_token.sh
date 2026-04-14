#!/usr/bin/env bash
# issue_token.sh — Issue a new NYC Property Intel MCP token for a customer.
#
# Usage:
#   ./scripts/issue_token.sh                          # interactive prompts
#   ./scripts/issue_token.sh user@example.com pro     # non-interactive
#   ./scripts/issue_token.sh user@example.com trial "Beta tester"
#
# Requires RAILWAY_DB env var (set in ~/.zshrc):
#   export RAILWAY_DB="postgresql://postgres:<pass>@switchback.proxy.rlwy.net:33576/railway"

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── Resolve Railway DB URL ────────────────────────────────────────────
DB_URL="${RAILWAY_DB:-}"
if [[ -z "$DB_URL" ]]; then
    echo "ERROR: RAILWAY_DB is not set."
    echo "  Add to ~/.zshrc:  export RAILWAY_DB=\"postgresql://postgres:<pass>@switchback.proxy.rlwy.net:33576/railway\""
    exit 1
fi

# ── Args or interactive prompts ───────────────────────────────────────
EMAIL="${1:-}"
PLAN="${2:-}"
NOTES="${3:-}"

if [[ -z "$EMAIL" ]]; then
    read -rp "Customer email: " EMAIL
fi

if [[ -z "$PLAN" ]]; then
    echo "Plans: trial (50 calls/day, 7-day expiry) | pro (500/day) | team (2000/day)"
    read -rp "Plan [trial]: " PLAN
    PLAN="${PLAN:-trial}"
fi

if [[ -z "$NOTES" ]]; then
    read -rp "Notes (optional): " NOTES
fi

# ── Issue token ───────────────────────────────────────────────────────
cd "$REPO_ROOT"
DATABASE_URL="$DB_URL" uv run python scripts/manage_tokens.py create \
    --email "$EMAIL" \
    --plan  "$PLAN" \
    --notes "$NOTES"
