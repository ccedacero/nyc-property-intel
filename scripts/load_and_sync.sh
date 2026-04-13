#!/bin/bash
# load_and_sync.sh — Load fresh data locally then sync to Railway
#
# Usage:
#   ./scripts/load_and_sync.sh --phase local    # Phases 1-4: load locally + dump
#   ./scripts/load_and_sync.sh --phase railway  # Phase 5: restore to Railway
#   ./scripts/load_and_sync.sh --phase verify   # Phase 7: verify counts match
#   ./scripts/load_and_sync.sh --phase views    # Rebuild materialized views on Railway
#
# Prerequisites:
#   - Upgrade Railway volume to 75GB in Railway dashboard BEFORE running --phase railway
#   - Run in tmux: tmux new -s sync
#   - Keeps a log at data/load_sync.log

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="$SCRIPT_DIR/../data"
DUMP_DIR="$DATA_DIR/dumps"
LOG="$DATA_DIR/load_sync.log"
STATE_FILE="$DATA_DIR/.load_sync_state"

LOCAL_DB_USER="nycdb"
LOCAL_DB_NAME="nycdb"
LOCAL_DB_HOST="localhost"
LOCAL_DB_PORT="5432"
LOCAL_DB="postgresql://${LOCAL_DB_USER}:${LOCAL_DB_USER}@${LOCAL_DB_HOST}:${LOCAL_DB_PORT}/${LOCAL_DB_NAME}"

RAILWAY_DB="${RAILWAY_DB:-}"

# ── helpers ────────────────────────────────────────────────────────────────────

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

state_set() { echo "$1=$2" >> "$STATE_FILE"; }
state_get() { grep "^$1=" "$STATE_FILE" 2>/dev/null | tail -1 | cut -d= -f2-; }

check_prereqs() {
    log "Checking prerequisites..."
    command -v psql    >/dev/null 2>&1 || { log "ERROR: psql not found"; exit 1; }
    command -v pg_dump >/dev/null 2>&1 || { log "ERROR: pg_dump not found"; exit 1; }
    command -v nycdb   >/dev/null 2>&1 || { log "ERROR: nycdb not found (pip install nycdb)"; exit 1; }

    # Check all required CSVs exist
    local missing=0
    for csv in hpd_violations.csv \
               acris_real_property_master.csv acris_real_property_legals.csv \
               acris_real_property_parties.csv acris_real_property_references.csv \
               acris_real_property_remarks.csv \
               acris_personal_property_master.csv acris_personal_property_legals.csv \
               acris_personal_property_parties.csv acris_personal_property_references.csv \
               acris_personal_property_remarks.csv \
               acris_country_codes.csv acris_document_control_codes.csv \
               acris_property_type_codes.csv acris_ucc_collateral_codes.csv; do
        if [ ! -f "$DATA_DIR/$csv" ]; then
            log "ERROR: Missing CSV: $csv"
            missing=1
        fi
    done
    [ "$missing" -eq 1 ] && exit 1

    mkdir -p "$DUMP_DIR"
    log "Prerequisites OK."
}

nycdb_load() {
    local dataset="$1"
    log "Loading $dataset into local PostgreSQL..."
    nycdb --load "$dataset" \
        -U "$LOCAL_DB_USER" -D "$LOCAL_DB_NAME" -P "$LOCAL_DB_USER" \
        -H "$LOCAL_DB_HOST" --port "$LOCAL_DB_PORT" \
        --root-dir "$DATA_DIR"
}

# ── Phase 1: Load hpd_violations locally ──────────────────────────────────────

phase_hpd_violations() {
    if [ "$(state_get hpd_violations_loaded)" = "done" ]; then
        log "SKIP: hpd_violations already loaded locally"
        return
    fi

    log "=== Phase 1: Reload hpd_violations locally (10.8M rows) ==="
    log "Dropping old hpd_violations table (had 1.9M rows)..."
    psql -U "$LOCAL_DB_USER" -d "$LOCAL_DB_NAME" \
        -c "DROP TABLE IF EXISTS hpd_violations CASCADE;"

    nycdb_load "hpd_violations"

    local count
    count=$(psql -U "$LOCAL_DB_USER" -d "$LOCAL_DB_NAME" -tAc "SELECT count(*) FROM hpd_violations;")
    log "hpd_violations loaded: $count rows"

    if [ "$count" -lt 10000000 ]; then
        log "ERROR: Expected ~10.8M rows, got $count — aborting"
        exit 1
    fi

    state_set hpd_violations_loaded done
}

# ── Phase 2: Load ACRIS locally ───────────────────────────────────────────────

phase_acris() {
    if [ "$(state_get acris_loaded)" = "done" ]; then
        log "SKIP: ACRIS already loaded locally"
        return
    fi

    log "=== Phase 2: Load ACRIS (14 tables) locally ==="
    log "Dropping any partial ACRIS tables..."
    for tbl in real_property_master real_property_legals real_property_parties \
               real_property_references real_property_remarks \
               personal_property_master personal_property_legals personal_property_parties \
               personal_property_references personal_property_remarks \
               acris_country_codes acris_document_control_codes \
               acris_property_type_codes acris_ucc_collateral_codes; do
        psql -U "$LOCAL_DB_USER" -d "$LOCAL_DB_NAME" \
            -c "DROP TABLE IF EXISTS $tbl CASCADE;" 2>/dev/null || true
    done

    nycdb_load "acris"

    # Spot-check key tables
    local rp_master rp_legals rp_parties
    rp_master=$(psql -U "$LOCAL_DB_USER" -d "$LOCAL_DB_NAME" -tAc "SELECT count(*) FROM real_property_master;")
    rp_legals=$(psql -U "$LOCAL_DB_USER" -d "$LOCAL_DB_NAME" -tAc "SELECT count(*) FROM real_property_legals;")
    rp_parties=$(psql -U "$LOCAL_DB_USER" -d "$LOCAL_DB_NAME" -tAc "SELECT count(*) FROM real_property_parties;")
    log "real_property_master: $rp_master (expect ~16.9M)"
    log "real_property_legals: $rp_legals (expect ~22.5M)"
    log "real_property_parties: $rp_parties (expect ~46.1M)"

    if [ "$rp_parties" -lt 40000000 ]; then
        log "ERROR: real_property_parties too low ($rp_parties) — aborting"
        exit 1
    fi

    state_set acris_loaded done
}

# ── Phase 3: Build local indexes ──────────────────────────────────────────────

phase_indexes() {
    if [ "$(state_get indexes_built)" = "done" ]; then
        log "SKIP: Indexes already built locally"
        return
    fi

    log "=== Phase 3: Build indexes locally ==="
    psql -U "$LOCAL_DB_USER" -d "$LOCAL_DB_NAME" \
        -f "$SCRIPT_DIR/create_indexes.sql"

    log "Running ANALYZE on new tables..."
    for tbl in hpd_violations real_property_master real_property_legals real_property_parties \
               real_property_references real_property_remarks \
               personal_property_master personal_property_legals personal_property_parties; do
        psql -U "$LOCAL_DB_USER" -d "$LOCAL_DB_NAME" -c "ANALYZE $tbl;" 2>/dev/null || true
    done

    state_set indexes_built done
    log "Indexes built."
}

# ── Phase 4: Dump tables ──────────────────────────────────────────────────────

dump_table() {
    local tbl="$1"
    local dump_file="$DUMP_DIR/${tbl}.dump"

    if [ -f "$dump_file" ] && [ -s "$dump_file" ]; then
        log "SKIP: $dump_file already exists ($(du -h "$dump_file" | cut -f1))"
        return
    fi

    log "Dumping $tbl..."
    pg_dump -U "$LOCAL_DB_USER" -d "$LOCAL_DB_NAME" \
        --no-owner --table="$tbl" -Fc \
        -f "${dump_file}.tmp"
    mv "${dump_file}.tmp" "$dump_file"
    log "  -> $(du -h "$dump_file" | cut -f1)"
}

phase_dump() {
    log "=== Phase 4: Dump tables to $DUMP_DIR ==="

    # Dump in order: smallest first so we detect issues early
    dump_table acris_country_codes
    dump_table acris_document_control_codes
    dump_table acris_property_type_codes
    dump_table acris_ucc_collateral_codes
    dump_table personal_property_remarks
    dump_table real_property_remarks
    dump_table personal_property_legals
    dump_table personal_property_references
    dump_table personal_property_master
    dump_table real_property_references
    dump_table hpd_violations
    dump_table real_property_master
    dump_table real_property_legals
    dump_table personal_property_parties
    dump_table real_property_parties

    log "All dumps complete:"
    ls -lh "$DUMP_DIR"/*.dump
}

# ── Phase 5: Restore to Railway ───────────────────────────────────────────────

railway_check_space() {
    local used
    used=$(psql "$RAILWAY_DB" -tAc "SELECT pg_size_pretty(pg_database_size(current_database()));" 2>/dev/null || echo "UNKNOWN")
    log "Railway DB current size: $used"
}

restore_table() {
    local tbl="$1"
    local dump_file="$DUMP_DIR/${tbl}.dump"
    local state_key="railway_${tbl}"

    if [ "$(state_get "$state_key")" = "done" ]; then
        log "SKIP: $tbl already restored to Railway"
        return
    fi

    if [ ! -f "$dump_file" ]; then
        log "ERROR: Missing dump file: $dump_file"
        exit 1
    fi

    log "Restoring $tbl to Railway ($(du -h "$dump_file" | cut -f1) compressed)..."

    # For hpd_violations: table exists, truncate first then data-only restore
    if [ "$tbl" = "hpd_violations" ]; then
        log "  Truncating existing hpd_violations on Railway..."
        psql "$RAILWAY_DB" -c "TRUNCATE hpd_violations;" || {
            log "ERROR: TRUNCATE failed — run --phase views first to drop materialized views"
            exit 1
        }
        pg_restore --no-owner --data-only \
            -d "$RAILWAY_DB" "$dump_file" 2>&1 | grep -v "^$" | tee -a "$LOG" || true
    else
        # ACRIS tables: don't exist on Railway yet, full restore (schema + data)
        pg_restore --no-owner \
            -d "$RAILWAY_DB" "$dump_file" 2>&1 | grep -v "already exists" | grep -v "^$" | tee -a "$LOG" || true
    fi

    # Verify
    local count
    count=$(psql "$RAILWAY_DB" -tAc "SELECT count(*) FROM $tbl;" 2>/dev/null || echo "ERROR")
    log "  Railway $tbl: $count rows"
    state_set "$state_key" done
}

phase_railway() {
    log "=== Phase 5: Restore to Railway ==="
    railway_check_space

    # Order: smallest first → quick wins, detect space issues early
    # Lookup tables (tiny)
    restore_table acris_country_codes
    restore_table acris_document_control_codes
    restore_table acris_property_type_codes
    restore_table acris_ucc_collateral_codes

    railway_check_space

    # Small ACRIS tables
    restore_table personal_property_remarks
    restore_table real_property_remarks
    restore_table personal_property_legals
    restore_table personal_property_references
    restore_table personal_property_master
    restore_table real_property_references

    railway_check_space

    # hpd_violations reload (TRUNCATE + data-only)
    restore_table hpd_violations

    railway_check_space

    # Large ACRIS tables
    restore_table real_property_master
    restore_table real_property_legals
    restore_table personal_property_parties
    restore_table real_property_parties  # biggest — save for last

    railway_check_space
    log "Phase 5 complete."
}

# ── Phase 6: Rebuild materialized views ───────────────────────────────────────

phase_views() {
    log "=== Phase 6: Drop + rebuild materialized views on Railway ==="

    # Must drop before TRUNCATE hpd_violations to avoid CASCADE errors
    psql "$RAILWAY_DB" -c "
        DROP MATERIALIZED VIEW IF EXISTS mv_violation_summary CASCADE;
        DROP MATERIALIZED VIEW IF EXISTS mv_property_profile CASCADE;
        DROP MATERIALIZED VIEW IF EXISTS mv_current_ownership CASCADE;
    " 2>/dev/null || true

    log "Materialized views dropped. Run --phase views again after railway restore to rebuild."

    if [ "$(state_get railway_hpd_violations)" = "done" ] && \
       [ "$(state_get railway_real_property_master)" = "done" ]; then
        log "Rebuilding materialized views..."
        psql "$RAILWAY_DB" -f "$SCRIPT_DIR/create_views.sql" 2>&1 | tee -a "$LOG" || true
        log "Views rebuilt."
    else
        log "INFO: Railway restore not complete — views will be rebuilt after --phase railway finishes."
    fi
}

# ── Phase 7: Verify counts match ──────────────────────────────────────────────

phase_verify() {
    log "=== Phase 7: Verify Railway vs Local counts ==="
    printf "%-40s | %-12s | %-12s | %s\n" "TABLE" "LOCAL" "RAILWAY" "STATUS"
    printf "%-40s | %-12s | %-12s | %s\n" "-----" "-----" "-------" "------"

    local all_ok=1
    for tbl in hpd_violations \
               real_property_master real_property_legals real_property_parties \
               real_property_references real_property_remarks \
               personal_property_master personal_property_legals personal_property_parties \
               personal_property_references personal_property_remarks \
               acris_country_codes acris_document_control_codes \
               acris_property_type_codes acris_ucc_collateral_codes; do

        local local_ct rail_ct status
        local_ct=$(psql -U "$LOCAL_DB_USER" -d "$LOCAL_DB_NAME" -tAc "SELECT count(*) FROM $tbl;" 2>/dev/null || echo "MISSING")
        rail_ct=$(psql "$RAILWAY_DB" -tAc "SELECT count(*) FROM $tbl;" 2>/dev/null || echo "MISSING")

        if [ "$local_ct" = "$rail_ct" ]; then
            status="OK"
        else
            status="MISMATCH"
            all_ok=0
        fi

        printf "%-40s | %-12s | %-12s | %s\n" "$tbl" "$local_ct" "$rail_ct" "$status"
    done | tee -a "$LOG"

    if [ "$all_ok" -eq 1 ]; then
        log "All tables match. Railway DB is fully in sync."
    else
        log "WARNING: Some tables have count mismatches. Check above."
    fi
}

# ── Main ──────────────────────────────────────────────────────────────────────

PHASE="${1:-}"

if [ -z "$PHASE" ]; then
    echo "Usage: $0 --phase <local|railway|verify|views>"
    echo ""
    echo "  --phase local    Phases 1-4: load hpd_violations + ACRIS locally, build indexes, dump"
    echo "  --phase views    Drop materialized views on Railway (run BEFORE --phase railway)"
    echo "  --phase railway  Phase 5: restore all tables to Railway (run in tmux, takes 8-12h)"
    echo "  --phase verify   Phase 7: compare row counts local vs Railway"
    exit 1
fi

# Keep Mac awake for long-running phases
caffeinate -dims &
CAFF_PID=$!
trap "kill $CAFF_PID 2>/dev/null || true" EXIT

log "=========================================="
log "Starting: $PHASE"
log "=========================================="

case "$PHASE" in
    --phase)
        SUBPHASE="${2:-}"
        case "$SUBPHASE" in
            local)
                check_prereqs
                phase_hpd_violations
                phase_acris
                phase_indexes
                phase_dump
                log "Local phases complete. Ready for Railway restore."
                log "NEXT STEP: open tmux, then run: ./scripts/load_and_sync.sh --phase views && ./scripts/load_and_sync.sh --phase railway"
                ;;
            views)
                phase_views
                ;;
            railway)
                phase_railway
                ;;
            verify)
                phase_verify
                ;;
            *)
                echo "Unknown subphase: $SUBPHASE"
                exit 1
                ;;
        esac
        ;;
    *)
        echo "Unknown option: $PHASE"
        exit 1
        ;;
esac
