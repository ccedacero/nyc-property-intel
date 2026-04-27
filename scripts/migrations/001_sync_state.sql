-- 001_sync_state.sql
-- Adds sync_state tracking table and primary key on hpd_violations.
-- Idempotent — safe to re-run.

BEGIN;

-- ── sync_state ────────────────────────────────────────────────────────
-- Single source of truth for delta-sync cursors and run metadata.
CREATE TABLE IF NOT EXISTS sync_state (
    dataset_key       TEXT PRIMARY KEY,
    socrata_id        TEXT NOT NULL,
    table_name        TEXT NOT NULL,
    cursor_column     TEXT NOT NULL,
    cursor_value      TEXT,
    last_run_at       TIMESTAMPTZ,
    last_success_at   TIMESTAMPTZ,
    last_error        TEXT,
    rows_added_total  BIGINT DEFAULT 0,
    expected_rows     BIGINT,
    actual_rows       BIGINT
);

-- ── hpd_violations primary key ────────────────────────────────────────
-- Required for ON CONFLICT upserts.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'hpd_violations'::regclass AND contype = 'p'
    ) THEN
        -- Strip any rows where violationid is NULL (shouldn't exist, but defensive)
        DELETE FROM hpd_violations WHERE violationid IS NULL;
        ALTER TABLE hpd_violations ADD CONSTRAINT hpd_violations_pkey PRIMARY KEY (violationid);
    END IF;
END $$;

-- ── seed sync_state for hpd_violations ────────────────────────────────
INSERT INTO sync_state (dataset_key, socrata_id, table_name, cursor_column, cursor_value)
VALUES (
    'hpd_violations',
    'wvxf-dwi5',
    'hpd_violations',
    'novissueddate',
    -- Initialize cursor to current MAX so first run picks up only new rows.
    -- For full backfill, manually UPDATE sync_state SET cursor_value = NULL.
    (SELECT MAX(novissueddate)::text FROM hpd_violations)
)
ON CONFLICT (dataset_key) DO NOTHING;

COMMIT;
