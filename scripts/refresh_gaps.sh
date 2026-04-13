#!/bin/bash
# =============================================================================
# NYC Property Intel — Gap Dataset Refresh
# =============================================================================
# Re-downloads the 5 datasets with significant gaps, loads locally,
# verifies against Socrata live counts, then syncs to Railway.
#
# Usage:
#   ./scripts/refresh_gaps.sh                    # Full run (all phases)
#   ./scripts/refresh_gaps.sh --phase download   # Just download + load locally
#   ./scripts/refresh_gaps.sh --phase verify     # Just verify local vs Socrata
#   ./scripts/refresh_gaps.sh --phase railway    # Just sync to Railway
#
# Safe to re-run — tracks state per dataset and skips completed steps.
# Compatible with macOS bash 3.2 (no associative arrays).
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROJECT_DIR="/Users/devtzi/dev/nyc-property-intel"
DATA_DIR="$PROJECT_DIR/data"
DUMP_DIR="$DATA_DIR/dumps"
STATE_FILE="$DATA_DIR/.refresh_state"
LOG_FILE="$DATA_DIR/refresh.log"

LOCAL_DB_USER="nycdb"
LOCAL_DB_NAME="nycdb"
LOCAL_DB_PASS="nycdb"
LOCAL_DB_HOST="localhost"
LOCAL_DB_PORT="5432"
LOCAL_DB="postgresql://${LOCAL_DB_USER}:${LOCAL_DB_PASS}@${LOCAL_DB_HOST}:${LOCAL_DB_PORT}/${LOCAL_DB_NAME}"

RAILWAY_DB="${RAILWAY_DB:-}"

NYCDB_BIN="${NYCDB_BIN:-$(command -v nycdb 2>/dev/null || echo "$HOME/Library/Python/3.9/bin/nycdb")}"
NYCDB_FLAGS="-U $LOCAL_DB_USER -D $LOCAL_DB_NAME -P $LOCAL_DB_PASS -H $LOCAL_DB_HOST --port $LOCAL_DB_PORT --root-dir $DATA_DIR"

MAX_RETRIES=3
RETRY_BACKOFF_BASE=10

# Datasets to refresh (ordered: fastest first, ACRIS last)
REFRESH_DATASETS="hpd_violations ecb_violations hpd_complaints dobjobs acris"

# Materialized views to drop/rebuild
MAT_VIEWS="mv_current_ownership mv_violation_summary mv_property_profile"

# ---------------------------------------------------------------------------
# Lookup functions (replaces associative arrays for bash 3.2)
# ---------------------------------------------------------------------------

get_tables_for_dataset() {
    case "$1" in
        hpd_violations) echo "hpd_violations" ;;
        ecb_violations) echo "ecb_violations" ;;
        hpd_complaints) echo "hpd_complaints_and_problems" ;;
        dobjobs)        echo "dobjobs dob_now_jobs" ;;
        acris)          echo "real_property_master real_property_legals real_property_parties real_property_references real_property_remarks personal_property_master personal_property_legals personal_property_parties personal_property_references personal_property_remarks acris_country_codes acris_document_control_codes acris_property_type_codes acris_ucc_collateral_codes" ;;
        *)              echo "" ;;
    esac
}

get_csv_patterns_for_dataset() {
    case "$1" in
        hpd_violations) echo "hpd_violations*.csv" ;;
        ecb_violations) echo "ecb_violations*.csv" ;;
        hpd_complaints) echo "hpd_complaints*.csv" ;;
        dobjobs)        echo "dobjobs*.csv dob_now_jobs*.csv" ;;
        acris)          echo "acris_*.csv real_property_*.csv personal_property_*.csv" ;;
        *)              echo "" ;;
    esac
}

get_socrata_id() {
    case "$1" in
        hpd_violations)       echo "wvxf-dwi5" ;;
        ecb_violations)       echo "6bgk-3dad" ;;
        dobjobs)              echo "ic3t-wcy2" ;;
        real_property_master) echo "bnx9-e6tj" ;;
        real_property_legals) echo "8h5j-fqxa" ;;
        real_property_parties) echo "636b-3b5g" ;;
        *)                    echo "" ;;
    esac
}

# Tables with known Socrata IDs for verification
SOCRATA_TABLES="hpd_violations ecb_violations dobjobs real_property_master real_property_legals real_property_parties"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

log() {
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $*"
    echo "$msg"
    echo "$msg" >> "$LOG_FILE"
}

err() {
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $*"
    echo "$msg" >&2
    echo "$msg" >> "$LOG_FILE"
}

get_state() {
    local ds="$1"
    grep "^${ds}=" "$STATE_FILE" 2>/dev/null | cut -d= -f2 || echo "pending"
}

set_state() {
    local ds="$1"
    local state="$2"
    if grep -q "^${ds}=" "$STATE_FILE" 2>/dev/null; then
        sed -i '' "s/^${ds}=.*/${ds}=${state}/" "$STATE_FILE"
    else
        echo "${ds}=${state}" >> "$STATE_FILE"
    fi
    log "  State: $ds -> $state"
}

local_count() {
    local table="$1"
    psql "$LOCAL_DB" -tAc "SELECT count(*) FROM $table;" 2>/dev/null || echo "0"
}

railway_count() {
    local table="$1"
    psql "$RAILWAY_DB" -tAc "SELECT count(*) FROM $table;" 2>/dev/null || echo "0"
}

socrata_count() {
    local dataset_id="$1"
    curl -s "https://data.cityofnewyork.us/resource/${dataset_id}.json?\$select=count(*)&\$limit=1" 2>/dev/null \
        | grep -o '"[0-9]*"' | tr -d '"' || echo "N/A"
}

# ---------------------------------------------------------------------------
# Phase 1: Drop stale tables + delete old CSVs
# ---------------------------------------------------------------------------

phase_drop() {
    log ""
    log "======================================================="
    log "PHASE 1: Drop stale tables + delete old CSVs"
    log "======================================================="

    # Drop materialized views first (they depend on tables we're dropping)
    for view in $MAT_VIEWS; do
        log "Dropping materialized view: $view"
        psql "$LOCAL_DB" -c "DROP MATERIALIZED VIEW IF EXISTS $view CASCADE;" 2>/dev/null || true
    done

    for ds in $REFRESH_DATASETS; do
        local state
        state=$(get_state "$ds")
        if [ "$state" = "verified" ] || [ "$state" = "loaded" ] || [ "$state" = "downloaded" ]; then
            log "SKIP: $ds already past drop phase (state=$state)"
            continue
        fi

        log "Dropping dataset: $ds"
        $NYCDB_BIN --drop "$ds" $NYCDB_FLAGS 2>&1 || {
            log "  nycdb --drop failed, trying manual drop..."
            for table in $(get_tables_for_dataset "$ds"); do
                psql "$LOCAL_DB" -c "DROP TABLE IF EXISTS $table CASCADE;" 2>/dev/null || true
            done
        }

        # Delete stale CSVs so nycdb --download fetches fresh copies
        local patterns
        patterns=$(get_csv_patterns_for_dataset "$ds")
        log "  Deleting stale CSVs: $patterns"
        cd "$DATA_DIR"
        for pattern in $patterns; do
            rm -f $pattern 2>/dev/null || true
        done
        cd "$PROJECT_DIR"

        set_state "$ds" "dropped"
    done

    log "Phase 1 complete."
}

# ---------------------------------------------------------------------------
# Phase 2: Download + Load + nycdb verify
# ---------------------------------------------------------------------------

phase_download() {
    log ""
    log "======================================================="
    log "PHASE 2: Download + Load datasets via nycdb"
    log "======================================================="

    for ds in $REFRESH_DATASETS; do
        local state
        state=$(get_state "$ds")
        if [ "$state" = "verified" ] || [ "$state" = "loaded" ]; then
            log "SKIP: $ds already loaded (state=$state)"
            continue
        fi

        local attempt=1
        local success=false

        while [ $attempt -le $MAX_RETRIES ]; do
            log "--- $ds (attempt $attempt/$MAX_RETRIES) ---"

            # Download
            log "  Downloading $ds..."
            if $NYCDB_BIN --download "$ds" $NYCDB_FLAGS 2>&1; then
                log "  Download complete."
            else
                err "  Download failed for $ds"
                local wait_time=$((RETRY_BACKOFF_BASE * attempt))
                log "  Retrying in ${wait_time}s..."
                sleep "$wait_time"
                attempt=$((attempt + 1))
                continue
            fi

            # Load
            log "  Loading $ds into PostgreSQL..."
            if $NYCDB_BIN --load "$ds" $NYCDB_FLAGS 2>&1; then
                log "  Load complete."
            else
                err "  Load failed for $ds"
                local wait_time=$((RETRY_BACKOFF_BASE * attempt))
                log "  Retrying in ${wait_time}s..."
                sleep "$wait_time"
                attempt=$((attempt + 1))
                continue
            fi

            # Verify via nycdb
            log "  Verifying $ds via nycdb..."
            if $NYCDB_BIN --verify "$ds" $NYCDB_FLAGS 2>&1; then
                log "  nycdb verification passed."
                set_state "$ds" "loaded"
                success=true
                break
            else
                err "  nycdb verification failed for $ds"
                local wait_time=$((RETRY_BACKOFF_BASE * attempt))
                log "  Retrying in ${wait_time}s..."
                sleep "$wait_time"
                attempt=$((attempt + 1))
            fi
        done

        if [ "$success" = false ]; then
            err "FAILED: $ds after $MAX_RETRIES attempts. Continuing with next dataset."
        fi

        # Print row counts for loaded tables
        for table in $(get_tables_for_dataset "$ds"); do
            local count
            count=$(local_count "$table")
            log "  $table: $count rows"
        done
    done

    log "Phase 2 complete."
}

# ---------------------------------------------------------------------------
# Phase 3: Verify against Socrata live counts
# ---------------------------------------------------------------------------

phase_verify_socrata() {
    log ""
    log "======================================================="
    log "PHASE 3: Verify local counts vs Socrata live"
    log "======================================================="

    log ""
    printf "%-30s %12s %12s %8s\n" "TABLE" "LOCAL" "LIVE" "RATIO"
    printf "%-30s %12s %12s %8s\n" "-----" "-----" "----" "-----"

    local all_pass=true

    for table in $SOCRATA_TABLES; do
        local dataset_id
        dataset_id=$(get_socrata_id "$table")
        local local_ct
        local_ct=$(local_count "$table")
        local live_ct
        live_ct=$(socrata_count "$dataset_id")

        local ratio="N/A"
        if [ "$live_ct" != "N/A" ] && [ "$live_ct" -gt 0 ] 2>/dev/null; then
            ratio=$(echo "scale=1; $local_ct * 100 / $live_ct" | bc)%
        fi

        printf "%-30s %12s %12s %8s\n" "$table" "$local_ct" "$live_ct" "$ratio"

        # Flag if below 90%
        if [ "$live_ct" != "N/A" ] && [ "$live_ct" -gt 0 ] 2>/dev/null; then
            local threshold=$((live_ct * 90 / 100))
            if [ "$local_ct" -lt "$threshold" ]; then
                err "  WARNING: $table is below 90% of live count!"
                all_pass=false
            fi
        fi
    done

    # Also check tables without Socrata IDs
    for ds in $REFRESH_DATASETS; do
        for table in $(get_tables_for_dataset "$ds"); do
            local sid
            sid=$(get_socrata_id "$table")
            if [ -z "$sid" ]; then
                local count
                count=$(local_count "$table")
                printf "%-30s %12s %12s %8s\n" "$table" "$count" "(no API)" "-"
            fi
        done
    done

    log ""
    if [ "$all_pass" = true ]; then
        log "All tables pass Socrata verification (>=90% of live count)."
        for ds in $REFRESH_DATASETS; do
            set_state "$ds" "verified"
        done
    else
        err "Some tables are below 90% threshold. Review before proceeding."
        err "To continue anyway, manually set state to 'verified' in $STATE_FILE"
    fi
}

# ---------------------------------------------------------------------------
# Phase 4: Rebuild local indexes + materialized views
# ---------------------------------------------------------------------------

phase_rebuild_local() {
    log ""
    log "======================================================="
    log "PHASE 4: Rebuild local indexes + views"
    log "======================================================="

    log "Creating indexes..."
    psql "$LOCAL_DB" -f "$PROJECT_DIR/scripts/create_indexes.sql" 2>&1 || {
        err "Some indexes may have failed (harmless if 'already exists')."
    }

    log "Creating materialized views..."
    psql "$LOCAL_DB" -f "$PROJECT_DIR/scripts/create_views.sql" 2>&1 || {
        err "Some views may have failed. Check output."
    }

    log "Running ANALYZE on refreshed tables..."
    for ds in $REFRESH_DATASETS; do
        for table in $(get_tables_for_dataset "$ds"); do
            psql "$LOCAL_DB" -c "ANALYZE $table;" 2>/dev/null || true
        done
    done

    log "Phase 4 complete."
}

# ---------------------------------------------------------------------------
# Phase 5: Dump refreshed tables
# ---------------------------------------------------------------------------

phase_dump() {
    log ""
    log "======================================================="
    log "PHASE 5: pg_dump refreshed tables"
    log "======================================================="

    mkdir -p "$DUMP_DIR"

    for ds in $REFRESH_DATASETS; do
        for table in $(get_tables_for_dataset "$ds"); do
            local dump_file="$DUMP_DIR/${table}.dump"
            if [ -f "$dump_file" ]; then
                log "  SKIP: $dump_file already exists (delete to re-dump)"
                continue
            fi

            log "  Dumping $table..."
            pg_dump -U "$LOCAL_DB_USER" -d "$LOCAL_DB_NAME" \
                --no-owner --table="$table" -Fc \
                -f "$dump_file" 2>&1

            local size
            size=$(ls -lh "$dump_file" | awk '{print $5}')
            log "  -> $dump_file ($size)"
        done
    done

    # Dump materialized views
    for view in $MAT_VIEWS; do
        local dump_file="$DUMP_DIR/${view}.dump"
        if [ -f "$dump_file" ]; then
            log "  SKIP: $dump_file already exists"
            continue
        fi

        log "  Dumping $view..."
        pg_dump -U "$LOCAL_DB_USER" -d "$LOCAL_DB_NAME" \
            --no-owner --table="$view" -Fc \
            -f "$dump_file" 2>&1

        local size
        size=$(ls -lh "$dump_file" | awk '{print $5}')
        log "  -> $dump_file ($size)"
    done

    log "Phase 5 complete."
}

# ---------------------------------------------------------------------------
# Phase 6: Restore to Railway (single-threaded, table by table)
# ---------------------------------------------------------------------------

phase_restore_railway() {
    log ""
    log "======================================================="
    log "PHASE 6: Restore to Railway"
    log "======================================================="

    # Drop materialized views on Railway first
    for view in $MAT_VIEWS; do
        log "  Dropping Railway view: $view"
        psql "$RAILWAY_DB" -c "DROP MATERIALIZED VIEW IF EXISTS $view CASCADE;" 2>/dev/null || true
    done

    for ds in $REFRESH_DATASETS; do
        for table in $(get_tables_for_dataset "$ds"); do
            local dump_file="$DUMP_DIR/${table}.dump"
            if [ ! -f "$dump_file" ]; then
                err "  MISSING: $dump_file — skipping $table"
                continue
            fi

            log "  Truncating $table on Railway..."
            psql "$RAILWAY_DB" -c "TRUNCATE $table CASCADE;" 2>/dev/null || {
                log "  Table may not exist yet, will be created by pg_restore."
            }

            log "  Restoring $table to Railway..."
            pg_restore --no-owner --data-only \
                -d "$RAILWAY_DB" "$dump_file" 2>&1 | grep -v "already exists" || true

            local local_ct railway_ct
            local_ct=$(local_count "$table")
            railway_ct=$(railway_count "$table")

            if [ "$local_ct" = "$railway_ct" ]; then
                log "  OK $table: $railway_ct rows (matches local)"
            else
                err "  MISMATCH $table: Railway=$railway_ct Local=$local_ct"
            fi
        done
    done

    log "Phase 6 complete."
}

# ---------------------------------------------------------------------------
# Phase 7: Rebuild Railway indexes + views
# ---------------------------------------------------------------------------

phase_rebuild_railway() {
    log ""
    log "======================================================="
    log "PHASE 7: Rebuild Railway indexes + views"
    log "======================================================="

    log "Creating indexes on Railway..."
    psql "$RAILWAY_DB" -f "$PROJECT_DIR/scripts/create_indexes.sql" 2>&1 || {
        err "Some indexes may have failed (harmless)."
    }

    log "Creating materialized views on Railway..."
    psql "$RAILWAY_DB" -f "$PROJECT_DIR/scripts/create_views.sql" 2>&1 || {
        err "Some views may have failed."
    }

    log "Phase 7 complete."
}

# ---------------------------------------------------------------------------
# Phase 8: Final verification
# ---------------------------------------------------------------------------

phase_final_verify() {
    log ""
    log "======================================================="
    log "PHASE 8: Final verification — Local vs Railway"
    log "======================================================="

    log ""
    printf "%-40s %12s %12s %8s\n" "TABLE" "LOCAL" "RAILWAY" "MATCH"
    printf "%-40s %12s %12s %8s\n" "-----" "-----" "-------" "-----"

    local all_match=true

    for ds in $REFRESH_DATASETS; do
        for table in $(get_tables_for_dataset "$ds"); do
            local local_ct railway_ct
            local_ct=$(local_count "$table")
            railway_ct=$(railway_count "$table")

            local match="OK"
            if [ "$local_ct" != "$railway_ct" ]; then
                match="MISMATCH"
                all_match=false
            fi

            printf "%-40s %12s %12s %8s\n" "$table" "$local_ct" "$railway_ct" "$match"
        done
    done

    # Check materialized views
    for view in $MAT_VIEWS; do
        local local_ct railway_ct
        local_ct=$(psql "$LOCAL_DB" -tAc "SELECT count(*) FROM $view;" 2>/dev/null || echo "N/A")
        railway_ct=$(psql "$RAILWAY_DB" -tAc "SELECT count(*) FROM $view;" 2>/dev/null || echo "N/A")

        local match="OK"
        if [ "$local_ct" != "$railway_ct" ]; then
            match="MISMATCH"
            all_match=false
        fi

        printf "%-40s %12s %12s %8s\n" "$view" "$local_ct" "$railway_ct" "$match"
    done

    log ""
    if [ "$all_match" = true ]; then
        log "ALL TABLES MATCH. Refresh complete."
    else
        err "Some tables have mismatches. Review above."
    fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

PHASE_ARG="${1:-}"
RUN_PHASE="${2:-}"

if [ "$PHASE_ARG" = "--phase" ]; then
    RUN_PHASE=$(echo "$RUN_PHASE" | tr '[:upper:]' '[:lower:]')
else
    RUN_PHASE="all"
fi

mkdir -p "$DATA_DIR" "$DUMP_DIR"
touch "$STATE_FILE" "$LOG_FILE"

log "======================================================="
log "NYC Property Intel — Gap Dataset Refresh"
log "======================================================="
log "Phase: $RUN_PHASE"
log "State file: $STATE_FILE"
log "Log file: $LOG_FILE"
log "======================================================="

# Pre-flight
if [ ! -x "$NYCDB_BIN" ]; then
    err "nycdb not found at $NYCDB_BIN. Install with: pip3 install nycdb"
    exit 1
fi

# Prevent Mac sleep
caffeinate -dims &
CAFFEINE_PID=$!
trap "kill $CAFFEINE_PID 2>/dev/null; log 'Caffeinate stopped.'" EXIT
log "Caffeinate started (PID $CAFFEINE_PID) — Mac will stay awake."

# Baseline counts
log ""
log "Baseline local row counts:"
for ds in $REFRESH_DATASETS; do
    for table in $(get_tables_for_dataset "$ds"); do
        count=$(local_count "$table" 2>/dev/null || echo "N/A")
        log "  $table: $count"
    done
done

# Run phases
case "$RUN_PHASE" in
    all)
        phase_drop
        phase_download
        phase_verify_socrata
        phase_rebuild_local
        phase_dump
        phase_restore_railway
        phase_rebuild_railway
        phase_final_verify
        ;;
    download)
        phase_drop
        phase_download
        phase_verify_socrata
        phase_rebuild_local
        ;;
    verify)
        phase_verify_socrata
        ;;
    railway)
        phase_dump
        phase_restore_railway
        phase_rebuild_railway
        phase_final_verify
        ;;
    *)
        err "Unknown phase: $RUN_PHASE"
        err "Usage: $0 [--phase download|verify|railway|all]"
        exit 1
        ;;
esac

log ""
log "======================================================="
log "DONE. Full log at: $LOG_FILE"
log "======================================================="
