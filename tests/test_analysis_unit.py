"""Unit tests for analysis.py builder and observation helpers.

No database or network connections required — all helpers are pure functions
that take already-fetched data dicts and return structured sections.
"""

from __future__ import annotations

import pytest

from nyc_property_intel.tools.analysis import (
    _build_comparable_market,
    _build_development_potential,
    _build_financial_snapshot,
    _build_ownership_and_legal,
    _build_property_summary,
    _build_tenant_and_operations,
    _build_violations_and_compliance,
    _generate_observations,
    _safe_float,
)


# ── _safe_float ───────────────────────────────────────────────────────

class TestSafeFloat:
    def test_none_returns_none(self):
        assert _safe_float(None) is None

    def test_int_converts(self):
        assert _safe_float(42) == 42.0

    def test_string_number_converts(self):
        assert _safe_float("3.14") == pytest.approx(3.14)

    def test_invalid_string_returns_none(self):
        assert _safe_float("abc") is None

    def test_zero_converts(self):
        assert _safe_float(0) == 0.0


# ── _build_property_summary ───────────────────────────────────────────

class TestBuildPropertySummary:
    _profile = {
        "bbl": "1008350001",
        "address": "590 MADISON AVE",
        "borough": "MN",
        "ownername": "TEST OWNER LLC",
        "bldgclass": "O4",
        "zonedist1": "C5-3",
        "yearbuilt": 1931,
        "numfloors": 42,
        "unitstotal": 1,
        "unitsres": 0,
        "lotarea": 18750,
        "bldgarea": 975000,
        "histdist": None,
        "landmark": None,
        "latitude": 40.7605,
        "longitude": -73.9737,
    }
    _bbl_info = {
        "borough": "1",
        "block": "00835",
        "lot": "0001",
        "borough_name": "Manhattan",
        "bbl_formatted": "1-00835-0001",
    }

    def test_bbl_formatted(self):
        result = _build_property_summary(self._profile, self._bbl_info)
        assert result["bbl_formatted"] == "1-00835-0001"

    def test_borough_name(self):
        result = _build_property_summary(self._profile, self._bbl_info)
        assert result["borough"] == "Manhattan"

    def test_coordinates_included(self):
        result = _build_property_summary(self._profile, self._bbl_info)
        assert result["coordinates"]["latitude"] == pytest.approx(40.7605)
        assert result["coordinates"]["longitude"] == pytest.approx(-73.9737)

    def test_zoning_district_included(self):
        result = _build_property_summary(self._profile, self._bbl_info)
        assert result["zoning_district"] == "C5-3"

    def test_landmark_none_when_missing(self):
        result = _build_property_summary(self._profile, self._bbl_info)
        assert result["landmark_district"] is None


# ── _build_financial_snapshot ─────────────────────────────────────────

class TestBuildFinancialSnapshot:
    _profile = {
        "assessland": 5_000_000,
        "assesstot": 20_000_000,
        "exempttot": 0,
    }

    def test_no_sales_returns_none_price(self):
        result = _build_financial_snapshot(self._profile, [], [])
        assert result["last_sale_price"] is None
        assert result["last_sale_date"] is None

    def test_first_sale_used_as_last(self):
        sales = [
            {"saledate": "2023-06-15", "saleprice": 5_000_000},
            {"saledate": "2020-01-01", "saleprice": 3_000_000},
        ]
        result = _build_financial_snapshot(self._profile, sales, [])
        assert result["last_sale_price"] == 5_000_000.0
        assert result["last_sale_date"] == "2023-06-15"

    def test_exemptions_included(self):
        exemptions = [{"exmpcode": "421A", "exname": "421-A Tax Exemption", "curexmptot": 150_000}]
        result = _build_financial_snapshot(self._profile, [], exemptions)
        assert result["tax_exemptions"] is not None
        assert len(result["tax_exemptions"]) == 1
        assert result["tax_exemptions"][0]["code"] == "421A"

    def test_no_exemptions_returns_none(self):
        result = _build_financial_snapshot(self._profile, [], [])
        assert result["tax_exemptions"] is None

    def test_assessed_values_converted(self):
        result = _build_financial_snapshot(self._profile, [], [])
        assert result["assessed_land"] == 5_000_000.0
        assert result["assessed_total"] == 20_000_000.0


# ── _build_development_potential ─────────────────────────────────────

class TestBuildDevelopmentPotential:
    def test_unused_far_calculated(self):
        profile = {"builtfar": 2.0, "residfar": 6.0, "commfar": 2.0, "facilfar": None, "lotarea": 5000}
        result = _build_development_potential(profile)
        assert result["unused_far"] == pytest.approx(4.0)
        assert result["unused_sqft"] == 20000
        assert result["is_maxed_out"] is False

    def test_maxed_out_flag(self):
        profile = {"builtfar": 6.0, "residfar": 6.0, "commfar": None, "facilfar": None, "lotarea": 5000}
        result = _build_development_potential(profile)
        assert result["is_maxed_out"] is True

    def test_no_far_data_returns_none(self):
        profile = {"builtfar": None, "residfar": None, "commfar": None, "facilfar": None, "lotarea": 5000}
        result = _build_development_potential(profile)
        assert result["unused_far"] is None
        assert result["is_maxed_out"] is None


# ── _build_violations_and_compliance ─────────────────────────────────

class TestBuildViolationsAndCompliance:
    _violations = {
        "hpd_total": 50, "hpd_class_a": 10, "hpd_class_b": 25, "hpd_class_c": 15,
        "hpd_open": 8, "hpd_most_recent": "2024-03-01",
        "dob_total": 12, "dob_no_disposition": 3, "dob_has_disposition": 9,
        "dob_most_recent": "2024-01-15",
    }

    def test_hpd_violations_section(self):
        result = _build_violations_and_compliance(self._violations, None, None, None)
        hpd = result["hpd_violations"]
        assert hpd["total"] == 50
        assert hpd["class_c"] == 15
        assert hpd["open"] == 8

    def test_dob_violations_section(self):
        result = _build_violations_and_compliance(self._violations, None, None, None)
        dob = result["dob_violations"]
        assert dob["total"] == 12
        assert dob["open_or_no_disposition"] == 3

    def test_none_violations_returns_none_sections(self):
        result = _build_violations_and_compliance(None, None, None, None)
        assert result["hpd_violations"] is None
        assert result["dob_violations"] is None

    def test_hpd_complaints_section(self):
        complaints = {"total_complaints": 30, "open_complaints": 5, "most_recent": "2024-04-01"}
        result = _build_violations_and_compliance(self._violations, complaints, None, None)
        assert result["hpd_complaints"]["total"] == 30
        assert result["hpd_complaints"]["open"] == 5

    def test_hpd_complaints_zero_returns_none(self):
        complaints = {"total_complaints": 0, "open_complaints": 0, "most_recent": None}
        result = _build_violations_and_compliance(self._violations, complaints, None, None)
        assert result["hpd_complaints"] is None

    def test_hpd_litigations_section(self):
        litigations = {"total_cases": 3, "open_cases": 1, "harassment_findings": 2, "most_recent_case": "2023-11-01"}
        result = _build_violations_and_compliance(self._violations, None, litigations, None)
        assert result["hpd_litigations"]["total_cases"] == 3
        assert result["hpd_litigations"]["harassment_findings"] == 2

    def test_hpd_litigations_zero_total_returns_none(self):
        litigations = {"total_cases": 0, "open_cases": 0, "harassment_findings": 0, "most_recent_case": None}
        result = _build_violations_and_compliance(self._violations, None, litigations, None)
        assert result["hpd_litigations"] is None

    def test_permits_section(self):
        permits = {"total_filings": 5, "new_buildings": 0, "alterations": 4, "demolitions": 1, "most_recent_filing": "2022-06-01"}
        result = _build_violations_and_compliance(self._violations, None, None, permits)
        assert result["building_permits"]["total_filings"] == 5
        assert result["building_permits"]["alterations"] == 4

    def test_permits_zero_returns_none(self):
        permits = {"total_filings": 0, "new_buildings": 0, "alterations": 0, "demolitions": 0, "most_recent_filing": None}
        result = _build_violations_and_compliance(self._violations, None, None, permits)
        assert result["building_permits"] is None


# ── _build_ownership_and_legal ────────────────────────────────────────

class TestBuildOwnershipAndLegal:
    def test_no_tax_lien(self):
        result = _build_ownership_and_legal(None, None, None, None)
        assert result["tax_liens"]["has_tax_liens"] is False

    def test_tax_lien_present(self):
        lien = {"cycle": "2024", "waterdebtonly": False}
        result = _build_ownership_and_legal(None, None, lien, None)
        assert result["tax_liens"]["has_tax_liens"] is True
        assert result["tax_liens"]["cycle"] == "2024"

    def test_deed_owner_passed_through(self):
        ownership = {"owner_name": "TEST CORP", "doctype": "DEED"}
        result = _build_ownership_and_legal(ownership, None, None, None)
        assert result["deed_owner"]["owner_name"] == "TEST CORP"

    def test_hpd_registration_passed_through(self):
        reg = {"registered": True, "managing_agent": "MGMT CO LLC"}
        result = _build_ownership_and_legal(None, reg, None, None)
        assert result["hpd_registration"]["managing_agent"] == "MGMT CO LLC"

    def test_mortgages_section(self):
        mortgages = {
            "total_recorded": 4, "active_mortgages": 2, "satisfactions": 2,
            "most_recent_date": "2022-01-01", "total_mortgage_amount": 2_000_000,
        }
        result = _build_ownership_and_legal(None, None, None, mortgages)
        assert result["mortgages"]["total_recorded"] == 4
        assert result["mortgages"]["active_mortgages"] == 2

    def test_mortgages_zero_returns_none(self):
        mortgages = {
            "total_recorded": 0, "active_mortgages": 0, "satisfactions": 0,
            "most_recent_date": None, "total_mortgage_amount": 0,
        }
        result = _build_ownership_and_legal(None, None, None, mortgages)
        assert result["mortgages"] is None


# ── _build_tenant_and_operations ─────────────────────────────────────

class TestBuildTenantAndOperations:
    def test_no_data_returns_none_sections(self):
        result = _build_tenant_and_operations(None, None, None)
        assert result["rent_stabilization"] is None
        assert result["evictions"] is None
        assert result["complaints_311"] is None

    def test_rent_stabilization_section(self):
        rs = {"uc2017": 24, "uc2016": 22, "uc2015": 20, "est2017": False, "unitsres": 30}
        result = _build_tenant_and_operations(rs, None, None)
        assert result["rent_stabilization"]["is_rent_stabilized"] is True
        assert result["rent_stabilization"]["latest_stabilized_units"] == 24

    def test_evictions_section(self):
        ev = {"total_evictions": 5, "residential_evictions": 4, "commercial_evictions": 1, "most_recent": "2023-08-10"}
        result = _build_tenant_and_operations(None, ev, None)
        assert result["evictions"]["total"] == 5
        assert result["evictions"]["residential"] == 4

    def test_evictions_zero_returns_none(self):
        ev = {"total_evictions": 0, "residential_evictions": 0, "commercial_evictions": 0, "most_recent": None}
        result = _build_tenant_and_operations(None, ev, None)
        assert result["evictions"] is None

    def test_311_complaints_section(self):
        c311 = {"total_complaints": 12, "open_complaints": 3, "most_recent": "2024-02-20"}
        result = _build_tenant_and_operations(None, None, c311)
        assert result["complaints_311"]["total"] == 12
        assert result["complaints_311"]["open"] == 3

    def test_311_zero_returns_none(self):
        c311 = {"total_complaints": 0, "open_complaints": 0, "most_recent": None}
        result = _build_tenant_and_operations(None, None, c311)
        assert result["complaints_311"] is None


# ── _build_comparable_market ──────────────────────────────────────────

class TestBuildComparableMarket:
    def test_empty_comps(self):
        result = _build_comparable_market("10022", [])
        assert result["num_recent_sales"] == 0
        assert result["median_price_per_sqft"] is None

    def test_fewer_than_5_comps_suppresses_ppsf(self):
        comps = [{"saleprice": 1_000_000, "grosssquarefeet": 1000}] * 4
        result = _build_comparable_market("10022", comps)
        assert result["median_price_per_sqft"] is None
        assert "ppsf_note" in result

    def test_5_or_more_comps_calculates_ppsf(self):
        comps = [{"saleprice": 1_000_000, "grosssquarefeet": 1000}] * 5
        result = _build_comparable_market("10022", comps)
        assert result["median_price_per_sqft"] == pytest.approx(1000.0)

    def test_zip_code_passed_through(self):
        result = _build_comparable_market("11201", [])
        assert result["zip_code"] == "11201"


# ── _generate_observations ────────────────────────────────────────────

class TestGenerateObservations:
    _financial_clean = {"last_sale_price": 5_000_000, "assessed_total": 2_000_000, "last_sale_date": "2023-01-01"}
    _dev_unused = {"unused_far": 3.0, "unused_sqft": 15000, "is_maxed_out": False}
    _dev_maxed = {"unused_far": 0.05, "unused_sqft": 250, "is_maxed_out": True}
    _ownership_no_lien = {"tax_liens": {"has_tax_liens": False}, "deed_owner": None, "hpd_registration": None, "mortgages": None}
    _tenant_empty = {"rent_stabilization": None, "evictions": None, "complaints_311": None}

    def test_class_c_violation_flagged(self):
        vc = {"hpd_violations": {"class_c": 3, "open": 2}, "dob_violations": None, "hpd_complaints": None, "hpd_litigations": None, "building_permits": None}
        obs = _generate_observations(vc, self._financial_clean, self._dev_unused, self._ownership_no_lien, self._tenant_empty)
        assert any("Class C" in o for o in obs)

    def test_over_10_open_hpd_violations_flagged(self):
        vc = {"hpd_violations": {"class_c": 0, "open": 15}, "dob_violations": None, "hpd_complaints": None, "hpd_litigations": None, "building_permits": None}
        obs = _generate_observations(vc, self._financial_clean, self._dev_unused, self._ownership_no_lien, self._tenant_empty)
        assert any("open HPD violations" in o for o in obs)

    def test_harassment_finding_flagged(self):
        vc = {"hpd_violations": None, "dob_violations": None, "hpd_complaints": None,
              "hpd_litigations": {"total_cases": 2, "open_cases": 1, "harassment_findings": 1}, "building_permits": None}
        obs = _generate_observations(vc, self._financial_clean, self._dev_unused, self._ownership_no_lien, self._tenant_empty)
        assert any("harassment" in o.lower() for o in obs)

    def test_non_arms_length_sale_flagged(self):
        financial = {**self._financial_clean, "last_sale_price": 0}
        vc = {"hpd_violations": None, "dob_violations": None, "hpd_complaints": None, "hpd_litigations": None, "building_permits": None}
        obs = _generate_observations(vc, financial, self._dev_unused, self._ownership_no_lien, self._tenant_empty)
        assert any("non-arm's-length" in o for o in obs)

    def test_unused_far_flagged(self):
        vc = {"hpd_violations": None, "dob_violations": None, "hpd_complaints": None, "hpd_litigations": None, "building_permits": None}
        obs = _generate_observations(vc, self._financial_clean, self._dev_unused, self._ownership_no_lien, self._tenant_empty)
        assert any("unused FAR" in o for o in obs)

    def test_maxed_out_far_flagged(self):
        vc = {"hpd_violations": None, "dob_violations": None, "hpd_complaints": None, "hpd_litigations": None, "building_permits": None}
        obs = _generate_observations(vc, self._financial_clean, self._dev_maxed, self._ownership_no_lien, self._tenant_empty)
        assert any("maxed out" in o for o in obs)

    def test_tax_lien_flagged(self):
        ownership = {**self._ownership_no_lien, "tax_liens": {"has_tax_liens": True, "cycle": "2024"}}
        vc = {"hpd_violations": None, "dob_violations": None, "hpd_complaints": None, "hpd_litigations": None, "building_permits": None}
        obs = _generate_observations(vc, self._financial_clean, self._dev_unused, ownership, self._tenant_empty)
        assert any("tax lien" in o.lower() for o in obs)

    def test_rent_stabilized_units_flagged(self):
        vc = {"hpd_violations": None, "dob_violations": None, "hpd_complaints": None, "hpd_litigations": None, "building_permits": None}
        tenant = {"rent_stabilization": {"is_rent_stabilized": True, "latest_stabilized_units": 12}, "evictions": None, "complaints_311": None}
        obs = _generate_observations(vc, self._financial_clean, self._dev_unused, self._ownership_no_lien, tenant)
        assert any("rent-stabilized" in o for o in obs)

    def test_evictions_threshold_flagged(self):
        vc = {"hpd_violations": None, "dob_violations": None, "hpd_complaints": None, "hpd_litigations": None, "building_permits": None}
        tenant = {"rent_stabilization": None, "evictions": {"total": 5, "residential": 4, "commercial": 1}, "complaints_311": None}
        obs = _generate_observations(vc, self._financial_clean, self._dev_unused, self._ownership_no_lien, tenant)
        assert any("eviction" in o.lower() for o in obs)

    def test_below_evictions_threshold_not_flagged(self):
        vc = {"hpd_violations": None, "dob_violations": None, "hpd_complaints": None, "hpd_litigations": None, "building_permits": None}
        tenant = {"rent_stabilization": None, "evictions": {"total": 2, "residential": 2, "commercial": 0}, "complaints_311": None}
        obs = _generate_observations(vc, self._financial_clean, self._dev_unused, self._ownership_no_lien, tenant)
        assert not any("eviction" in o.lower() for o in obs)

    def test_clean_property_no_observations(self):
        vc = {"hpd_violations": {"class_c": 0, "open": 0}, "dob_violations": None, "hpd_complaints": None, "hpd_litigations": None, "building_permits": None}
        financial = {"last_sale_price": 500_000, "assessed_total": 400_000, "last_sale_date": "2023-01-01"}
        dev = {"unused_far": 0.3, "unused_sqft": 500, "is_maxed_out": False}
        obs = _generate_observations(vc, financial, dev, self._ownership_no_lien, self._tenant_empty)
        assert obs == []
