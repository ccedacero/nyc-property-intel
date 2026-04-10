"""Input validation tests for Phase B/C tools.

Validates that each tool rejects bad inputs with ``ToolError`` *before* any
database call is attempted.  No live database connection is required for these
tests — ``validate_bbl`` (and the individual limit/date/enum guards) all raise
``ToolError`` synchronously at the top of each tool function.

The autouse ``_init_db_pool_for_integration`` fixture in conftest.py will
still run, which means a live PostgreSQL instance at the default URL is
expected.  All tests here should fail fast on the validation path and never
reach the actual DB query.
"""

from __future__ import annotations

import pytest
from mcp.server.fastmcp.exceptions import ToolError


# ── HPD complaints ────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestGetHpdComplaintsValidation:
    """``get_hpd_complaints`` — validation layer tests."""

    async def test_invalid_bbl_raises(self):
        from nyc_property_intel.tools.hpd_complaints import get_hpd_complaints

        with pytest.raises(ToolError):
            await get_hpd_complaints(bbl="bad")

    async def test_limit_too_low_raises(self):
        from nyc_property_intel.tools.hpd_complaints import get_hpd_complaints

        with pytest.raises(ToolError, match="limit must be between"):
            await get_hpd_complaints(bbl="1008350001", limit=0)

    async def test_limit_too_high_raises(self):
        from nyc_property_intel.tools.hpd_complaints import get_hpd_complaints

        with pytest.raises(ToolError, match="limit must be between"):
            await get_hpd_complaints(bbl="1008350001", limit=201)

    async def test_invalid_date_raises(self):
        from nyc_property_intel.tools.hpd_complaints import get_hpd_complaints

        with pytest.raises(ToolError, match="Invalid date format"):
            await get_hpd_complaints(bbl="1008350001", since_date="not-a-date")

    async def test_bbl_wrong_borough_prefix_raises(self):
        from nyc_property_intel.tools.hpd_complaints import get_hpd_complaints

        with pytest.raises(ToolError):
            await get_hpd_complaints(bbl="9000000001")

    async def test_bbl_too_short_raises(self):
        from nyc_property_intel.tools.hpd_complaints import get_hpd_complaints

        with pytest.raises(ToolError):
            await get_hpd_complaints(bbl="100835000")  # 9 digits, one short


# ── Liens and encumbrances ────────────────────────────────────────────


@pytest.mark.asyncio
class TestGetLiensValidation:
    """``get_liens_and_encumbrances`` — validation layer tests."""

    async def test_invalid_bbl_raises(self):
        from nyc_property_intel.tools.liens import get_liens_and_encumbrances

        with pytest.raises(ToolError):
            await get_liens_and_encumbrances(bbl="bad")

    async def test_limit_too_low_raises(self):
        from nyc_property_intel.tools.liens import get_liens_and_encumbrances

        with pytest.raises(ToolError, match="limit must be between"):
            await get_liens_and_encumbrances(bbl="1008350001", limit=0)

    async def test_limit_too_high_raises(self):
        from nyc_property_intel.tools.liens import get_liens_and_encumbrances

        with pytest.raises(ToolError, match="limit must be between"):
            await get_liens_and_encumbrances(bbl="1008350001", limit=101)

    async def test_bbl_all_zeros_raises(self):
        from nyc_property_intel.tools.liens import get_liens_and_encumbrances

        with pytest.raises(ToolError):
            await get_liens_and_encumbrances(bbl="0000000000")  # borough 0


# ── Building permits ──────────────────────────────────────────────────


@pytest.mark.asyncio
class TestGetBuildingPermitsValidation:
    """``get_building_permits`` — validation layer tests."""

    async def test_invalid_bbl_raises(self):
        from nyc_property_intel.tools.permits import get_building_permits

        with pytest.raises(ToolError):
            await get_building_permits(bbl="bad")

    async def test_invalid_job_type_raises(self):
        from nyc_property_intel.tools.permits import get_building_permits

        with pytest.raises(ToolError, match="Invalid job_type"):
            await get_building_permits(bbl="1008350001", job_type="XX")

    async def test_limit_too_low_raises(self):
        from nyc_property_intel.tools.permits import get_building_permits

        with pytest.raises(ToolError, match="limit must be between"):
            await get_building_permits(bbl="1008350001", limit=0)

    async def test_limit_too_high_raises(self):
        from nyc_property_intel.tools.permits import get_building_permits

        # The permits tool enforces a maximum of 100 (not 200).
        with pytest.raises(ToolError, match="limit must be between"):
            await get_building_permits(bbl="1008350001", limit=101)

    async def test_valid_job_types_accepted(self):
        """Known job type codes must not raise on the validation step.

        The function will proceed to the DB call after validation; we catch
        any downstream error that is not a ToolError and consider the
        validation itself successful.
        """
        from nyc_property_intel.tools.permits import get_building_permits

        valid_types = ["NB", "A1", "A2", "A3", "DM", "SG"]
        for jt in valid_types:
            try:
                await get_building_permits(bbl="1008350001", job_type=jt)
            except ToolError as exc:
                pytest.fail(
                    f"Valid job_type {jt!r} raised ToolError unexpectedly: {exc}"
                )
            except Exception:
                # DB/connection errors are acceptable — validation passed.
                pass


# ── Neighborhood statistics ───────────────────────────────────────────


@pytest.mark.asyncio
class TestSearchNeighborhoodStatsValidation:
    """``search_neighborhood_stats`` — validation layer tests."""

    async def test_no_zip_no_neighborhood_raises(self):
        from nyc_property_intel.tools.neighborhood import search_neighborhood_stats

        with pytest.raises(ToolError, match="At least one"):
            await search_neighborhood_stats()

    async def test_months_too_low_raises(self):
        from nyc_property_intel.tools.neighborhood import search_neighborhood_stats

        with pytest.raises(ToolError, match="months must be between"):
            await search_neighborhood_stats(zip_code="10001", months=0)

    async def test_months_too_high_raises(self):
        from nyc_property_intel.tools.neighborhood import search_neighborhood_stats

        with pytest.raises(ToolError, match="months must be between"):
            await search_neighborhood_stats(zip_code="10001", months=121)

    async def test_neighborhood_alone_accepted(self):
        """Providing only ``neighborhood`` should pass validation."""
        from nyc_property_intel.tools.neighborhood import search_neighborhood_stats

        try:
            await search_neighborhood_stats(neighborhood="Bushwick")
        except ToolError as exc:
            pytest.fail(
                f"Valid neighborhood-only call raised ToolError unexpectedly: {exc}"
            )
        except Exception:
            # DB/connection errors are acceptable — validation passed.
            pass


# ── Rent stabilization ────────────────────────────────────────────────


@pytest.mark.asyncio
class TestGetRentStabilizationValidation:
    """``get_rent_stabilization`` — validation layer tests."""

    async def test_invalid_bbl_raises(self):
        from nyc_property_intel.tools.rentstab import get_rent_stabilization

        with pytest.raises(ToolError):
            await get_rent_stabilization(bbl="bad")

    async def test_bbl_with_letters_raises(self):
        from nyc_property_intel.tools.rentstab import get_rent_stabilization

        with pytest.raises(ToolError):
            await get_rent_stabilization(bbl="10083500AB")

    async def test_empty_bbl_raises(self):
        from nyc_property_intel.tools.rentstab import get_rent_stabilization

        with pytest.raises(ToolError):
            await get_rent_stabilization(bbl="")


# ── HPD registration ──────────────────────────────────────────────────


@pytest.mark.asyncio
class TestGetHpdRegistrationValidation:
    """``get_hpd_registration`` — validation layer tests."""

    async def test_invalid_bbl_raises(self):
        from nyc_property_intel.tools.hpd_registration import get_hpd_registration

        with pytest.raises(ToolError):
            await get_hpd_registration(bbl="bad")

    async def test_bbl_too_long_raises(self):
        from nyc_property_intel.tools.hpd_registration import get_hpd_registration

        with pytest.raises(ToolError):
            await get_hpd_registration(bbl="10083500010")  # 11 digits

    async def test_bbl_non_numeric_raises(self):
        from nyc_property_intel.tools.hpd_registration import get_hpd_registration

        with pytest.raises(ToolError):
            await get_hpd_registration(bbl="1008AB0001")  # contains letters


# ── HPD litigations ───────────────────────────────────────────────────


@pytest.mark.asyncio
class TestGetHpdLitigationsValidation:
    """``get_hpd_litigations`` — validation layer tests."""

    async def test_invalid_bbl_raises(self):
        from nyc_property_intel.tools.hpd_litigations import get_hpd_litigations

        with pytest.raises(ToolError):
            await get_hpd_litigations(bbl="bad")

    async def test_bbl_with_spaces_raises(self):
        from nyc_property_intel.tools.hpd_litigations import get_hpd_litigations

        with pytest.raises(ToolError):
            await get_hpd_litigations(bbl="1 008350001")

    async def test_bbl_borough_6_raises(self):
        from nyc_property_intel.tools.hpd_litigations import get_hpd_litigations

        with pytest.raises(ToolError):
            await get_hpd_litigations(bbl="6008350001")  # borough 6 is invalid
