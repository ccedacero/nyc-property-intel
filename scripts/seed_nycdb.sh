#!/bin/bash
# =============================================================================
# NYC Property Intel — NYCDB Dataset Loader
# =============================================================================
# Downloads, loads, and verifies NYCDB datasets into PostgreSQL.
#
# Usage:
#   ./scripts/seed_nycdb.sh                # Load all phases (A, B, C)
#   ./scripts/seed_nycdb.sh --phase A      # Load only Phase A datasets
#   ./scripts/seed_nycdb.sh --phase B      # Load only Phase B datasets
#   ./scripts/seed_nycdb.sh --phase C      # Load only Phase C datasets
#
# Environment variables:
#   DB_USER   (default: nycdb)
#   DB_NAME   (default: nycdb)
#   DB_PASS   (default: nycdb)
#   DB_HOST   (default: localhost)
#   DB_PORT   (default: 5432)
#   DATA_DIR  (default: ./data)
#
# Estimated load times (SSD, 16GB RAM, broadband):
#   Phase A: ~30 min  (pluto_latest ~10m, pad ~10m, hpd_violations ~10m)
#   Phase B: ~45 min  (dof_annual_sales ~15m, dob_violations ~10m, others ~20m)
#   Phase C: ~90 min  (acris ~60m, dobjobs ~20m, dob_now_jobs ~10m)
#   Total:   ~2.5 hours first run
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DB_USER="${DB_USER:-nycdb}"
DB_NAME="${DB_NAME:-nycdb}"
DB_PASS="${DB_PASS:-nycdb}"
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
DATA_DIR="${DATA_DIR:-./data}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

MAX_RETRIES=3
RETRY_BACKOFF_BASE=10  # seconds; retry 1 = 10s, retry 2 = 20s, retry 3 = 30s

# Find nycdb binary
NYCDB_BIN="${NYCDB_BIN:-$(command -v nycdb 2>/dev/null || echo "$HOME/Library/Python/3.9/bin/nycdb")}"
if [ ! -x "$NYCDB_BIN" ]; then
    echo "ERROR: nycdb not found. Install with: pip3 install nycdb" >&2
    exit 1
fi

# NYCDB common flags
NYCDB_FLAGS="-U $DB_USER -D $DB_NAME -P $DB_PASS -H $DB_HOST --port $DB_PORT --root-dir $DATA_DIR"

# ---------------------------------------------------------------------------
# Phase definitions
# ---------------------------------------------------------------------------

# Phase A: Core property data — enough for lookup_property + get_property_issues (HPD only)
# Est. disk: ~4 GB, Est. time: ~30 min
PHASE_A_DATASETS=(
    pluto_latest          # ~870K rows, ~800MB — core property profile
    pad                   # ~large, ~500MB — address-to-BBL fallback
    hpd_violations        # ~4M rows, ~2GB — housing violations
)

# Phase B: Sales, DOB violations, tax data, rent stabilization
# Est. disk: ~6 GB, Est. time: ~45 min
PHASE_B_DATASETS=(
    dof_sales                               # ~60K rows, ~50MB — rolling sales
    dof_annual_sales                        # ~1M rows, ~500MB — historical sales
    dob_violations                          # ~2M rows, ~1GB — building code violations
    dof_property_valuation_and_assessments  # ~6M rows, ~3GB — tax assessments
    dof_exemptions                          # ~740K rows, ~300MB — tax exemptions
    dof_tax_lien_sale_list                  # varies, ~10MB — tax liens
    hpd_complaints                          # large, ~1GB — tenant complaints
    hpd_registrations                       # ~150K+600K rows, ~300MB — owner/agent registration
    hpd_litigations                         # moderate, ~100MB — lawsuits
    rentstab                                # ~45K rows, ~20MB — rent stabilization
    ecb_violations                          # large, ~500MB — ECB violations
)

# Phase C: ACRIS (ownership/transactions), DOB jobs (permits)
# Est. disk: ~6 GB, Est. time: ~90 min
PHASE_C_DATASETS=(
    acris                 # 14 tables, millions of rows, ~4GB — deeds, mortgages, liens
    dobjobs               # ~1M rows, ~2GB — legacy DOB permits
)
# Note: dob_now_jobs is loaded via the dobjobs dataset in nycdb

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

err() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $*" >&2
}

# Check if a dataset's primary table already exists and has data
dataset_loaded() {
    local ds="$1"
    local table_name

    # Map dataset name to its primary table for existence check
    case "$ds" in
        pluto_latest)       table_name="pluto_latest" ;;
        pad)                table_name="pad_adr" ;;
        hpd_violations)     table_name="hpd_violations" ;;
        hpd_complaints)     table_name="hpd_complaints" ;;
        hpd_registrations)  table_name="hpd_registrations" ;;
        hpd_litigations)    table_name="hpd_litigations" ;;
        dof_sales)          table_name="dof_sales" ;;
        dof_annual_sales)   table_name="dof_annual_sales" ;;
        dob_violations)     table_name="dob_violations" ;;
        dof_property_valuation_and_assessments) table_name="dof_property_valuation_and_assessments" ;;
        dof_exemptions)     table_name="dof_exemptions" ;;
        dof_tax_lien_sale_list) table_name="dof_tax_lien_sale_list" ;;
        rentstab)           table_name="rentstab" ;;
        ecb_violations)     table_name="ecb_violations" ;;
        acris)              table_name="acris_real_property_master" ;;
        dobjobs)            table_name="dobjobs" ;;
        *)                  table_name="$ds" ;;
    esac

    local count
    count=$(PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
        -tAc "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = '$table_name'" 2>/dev/null || echo "0")

    if [ "$count" = "1" ]; then
        local row_count
        row_count=$(PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
            -tAc "SELECT reltuples::bigint FROM pg_class WHERE relname = '$table_name'" 2>/dev/null || echo "0")
        if [ "$row_count" != "0" ] && [ "$row_count" != "-1" ]; then
            log "  SKIP: $ds already loaded ($table_name has ~$row_count rows)"
            return 0
        fi
    fi
    return 1
}

# Load a single dataset with retry logic
load_dataset() {
    local ds="$1"
    local attempt=1

    # Check if already loaded
    if dataset_loaded "$ds"; then
        return 0
    fi

    while [ $attempt -le $MAX_RETRIES ]; do
        log "  Downloading $ds (attempt $attempt/$MAX_RETRIES)..."
        if $NYCDB_BIN --download "$ds" $NYCDB_FLAGS 2>&1; then
            log "  Download complete."
        else
            err "Download failed for $ds (attempt $attempt/$MAX_RETRIES)"
            if [ $attempt -lt $MAX_RETRIES ]; then
                local wait_time=$((RETRY_BACKOFF_BASE * attempt))
                log "  Retrying in ${wait_time}s..."
                sleep "$wait_time"
                attempt=$((attempt + 1))
                continue
            else
                err "FAILED: $ds download after $MAX_RETRIES attempts. Skipping."
                return 1
            fi
        fi

        log "  Loading $ds into PostgreSQL..."
        if $NYCDB_BIN --load "$ds" $NYCDB_FLAGS 2>&1; then
            log "  Load complete."
        else
            err "Load failed for $ds (attempt $attempt/$MAX_RETRIES)"
            if [ $attempt -lt $MAX_RETRIES ]; then
                local wait_time=$((RETRY_BACKOFF_BASE * attempt))
                log "  Retrying in ${wait_time}s..."
                sleep "$wait_time"
                attempt=$((attempt + 1))
                continue
            else
                err "FAILED: $ds load after $MAX_RETRIES attempts. Skipping."
                return 1
            fi
        fi

        # Verify
        log "  Verifying $ds..."
        if $NYCDB_BIN --verify "$ds" $NYCDB_FLAGS 2>&1; then
            log "  Verification passed."
            return 0
        else
            err "Verification failed for $ds (attempt $attempt/$MAX_RETRIES)"
            if [ $attempt -lt $MAX_RETRIES ]; then
                local wait_time=$((RETRY_BACKOFF_BASE * attempt))
                log "  Retrying from scratch in ${wait_time}s..."
                sleep "$wait_time"
                attempt=$((attempt + 1))
            else
                err "FAILED: $ds verification after $MAX_RETRIES attempts."
                return 1
            fi
        fi
    done
}

# Apply SQL scripts (indexes and/or views) for a given phase
apply_sql() {
    local phase="$1"
    log "Applying indexes for Phase $phase..."
    PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
        -f "$SCRIPT_DIR/create_indexes.sql" 2>&1 || {
        err "Some indexes may have failed (tables not yet loaded). This is OK if running partial phases."
    }

    log "Applying materialized views for Phase $phase..."
    # Views depend on multiple tables. Only create views whose tables exist.
    # We run the full script but individual CREATE MATERIALIZED VIEW IF NOT EXISTS
    # will fail gracefully if source tables are missing.
    PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
        -f "$SCRIPT_DIR/create_views.sql" 2>&1 || {
        err "Some views may have failed (source tables not yet loaded). Re-run after loading more phases."
    }
}

# Verify row counts for loaded datasets
verify_counts() {
    log "=== Dataset Row Counts ==="
    PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -c "
        SELECT
            relname AS table_name,
            reltuples::bigint AS estimated_rows
        FROM pg_class
        WHERE relname IN (
            'pluto_latest', 'pad_adr', 'pad_bbl',
            'hpd_violations', 'hpd_complaints', 'hpd_registrations', 'hpd_contacts',
            'hpd_litigations',
            'dof_sales', 'dof_annual_sales',
            'dof_property_valuation_and_assessments',
            'dof_exemptions', 'dof_exemption_classification_codes',
            'dof_tax_lien_sale_list',
            'dob_violations', 'dobjobs', 'dob_now_jobs',
            'rentstab', 'ecb_violations',
            'acris_real_property_master', 'acris_real_property_legals',
            'acris_real_property_parties', 'acris_document_control_codes',
            'mv_property_profile', 'mv_violation_summary', 'mv_current_ownership'
        )
        ORDER BY relname;
    " 2>/dev/null || log "Could not query row counts."
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

# Parse arguments
PHASE="${1:-}"
if [ "$PHASE" = "--phase" ]; then
    PHASE="${2:-}"
    if [ -z "$PHASE" ]; then
        err "Usage: $0 [--phase A|B|C]"
        exit 1
    fi
    PHASE=$(echo "$PHASE" | tr '[:lower:]' '[:upper:]')
else
    PHASE="ALL"
fi

mkdir -p "$DATA_DIR"

log "============================================="
log "NYC Property Intel — NYCDB Dataset Loader"
log "============================================="
log "Phase: $PHASE"
log "Database: $DB_USER@$DB_HOST:$DB_PORT/$DB_NAME"
log "Data dir: $DATA_DIR"
log "============================================="

FAILED_DATASETS=()

# Phase A
if [ "$PHASE" = "ALL" ] || [ "$PHASE" = "A" ]; then
    log ""
    log "=== PHASE A: Core property data ==="
    for ds in "${PHASE_A_DATASETS[@]}"; do
        log "--- $ds ---"
        if ! load_dataset "$ds"; then
            FAILED_DATASETS+=("$ds")
        fi
    done
    apply_sql "A"
fi

# Phase B
if [ "$PHASE" = "ALL" ] || [ "$PHASE" = "B" ]; then
    log ""
    log "=== PHASE B: Sales, DOB violations, tax data ==="
    for ds in "${PHASE_B_DATASETS[@]}"; do
        log "--- $ds ---"
        if ! load_dataset "$ds"; then
            FAILED_DATASETS+=("$ds")
        fi
    done
    apply_sql "B"
fi

# Phase C
if [ "$PHASE" = "ALL" ] || [ "$PHASE" = "C" ]; then
    log ""
    log "=== PHASE C: ACRIS + DOB permits ==="
    for ds in "${PHASE_C_DATASETS[@]}"; do
        log "--- $ds ---"
        if ! load_dataset "$ds"; then
            FAILED_DATASETS+=("$ds")
        fi
    done
    apply_sql "C"
fi

# Final verification
log ""
verify_counts

# Summary
log ""
log "============================================="
if [ ${#FAILED_DATASETS[@]} -eq 0 ]; then
    log "All datasets loaded successfully!"
else
    err "The following datasets FAILED to load:"
    for ds in "${FAILED_DATASETS[@]}"; do
        err "  - $ds"
    done
    err "Re-run this script to retry failed datasets (already-loaded datasets will be skipped)."
    exit 1
fi
log "============================================="
