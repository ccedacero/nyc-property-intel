#!/usr/bin/env python3
"""
Load 311, FDNY, and NYPD datasets from local CSVs into Postgres.

Usage:
    uv run python scripts/load_local_datasets.py [311|fdny|nypd|all]

Defaults to loading all three datasets.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

DB = os.environ.get("DATABASE_URL", "postgresql://nycdb:nycdb@localhost:5432/nycdb")
DATA = Path(__file__).parent.parent / "data"


# ── helpers ──────────────────────────────────────────────────────────────────

def psql(sql: str, description: str = "") -> None:
    if description:
        print(f"  {description} ...", flush=True)
    result = subprocess.run(["psql", DB, "-c", sql], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)
    out = result.stdout.strip()
    if out:
        print(f"  → {out}", flush=True)


def copy_table(table: str, path: Path, null_str: str | None = None) -> None:
    null_clause = f"NULL '{null_str}'" if null_str else ""
    cmd = f"\\COPY {table} FROM '{path.resolve()}' CSV HEADER {null_clause};"
    print(f"  COPY {path.name} → {table} ...", flush=True)
    t0 = time.time()
    result = subprocess.run(["psql", DB, "-c", cmd], capture_output=True, text=True)
    elapsed = time.time() - t0
    if result.returncode != 0:
        print(f"ERROR:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)
    rows = result.stdout.strip()
    print(f"  → {rows} in {elapsed:.1f}s", flush=True)


def section(title: str) -> None:
    print(f"\n{'='*60}\n  {title}\n{'='*60}", flush=True)


# ── 311 ──────────────────────────────────────────────────────────────────────

DDL_311 = """\
DROP TABLE IF EXISTS nyc_311_complaints;
CREATE TABLE nyc_311_complaints (
    unique_key               TEXT,
    created_date             TEXT,
    closed_date              TEXT,
    agency                   TEXT,
    agency_name              TEXT,
    complaint_type           TEXT,
    descriptor               TEXT,
    location_type            TEXT,
    incident_zip             TEXT,
    incident_address         TEXT,
    street_name              TEXT,
    cross_street_1           TEXT,
    cross_street_2           TEXT,
    intersection_street_1    TEXT,
    intersection_street_2    TEXT,
    address_type             TEXT,
    city                     TEXT,
    landmark                 TEXT,
    status                   TEXT,
    resolution_description   TEXT,
    resolution_action_updated_date TEXT,
    community_board          TEXT,
    council_district         TEXT,
    police_precinct          TEXT,
    bbl                      TEXT,
    borough                  TEXT,
    x_coordinate_state_plane TEXT,
    y_coordinate_state_plane TEXT,
    open_data_channel_type   TEXT,
    park_facility_name       TEXT,
    park_borough             TEXT,
    latitude                 DOUBLE PRECISION,
    longitude                DOUBLE PRECISION,
    location                 TEXT
);
"""

INDEXES_311 = [
    ("nyc_311_bbl_idx",            "nyc_311_complaints (bbl)"),
    ("nyc_311_address_idx",        "nyc_311_complaints (incident_address text_pattern_ops)"),
    ("nyc_311_created_idx",        "nyc_311_complaints (created_date DESC)"),
    ("nyc_311_complaint_type_idx", "nyc_311_complaints (complaint_type)"),
    ("nyc_311_borough_idx",        "nyc_311_complaints (borough)"),
    ("nyc_311_status_idx",         "nyc_311_complaints (status)"),
]


def load_311() -> None:
    section("311 Service Requests")
    path = DATA / "nyc_311.csv"
    if not path.exists():
        print(f"  ✗ {path} not found — skipping.", file=sys.stderr)
        return

    psql(DDL_311, "Creating nyc_311_complaints table")
    copy_table("nyc_311_complaints", path)

    for idx_name, idx_def in INDEXES_311:
        psql(f"CREATE INDEX {idx_name} ON {idx_def};",
             f"Index {idx_name}")

    psql("SELECT COUNT(*) FROM nyc_311_complaints;", "Row count")
    print("  ✅ 311 done.\n", flush=True)


# ── FDNY ─────────────────────────────────────────────────────────────────────

DDL_FDNY = """\
DROP TABLE IF EXISTS fdny_incidents;
CREATE TABLE fdny_incidents (
    starfire_incident_id          TEXT,
    incident_datetime             TEXT,
    alarm_box_borough             TEXT,
    alarm_box_number              TEXT,
    alarm_box_location            TEXT,
    incident_borough              TEXT,
    zipcode                       TEXT,
    policeprecinct                TEXT,
    citycouncildistrict           TEXT,
    communitydistrict             TEXT,
    communityschooldistrict       TEXT,
    congressionaldistrict         TEXT,
    alarm_source_description_tx   TEXT,
    alarm_level_index_description TEXT,
    highest_alarm_level           TEXT,
    incident_classification       TEXT,
    incident_classification_group TEXT,
    dispatch_response_seconds_qy  TEXT,
    first_assignment_datetime     TEXT,
    first_activation_datetime     TEXT,
    first_on_scene_datetime       TEXT,
    incident_close_datetime       TEXT,
    valid_dispatch_rspns_time_indc TEXT,
    valid_incident_rspns_time_indc TEXT,
    incident_response_seconds_qy  TEXT,
    incident_travel_tm_seconds_qy TEXT,
    engines_assigned_quantity     TEXT,
    ladders_assigned_quantity     TEXT,
    other_units_assigned_quantity TEXT
);
"""

INDEXES_FDNY = [
    ("fdny_borough_idx",    "fdny_incidents (incident_borough)"),
    ("fdny_zip_idx",        "fdny_incidents (zipcode)"),
    ("fdny_datetime_idx",   "fdny_incidents (incident_datetime DESC)"),
    ("fdny_class_idx",      "fdny_incidents (incident_classification)"),
    ("fdny_classgrp_idx",   "fdny_incidents (incident_classification_group)"),
]


def load_fdny() -> None:
    section("FDNY Fire Incidents")
    path = DATA / "fdny_incidents.csv"
    if not path.exists():
        print(f"  ✗ {path} not found — skipping.", file=sys.stderr)
        return

    psql(DDL_FDNY, "Creating fdny_incidents table")
    copy_table("fdny_incidents", path)

    for idx_name, idx_def in INDEXES_FDNY:
        psql(f"CREATE INDEX {idx_name} ON {idx_def};", f"Index {idx_name}")

    psql("SELECT COUNT(*) FROM fdny_incidents;", "Row count")
    print("  ✅ FDNY done.\n", flush=True)


# ── NYPD ─────────────────────────────────────────────────────────────────────

DDL_NYPD = """\
DROP TABLE IF EXISTS nypd_crime_complaints;
CREATE TABLE nypd_crime_complaints (
    cmplnt_num       TEXT,
    addr_pct_cd      TEXT,
    boro_nm          TEXT,
    cmplnt_fr_dt     TEXT,
    cmplnt_fr_tm     TEXT,
    cmplnt_to_tm     TEXT,
    crm_atpt_cptd_cd TEXT,
    hadevelopt       TEXT,
    jurisdiction_code TEXT,
    juris_desc       TEXT,
    ky_cd            TEXT,
    law_cat_cd       TEXT,
    loc_of_occur_desc TEXT,
    ofns_desc        TEXT,
    parks_nm         TEXT,
    patrol_boro      TEXT,
    pd_desc          TEXT,
    prem_typ_desc    TEXT,
    rpt_dt           TEXT,
    station_name     TEXT,
    susp_age_group   TEXT,
    susp_race        TEXT,
    susp_sex         TEXT,
    vic_age_group    TEXT,
    vic_race         TEXT,
    vic_sex          TEXT,
    x_coord_cd       TEXT,
    y_coord_cd       TEXT,
    latitude         DOUBLE PRECISION,
    longitude        DOUBLE PRECISION,
    lat_lon          TEXT,
    geocoded_column  TEXT
);
"""

INDEXES_NYPD = [
    ("nypd_lat_idx",      "nypd_crime_complaints (latitude)"),
    ("nypd_lon_idx",      "nypd_crime_complaints (longitude)"),
    ("nypd_latlon_idx",   "nypd_crime_complaints (latitude, longitude)"),
    ("nypd_date_idx",     "nypd_crime_complaints (cmplnt_fr_dt DESC)"),
    ("nypd_law_cat_idx",  "nypd_crime_complaints (law_cat_cd)"),
    ("nypd_boro_idx",     "nypd_crime_complaints (boro_nm)"),
    ("nypd_offense_idx",  "nypd_crime_complaints (ofns_desc)"),
]


def load_nypd() -> None:
    section("NYPD Crime Complaints")
    path = DATA / "nypd_crime.csv"
    if not path.exists():
        print(f"  ✗ {path} not found — skipping.", file=sys.stderr)
        return

    psql(DDL_NYPD, "Creating nypd_crime_complaints table")
    # NYPD uses "(null)" as null indicator
    copy_table("nypd_crime_complaints", path, null_str="(null)")

    for idx_name, idx_def in INDEXES_NYPD:
        psql(f"CREATE INDEX {idx_name} ON {idx_def};", f"Index {idx_name}")

    psql("SELECT COUNT(*) FROM nypd_crime_complaints;", "Row count")
    print("  ✅ NYPD done.\n", flush=True)


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    targets = sys.argv[1:] or ["all"]
    want = set(t.lower() for t in targets)

    if "all" in want or "311" in want:
        load_311()
    if "all" in want or "fdny" in want:
        load_fdny()
    if "all" in want or "nypd" in want:
        load_nypd()

    print("\nAll done ✅")


if __name__ == "__main__":
    main()
