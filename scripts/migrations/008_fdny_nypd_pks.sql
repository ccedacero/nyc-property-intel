-- Migration 008: Natural PKs for fdny_incidents and nypd_crime_complaints
-- FDNY has 428 dups (same incident, different close-time snapshots — keep latest).
-- NYPD has 0 dups on cmplnt_num — just add the PK constraint.

BEGIN;

-- ── fdny_incidents  (428 dup starfire_incident_ids — keep latest close time) ──
DELETE FROM fdny_incidents WHERE ctid IN (
    SELECT ctid FROM (
        SELECT ctid, ROW_NUMBER() OVER (
            PARTITION BY starfire_incident_id
            ORDER BY incident_close_datetime DESC NULLS LAST,
                     incident_datetime       DESC NULLS LAST,
                     ctid DESC
        ) AS rn FROM fdny_incidents
    ) ranked WHERE rn > 1
);
ALTER TABLE fdny_incidents ADD PRIMARY KEY (starfire_incident_id);
CREATE INDEX IF NOT EXISTS idx_fdny_datetime ON fdny_incidents (incident_datetime DESC);
CREATE INDEX IF NOT EXISTS idx_fdny_borough  ON fdny_incidents (incident_borough);

-- ── nypd_crime_complaints  (0 dups — safe to add PK directly) ───────────────
ALTER TABLE nypd_crime_complaints ADD PRIMARY KEY (cmplnt_num);
CREATE INDEX IF NOT EXISTS idx_nypd_rpt_dt   ON nypd_crime_complaints (rpt_dt DESC);
CREATE INDEX IF NOT EXISTS idx_nypd_boro     ON nypd_crime_complaints (boro_nm);

COMMIT;
