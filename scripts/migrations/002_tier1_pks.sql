-- 002_tier1_pks.sql
-- Adds primary keys + sync_state seeds for the rest of clean-PK Tier 1 datasets.
-- Idempotent — safe to re-run.
-- Skipped (handled in 003): dob_complaints (source-level dupes), real_property_master (minor dupes), dob_now_jobs (empty).

BEGIN;

-- ── hpd_complaints_and_problems ──────────────────────────────────────
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conrelid = 'hpd_complaints_and_problems'::regclass AND contype = 'p') THEN
        DELETE FROM hpd_complaints_and_problems WHERE problemid IS NULL;
        ALTER TABLE hpd_complaints_and_problems
            ADD CONSTRAINT hpd_complaints_and_problems_pkey PRIMARY KEY (problemid);
    END IF;
END $$;

-- ── hpd_litigations ──────────────────────────────────────────────────
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conrelid = 'hpd_litigations'::regclass AND contype = 'p') THEN
        DELETE FROM hpd_litigations WHERE litigationid IS NULL;
        ALTER TABLE hpd_litigations
            ADD CONSTRAINT hpd_litigations_pkey PRIMARY KEY (litigationid);
    END IF;
END $$;

-- ── dob_violations ───────────────────────────────────────────────────
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conrelid = 'dob_violations'::regclass AND contype = 'p') THEN
        DELETE FROM dob_violations WHERE isndobbisviol IS NULL;
        ALTER TABLE dob_violations
            ADD CONSTRAINT dob_violations_pkey PRIMARY KEY (isndobbisviol);
    END IF;
END $$;

-- ── dobjobs ──────────────────────────────────────────────────────────
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conrelid = 'dobjobs'::regclass AND contype = 'p') THEN
        DELETE FROM dobjobs WHERE id IS NULL;
        ALTER TABLE dobjobs
            ADD CONSTRAINT dobjobs_pkey PRIMARY KEY (id);
    END IF;
END $$;

-- ── ecb_violations ───────────────────────────────────────────────────
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conrelid = 'ecb_violations'::regclass AND contype = 'p') THEN
        DELETE FROM ecb_violations WHERE ecbviolationnumber IS NULL;
        ALTER TABLE ecb_violations
            ADD CONSTRAINT ecb_violations_pkey PRIMARY KEY (ecbviolationnumber);
    END IF;
END $$;

-- ── seed sync_state for each new dataset ─────────────────────────────
-- Cursor initialized to MAX so first run picks up only new rows.
-- For full backfill: UPDATE sync_state SET cursor_value = NULL WHERE dataset_key = '...';

INSERT INTO sync_state (dataset_key, socrata_id, table_name, cursor_column, cursor_value)
VALUES
    ('hpd_complaints_and_problems', 'ygpa-z7cr', 'hpd_complaints_and_problems', 'receiveddate',
        (SELECT MAX(receiveddate)::text FROM hpd_complaints_and_problems)),
    ('hpd_litigations',             '59kj-x8nc', 'hpd_litigations',             'caseopendate',
        (SELECT MAX(caseopendate)::text FROM hpd_litigations)),
    ('dob_violations',              '3h2n-5cm9', 'dob_violations',              'issuedate',
        (SELECT MAX(issuedate)::text FROM dob_violations)),
    ('dobjobs',                     'ic3t-wcy2', 'dobjobs',                     'latestactiondate',
        (SELECT MAX(latestactiondate)::text FROM dobjobs)),
    ('ecb_violations',              '6bgk-3dad', 'ecb_violations',              'issuedate',
        (SELECT MAX(issuedate)::text FROM ecb_violations))
ON CONFLICT (dataset_key) DO NOTHING;

COMMIT;
