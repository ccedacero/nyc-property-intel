-- Migration 006: Replace synthetic id PK on dobjobs with natural (job, doc) PK
--
-- Problem: NYCDB loaded multiple historical snapshots producing 783k duplicate
-- (job, doc) pairs. The synthetic id column has no equivalent in Socrata so
-- incremental upserts are impossible.
--
-- Fix:
--   1. Deduplicate — keep the row with the latest latestactiondate per (job,doc)
--   2. Drop the id column and its index
--   3. Add PRIMARY KEY (job, doc)
--   4. Recreate supporting indexes

BEGIN;

-- Step 1: delete all but the most-recent row per (job, doc)
DELETE FROM dobjobs
WHERE id IN (
    SELECT id FROM (
        SELECT id,
               ROW_NUMBER() OVER (
                   PARTITION BY job, doc
                   ORDER BY latestactiondate DESC NULLS LAST, id DESC
               ) AS rn
        FROM dobjobs
    ) ranked
    WHERE rn > 1
);

-- Step 2: drop synthetic PK
ALTER TABLE dobjobs DROP CONSTRAINT dobjobs_pkey;
ALTER TABLE dobjobs DROP COLUMN id;

-- Step 3: natural PK
ALTER TABLE dobjobs ADD PRIMARY KEY (job, doc);

-- Step 4: recreate indexes (bbl-based ones stay, date index recreated without id)
DROP INDEX IF EXISTS idx_dobjobs_date;
CREATE INDEX idx_dobjobs_date ON dobjobs (latestactiondate DESC);

COMMIT;
