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
CREATE INDEX IF NOT EXISTS idx_pad_bbl_bbl
    ON pad_bbl (bbl);

-- HPD Violations (~4M rows)
CREATE INDEX IF NOT EXISTS idx_hpd_violations_bbl
    ON hpd_violations (bbl);
CREATE INDEX IF NOT EXISTS idx_hpd_violations_date
    ON hpd_violations (inspectiondate DESC);
CREATE INDEX IF NOT EXISTS idx_hpd_violations_class
    ON hpd_violations (class);
CREATE INDEX IF NOT EXISTS idx_hpd_violations_status
    ON hpd_violations (currentstatus);
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
    ON acris_real_property_legals (borough, block, lot);
CREATE INDEX IF NOT EXISTS idx_acris_legals_documentid
    ON acris_real_property_legals (documentid);

-- ACRIS Real Property Master — document metadata
CREATE INDEX IF NOT EXISTS idx_acris_master_documentid
    ON acris_real_property_master (documentid);
CREATE INDEX IF NOT EXISTS idx_acris_master_docdate
    ON acris_real_property_master (docdate DESC);
CREATE INDEX IF NOT EXISTS idx_acris_master_doctype
    ON acris_real_property_master (doctype);
-- Composite: ownership view build (doctype + date)
CREATE INDEX IF NOT EXISTS idx_acris_master_doctype_date
    ON acris_real_property_master (doctype, docdate DESC);

-- ACRIS Real Property Parties — grantor/grantee info
CREATE INDEX IF NOT EXISTS idx_acris_parties_documentid
    ON acris_real_property_parties (documentid);
CREATE INDEX IF NOT EXISTS idx_acris_parties_docid_type
    ON acris_real_property_parties (documentid, partytype);

-- ACRIS Document Control Codes — doc type reference
CREATE INDEX IF NOT EXISTS idx_acris_dcc_doctype
    ON acris_document_control_codes (doctype);

-- ACRIS Real Property References
CREATE INDEX IF NOT EXISTS idx_acris_refs_documentid
    ON acris_real_property_references (documentid);

-- ACRIS Real Property Remarks
CREATE INDEX IF NOT EXISTS idx_acris_remarks_documentid
    ON acris_real_property_remarks (documentid);

-- ACRIS Personal Property Legals (UCC filings)
CREATE INDEX IF NOT EXISTS idx_acris_personal_legals_documentid
    ON acris_personal_property_legals (documentid);

-- ACRIS Personal Property Master
CREATE INDEX IF NOT EXISTS idx_acris_personal_master_documentid
    ON acris_personal_property_master (documentid);
CREATE INDEX IF NOT EXISTS idx_acris_personal_master_doctype
    ON acris_personal_property_master (doctype);

-- ACRIS Personal Property Parties
CREATE INDEX IF NOT EXISTS idx_acris_personal_parties_documentid
    ON acris_personal_property_parties (documentid);

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
