-- 005_marshal_evictions_composite_pk.sql
-- Replace single-column courtindexnumber PK with composite (courtindexnumber,
-- docketnumber, executeddate).
--
-- Root cause: courtindexnumber is not unique in Socrata — the same case can
-- have multiple eviction attempts (different dates) or different docket
-- numbers. The single-column PK caused UPSERT to silently drop ~10K rows.
-- The new composite PK retains all distinct eviction events.
--
-- True exact duplicates (same cin+docket+date, ~186 rows) still exist in
-- Socrata and will be deduplicated by UPSERT — that is intentional.

BEGIN;

-- 1. Drop the single-column PK.
ALTER TABLE marshal_evictions_all
    DROP CONSTRAINT IF EXISTS marshal_evictions_all_pkey;

-- 2. Remove the small number of rows that would violate the new PK
--    (courtindexnumber NULL already cleaned in 004; guard docketnumber/executeddate too).
DELETE FROM marshal_evictions_all
WHERE courtindexnumber IS NULL
   OR docketnumber IS NULL
   OR executeddate IS NULL;

-- 3. Deduplicate any rows that share the new composite key (keep one arbitrarily).
DELETE FROM marshal_evictions_all a
USING marshal_evictions_all b
WHERE a.ctid > b.ctid
  AND a.courtindexnumber = b.courtindexnumber
  AND a.docketnumber      = b.docketnumber
  AND a.executeddate      = b.executeddate;

-- 4. Add composite PK.
ALTER TABLE marshal_evictions_all
    ADD CONSTRAINT marshal_evictions_all_pkey
    PRIMARY KEY (courtindexnumber, docketnumber, executeddate);

COMMIT;
