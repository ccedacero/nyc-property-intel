-- =============================================================================
-- NYC Property Intel — Index Creation Script
-- =============================================================================
-- Run AFTER nycdb loads each phase's datasets.
-- Indexes are idempotent (IF NOT EXISTS) so re-running is safe.
--
-- Estimated creation times (on SSD, 16GB RAM):
--   Phase A indexes: ~5 min
--   Phase B indexes: ~3 min
--   Phase C indexes: ~10 min (ACRIS tables are large)
-- =============================================================================

-- ---------------------------------------------------------------------------
-- PHASE A: pluto_latest, pad, hpd_violations
-- ---------------------------------------------------------------------------

-- PLUTO — primary property profile table (~870K rows)
CREATE INDEX IF NOT EXISTS idx_pluto_bbl
    ON pluto_latest (bbl);
CREATE INDEX IF NOT EXISTS idx_pluto_address_gin
    ON pluto_latest USING gin (to_tsvector('english', address));
CREATE INDEX IF NOT EXISTS idx_pluto_postcode
    ON pluto_latest (postcode);
CREATE INDEX IF NOT EXISTS idx_pluto_bldgclass
    ON pluto_latest (bldgclass);
-- Composite: comps lookup by zip + building class
CREATE INDEX IF NOT EXISTS idx_pluto_zip_class
    ON pluto_latest (postcode, bldgclass);

-- PAD — Property Address Directory (address-to-BBL fallback)
CREATE INDEX IF NOT EXISTS idx_pad_adr_stname_gin
    ON pad_adr USING gin (to_tsvector('english', stname));
CREATE INDEX IF NOT EXISTS idx_pad_adr_boro
    ON pad_adr (boro);
-- Composite: the full address lookup pattern (boro + street + house number range)
CREATE INDEX IF NOT EXISTS idx_pad_adr_boro_stname
    ON pad_adr (boro, stname);
-- pad_bbl is not created by nycdb --load pad; index omitted.

-- HPD Violations (~4M rows)
CREATE INDEX IF NOT EXISTS idx_hpd_violations_bbl
    ON hpd_violations (bbl);
CREATE INDEX IF NOT EXISTS idx_hpd_violations_date
    ON hpd_violations (inspectiondate DESC);
CREATE INDEX IF NOT EXISTS idx_hpd_violations_class
    ON hpd_violations (class);
CREATE INDEX IF NOT EXISTS idx_hpd_violations_status
    ON hpd_violations (currentstatus);
-- Functional index for case-insensitive status filtering (upper(currentstatus) = upper($3))
CREATE INDEX IF NOT EXISTS idx_hpd_violations_status_upper
    ON hpd_violations (upper(currentstatus));
-- Composite: the most common filter pattern (bbl + class + status)
CREATE INDEX IF NOT EXISTS idx_hpd_violations_bbl_class_status
    ON hpd_violations (bbl, class, currentstatus);

-- HPD Complaints
CREATE INDEX IF NOT EXISTS idx_hpd_complaints_bbl
    ON hpd_complaints (bbl);

-- HPD Registrations (joined by boroid/block/lot, not bbl)
CREATE INDEX IF NOT EXISTS idx_hpd_registrations_bbl
    ON hpd_registrations (boroid, block, lot);
CREATE INDEX IF NOT EXISTS idx_hpd_contacts_regid
    ON hpd_contacts (registrationid);

-- HPD Litigations
CREATE INDEX IF NOT EXISTS idx_hpd_litigations_bbl
    ON hpd_litigations (bbl);

-- ---------------------------------------------------------------------------
-- PHASE B: dof_sales, dof_annual_sales, dob_violations,
--          dof_property_valuation_and_assessments, dof_exemptions,
--          dof_tax_lien_sale_list, rentstab, ecb_violations,
--          hpd_complaints, hpd_registrations, hpd_litigations
-- ---------------------------------------------------------------------------

-- DOF Rolling Sales (~60K rows)
CREATE INDEX IF NOT EXISTS idx_dof_sales_bbl
    ON dof_sales (bbl);
CREATE INDEX IF NOT EXISTS idx_dof_sales_date
    ON dof_sales (saledate DESC);
CREATE INDEX IF NOT EXISTS idx_dof_sales_zipcode
    ON dof_sales (zipcode);
CREATE INDEX IF NOT EXISTS idx_dof_sales_neighborhood
    ON dof_sales (neighborhood);
-- Composite: comps search by zip + building class + date
CREATE INDEX IF NOT EXISTS idx_dof_sales_zip_class_date
    ON dof_sales (zipcode, buildingclassattimeofsale, saledate DESC);
-- Composite: dedup key for UNION with annual sales
CREATE INDEX IF NOT EXISTS idx_dof_sales_dedup
    ON dof_sales (bbl, saledate, saleprice);

-- DOF Annual Sales (~1M rows)
CREATE INDEX IF NOT EXISTS idx_dof_annual_sales_bbl
    ON dof_annual_sales (bbl);
CREATE INDEX IF NOT EXISTS idx_dof_annual_sales_date
    ON dof_annual_sales (sale_date DESC);
CREATE INDEX IF NOT EXISTS idx_dof_annual_sales_zipcode
    ON dof_annual_sales (zip_code);
CREATE INDEX IF NOT EXISTS idx_dof_annual_sales_zip_class_date
    ON dof_annual_sales (zip_code, building_class_at_time_of_sale, sale_date DESC);
CREATE INDEX IF NOT EXISTS idx_dof_annual_sales_dedup
    ON dof_annual_sales (bbl, sale_date, sale_price);

-- DOB Violations (~2M rows)
CREATE INDEX IF NOT EXISTS idx_dob_violations_bbl
    ON dob_violations (bbl);
CREATE INDEX IF NOT EXISTS idx_dob_violations_date
    ON dob_violations (issuedate DESC);
CREATE INDEX IF NOT EXISTS idx_dob_violations_disposition
    ON dob_violations (dispositiondate);

-- DOF Property Valuation and Assessments (~6M rows)
CREATE INDEX IF NOT EXISTS idx_dof_val_bbl
    ON dof_property_valuation_and_assessments (bbl);

-- DOF Exemptions (~740K rows)
CREATE INDEX IF NOT EXISTS idx_dof_exemptions_bbl
    ON dof_exemptions (bbl);

-- DOF Tax Lien Sale List
CREATE INDEX IF NOT EXISTS idx_dof_liens_bbl
    ON dof_tax_lien_sale_list (bbl);

-- Rent Stabilization (~45K rows)
CREATE INDEX IF NOT EXISTS idx_rentstab_bbl
    ON rentstab (ucbbl);

-- ECB Violations
CREATE INDEX IF NOT EXISTS idx_ecb_violations_bbl
    ON ecb_violations (bbl);

-- ---------------------------------------------------------------------------
-- PHASE C: acris (14 tables), dobjobs, dob_now_jobs
-- ---------------------------------------------------------------------------

-- ACRIS Real Property Legals — the BBL join table (millions of rows)
-- This is the primary entry point for all ACRIS queries by property
CREATE INDEX IF NOT EXISTS idx_acris_legals_bbl
    ON real_property_legals (borough, block, lot);
CREATE INDEX IF NOT EXISTS idx_acris_legals_documentid
    ON real_property_legals (documentid);

-- ACRIS Real Property Master — document metadata
CREATE INDEX IF NOT EXISTS idx_acris_master_documentid
    ON real_property_master (documentid);
CREATE INDEX IF NOT EXISTS idx_acris_master_docdate
    ON real_property_master (docdate DESC);
CREATE INDEX IF NOT EXISTS idx_acris_master_doctype
    ON real_property_master (doctype);
-- Composite: ownership view build (doctype + date)
CREATE INDEX IF NOT EXISTS idx_acris_master_doctype_date
    ON real_property_master (doctype, docdate DESC);

-- ACRIS Real Property Parties — grantor/grantee info
CREATE INDEX IF NOT EXISTS idx_acris_parties_documentid
    ON real_property_parties (documentid);
CREATE INDEX IF NOT EXISTS idx_acris_parties_docid_type
    ON real_property_parties (documentid, partytype);

-- ACRIS Document Control Codes — doc type reference
CREATE INDEX IF NOT EXISTS idx_acris_dcc_doctype
    ON acris_document_control_codes (doctype);

-- ACRIS Real Property References
CREATE INDEX IF NOT EXISTS idx_acris_refs_documentid
    ON real_property_references (documentid);

-- ACRIS Real Property Remarks
CREATE INDEX IF NOT EXISTS idx_acris_remarks_documentid
    ON real_property_remarks (documentid);

-- ACRIS Personal Property Legals (UCC filings)
CREATE INDEX IF NOT EXISTS idx_acris_personal_legals_documentid
    ON personal_property_legals (documentid);

-- ACRIS Personal Property Master
CREATE INDEX IF NOT EXISTS idx_acris_personal_master_documentid
    ON personal_property_master (documentid);
CREATE INDEX IF NOT EXISTS idx_acris_personal_master_doctype
    ON personal_property_master (doctype);

-- ACRIS Personal Property Parties
CREATE INDEX IF NOT EXISTS idx_acris_personal_parties_documentid
    ON personal_property_parties (documentid);

-- DOB Jobs (legacy BIS system, ~1M rows)
CREATE INDEX IF NOT EXISTS idx_dobjobs_bbl
    ON dobjobs (bbl);
CREATE INDEX IF NOT EXISTS idx_dobjobs_type
    ON dobjobs (jobtype);
CREATE INDEX IF NOT EXISTS idx_dobjobs_date
    ON dobjobs (prefilingdate DESC);
-- Composite: filter by bbl + job type
CREATE INDEX IF NOT EXISTS idx_dobjobs_bbl_type
    ON dobjobs (bbl, jobtype);

-- DOB NOW Jobs (~380K rows)
CREATE INDEX IF NOT EXISTS idx_dob_now_jobs_bbl
    ON dob_now_jobs (bbl);
CREATE INDEX IF NOT EXISTS idx_dob_now_jobs_type
    ON dob_now_jobs (jobtype);
CREATE INDEX IF NOT EXISTS idx_dob_now_jobs_date
    ON dob_now_jobs (filingdate DESC);
CREATE INDEX IF NOT EXISTS idx_dob_now_jobs_bbl_type
    ON dob_now_jobs (bbl, jobtype);

-- ---------------------------------------------------------------------------
-- PHASE D: dob_complaints, marshal_evictions
-- (nycdb already creates bin idx on dob_complaints and bbl idx on
--  marshal_evictions_all; these add the address-lookup indexes)
-- ---------------------------------------------------------------------------

-- DOB Complaints (~3M rows) — nycdb creates bin idx; add street search
CREATE INDEX IF NOT EXISTS idx_dob_complaints_housestreet
    ON dob_complaints (upper(housestreet));
CREATE INDEX IF NOT EXISTS idx_dob_complaints_dateentered
    ON dob_complaints (dateentered DESC);
CREATE INDEX IF NOT EXISTS idx_dob_complaints_category
    ON dob_complaints (complaintcategory);
-- Composite: most common filter (bin + date)
CREATE INDEX IF NOT EXISTS idx_dob_complaints_bin_date
    ON dob_complaints (bin, dateentered DESC);

-- Marshal Evictions All (~109K rows) — nycdb creates bbl idx; add date + type
-- Note: executeddate may be date or text depending on nycdb load version.
-- The tool uses executeddate::text comparisons to handle both safely.
CREATE INDEX IF NOT EXISTS idx_marshal_evictions_all_date
    ON marshal_evictions_all (executeddate DESC);
CREATE INDEX IF NOT EXISTS idx_marshal_evictions_all_type
    ON marshal_evictions_all (residentialcommercialind);
-- Functional upper() index supports case-insensitive eviction_type filter
CREATE INDEX IF NOT EXISTS idx_marshal_evictions_all_type_upper
    ON marshal_evictions_all (upper(residentialcommercialind));
-- Composite: primary tool query (bbl + date)
CREATE INDEX IF NOT EXISTS idx_marshal_evictions_all_bbl_date
    ON marshal_evictions_all (bbl, executeddate DESC);

-- ---------------------------------------------------------------------------
-- PHASE E: 311 complaints — trigram indexes for address substring search
-- Requires pg_trgm extension: CREATE EXTENSION IF NOT EXISTS pg_trgm;
-- Build CONCURRENTLY in production to avoid locking: CREATE INDEX CONCURRENTLY
-- ---------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Trigram GIN index on incident_address (upper) — enables fast LIKE '%...%' scans
CREATE INDEX IF NOT EXISTS idx_311_incident_address_trgm
    ON nyc_311_complaints USING gin (upper(incident_address) gin_trgm_ops);
-- Trigram index on complaint_type (upper) — fast filter on complaint_type keyword
CREATE INDEX IF NOT EXISTS idx_311_complaint_type_trgm
    ON nyc_311_complaints USING gin (upper(complaint_type) gin_trgm_ops);
-- BBL + date index — primary path when BBL is known
CREATE INDEX IF NOT EXISTS idx_311_bbl_date
    ON nyc_311_complaints (bbl, created_date DESC);
