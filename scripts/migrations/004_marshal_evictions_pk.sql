-- 004_marshal_evictions_pk.sql
-- Adds PK on marshal_evictions_all.courtindexnumber and seeds sync_state.
-- All 108,701 existing rows already have unique courtindexnumber — no dedup needed.

BEGIN;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'marshal_evictions_all'::regclass AND contype = 'p'
    ) THEN
        DELETE FROM marshal_evictions_all WHERE courtindexnumber IS NULL;
        ALTER TABLE marshal_evictions_all
            ADD CONSTRAINT marshal_evictions_all_pkey PRIMARY KEY (courtindexnumber);
    END IF;
END $$;

INSERT INTO sync_state (dataset_key, socrata_id, table_name, cursor_column, cursor_value)
VALUES (
    'marshal_evictions_all',
    '6z8x-wfk4',
    'marshal_evictions_all',
    'executeddate',
    (SELECT MAX(executeddate)::text FROM marshal_evictions_all)
)
ON CONFLICT (dataset_key) DO NOTHING;

COMMIT;
