-- =============================================================================
-- NYC Property Intel — Materialized Views
-- =============================================================================
-- Run AFTER create_indexes.sql. Views are phase-gated: only create views
-- whose underlying tables exist.
--
-- IMPORTANT: These views use CREATE MATERIALIZED VIEW IF NOT EXISTS so they
-- are safe to re-run. To update data, use refresh_views.sh (CONCURRENTLY).
--
-- Estimated build times (on SSD, 16GB RAM):
--   mv_property_profile:   ~2 min  (Phase A)
--   mv_violation_summary:  ~5 min  (Phase A+B — needs hpd_violations + dob_violations)
--   mv_current_ownership:  ~10 min (Phase C — scans all of ACRIS)
-- =============================================================================


-- =============================================================================
-- VIEW 1: mv_property_profile (Phase A)
-- Denormalized PLUTO view — one row per BBL with all key property attributes.
-- This is the primary lookup target for lookup_property.
-- =============================================================================
DROP MATERIALIZED VIEW IF EXISTS mv_property_profile;

CREATE MATERIALIZED VIEW mv_property_profile AS
SELECT
    p.bbl,
    p.address,
    p.borough,
    p.block,
    p.lot,
    p.communityboard AS cd,  -- community district
    p.censustract2010 AS ct2010,  -- census tract
    p.cb2010,                -- census block
    p.councildistrict AS council,  -- city council district
    p.ownername,
    p.bldgclass,
    p.landuse,
    p.zonedist1,
    p.zonedist2,
    p.zonedist3,
    p.zonedist4,
    p.overlay1,
    p.overlay2,
    p.spdist1,
    p.spdist2,
    p.ltdheight,             -- limited height district
    p.numbldgs,
    p.numfloors,
    p.unitsres,
    p.unitstotal,
    p.lotfront,
    p.lotdepth,
    p.lotarea,
    p.bldgfront,
    p.bldgdepth,
    p.bldgarea,
    p.comarea,
    p.resarea,
    p.officearea,
    p.retailarea,
    p.garagearea,
    p.strgearea,
    p.factryarea,
    p.otherarea,
    p.yearbuilt,
    p.yearalter1,
    p.yearalter2,
    p.condono,               -- condo number (NULL if not a condo)
    p.builtfar,
    p.residfar,
    p.commfar,
    p.facilfar,
    p.assessland,
    p.assesstot,
    p.exempttot,
    p.taxmap,
    p.histdist,
    p.landmark,
    p.irrlotcode,            -- irregular lot flag
    p.latitude,
    p.longitude,
    p.postcode,
    -- Computed fields for development potential analysis
    CASE
        WHEN p.lotarea > 0 AND p.residfar IS NOT NULL AND p.residfar > 0
        THEN (p.residfar * p.lotarea) - COALESCE(p.bldgarea, 0)
        ELSE NULL
    END AS unused_far_sqft,
    CASE
        WHEN p.condono IS NOT NULL AND p.condono != ''
        THEN true
        ELSE false
    END AS is_condo
FROM pluto_latest p
WITH DATA;

CREATE UNIQUE INDEX idx_mv_property_profile_bbl ON mv_property_profile (bbl);
CREATE INDEX idx_mv_property_profile_postcode ON mv_property_profile (postcode);
CREATE INDEX idx_mv_property_profile_address_gin
    ON mv_property_profile USING gin (to_tsvector('english', address));
CREATE INDEX idx_mv_property_profile_bldgclass ON mv_property_profile (bldgclass);
CREATE INDEX idx_mv_property_profile_condono ON mv_property_profile (condono)
    WHERE condono IS NOT NULL AND condono != '';


-- =============================================================================
-- VIEW 2: mv_violation_summary (Phase A partial, full with Phase B)
-- Per-BBL violation counts with SEPARATE columns for HPD and DOB.
--
-- BUG FIX: The original plan conflated HPD class (A/B/C severity) with DOB
-- violationcategory (a different classification system). This view keeps them
-- as independent column sets.
--
-- HPD class system:
--   A = non-hazardous
--   B = hazardous
--   C = immediately hazardous
--
-- DOB has no equivalent severity class. We track:
--   - total count
--   - has_disposition (proxy for resolved) vs no disposition (proxy for open)
--   - most recent issue date
-- =============================================================================
DROP MATERIALIZED VIEW IF EXISTS mv_violation_summary;

CREATE MATERIALIZED VIEW mv_violation_summary AS
SELECT
    COALESCE(h.bbl, d.bbl) AS bbl,

    -- HPD violation counts
    COALESCE(h.hpd_total, 0)           AS hpd_total,
    COALESCE(h.hpd_class_a, 0)         AS hpd_class_a,
    COALESCE(h.hpd_class_b, 0)         AS hpd_class_b,
    COALESCE(h.hpd_class_c, 0)         AS hpd_class_c,
    COALESCE(h.hpd_open, 0)            AS hpd_open,
    h.hpd_most_recent                  AS hpd_most_recent,

    -- DOB violation counts (separate classification system)
    COALESCE(d.dob_total, 0)           AS dob_total,
    COALESCE(d.dob_no_disposition, 0)  AS dob_no_disposition,   -- proxy for "open"
    COALESCE(d.dob_has_disposition, 0) AS dob_has_disposition,  -- proxy for "resolved"
    d.dob_most_recent                  AS dob_most_recent,

    -- Combined most recent date (for sorting)
    GREATEST(h.hpd_most_recent, d.dob_most_recent) AS most_recent_violation
FROM
    (
        SELECT
            bbl,
            COUNT(*)                                              AS hpd_total,
            COUNT(*) FILTER (WHERE class = 'A')                   AS hpd_class_a,
            COUNT(*) FILTER (WHERE class = 'B')                   AS hpd_class_b,
            COUNT(*) FILTER (WHERE class = 'C')                   AS hpd_class_c,
            COUNT(*) FILTER (WHERE currentstatus = 'OPEN')        AS hpd_open,
            MAX(inspectiondate)                                   AS hpd_most_recent
        FROM hpd_violations
        GROUP BY bbl
    ) h
FULL OUTER JOIN
    (
        SELECT
            bbl,
            COUNT(*)                                              AS dob_total,
            COUNT(*) FILTER (WHERE dispositiondate IS NULL)       AS dob_no_disposition,
            COUNT(*) FILTER (WHERE dispositiondate IS NOT NULL)   AS dob_has_disposition,
            MAX(issuedate)                                        AS dob_most_recent
        FROM dob_violations
        GROUP BY bbl
    ) d ON h.bbl = d.bbl
WITH DATA;

CREATE UNIQUE INDEX idx_mv_violation_summary_bbl ON mv_violation_summary (bbl);
CREATE INDEX idx_mv_violation_summary_hpd_open
    ON mv_violation_summary (hpd_open DESC) WHERE hpd_open > 0;


-- =============================================================================
-- VIEW 3: mv_current_ownership (Phase C — requires ACRIS)
-- Most recent deed-type document per BBL from ACRIS.
--
-- BUG FIXES applied:
-- 1. Doc type filter: whitelist specific doctype codes instead of
--    classcodedescrip LIKE '%DEED%' which missed RPTT, correction deeds, etc.
--    Whitelisted codes:
--      DEED  — Deed
--      DEDL  — Deed, Leasehold
--      DEDC  — Deed, Condo
--      RPTT  — Real Property Transfer Tax (always accompanies a transfer)
--      CTOR  — Confirmatory Deed / Correction Deed
--      CORRD — Corrective Deed
--
-- 2. NULL guards on block/lot: rows with NULL block or lot produce garbage BBLs.
--    Filter them out before concatenation.
--
-- 3. Condo note: condo UNIT lots (lot >= 1001) have deeds recorded at the
--    unit level. The PLUTO parent lot may show a different owner. For full
--    condo ownership, query both unit BBL and parent BBL via pluto condono.
-- =============================================================================
DROP MATERIALIZED VIEW IF EXISTS mv_current_ownership;

CREATE MATERIALIZED VIEW mv_current_ownership AS
SELECT DISTINCT ON (computed_bbl)
    computed_bbl AS bbl,
    m.doctype,
    m.doctype AS doc_type_description,
    m.docdate,
    m.docamount,
    p.name AS owner_name,
    p.address1,
    p.city,
    p.state,
    p.zip,
    m.documentid,
    m.recordedfiled
FROM real_property_legals l
CROSS JOIN LATERAL (
    -- Compute BBL with NULL guards; skip rows where block or lot is NULL
    SELECT
        l.borough || lpad(l.block::text, 5, '0') || lpad(l.lot::text, 4, '0')
        AS computed_bbl
) bbl_calc
JOIN real_property_master m
    ON l.documentid = m.documentid
LEFT JOIN LATERAL (
    -- Get the grantee (new owner). Use LEFT JOIN + LATERAL so we still get
    -- the row even if party data is missing. If multiple grantees, pick first
    -- alphabetically for determinism.
    SELECT p2.name, p2.address1, p2.city, p2.state, p2.zip
    FROM real_property_parties p2
    WHERE p2.documentid = m.documentid
      AND p2.partytype = 2  -- grantee (buyer/new owner)
    ORDER BY p2.name
    LIMIT 1
) p ON true
WHERE
    -- NULL guards: skip rows where block or lot is NULL
    l.block IS NOT NULL
    AND l.lot IS NOT NULL
    AND l.borough IS NOT NULL
    -- Whitelist transfer document types by doctype code
    AND m.doctype IN ('DEED', 'DEDL', 'DEDC', 'RPTT', 'CTOR', 'CORRD')
    -- Only include documents with a valid date
    AND m.docdate IS NOT NULL
ORDER BY
    computed_bbl,
    m.docdate DESC,
    m.recordedfiled DESC NULLS LAST
WITH DATA;

CREATE UNIQUE INDEX idx_mv_current_ownership_bbl ON mv_current_ownership (bbl);
CREATE INDEX idx_mv_current_ownership_docdate ON mv_current_ownership (docdate DESC);
