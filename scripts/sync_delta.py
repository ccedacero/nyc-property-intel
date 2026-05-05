#!/usr/bin/env python3
"""Cursor-based incremental delta sync for NYC Open Data → Postgres.

Usage:
    DATABASE_URL=postgres://...  SOCRATA_APP_TOKEN=...  \
        uv run python scripts/sync_delta.py hpd_violations [--dry-run] [--reset]

See docs/data-refresh-plan.md for the architecture.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from dataclasses import dataclass

import asyncpg
import httpx

logger = logging.getLogger("sync_delta")

# ── Dataset registry ──────────────────────────────────────────────────
# One entry per dataset. Add new tables here as we onboard them.
@dataclass(frozen=True)
class DatasetCfg:
    key: str               # internal name (matches sync_state.dataset_key)
    socrata_id: str        # Socrata 4x4 ID
    table: str             # Postgres table name
    cursor_col: str        # Column name in OUR table (used for cursor advance)
    pk_cols: tuple[str, ...]  # Columns forming the primary key for UPSERT (empty for refresh_by_documentid)
    tier: int              # 1 = daily, 2 = weekly, 3 = monthly+
    socrata_cursor_col: str | None = None  # source name if different (e.g. 'received_date' vs 'receiveddate')
    column_map: dict[str, str] | None = None  # source-stripped → target overrides
    # "upsert": ON CONFLICT DO UPDATE keyed on pk_cols (default).
    # "refresh_by_documentid": ACRIS sub-tables — no per-row PK; for each batch,
    #   delete all existing rows whose documentid appears in the page, then insert.
    sync_mode: str = "upsert"


def _normalize_socrata_keys(row: dict, column_map: dict[str, str] | None = None) -> dict:
    """Strip underscores from Socrata column names; apply explicit overrides last.

    Strip handles the common case (received_date → receiveddate).
    column_map handles cases where local schema diverged (document_amt → docamount).
    """
    out = {}
    for k, v in row.items():
        if k.startswith(":"):
            continue
        stripped = k.replace("_", "")
        target = column_map.get(stripped, stripped) if column_map else stripped
        out[target] = v
    return out


# Date formats observed across NYC Socrata datasets:
#   ISO 8601                "2014-01-06T00:00:00.000"   most datasets
#   ISO 8601 + Z            "2014-01-06T00:00:00.000Z"
#   M/D/YYYY                "06/23/2023"                eabe-havv, ic3t-wcy2
#   M/D/YYYY HH:MM:SS       "06/24/2023 00:00:00"       ic3t-wcy2.dobrundate
#   YYYYMMDD                "19881031"                  3h2n-5cm9.issue_date
#   YYYYMMDDHHMMSS          "20260503000000"            eabe-havv.dobrundate
# Sentinels treated as None: empty, "0", all-zeros, "Y\d+" (3h2n-5cm9 "no expiration").
def _parse_flexible_datetime(s: str):
    """Parse Socrata date strings across all observed formats. Return None on garbage."""
    from datetime import date, datetime
    s = s.strip()
    if not s or s == "0":
        return None
    if s.startswith("Y") and len(s) > 1 and s[1:].isdigit():
        return None
    if s.replace("0", "") == "":  # all zeros
        return None
    # ISO 8601: prefix matches YYYY-MM-DD
    if len(s) >= 10 and s[4:5] == "-" and s[7:8] == "-":
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            try:
                return datetime.combine(date.fromisoformat(s[:10]), datetime.min.time())
            except ValueError:
                pass
    # M/D/YYYY [HH:MM:SS]
    if "/" in s:
        for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
    # YYYYMMDDHHMMSS (14-digit)
    if len(s) == 14 and s.isdigit():
        try:
            return datetime(int(s[:4]), int(s[4:6]), int(s[6:8]),
                            int(s[8:10]), int(s[10:12]), int(s[12:14]))
        except ValueError:
            pass
    # YYYYMMDD (8-digit)
    if len(s) == 8 and s.isdigit():
        try:
            return datetime(int(s[:4]), int(s[4:6]), int(s[6:8]))
        except ValueError:
            pass
    return None


def _parse_flexible_date(s: str):
    dt = _parse_flexible_datetime(s)
    return dt.date() if dt else None


def _normalize_cursor_date(value: str) -> str:
    """Normalise any observed Socrata date format → ISO YYYY-MM-DD so cursors
    are stored uniformly. Returns the input unchanged if unparseable; callers
    must gate on _is_valid_date_cursor first."""
    s = value.strip()
    d = _parse_flexible_date(s)
    return d.isoformat() if d else s


def _is_valid_date_cursor(value) -> bool:
    """Reject junk date values (e.g. 'Y9990120', '0   0612') and absurd future
    dates that would poison the cursor and block all future incremental syncs."""
    if not isinstance(value, str):
        return False
    d = _parse_flexible_date(value)
    if d is None:
        return False
    from datetime import date, timedelta
    return d <= date.today() + timedelta(days=1)

DATASETS: dict[str, DatasetCfg] = {
    "hpd_violations": DatasetCfg(
        key="hpd_violations", socrata_id="wvxf-dwi5", table="hpd_violations",
        cursor_col="novissueddate", pk_cols=("violationid",), tier=1,
    ),
    "hpd_complaints_and_problems": DatasetCfg(
        key="hpd_complaints_and_problems", socrata_id="ygpa-z7cr",
        table="hpd_complaints_and_problems",
        cursor_col="receiveddate", pk_cols=("problemid",), tier=1,
        socrata_cursor_col="received_date",
    ),
    "hpd_litigations": DatasetCfg(
        key="hpd_litigations", socrata_id="59kj-x8nc", table="hpd_litigations",
        cursor_col="caseopendate", pk_cols=("litigationid",), tier=1,
    ),
    "dob_violations": DatasetCfg(
        key="dob_violations", socrata_id="3h2n-5cm9", table="dob_violations",
        cursor_col="issuedate", pk_cols=("isndobbisviol",), tier=1,
        socrata_cursor_col="issue_date",
    ),
    "ecb_violations": DatasetCfg(
        key="ecb_violations", socrata_id="6bgk-3dad", table="ecb_violations",
        cursor_col="issuedate", pk_cols=("ecbviolationnumber",), tier=1,
        socrata_cursor_col="issue_date",
    ),
    "marshal_evictions_all": DatasetCfg(
        key="marshal_evictions_all", socrata_id="6z8x-wfk4",
        table="marshal_evictions_all",
        cursor_col="executeddate", pk_cols=("courtindexnumber", "docketnumber", "executeddate"), tier=2,
        socrata_cursor_col="executed_date",
        column_map={
            "evictionpossession": "evictionlegalpossession",
        },
    ),
    "real_property_master": DatasetCfg(
        key="real_property_master", socrata_id="bnx9-e6tj",
        table="real_property_master",
        cursor_col="modifieddate", pk_cols=("documentid",), tier=1,
        socrata_cursor_col="modified_date",
        # Source uses different short names than our local schema.
        column_map={
            "documentamt": "docamount",
            "documentdate": "docdate",
            "percenttrans": "pcttransferred",
            "recordedborough": "borough",
            "reelpg": "reelpage",
            "reelyr": "reelyear",
        },
    ),
    "dobjobs": DatasetCfg(
        key="dobjobs", socrata_id="ic3t-wcy2",
        table="dobjobs",
        cursor_col="latestactiondate", pk_cols=("job", "doc"), tier=1,
        socrata_cursor_col="latest_action_date",
        column_map={
            "job__":                     "job",
            "doc__":                     "doc",
            "house__":                   "house",
            "street_name":               "streetname",
            "bin__":                     "bin",
            "job_type":                  "jobtype",
            "job_status":                "jobstatus",
            "job_status_descrp":         "jobstatusdescrp",
            "latest_action_date":        "latestactiondate",
            "building_type":             "buildingtype",
            "community___board":         "communityboard",
            "adult_estab":               "adultestab",
            "loft_board":                "loftboard",
            "little_e":                  "littlee",
            "efiling_filed":             "efilingfiled",
            "other_description":         "otherdescription",
            "applicant_s_first_name":    "applicantsfirstname",
            "applicant_s_last_name":     "applicantslastname",
            "applicant_professional_title": "applicantprofessionaltitle",
            "applicant_license__":       "applicantlicense",
            "professional_cert":         "professionalcert",
            "pre__filing_date":          "prefilingdate",
            "fully_paid":                "fullypaid",
            "fully_permitted":           "fullypermitted",
            "initial_cost":              "initialcost",
            "total_est__fee":            "totalestfee",
            "fee_status":                "feestatus",
            "existing_zoning_sqft":      "existingzoningsqft",
            "proposed_zoning_sqft":      "proposedzoningsqft",
            "enlargement_sq_footage":    "enlargementsqfootage",
            "street_frontage":           "streetfrontage",
            "existingno_of_stories":     "existingnoofstories",
            "proposed_no_of_stories":    "proposednoofstories",
            "existing_height":           "existingheight",
            "proposed_height":           "proposedheight",
            "existing_dwelling_units":   "existingdwellingunits",
            "proposed_dwelling_units":   "proposeddwellingunits",
            "existing_occupancy":        "existingoccupancy",
            "proposed_occupancy":        "proposedoccupancy",
            "site_fill":                 "sitefill",
            "zoning_dist1":              "zoningdist1",
            "zoning_dist2":              "zoningdist2",
            "owner_type":                "ownertype",
            "non_profit":                "nonprofit",
            "owner_s_first_name":        "ownersfirstname",
            "owner_s_last_name":         "ownerslastname",
            "owner_s_business_name":     "ownersbusinessname",
            "owner_sphone__":            "ownersphone",
            "job_s1_no":                 "jobs1no",
            "total_construction_floor_area": "totalconstructionfloorarea",
            "withdrawal_flag":           "withdrawalflag",
            "signoff_date":              "signoffdate",
            "special_action_status":     "specialactionstatus",
            "building_class":            "buildingclass",
            "job_no_good_count":         "jobnogoodcount",
            "gis_latitude":              "gislatitude",
            "gis_longitude":             "gislongitude",
            "gis_council_district":      "giscouncildistrict",
            "gis_census_tract":          "giscensustract",
            "gis_nta_name":              "gisntaname",
            "gis_bin":                   "gisbin",
        },
    ),
    "dob_complaints": DatasetCfg(
        key="dob_complaints", socrata_id="eabe-havv",
        table="dob_complaints",
        cursor_col="dateentered", pk_cols=("complaintnumber",), tier=1,
        socrata_cursor_col="date_entered",
        column_map={
            "complaint_number":   "complaintnumber",
            "date_entered":       "dateentered",
            "house_number":       "housenumber",
            "house_street":       "housestreet",
            "zip_code":           "zipcode",
            "community_board":    "communityboard",
            "special_district":   "specialdistrict",
            "complaint_category": "complaintcategory",
            "disposition_date":   "dispositiondate",
            "disposition_code":   "dispositioncode",
            "inspection_date":    "inspectiondate",
        },
    ),
    "dob_now_jobs": DatasetCfg(
        key="dob_now_jobs", socrata_id="w9ak-ipjd",
        table="dob_now_jobs",
        cursor_col="currentstatusdate", pk_cols=("jobfilingnumber",), tier=1,
        socrata_cursor_col="current_status_date",
        column_map={
            "job_filing_number":                        "jobfilingnumber",
            "filing_status":                            "filingstatus",
            "house_no":                                 "houseno",
            "street_name":                              "streetname",
            "commmunity_board":                         "commmunityboard",
            "work_on_floor":                            "workonfloor",
            "applicant_professional_title":             "applicantprofessionaltitle",
            "applicant_license":                        "applicantlicense",
            "applicant_first_name":                     "applicantfirstname",
            "applicants_middle_initial":                "applicantsmiddleinitial",
            "applicant_last_name":                      "applicantlastname",
            "owner_s_business_name":                    "ownersbusinessname",
            "owner_s_street_name":                      "ownersstreetname",
            "city":                                     "ownerscity",
            "state":                                    "ownersstate",
            "zip":                                      "ownerszip",
            "filing_representative_first_name":         "filingrepresentativefirstname",
            "filing_representative_last_name":          "filingrepresentativelastname",
            "filing_representative_business_name":      "filingrepresentativebusinessname",
            "filing_representative_street_name":        "filingrepresentativestreetname",
            "filing_representative_city":               "filingrepresentativecity",
            "filing_representative_state":              "filingrepresentativestate",
            "filing_representative_zip":                "filingrepresentativezip",
            "sprinkler_work_type":                      "sprinklerworktype",
            "plumbing_work_type":                       "plumbingworktype",
            "initial_cost":                             "initialcost",
            "total_construction_floor_area":            "totalconstructionfloorarea",
            "review_building_code":                     "reviewbuildingcode",
            "little_e":                                 "littlee",
            "unmapped_cco_street":                      "unmappedccostreet",
            "in_compliance_with_nycecc":                "incompliancewithnycecc",
            "exempt_from_nycecc":                       "exemptfromnycecc",
            "building_type":                            "buildingtype",
            "existing_dwelling_units":                  "existingdwellingunits",
            "proposed_dwelling_units":                  "proposeddwellingunits",
            "curb_cut":                                 "curbcut",
            "filing_date":                              "filingdate",
            "current_status_date":                      "currentstatusdate",
            "first_permit_date":                        "firstpermitdate",
            "boiler_equipment_work_type_":              "boilerequipmentworktype",
            "earth_work_work_type_":                    "earthworkworktype",
            "foundation_work_type_":                    "foundationworktype",
            "general_construction_work_type_":          "generalconstructionworktype",
            "mechanical_systems_work_type_":            "mechanicalsystemsworktype",
            "place_of_assembly_work_type_":             "placeofassemblyworktype",
            "protection_mechanical_methods_work_type_": "protectionmechanicalmethodsworktype",
            "sidewalk_shed_work_type_":                 "sidewalkshedworktype",
            "structural_work_type_":                    "structuralworktype",
            "support_of_excavation_work_type_":         "supportofexcavationworktype",
            "temporary_place_of_assembly_work_type_":   "temporaryplaceofassemblyworktype",
            "job_type":                                 "jobtype",
        },
    ),
    "nyc_311_complaints": DatasetCfg(
        key="nyc_311_complaints", socrata_id="erm2-nwe9",
        table="nyc_311_complaints",
        cursor_col="created_date", pk_cols=("unique_key",), tier=2,
        # Local schema preserves underscores. _normalize_socrata_keys strips
        # them (created_date → createddate), so without column_map the row
        # dict had no "created_date" key and every row was dropped at the
        # PK validation step. See known-issues.md / 2026-05-04 incident.
        column_map={
            "addresstype": "address_type",
            "agencyname": "agency_name",
            "closeddate": "closed_date",
            "communityboard": "community_board",
            "complainttype": "complaint_type",
            "councildistrict": "council_district",
            "createddate": "created_date",
            "crossstreet1": "cross_street_1",
            "crossstreet2": "cross_street_2",
            "incidentaddress": "incident_address",
            "incidentzip": "incident_zip",
            "intersectionstreet1": "intersection_street_1",
            "intersectionstreet2": "intersection_street_2",
            "locationtype": "location_type",
            "opendatachanneltype": "open_data_channel_type",
            "parkborough": "park_borough",
            "parkfacilityname": "park_facility_name",
            "policeprecinct": "police_precinct",
            "resolutionactionupdateddate": "resolution_action_updated_date",
            "resolutiondescription": "resolution_description",
            "streetname": "street_name",
            "uniquekey": "unique_key",
            "xcoordinatestateplane": "x_coordinate_state_plane",
            "ycoordinatestateplane": "y_coordinate_state_plane",
        },
    ),
    "personal_property_master": DatasetCfg(
        key="personal_property_master", socrata_id="sv7x-dduq",
        table="personal_property_master",
        cursor_col="modifieddate", pk_cols=("documentid",), tier=2,
        socrata_cursor_col="modified_date",
        column_map={
            "document_id":           "documentid",
            "record_type":           "recordtype",
            "recorded_borough":      "borough",
            "doc_type":              "doctype",
            "document_amt":          "docamount",
            "recorded_datetime":     "recordedfiled",
            "ucc_collateral":        "collateral",
            "fedtax_serial_nbr":     "slid",
            "fedtax_assessment_date": "assessmentdate",
            "rpttl_nbr":             "rptt",
            "modified_date":         "modifieddate",
            "reel_yr":               "reelyear",
            "reel_nbr":              "reelnbr",
            "reel_pg":               "reelpage",
            "file_nbr":              "filenumber",
            "good_through_date":     "goodthroughdate",
        },
    ),
    "hpd_registrations": DatasetCfg(
        key="hpd_registrations", socrata_id="tesw-yqqr",
        table="hpd_registrations",
        cursor_col="lastregistrationdate", pk_cols=("registrationid",), tier=3,
        # Socrata column names match DB — no column_map needed.
    ),
    "fdny_incidents": DatasetCfg(
        key="fdny_incidents", socrata_id="8m42-w767",
        table="fdny_incidents",
        cursor_col="incident_datetime", pk_cols=("starfire_incident_id",), tier=3,
        # Same underscore-stripping issue as nyc_311_complaints. Without this
        # map, starfire_incident_id PK was always None → every row dropped.
        column_map={
            "alarmboxborough": "alarm_box_borough",
            "alarmboxlocation": "alarm_box_location",
            "alarmboxnumber": "alarm_box_number",
            "alarmlevelindexdescription": "alarm_level_index_description",
            "alarmsourcedescriptiontx": "alarm_source_description_tx",
            "dispatchresponsesecondsqy": "dispatch_response_seconds_qy",
            "enginesassignedquantity": "engines_assigned_quantity",
            "firstactivationdatetime": "first_activation_datetime",
            "firstassignmentdatetime": "first_assignment_datetime",
            "firstonscenedatetime": "first_on_scene_datetime",
            "highestalarmlevel": "highest_alarm_level",
            "incidentborough": "incident_borough",
            "incidentclassification": "incident_classification",
            "incidentclassificationgroup": "incident_classification_group",
            "incidentclosedatetime": "incident_close_datetime",
            "incidentdatetime": "incident_datetime",
            "incidentresponsesecondsqy": "incident_response_seconds_qy",
            "incidenttraveltmsecondsqy": "incident_travel_tm_seconds_qy",
            "laddersassignedquantity": "ladders_assigned_quantity",
            "otherunitsassignedquantity": "other_units_assigned_quantity",
            "starfireincidentid": "starfire_incident_id",
            "validdispatchrspnstimeindc": "valid_dispatch_rspns_time_indc",
            "validincidentrspnstimeindc": "valid_incident_rspns_time_indc",
        },
    ),
    "nypd_crime_complaints": DatasetCfg(
        key="nypd_crime_complaints", socrata_id="qgea-i56i",
        table="nypd_crime_complaints",
        cursor_col="rpt_dt", pk_cols=("cmplnt_num",), tier=3,
        # Using Historic dataset (qgea-i56i); YTD is 5uac-w243 (current year only).
        # rpt_dt (report date) is more reliable cursor than cmplnt_fr_dt (crime date).
        # Same underscore-stripping issue as nyc_311 / fdny — without this map
        # cmplnt_num PK was always None → every row dropped during backfill.
        column_map={
            "addrpctcd": "addr_pct_cd",
            "boronm": "boro_nm",
            "cmplntfrdt": "cmplnt_fr_dt",
            "cmplntfrtm": "cmplnt_fr_tm",
            "cmplntnum": "cmplnt_num",
            "cmplnttotm": "cmplnt_to_tm",
            "crmatptcptdcd": "crm_atpt_cptd_cd",
            "geocodedcolumn": "geocoded_column",
            "jurisdesc": "juris_desc",
            "jurisdictioncode": "jurisdiction_code",
            "kycd": "ky_cd",
            "latlon": "lat_lon",
            "lawcatcd": "law_cat_cd",
            "locofoccurdesc": "loc_of_occur_desc",
            "ofnsdesc": "ofns_desc",
            "parksnm": "parks_nm",
            "patrolboro": "patrol_boro",
            "pddesc": "pd_desc",
            "premtypdesc": "prem_typ_desc",
            "rptdt": "rpt_dt",
            "stationname": "station_name",
            "suspagegroup": "susp_age_group",
            "susprace": "susp_race",
            "suspsex": "susp_sex",
            "vicagegroup": "vic_age_group",
            "vicrace": "vic_race",
            "vicsex": "vic_sex",
            "xcoordcd": "x_coord_cd",
            "ycoordcd": "y_coord_cd",
        },
    ),
    # ── ACRIS sub-tables ──────────────────────────────────────────────
    # No per-row PK: each (documentid, goodthroughdate) is a snapshot of a
    # composite-keyed record. Source ships full document state in monthly
    # batches; same documentid reappears with a fresher goodthroughdate when
    # corrected. ON CONFLICT upserts can't work, so we use refresh_by_documentid:
    # for each page, delete all existing rows whose documentid appears in the
    # batch, then insert. Cursor column is goodthroughdate.
    "real_property_legals": DatasetCfg(
        key="real_property_legals", socrata_id="8h5j-fqxa",
        table="real_property_legals",
        cursor_col="goodthroughdate", pk_cols=(), tier=3,
        socrata_cursor_col="good_through_date",
        sync_mode="refresh_by_documentid",
    ),
    "real_property_parties": DatasetCfg(
        key="real_property_parties", socrata_id="636b-3b5g",
        table="real_property_parties",
        cursor_col="goodthroughdate", pk_cols=(), tier=3,
        socrata_cursor_col="good_through_date",
        sync_mode="refresh_by_documentid",
    ),
    "real_property_references": DatasetCfg(
        key="real_property_references", socrata_id="pwkr-dpni",
        table="real_property_references",
        cursor_col="goodthroughdate", pk_cols=(), tier=3,
        socrata_cursor_col="good_through_date",
        sync_mode="refresh_by_documentid",
    ),
    "real_property_remarks": DatasetCfg(
        key="real_property_remarks", socrata_id="9p4w-7npp",
        table="real_property_remarks",
        cursor_col="goodthroughdate", pk_cols=(), tier=3,
        socrata_cursor_col="good_through_date",
        sync_mode="refresh_by_documentid",
        column_map={
            "sequencenumber": "remarklinenbr",
            "remarktext": "remarktextline",
        },
    ),
    "personal_property_legals": DatasetCfg(
        key="personal_property_legals", socrata_id="uqqa-hym2",
        table="personal_property_legals",
        cursor_col="goodthroughdate", pk_cols=(), tier=3,
        socrata_cursor_col="good_through_date",
        sync_mode="refresh_by_documentid",
    ),
    "personal_property_parties": DatasetCfg(
        key="personal_property_parties", socrata_id="nbbg-wtuz",
        table="personal_property_parties",
        cursor_col="goodthroughdate", pk_cols=(), tier=3,
        socrata_cursor_col="good_through_date",
        sync_mode="refresh_by_documentid",
    ),
    "personal_property_references": DatasetCfg(
        key="personal_property_references", socrata_id="6y3e-jcrc",
        table="personal_property_references",
        cursor_col="goodthroughdate", pk_cols=(), tier=3,
        socrata_cursor_col="good_through_date",
        sync_mode="refresh_by_documentid",
        column_map={
            # Source field is just `crfn`; local column is referencebycrfn.
            "crfn": "referencebycrfn",
        },
    ),
    "personal_property_remarks": DatasetCfg(
        key="personal_property_remarks", socrata_id="fuzi-5ks9",
        table="personal_property_remarks",
        cursor_col="goodthroughdate", pk_cols=(), tier=3,
        socrata_cursor_col="good_through_date",
        sync_mode="refresh_by_documentid",
        column_map={
            "sequencenumber": "remarklinenbr",
            "remarktext": "remarktextline",
        },
    ),
}

# ── Tunables ──────────────────────────────────────────────────────────
PAGE_SIZE = 50_000
INTER_PAGE_SLEEP_SEC = 0.25
RETRY_BACKOFF_SEC = [10, 30, 60, 120, 300, 600, 1200]
HTTP_TIMEOUT_SEC = 180
DRIFT_WARN_PCT = 5.0
DRIFT_ERR_PCT = 10.0


# ── Socrata I/O ───────────────────────────────────────────────────────
async def fetch_page(
    client: httpx.AsyncClient,
    socrata_id: str,
    cursor_col: str,
    cursor_value: str | None,
    offset: int = 0,
    column_map: dict[str, str] | None = None,
    use_offset_with_where: bool = False,
) -> list[dict]:
    """Fetch one page; retry with exp backoff. Raises on permanent failure.

    Three modes:
      - Backfill (cursor_value is None): paginate by $offset, no $where.
      - Incremental (cursor_value set): use $where on cursor_col, advance via
        the page max each iteration. Requires the source to be indexed on
        cursor_col.
      - Refresh-window (cursor_value set + use_offset_with_where=True): apply
        $where as a one-time range filter and paginate within it via $offset.
        Used when the cursor column is too coarse (e.g. ACRIS goodthroughdate
        where every row in a monthly batch shares one date).
    """
    url = f"https://data.cityofnewyork.us/resource/{socrata_id}.json"
    params: dict[str, str | int] = {
        "$limit": PAGE_SIZE,
        "$order": f"{cursor_col} ASC",
    }
    if cursor_value:
        # Normalize: PG-formatted timestamps ('2026-04-06 00:00:00') need to
        # become ISO-8601 ('2026-04-06T00:00:00') for SoQL.
        normalized = cursor_value.replace(" ", "T", 1)
        params["$where"] = f"{cursor_col} > '{normalized}'"
        if use_offset_with_where:
            params["$offset"] = offset
    else:
        params["$offset"] = offset

    for attempt, wait in enumerate([0, *RETRY_BACKOFF_SEC]):
        if wait:
            logger.warning("retrying in %ds (attempt %d)", wait, attempt)
            await asyncio.sleep(wait)
        try:
            r = await client.get(url, params=params)
            r.raise_for_status()
            rows = r.json()
            return [_normalize_socrata_keys(row, column_map) for row in rows]
        except httpx.HTTPStatusError as e:
            # Socrata 500s are transient on long $offset queries — observed
            # ~1 in 3 calls to the same URL flapping between 200 and 500
            # during dob_violations backfill. Retry with backoff.
            if e.response.status_code in (429, 500, 502, 503, 504):
                continue
            raise
        except (httpx.ReadError, httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError):
            continue
    raise RuntimeError(f"giving up after {len(RETRY_BACKOFF_SEC)} retries on {socrata_id}")


async def fetch_expected_rowcount(client: httpx.AsyncClient, socrata_id: str) -> int | None:
    """Pull /api/views/{id}.json metadata for drift comparison."""
    try:
        r = await client.get(f"https://data.cityofnewyork.us/api/views/{socrata_id}.json")
        r.raise_for_status()
        meta = r.json()
        # Socrata returns row count in different fields depending on dataset type
        if "rowsCount" in meta:
            return int(meta["rowsCount"])
        for col in meta.get("columns", []):
            cached = col.get("cachedContents", {})
            if "non_null" in cached:
                return int(cached["non_null"])
    except Exception as e:
        logger.warning("could not fetch expected row count: %s", e)
    return None


# ── DB ops ────────────────────────────────────────────────────────────
async def get_target_columns(conn: asyncpg.Connection, table: str) -> list[tuple[str, str, int | None]]:
    """Return ordered list of (column_name, data_type, max_length) for target table."""
    rows = await conn.fetch(
        """
        SELECT column_name, data_type, character_maximum_length
        FROM information_schema.columns
        WHERE table_name = $1 AND table_schema = 'public'
        ORDER BY ordinal_position
        """,
        table,
    )
    return [(r["column_name"], r["data_type"], r["character_maximum_length"]) for r in rows]


def _coerce(value, pg_type: str, max_len: int | None = None):
    """Convert Socrata's string value to the right Python type for asyncpg COPY.
    Respects character_maximum_length — truncates oversize strings rather than
    aborting the page. Source data sometimes has longer values than schema (e.g.
    ZIP+4 in a CHAR(5) column).
    """
    if value is None or value == "":
        return None
    s = str(value)
    # The outer try/except still serves int/float branches: int(float("abc"))
    # raises ValueError. Date branches now use non-throwing helpers.
    try:
        if pg_type in ("integer", "smallint", "bigint"):
            return int(float(s))
        if pg_type in ("numeric", "double precision", "real"):
            return float(s)
        if pg_type == "boolean":
            return s.upper() in ("Y", "T", "TRUE", "1", "YES")
        if pg_type == "date":
            return _parse_flexible_date(s)
        if pg_type in ("timestamp without time zone", "timestamp with time zone"):
            return _parse_flexible_datetime(s)
        # text, varchar, character types
        if max_len is not None and len(s) > max_len:
            return s[:max_len]
        return s
    except (ValueError, TypeError):
        return None


async def upsert_page(
    conn: asyncpg.Connection,
    cfg: DatasetCfg,
    target_cols: list[tuple[str, str, int | None]],
    rows: list[dict],
) -> int:
    """Stage page into a temp table and UPSERT into target. Returns rows affected.

    Only updates target columns that the source row actually provided. This
    preserves locally-derived columns (e.g., NYCDB extensions) that aren't
    in the Socrata API response — without this, ON CONFLICT DO UPDATE would
    NULL them out on every sync.
    """
    if not rows:
        return 0

    # Source sometimes returns duplicate PKs within a single page (e.g., when
    # records are updated multiple times since the last cursor). UPSERT can't
    # affect the same target row twice in one statement — dedupe by PK, keeping
    # the LAST occurrence (which Socrata's $order tends to deliver as the most
    # recent version for stable cursor columns).
    seen: dict[tuple, dict] = {}
    for r in rows:
        pk = tuple(r.get(c) for c in cfg.pk_cols)
        # Skip rows whose PK is NULL or empty-string — they can't be upserted
        # and would fail the NOT NULL constraint after _coerce() converts "" → None.
        if all(v is not None and str(v).strip() != "" for v in pk):
            seen[pk] = r
    rows = list(seen.values())
    if not rows:
        return 0

    col_names = [c for c, _, _ in target_cols]

    # Columns the source actually populated (union across all rows in this page).
    source_cols = set()
    for r in rows:
        source_cols.update(r.keys())
    updatable_cols = [c for c in col_names if c in source_cols and c not in cfg.pk_cols]

    projected = [
        tuple(_coerce(row.get(c), t, ml) for c, t, ml in target_cols) for row in rows
    ]

    # Defensive: drop rows where any PK column coerced to None (e.g. an alphanumeric
    # value cast against an integer-typed PK). The pre-coerce filter above misses
    # this because it sees the raw string. Without this, the COPY into _stage fails
    # the NOT NULL implied by the target PK and aborts the entire page.
    pk_indexes = [col_names.index(c) for c in cfg.pk_cols]
    pre_drop = len(projected)
    projected = [t for t in projected if all(t[i] is not None for i in pk_indexes)]
    dropped = pre_drop - len(projected)
    if dropped:
        logger.warning("dropped %d row(s) with NULL PK after coercion", dropped)
    if not projected:
        return 0

    async with conn.transaction():
        await conn.execute(
            f'CREATE TEMP TABLE _stage (LIKE {cfg.table} INCLUDING DEFAULTS) ON COMMIT DROP'
        )
        await conn.copy_records_to_table("_stage", records=projected, columns=col_names)

        col_list = ", ".join(f'"{c}"' for c in col_names)
        pk_list = ", ".join(f'"{c}"' for c in cfg.pk_cols)
        update_assign = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in updatable_cols)
        on_conflict = (
            f"ON CONFLICT ({pk_list}) DO UPDATE SET {update_assign}"
            if updatable_cols
            else f"ON CONFLICT ({pk_list}) DO NOTHING"
        )

        sql = (
            f'INSERT INTO "{cfg.table}" ({col_list}) '
            f"SELECT {col_list} FROM _stage "
            f"{on_conflict}"
        )
        result = await conn.execute(sql)
        affected = int(result.rsplit(" ", 1)[-1])

    return affected


async def refresh_page_by_doc(
    conn: asyncpg.Connection,
    cfg: DatasetCfg,
    target_cols: list[tuple[str, str, int | None]],
    rows: list[dict],
) -> int:
    """ACRIS sub-table sync: delete rows whose documentid appears in the page,
    then insert the page. Composite "natural" keys aren't enforced as a PK so
    UPSERT can't be used; the source ships full document state per batch, so
    replacing all rows for the documentids in scope is correct.
    """
    if not rows:
        return 0

    col_names = [c for c, _, _ in target_cols]
    if "documentid" not in col_names:
        raise RuntimeError(f"refresh_by_documentid requires a documentid column on {cfg.table}")

    projected = [
        tuple(_coerce(row.get(c), t, ml) for c, t, ml in target_cols) for row in rows
    ]
    docid_idx = col_names.index("documentid")
    pre_drop = len(projected)
    projected = [t for t in projected if t[docid_idx] is not None and str(t[docid_idx]).strip() != ""]
    dropped = pre_drop - len(projected)
    if dropped:
        logger.warning("dropped %d row(s) with NULL documentid", dropped)
    if not projected:
        return 0

    docids = list({t[docid_idx] for t in projected})

    async with conn.transaction():
        await conn.execute(
            f'DELETE FROM "{cfg.table}" WHERE documentid = ANY($1::text[])',
            docids,
        )
        await conn.execute(
            f'CREATE TEMP TABLE _stage (LIKE "{cfg.table}" INCLUDING DEFAULTS) ON COMMIT DROP'
        )
        await conn.copy_records_to_table("_stage", records=projected, columns=col_names)
        col_list = ", ".join(f'"{c}"' for c in col_names)
        result = await conn.execute(
            f'INSERT INTO "{cfg.table}" ({col_list}) SELECT {col_list} FROM _stage'
        )
        affected = int(result.rsplit(" ", 1)[-1])

    return affected


async def read_state(conn: asyncpg.Connection, key: str) -> dict | None:
    row = await conn.fetchrow("SELECT * FROM sync_state WHERE dataset_key = $1", key)
    return dict(row) if row else None


async def write_state(
    conn: asyncpg.Connection,
    key: str,
    *,
    cursor_value: str | None = None,
    last_error: str | None = None,
    rows_added: int | None = None,
    expected_rows: int | None = None,
    actual_rows: int | None = None,
    success: bool = False,
) -> None:
    sets = ["last_run_at = NOW()"]
    args: list = []
    i = 1

    def add(col: str, val):
        nonlocal i
        sets.append(f"{col} = ${i}")
        args.append(val)
        i += 1

    if cursor_value is not None:
        add("cursor_value", cursor_value)
    if success:
        sets.append("last_success_at = NOW()")
        add("last_error", None)
    elif last_error is not None:
        add("last_error", last_error)
    if rows_added is not None:
        sets.append(f"rows_added_total = rows_added_total + ${i}")
        args.append(rows_added)
        i += 1
    if expected_rows is not None:
        add("expected_rows", expected_rows)
    if actual_rows is not None:
        add("actual_rows", actual_rows)

    args.append(key)
    sql = f"UPDATE sync_state SET {', '.join(sets)} WHERE dataset_key = ${i}"
    await conn.execute(sql, *args)


# ── Main sync loop ────────────────────────────────────────────────────
async def sync_dataset(cfg: DatasetCfg, *, dry_run: bool, reset: bool) -> int:
    """Returns process exit code (0 ok, 1 partial, 2 fatal)."""
    db_url = os.environ["DATABASE_URL"]
    app_token = os.environ.get("SOCRATA_APP_TOKEN", "")

    # command_timeout=600s for backfills against multi-million-row tables.
    # nyc_311_complaints (17.86M rows) hit the original 120s ceiling on the
    # ON CONFLICT DO UPDATE step on 2026-05-04 because every row in the
    # earliest pages overlaps existing PKs (50K UPDATEs vs the index).
    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2, command_timeout=600)
    headers = {"X-App-Token": app_token} if app_token else {}

    async with httpx.AsyncClient(headers=headers, timeout=HTTP_TIMEOUT_SEC) as client:
        # Acquire a connection only briefly for setup, then release.
        async with pool.acquire() as conn:
            state = await read_state(conn, cfg.key)
            if not state:
                logger.error("no sync_state row for %s — run migration 001/002", cfg.key)
                return 2
            if reset:
                logger.warning("--reset: clearing cursor for full backfill")
                await conn.execute(
                    "UPDATE sync_state SET cursor_value = NULL WHERE dataset_key = $1", cfg.key
                )
                state["cursor_value"] = None
            target_cols = await get_target_columns(conn, cfg.table)

        cursor_value: str | None = state["cursor_value"]
        is_backfill = cursor_value is None
        is_refresh = cfg.sync_mode == "refresh_by_documentid"
        expected = await fetch_expected_rowcount(client, cfg.socrata_id)
        # In refresh mode, the cursor is a one-time range filter and pagination
        # advances via $offset (the cursor column is too coarse — every row in a
        # monthly batch shares one goodthroughdate). In upsert mode, the cursor
        # advances per page so $offset stays at 0.
        mode_label = (
            "refresh" if is_refresh
            else ("backfill" if is_backfill else "incremental")
        )
        logger.info(
            "starting sync %s: mode=%s cursor=%s expected_total=%s",
            cfg.key, mode_label, cursor_value, expected,
        )

        total_added = 0
        page_num = 0
        offset = 0
        max_cursor_seen: str | None = cursor_value

        while True:
            page_num += 1
            t0 = time.monotonic()
            try:
                rows = await fetch_page(
                    client, cfg.socrata_id,
                    cfg.socrata_cursor_col or cfg.cursor_col,
                    cursor_value=None if is_backfill else (cursor_value if is_refresh else max_cursor_seen),
                    offset=offset,
                    column_map=cfg.column_map,
                    use_offset_with_where=is_refresh,
                )
            except Exception as e:
                logger.exception("fatal fetch error on page %d", page_num)
                async with pool.acquire() as conn:
                    await write_state(conn, cfg.key, last_error=str(e)[:500])
                return 2

            if not rows:
                logger.info("end of stream — %d rows added across %d pages",
                            total_added, page_num - 1)
                break

            page_max = max(
                (_normalize_cursor_date(r[cfg.cursor_col]) for r in rows
                 if _is_valid_date_cursor(r.get(cfg.cursor_col))),
                default=max_cursor_seen,
            )

            if dry_run:
                logger.info("[dry-run] page %d: %d rows (max %s=%s)",
                            page_num, len(rows), cfg.cursor_col, page_max)
            else:
                try:
                    async with pool.acquire() as conn:
                        if is_refresh:
                            added = await refresh_page_by_doc(conn, cfg, target_cols, rows)
                        else:
                            added = await upsert_page(conn, cfg, target_cols, rows)
                        # Persist cursor + rows_added per page in both incremental
                        # and backfill modes. In backfill mode the next page is
                        # fetched by $offset (not $where), so the persisted cursor
                        # is informational only — but it lets a crashed run cleanly
                        # switch to incremental mode on resume (no --reset). Refresh
                        # mode still defers cursor write to the end because the
                        # cursor column is too coarse mid-run.
                        if is_refresh:
                            await write_state(conn, cfg.key, rows_added=added)
                        else:
                            await write_state(
                                conn, cfg.key, cursor_value=page_max, rows_added=added,
                            )
                except Exception as e:
                    logger.exception("fatal upsert error on page %d", page_num)
                    async with pool.acquire() as conn:
                        await write_state(conn, cfg.key, last_error=str(e)[:500])
                    return 2
                total_added += added
                logger.info("page %d: %d rows in %.1fs (max %s=%s)",
                            page_num, added, time.monotonic() - t0, cfg.cursor_col, page_max)

            max_cursor_seen = page_max
            # Refresh uses $where + $offset; upsert-incremental advances $where
            # per page (offset stays 0); backfill paginates via $offset.
            if is_backfill or is_refresh:
                offset += len(rows)

            if len(rows) < PAGE_SIZE:
                logger.info("partial page (%d < %d) — end of stream", len(rows), PAGE_SIZE)
                break

            await asyncio.sleep(INTER_PAGE_SLEEP_SEC)

        # ── final state + drift check ──────────────────────────────────
        async with pool.acquire() as conn:
            actual = await conn.fetchval(f'SELECT COUNT(*) FROM "{cfg.table}"')
            if not dry_run:
                # Refresh mode commits cursor only at the end (the source's
                # cursor column is too coarse mid-run). Backfill and incremental
                # both persist cursor per page; this final write is a no-op for
                # cursor in those modes (final_cursor=None leaves it alone) but
                # still records expected_rows / actual_rows / success.
                if is_refresh:
                    final_cursor = max_cursor_seen
                else:
                    final_cursor = None
                await write_state(
                    conn, cfg.key,
                    cursor_value=final_cursor,
                    expected_rows=expected, actual_rows=actual, success=True,
                )

        if expected and expected > 0 and actual < expected:
            missing_pct = (expected - actual) / expected * 100
            logger.info("drift check: actual=%s expected=%s (missing %.2f%%)",
                        actual, expected, missing_pct)
            if missing_pct >= DRIFT_ERR_PCT:
                logger.error("MISSING >= %.0f%% — alert needed", DRIFT_ERR_PCT)
                return 1
            if missing_pct >= DRIFT_WARN_PCT:
                logger.warning("missing >= %.0f%% — warn", DRIFT_WARN_PCT)
        else:
            logger.info("drift check: actual=%s expected=%s (ok)", actual, expected)

    await pool.close()
    return 0


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("dataset", help=f"one of: {', '.join(DATASETS)}")
    p.add_argument("--dry-run", action="store_true", help="fetch but do not write")
    p.add_argument("--reset", action="store_true", help="clear cursor for full backfill")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.dataset not in DATASETS:
        logger.error("unknown dataset: %s", args.dataset)
        sys.exit(2)

    rc = asyncio.run(sync_dataset(DATASETS[args.dataset], dry_run=args.dry_run, reset=args.reset))
    sys.exit(rc)


if __name__ == "__main__":
    main()
