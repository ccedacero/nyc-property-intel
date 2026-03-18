#!/bin/bash
# =============================================================================
# NYC Property Intel — Materialized View Refresh
# =============================================================================
# Refreshes all materialized views CONCURRENTLY (non-blocking reads).
# Requires UNIQUE indexes on each view (created by create_views.sql).
#
# WARNING: Do NOT call this from the MCP server. Run via cron or manually.
#
# Usage:
#   ./scripts/refresh_views.sh                   # Refresh all views
#   ./scripts/refresh_views.sh --view profile    # Refresh only mv_property_profile
#   ./scripts/refresh_views.sh --view violations # Refresh only mv_violation_summary
#   ./scripts/refresh_views.sh --view ownership  # Refresh only mv_current_ownership
#
# Suggested cron schedule:
#   # Weekly refresh (Sunday 3am)
#   0 3 * * 0 /path/to/nyc-property-intel/scripts/refresh_views.sh >> /var/log/npi-refresh.log 2>&1
#
# Estimated refresh times:
#   mv_property_profile:   ~2 min
#   mv_violation_summary:  ~5 min
#   mv_current_ownership:  ~10 min
#   Total:                 ~17 min
# =============================================================================

set -euo pipefail

DB_USER="${DB_USER:-nycdb}"
DB_NAME="${DB_NAME:-nycdb}"
DB_PASS="${DB_PASS:-nycdb}"
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"

# Timeout per view refresh (in seconds). These views scan large tables.
# mv_current_ownership scans all of ACRIS and can take 10+ minutes.
STATEMENT_TIMEOUT_MS=900000  # 15 minutes

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

err() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $*" >&2
}

refresh_view() {
    local view_name="$1"
    local start_time end_time elapsed

    # Check that the view exists
    local exists
    exists=$(PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
        -tAc "SELECT COUNT(*) FROM pg_matviews WHERE matviewname = '$view_name'" 2>/dev/null || echo "0")

    if [ "$exists" != "1" ]; then
        err "View $view_name does not exist. Skipping."
        return 1
    fi

    log "Refreshing $view_name..."
    start_time=$(date +%s)

    PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
        -c "SET statement_timeout = '$STATEMENT_TIMEOUT_MS';" \
        -c "REFRESH MATERIALIZED VIEW CONCURRENTLY $view_name;" 2>&1

    local exit_code=$?
    end_time=$(date +%s)
    elapsed=$((end_time - start_time))

    if [ $exit_code -eq 0 ]; then
        log "  $view_name refreshed in ${elapsed}s"
    else
        err "  $view_name refresh FAILED after ${elapsed}s"
        return 1
    fi
}

# Parse arguments
VIEW_FILTER="${1:-}"
SPECIFIC_VIEW="${2:-}"

if [ "$VIEW_FILTER" = "--view" ] && [ -n "$SPECIFIC_VIEW" ]; then
    case "$SPECIFIC_VIEW" in
        profile)    VIEWS=("mv_property_profile") ;;
        violations) VIEWS=("mv_violation_summary") ;;
        ownership)  VIEWS=("mv_current_ownership") ;;
        *)
            err "Unknown view: $SPECIFIC_VIEW"
            err "Valid options: profile, violations, ownership"
            exit 1
            ;;
    esac
else
    VIEWS=(
        mv_property_profile
        mv_violation_summary
        mv_current_ownership
    )
fi

log "============================================="
log "NYC Property Intel — View Refresh"
log "============================================="
log "Database: $DB_USER@$DB_HOST:$DB_PORT/$DB_NAME"
log "Views to refresh: ${VIEWS[*]}"
log "Statement timeout: ${STATEMENT_TIMEOUT_MS}ms"
log "============================================="

FAILED=0
TOTAL_START=$(date +%s)

for view in "${VIEWS[@]}"; do
    if ! refresh_view "$view"; then
        FAILED=$((FAILED + 1))
    fi
done

TOTAL_END=$(date +%s)
TOTAL_ELAPSED=$((TOTAL_END - TOTAL_START))

log ""
log "============================================="
if [ $FAILED -eq 0 ]; then
    log "All views refreshed successfully in ${TOTAL_ELAPSED}s"
else
    err "$FAILED view(s) failed to refresh"
    exit 1
fi
log "============================================="
