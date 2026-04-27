-- 003_acris_master_pk.sql
-- Dedupe real_property_master by documentid (keep most recent modifieddate),
-- add PK, seed sync_state.
-- Idempotent — safe to re-run.

BEGIN;

-- ── dedupe ────────────────────────────────────────────────────────────
-- ~11K duplicate documentids exist (~0.07% of 16.9M rows). Keep the row
-- with the most recent modifieddate; tie-break by ctid for determinism.
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'real_property_master'::regclass AND contype = 'p'
    ) THEN
        DELETE FROM real_property_master t
        USING (
            SELECT documentid, ctid FROM (
                SELECT documentid, ctid,
                       ROW_NUMBER() OVER (
                           PARTITION BY documentid
                           ORDER BY modifieddate DESC NULLS LAST, ctid
                       ) AS rn
                FROM real_property_master
                WHERE documentid IS NOT NULL
            ) s WHERE rn > 1
        ) d
        WHERE t.ctid = d.ctid;

        DELETE FROM real_property_master WHERE documentid IS NULL;
        ALTER TABLE real_property_master
            ADD CONSTRAINT real_property_master_pkey PRIMARY KEY (documentid);
    END IF;
END $$;

-- ── seed sync_state ───────────────────────────────────────────────────
INSERT INTO sync_state (dataset_key, socrata_id, table_name, cursor_column, cursor_value)
VALUES (
    'real_property_master',
    'bnx9-e6tj',
    'real_property_master',
    'modifieddate',
    (SELECT MAX(modifieddate)::text FROM real_property_master WHERE modifieddate IS NOT NULL)
)
ON CONFLICT (dataset_key) DO NOTHING;

COMMIT;
