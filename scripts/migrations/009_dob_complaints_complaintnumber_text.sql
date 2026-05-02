-- Migration 009: change dob_complaints.complaintnumber from integer to text.
--
-- Why: Socrata returns ~82K alphanumeric complaint numbers (pattern '<boro>A<digits>',
-- e.g. '3A43512') alongside the standard numeric ones. With complaintnumber typed
-- as integer, _coerce() in sync_delta.py converted those values to NULL during the
-- staging COPY, which violated the NOT NULL implied by the primary key and aborted
-- the entire page. Net effect: dob_complaints incremental sync had never succeeded
-- since migration 007 added the PK.
--
-- Existing 3M rows are pure numeric — the USING cast preserves them as text strings
-- (e.g. 4225962 → '4225962'), and the rebuilt PK btree compares fine. App code
-- (src/.../tools/dob_complaints.py) only SELECTs and equality-compares complaintnumber,
-- never does arithmetic on it.
--
-- Idempotent: re-runs are no-ops once the column is text.

BEGIN;

DO $$
BEGIN
    IF (SELECT data_type FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name   = 'dob_complaints'
          AND column_name  = 'complaintnumber') = 'integer' THEN
        ALTER TABLE dob_complaints
            ALTER COLUMN complaintnumber TYPE text USING complaintnumber::text;
    END IF;
END $$;

COMMIT;
