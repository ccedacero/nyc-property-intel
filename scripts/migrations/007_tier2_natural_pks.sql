-- Migration 007: Dedup and add natural PKs to tier-2/3 datasets
-- Tables: nyc_311_complaints, personal_property_master, hpd_registrations,
--         dob_complaints, dob_now_jobs
-- Pattern: delete all-but-latest duplicate per natural key, then add PK.

-- ── Part 1: clean tables (no NULLs in PK column) ───────────────────────────
BEGIN;

-- nyc_311_complaints  (6 dups on unique_key)
DELETE FROM nyc_311_complaints WHERE ctid IN (
    SELECT ctid FROM (
        SELECT ctid, ROW_NUMBER() OVER (
            PARTITION BY unique_key
            ORDER BY closed_date DESC NULLS LAST, ctid DESC
        ) AS rn FROM nyc_311_complaints
    ) ranked WHERE rn > 1
);
ALTER TABLE nyc_311_complaints ADD PRIMARY KEY (unique_key);
CREATE INDEX IF NOT EXISTS idx_311_created   ON nyc_311_complaints (created_date);
CREATE INDEX IF NOT EXISTS idx_311_bbl       ON nyc_311_complaints (bbl);

-- personal_property_master  (1742 dups on documentid)
DELETE FROM personal_property_master WHERE ctid IN (
    SELECT ctid FROM (
        SELECT ctid, ROW_NUMBER() OVER (
            PARTITION BY documentid
            ORDER BY goodthroughdate DESC NULLS LAST, ctid DESC
        ) AS rn FROM personal_property_master
    ) ranked WHERE rn > 1
);
ALTER TABLE personal_property_master ADD PRIMARY KEY (documentid);
CREATE INDEX IF NOT EXISTS idx_ppm_modifieddate ON personal_property_master (modifieddate DESC);

-- hpd_registrations  (9669 dups on registrationid)
DELETE FROM hpd_registrations WHERE ctid IN (
    SELECT ctid FROM (
        SELECT ctid, ROW_NUMBER() OVER (
            PARTITION BY registrationid
            ORDER BY lastregistrationdate DESC NULLS LAST, ctid DESC
        ) AS rn FROM hpd_registrations
    ) ranked WHERE rn > 1
);
ALTER TABLE hpd_registrations ADD PRIMARY KEY (registrationid);
CREATE INDEX IF NOT EXISTS idx_hpdreg_date ON hpd_registrations (lastregistrationdate DESC);
CREATE INDEX IF NOT EXISTS idx_hpdreg_bbl  ON hpd_registrations (bbl);

-- dob_now_jobs  (empty, just add PK for future sync)
ALTER TABLE dob_now_jobs ADD PRIMARY KEY (jobfilingnumber);
CREATE INDEX IF NOT EXISTS idx_dobnow_statusdate ON dob_now_jobs (currentstatusdate DESC);

COMMIT;

-- ── Part 2: dob_complaints (has NULLs + dups on complaintnumber) ──────────
BEGIN;

-- First remove rows with NULL complaintnumber (unidentifiable records)
DELETE FROM dob_complaints WHERE complaintnumber IS NULL;

-- Then deduplicate remaining rows
DELETE FROM dob_complaints WHERE ctid IN (
    SELECT ctid FROM (
        SELECT ctid, ROW_NUMBER() OVER (
            PARTITION BY complaintnumber
            ORDER BY dobrundate DESC NULLS LAST, dateentered DESC NULLS LAST, ctid DESC
        ) AS rn FROM dob_complaints
    ) ranked WHERE rn > 1
);
ALTER TABLE dob_complaints ADD PRIMARY KEY (complaintnumber);
CREATE INDEX IF NOT EXISTS idx_dobcmp_date ON dob_complaints (dateentered DESC);
CREATE INDEX IF NOT EXISTS idx_dobcmp_bin  ON dob_complaints (bin);

COMMIT;
