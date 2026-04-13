#!/bin/bash
# fresh_start.sh — Full clean reload: local PostgreSQL → Railway
#
# Usage:
#   ./scripts/fresh_start.sh --phase check      # Verify all CSVs are ready
#   ./scripts/fresh_start.sh --phase local      # Drop + reload local DB from CSVs (RESUMABLE)
#   ./scripts/fresh_start.sh --phase indexes    # Build indexes locally
#   ./scripts/fresh_start.sh --phase dump       # Dump all tables to .dump files
#   ./scripts/fresh_start.sh --phase wipe       # Wipe Railway DB
#   ./scripts/fresh_start.sh --phase railway    # Restore all tables to Railway (run in tmux)
#   ./scripts/fresh_start.sh --phase views      # Rebuild materialized views on Railway
#   ./scripts/fresh_start.sh --phase verify     # Final count comparison local vs Railway
#
# BEFORE STARTING:
#   1. Upgrade Railway volume to 100GB+ in Railway dashboard
#   2. Run --phase check first to verify CSVs
#   3. Run in tmux for long phases: tmux new -s nyc
#
# FAILURE RECOVERY:
#   --phase local is resumable — it checks which sub-phases (A/B/C) are done
#     and skips completed ones. If it fails mid-phase, re-run to retry from
#     where it left off (only the CURRENT sub-phase restarts, not everything).
#   --phase railway is resumable per-table via state tracking.
#   To force a full restart: rm data/.fresh_start_state

set -uo pipefail
# NOTE: We do NOT use set -e. Instead, we check errors explicitly.
#   set -e causes cascading aborts on non-fatal issues (e.g., slightly off
#   row counts) which would leave the DB in a half-loaded state.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="$SCRIPT_DIR/../data"
DUMP_DIR="$DATA_DIR/dumps"
LOG="$DATA_DIR/fresh_start.log"
STATE="$DATA_DIR/.fresh_start_state"

LOCAL_USER="nycdb"
LOCAL_DB="nycdb"
LOCAL_HOST="localhost"
LOCAL_PORT="5432"

RAILWAY_DB="${RAILWAY_DB:-}"

# Full path to nycdb (not always in PATH when running as a script)
NYCDB="/Users/devtzi/Library/Python/3.9/bin/nycdb"

ERRORS=0  # global error counter

# ── helpers ────────────────────────────────────────────────────────────────────
log()       { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

state_set() {
    grep -v "^$1=" "$STATE" 2>/dev/null > "$STATE.tmp" || true
    echo "$1=$2" >> "$STATE.tmp"
    mv "$STATE.tmp" "$STATE"
}

state_get() {
    grep "^$1=" "$STATE" 2>/dev/null | tail -1 | cut -d= -f2- || echo ""
}

lpsql()  { psql -U "$LOCAL_USER" -d "$LOCAL_DB" -h "$LOCAL_HOST" -p "$LOCAL_PORT" "$@"; }
rpsql()  { psql "$RAILWAY_DB" "$@"; }

nycdb_load() {
    local dataset="$1"
    log "  nycdb --load $dataset ..."
    if ! "$NYCDB" --load "$dataset" \
        -U "$LOCAL_USER" -D "$LOCAL_DB" -P "$LOCAL_USER" \
        -H "$LOCAL_HOST" --port "$LOCAL_PORT" \
        --root-dir "$DATA_DIR" 2>&1 | tee -a "$LOG"; then
        log "  ERROR: nycdb --load $dataset FAILED"
        ERRORS=$((ERRORS + 1))
        return 1
    fi
}

# check_count: NON-FATAL — logs warning but does NOT abort
check_count() {
    local table="$1" expected="$2"
    local actual
    actual=$(lpsql -tAc "SELECT count(*) FROM $table;" 2>/dev/null || echo "MISSING")
    if [ "$actual" = "MISSING" ]; then
        log "  WARNING: $table — TABLE MISSING"
        ERRORS=$((ERRORS + 1))
        return 0  # non-fatal
    fi
    local diff=$(( actual - expected ))
    if [ "$diff" -lt -1000 ]; then
        log "  WARNING: $table — $actual rows (expected ~$expected, short by $(( -diff )))"
        ERRORS=$((ERRORS + 1))
    else
        log "  OK: $table — $actual rows"
    fi
    return 0  # always non-fatal
}

# ── Phase: check ──────────────────────────────────────────────────────────────
phase_check() {
    log "=== Checking all CSVs are present and complete ==="
    local ok=1

    CSV_LIST="
acris_real_property_legals.csv:22543599
acris_real_property_master.csv:16932186
acris_real_property_parties.csv:46152818
acris_real_property_references.csv:8583995
acris_real_property_remarks.csv:5727189
acris_personal_property_legals.csv:3944154
acris_personal_property_master.csv:4517603
acris_personal_property_parties.csv:10956924
acris_personal_property_references.csv:7706266
acris_personal_property_remarks.csv:492951
acris_country_codes.csv:250
acris_document_control_codes.csv:126
acris_property_type_codes.csv:48
acris_ucc_collateral_codes.csv:8
hpd_violations.csv:10822300
hpd_complaints_and_problems.csv:15994361
hpd_registrations.csv:202667
hpd_litigations.csv:236829
hpd_contacts.csv:780016
ecb_violations.csv:1805598
dob_violations.csv:2473610
dobjobs.csv:2714649
dob_now_jobs.csv:888411
dof_property_valuation_and_assessments.csv:10505228
dof_exemptions.csv:3326955
dof_exemption_classification_codes.csv:222
dof_sales.csv:760914
dof_tax_lien_sale_list.csv:264142
pluto_latest.csv:858644
taxbills_joined.csv:46461
"

    while IFS=: read -r csv exp; do
        [ -z "$csv" ] && continue
        filepath="$DATA_DIR/$csv"
        if [ ! -f "$filepath" ]; then
            log "  MISSING: $csv"
            ok=0
            continue
        fi
        first=$(head -c 1 "$filepath")
        if [ "$first" = "{" ]; then
            log "  CORRUPT: $csv — contains JSON error response!"
            ok=0
            continue
        fi
        rows=$(( $(wc -l < "$filepath") - 1 ))
        diff=$(( rows - exp ))
        if [ "$diff" -lt -1000 ]; then
            log "  SHORT:   $csv — $rows rows (expected $exp, missing $(( -diff )))"
            ok=0
        else
            log "  OK:      $csv — $rows rows"
        fi
    done <<EOF
$CSV_LIST
EOF

    if [ ! -f "$DATA_DIR/pad.zip" ]; then
        log "  MISSING: pad.zip"
        ok=0
    else
        log "  OK:      pad.zip ($(du -h "$DATA_DIR/pad.zip" | cut -f1))"
    fi

    if [ "$ok" -eq 1 ]; then
        log "All CSVs verified. Ready to proceed with --phase local"
    else
        log "Some files are missing or corrupt. Fix before proceeding."
        exit 1
    fi
}

# ── Phase: local (RESUMABLE by sub-phase) ────────────────────────────────────
phase_local() {
    log "=== Phase: Fresh local DB load ==="

    # Only drop+recreate if we haven't started yet
    if [ "$(state_get db_recreated)" != "done" ]; then
        log "WARNING: This will DROP and recreate the local '$LOCAL_DB' database!"
        log "Proceeding in 5 seconds... (Ctrl+C to abort)"
        sleep 5

        log "Dropping local DB..."
        dropdb -U "$LOCAL_USER" -h "$LOCAL_HOST" -p "$LOCAL_PORT" "$LOCAL_DB" 2>/dev/null || true
        createdb -U "$LOCAL_USER" -h "$LOCAL_HOST" -p "$LOCAL_PORT" -O "$LOCAL_USER" "$LOCAL_DB"
        log "Database recreated."

        # Clear any previous state for local phases
        state_set phase_a ""
        state_set phase_b ""
        state_set phase_c ""
        state_set db_recreated done
    else
        log "DB already recreated (resuming from where we left off)"
    fi

    # Phase A: Core property tables
    if [ "$(state_get phase_a)" != "done" ]; then
        log "--- Phase A: Core tables ---"
        ERRORS=0
        nycdb_load "pad"
        nycdb_load "pluto_latest"
        nycdb_load "hpd_violations"
        nycdb_load "hpd_complaints"

        log "--- Phase A verification ---"
        check_count "pad_adr" 900000
        check_count "pluto_latest" 858644
        check_count "hpd_violations" 10822300
        check_count "hpd_complaints_and_problems" 15994361

        if [ "$ERRORS" -gt 0 ]; then
            log "Phase A completed with $ERRORS warnings. Review above."
            log "To retry Phase A: rm the phase_a line from $STATE and re-run."
        fi
        state_set phase_a done
        log "Phase A complete."
    else
        log "SKIP: Phase A already done"
    fi

    # Phase B: Violations, sales, assessments
    if [ "$(state_get phase_b)" != "done" ]; then
        log "--- Phase B: Violations + financials ---"
        ERRORS=0
        nycdb_load "dob_violations"
        nycdb_load "ecb_violations"
        nycdb_load "hpd_litigations"
        nycdb_load "hpd_registrations"
        nycdb_load "dobjobs"
        nycdb_load "dof_sales"
        nycdb_load "dof_annual_sales"
        nycdb_load "dof_tax_lien_sale_list"
        nycdb_load "dof_property_valuation_and_assessments"
        nycdb_load "dof_exemptions"
        nycdb_load "rentstab"

        log "--- Phase B verification ---"
        check_count "dob_violations" 2473610
        check_count "ecb_violations" 1805598
        check_count "hpd_litigations" 236829
        check_count "hpd_registrations" 202667
        check_count "hpd_contacts" 780016
        check_count "dobjobs" 2714649
        check_count "dob_now_jobs" 888411
        check_count "dof_property_valuation_and_assessments" 10505228
        check_count "dof_exemptions" 3326955
        check_count "dof_sales" 760914
        check_count "dof_annual_sales" 1500000
        check_count "rentstab" 46461
        check_count "dof_tax_lien_sale_list" 264142

        if [ "$ERRORS" -gt 0 ]; then
            log "Phase B completed with $ERRORS warnings. Review above."
        fi
        state_set phase_b done
        log "Phase B complete."
    else
        log "SKIP: Phase B already done"
    fi

    # Phase C: ACRIS (largest, takes ~60-90 min)
    if [ "$(state_get phase_c)" != "done" ]; then
        log "--- Phase C: ACRIS (slow, ~60-90 min) ---"
        ERRORS=0
        nycdb_load "acris"

        log "--- Phase C verification ---"
        check_count "real_property_legals" 22543599
        check_count "real_property_master" 16932186
        check_count "real_property_parties" 46152818
        check_count "real_property_references" 8583995
        check_count "real_property_remarks" 5727189
        check_count "personal_property_legals" 3944154
        check_count "personal_property_master" 4517603
        check_count "personal_property_parties" 10956924

        if [ "$ERRORS" -gt 0 ]; then
            log "Phase C completed with $ERRORS warnings. Review above."
        fi
        state_set phase_c done
        log "Phase C complete."
    else
        log "SKIP: Phase C already done"
    fi

    # Phase D: Real-time datasets (DOB complaints + marshal evictions via nycdb)
    if [ "$(state_get phase_d)" != "done" ]; then
        log "--- Phase D: DOB complaints + marshal evictions ---"
        ERRORS=0
        nycdb_load "dob_complaints"
        nycdb_load "marshal_evictions"

        log "--- Phase D verification ---"
        check_count "dob_complaints" 3000000
        check_count "marshal_evictions_all" 100000
        check_count "marshal_evictions_17" 15000
        check_count "marshal_evictions_18" 17000
        check_count "marshal_evictions_19" 14000

        if [ "$ERRORS" -gt 0 ]; then
            log "Phase D completed with $ERRORS warnings. Review above."
        fi
        state_set phase_d done
        log "Phase D complete."
    else
        log "SKIP: Phase D already done"
    fi

    log "=== Local load complete ==="
    log "Row counts:"
    lpsql -c "SELECT relname AS table, n_live_tup AS approx_rows FROM pg_stat_user_tables ORDER BY n_live_tup DESC;" || true
    state_set local_load done
}

# ── Phase: indexes ────────────────────────────────────────────────────────────
phase_indexes() {
    if [ "$(state_get indexes_done)" = "done" ]; then
        log "SKIP: Indexes already built"
        return
    fi
    log "=== Phase: Build indexes locally ==="

    # Fix known table name mismatches in create_indexes.sql before running
    sed \
        -e 's/ON hpd_complaints (/ON hpd_complaints_and_problems (/g' \
        "$SCRIPT_DIR/create_indexes.sql" | \
        lpsql 2>&1 | tee -a "$LOG" || true
    # Note: || true because some indexes may fail (e.g., pad_bbl if not created)
    # CREATE INDEX IF NOT EXISTS means existing indexes are safely skipped

    log "Running ANALYZE on all tables..."
    lpsql -c "ANALYZE;" || true
    state_set indexes_done done
    log "Indexes built."
}

# ── Phase: dump ───────────────────────────────────────────────────────────────
dump_one() {
    local tbl="$1"
    local f="$DUMP_DIR/${tbl}.dump"

    if [ -f "$f" ] && [ -s "$f" ]; then
        log "  SKIP: $tbl ($(du -h "$f" | cut -f1))"
        return 0
    fi

    # Check table exists before dumping
    local exists
    exists=$(lpsql -tAc "SELECT 1 FROM pg_tables WHERE schemaname='public' AND tablename='$tbl';" 2>/dev/null || echo "")
    if [ "$exists" != "1" ]; then
        log "  SKIP: $tbl — table does not exist"
        return 0
    fi

    log "  Dumping $tbl..."
    if pg_dump -U "$LOCAL_USER" -d "$LOCAL_DB" \
        --no-owner --table="$tbl" -Fc -f "${f}.tmp"; then
        mv "${f}.tmp" "$f"
        log "  -> $tbl: $(du -h "$f" | cut -f1)"
    else
        log "  ERROR: pg_dump failed for $tbl"
        rm -f "${f}.tmp"
        ERRORS=$((ERRORS + 1))
    fi
}

phase_dump() {
    log "=== Phase: Dump all tables ==="
    mkdir -p "$DUMP_DIR"

    # Clear old dumps to avoid stale data
    if [ "$(state_get dump_cleared)" != "done" ]; then
        log "Clearing old dumps..."
        rm -f "$DUMP_DIR"/*.dump
        state_set dump_cleared done
    fi

    ERRORS=0
    # Smallest first — detect issues early
    for tbl in \
        acris_country_codes acris_document_control_codes \
        acris_property_type_codes acris_ucc_collateral_codes \
        dof_exemption_classification_codes \
        rentstab dof_tax_lien_sale_list \
        hpd_litigations hpd_registrations \
        hpd_business_addresses hpd_corporate_owners \
        hpd_registrations_grouped_by_bbl \
        hpd_registrations_grouped_by_bbl_with_contacts \
        dof_sales dof_annual_sales \
        pad_adr \
        marshal_evictions_17 marshal_evictions_18 marshal_evictions_19 \
        dob_violations dob_now_jobs dobjobs \
        ecb_violations pluto_latest \
        marshal_evictions_all \
        hpd_contacts hpd_complaints_and_problems \
        dof_exemptions dof_property_valuation_and_assessments \
        hpd_violations \
        personal_property_remarks personal_property_legals \
        personal_property_references personal_property_master \
        real_property_remarks real_property_references \
        real_property_legals real_property_master \
        personal_property_parties real_property_parties \
        dob_complaints; do
        dump_one "$tbl"
    done

    log "Dump complete. Files:"
    ls -lhS "$DUMP_DIR"/*.dump 2>/dev/null | awk '{print $5, $9}' || true
    if [ "$ERRORS" -gt 0 ]; then
        log "WARNING: $ERRORS tables failed to dump. Review log."
    fi
    state_set dump_done done
}

# ── Phase: wipe ───────────────────────────────────────────────────────────────
phase_wipe() {
    log "=== Phase: Wipe Railway DB ==="

    # Test connectivity first
    if ! rpsql -c "SELECT 1;" > /dev/null 2>&1; then
        log "ERROR: Cannot connect to Railway. Check connection string and network."
        exit 1
    fi

    log "Current Railway DB size: $(rpsql -tAc "SELECT pg_size_pretty(pg_database_size(current_database()));" 2>/dev/null || echo "unknown")"
    log "WARNING: This drops ALL tables on Railway. Proceeding in 5 seconds..."
    sleep 5
    rpsql -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public; GRANT ALL ON SCHEMA public TO postgres; GRANT ALL ON SCHEMA public TO public;"
    log "Railway DB wiped."

    # Clear all railway state so restore_one runs fresh
    grep -v "^rail_" "$STATE" 2>/dev/null > "$STATE.tmp" || true
    mv "$STATE.tmp" "$STATE"
    state_set railway_wiped done
}

# ── Phase: railway ────────────────────────────────────────────────────────────
restore_one() {
    local tbl="$1"
    local f="$DUMP_DIR/${tbl}.dump"
    local key="rail_${tbl}"

    if [ "$(state_get "$key")" = "done" ]; then
        log "  SKIP: $tbl already restored"
        return 0
    fi
    if [ ! -f "$f" ]; then
        log "  SKIP: $f not found"
        return 0
    fi

    log "  Restoring $tbl ($(du -h "$f" | cut -f1))..."
    local restore_exit=0
    pg_restore --no-owner -d "$RAILWAY_DB" "$f" 2>&1 \
        | grep -v "^$" | tee -a "$LOG" || restore_exit=$?

    # Verify the table was actually created and has rows
    local cnt
    cnt=$(rpsql -tAc "SELECT count(*) FROM $tbl;" 2>/dev/null || echo "ERROR")

    if [ "$cnt" = "ERROR" ]; then
        log "  FAILED: $tbl — table not created on Railway"
        ERRORS=$((ERRORS + 1))
        # Do NOT mark as done — allows retry
        return 1
    fi

    # Compare with local count
    local local_cnt
    local_cnt=$(lpsql -tAc "SELECT count(*) FROM $tbl;" 2>/dev/null || echo "0")
    if [ "$cnt" != "$local_cnt" ] && [ "$local_cnt" != "0" ]; then
        local shortfall=$(( local_cnt - cnt ))
        if [ "$shortfall" -gt 1000 ]; then
            log "  PARTIAL: $tbl — Railway=$cnt vs Local=$local_cnt (short by $shortfall)"
            log "  NOT marking as done — will retry on next run"
            ERRORS=$((ERRORS + 1))
            # Drop the partial table so retry starts clean
            rpsql -c "DROP TABLE IF EXISTS $tbl CASCADE;" 2>/dev/null || true
            return 1
        fi
    fi

    log "  OK: Railway $tbl = $cnt rows"
    state_set "$key" done
    return 0
}

phase_railway() {
    log "=== Phase: Restore to Railway ==="

    # Test connectivity
    if ! rpsql -c "SELECT 1;" > /dev/null 2>&1; then
        log "ERROR: Cannot connect to Railway. Check connection and network."
        exit 1
    fi

    log "DB size before: $(rpsql -tAc "SELECT pg_size_pretty(pg_database_size(current_database()));" 2>/dev/null)"
    ERRORS=0

    # Reference/lookup tables first
    for tbl in \
        acris_country_codes acris_document_control_codes \
        acris_property_type_codes acris_ucc_collateral_codes \
        dof_exemption_classification_codes; do
        restore_one "$tbl" || true
    done

    # Small tables
    for tbl in \
        rentstab dof_tax_lien_sale_list \
        hpd_litigations hpd_registrations hpd_contacts \
        hpd_business_addresses hpd_corporate_owners \
        hpd_registrations_grouped_by_bbl \
        hpd_registrations_grouped_by_bbl_with_contacts \
        dof_sales dof_annual_sales \
        marshal_evictions_17 marshal_evictions_18 marshal_evictions_19; do
        restore_one "$tbl" || true
    done

    log "DB size check: $(rpsql -tAc "SELECT pg_size_pretty(pg_database_size(current_database()));" 2>/dev/null)"

    # Medium tables
    for tbl in \
        pad_adr pluto_latest \
        dob_violations ecb_violations \
        dob_now_jobs dobjobs \
        marshal_evictions_all; do
        restore_one "$tbl" || true
    done

    log "DB size check: $(rpsql -tAc "SELECT pg_size_pretty(pg_database_size(current_database()));" 2>/dev/null)"

    # Large tables
    for tbl in \
        dof_exemptions \
        dof_property_valuation_and_assessments \
        hpd_violations \
        hpd_complaints_and_problems \
        dob_complaints; do
        restore_one "$tbl" || true
    done

    log "DB size check: $(rpsql -tAc "SELECT pg_size_pretty(pg_database_size(current_database()));" 2>/dev/null)"

    # ACRIS — biggest last
    for tbl in \
        personal_property_remarks personal_property_legals \
        personal_property_references personal_property_master \
        real_property_remarks real_property_references \
        real_property_legals real_property_master \
        personal_property_parties real_property_parties; do
        restore_one "$tbl" || true
    done

    log "=== Railway restore complete ==="
    log "Final DB size: $(rpsql -tAc "SELECT pg_size_pretty(pg_database_size(current_database()));" 2>/dev/null)"
    if [ "$ERRORS" -gt 0 ]; then
        log "WARNING: $ERRORS tables had issues. Re-run --phase railway to retry failed tables."
    else
        log "All tables restored successfully."
    fi
}

# ── Phase: views ──────────────────────────────────────────────────────────────
phase_views() {
    log "=== Phase: Rebuild materialized views on Railway ==="
    if ! rpsql -c "SELECT 1;" > /dev/null 2>&1; then
        log "ERROR: Cannot connect to Railway."
        exit 1
    fi
    rpsql -f "$SCRIPT_DIR/create_views.sql" 2>&1 | tee -a "$LOG" || true
    log "Views rebuilt."
}

# ── Phase: verify (fixed — no subshell variable bug) ─────────────────────────
phase_verify() {
    log "=== Phase: Verify local vs Railway ==="

    local all_ok=1
    local output=""

    for tbl in \
        pad_adr pluto_latest rentstab \
        hpd_violations hpd_complaints_and_problems \
        hpd_litigations hpd_registrations hpd_contacts \
        dob_violations ecb_violations dobjobs dob_now_jobs \
        dob_complaints \
        marshal_evictions_all marshal_evictions_17 marshal_evictions_18 marshal_evictions_19 \
        dof_sales dof_annual_sales dof_tax_lien_sale_list \
        dof_property_valuation_and_assessments dof_exemptions \
        real_property_legals real_property_master real_property_parties \
        real_property_references real_property_remarks; do

        local l r st
        l=$(lpsql -tAc "SELECT count(*) FROM $tbl;" 2>/dev/null || echo "MISSING")
        r=$(rpsql -tAc "SELECT count(*) FROM $tbl;" 2>/dev/null || echo "MISSING")
        if [ "$l" = "$r" ]; then
            st="OK"
        else
            st="MISMATCH"
            all_ok=0
        fi
        line=$(printf "%-50s | %-12s | %-12s | %s" "$tbl" "$l" "$r" "$st")
        output="$output$line
"
        log "  $line"
    done

    echo ""
    printf "%-50s | %-12s | %-12s | %s\n" "TABLE" "LOCAL" "RAILWAY" "STATUS"
    printf "%-50s | %-12s | %-12s | %s\n" "---" "---" "---" "---"
    echo "$output"

    if [ "$all_ok" -eq 1 ]; then
        log "ALL TABLES MATCH. Railway is fully synced."
    else
        log "MISMATCHES FOUND. Review above. Re-run failed phases to fix."
    fi
}

# ── Main ──────────────────────────────────────────────────────────────────────
if [ "${1:-}" != "--phase" ] || [ -z "${2:-}" ]; then
    echo "Usage: $0 --phase <check|local|indexes|dump|wipe|railway|views|verify>"
    echo ""
    echo "Phases (run in order):"
    echo "  check    - Verify all CSV files are present and correct"
    echo "  local    - Drop + reload local PostgreSQL (RESUMABLE by sub-phase)"
    echo "  indexes  - Build indexes on local DB"
    echo "  dump     - pg_dump all tables to .dump files"
    echo "  wipe     - DROP all tables on Railway"
    echo "  railway  - pg_restore all tables to Railway (RESUMABLE per-table)"
    echo "  views    - Rebuild materialized views on Railway"
    echo "  verify   - Compare row counts: local vs Railway"
    echo ""
    echo "To reset state: rm data/.fresh_start_state"
    exit 1
fi

PHASE="$2"

# Keep Mac awake
caffeinate -dims &
CAFF=$!
trap "kill $CAFF 2>/dev/null || true" EXIT

log "=============================="
log "Phase: $PHASE"
log "=============================="

case "$PHASE" in
    check)   phase_check ;;
    local)   phase_local ;;
    indexes) phase_indexes ;;
    dump)    phase_dump ;;
    wipe)    phase_wipe ;;
    railway) phase_railway ;;
    views)   phase_views ;;
    verify)  phase_verify ;;
    *)       echo "Unknown phase: $PHASE"; exit 1 ;;
esac
