"""Shared utility functions for NYC property data.

Provides BBL validation/parsing, currency formatting, borough mappings,
and data-freshness helpers used across all tool modules.
"""

from __future__ import annotations

import re

# ── Borough mappings ──────────────────────────────────────────────────

BOROUGH_CODE_TO_NAME: dict[str, str] = {
    "1": "Manhattan",
    "2": "Bronx",
    "3": "Brooklyn",
    "4": "Queens",
    "5": "Staten Island",
}

BOROUGH_NAME_TO_CODE: dict[str, str] = {
    name.lower(): code for code, name in BOROUGH_CODE_TO_NAME.items()
}

# Common aliases
BOROUGH_NAME_TO_CODE.update({
    "new york": "1",
    "new york county": "1",
    "ny": "1",
    "mn": "1",
    "bronx county": "2",
    "bx": "2",
    "kings": "3",
    "kings county": "3",
    "bk": "3",
    "queens county": "4",
    "qn": "4",
    "richmond": "5",
    "richmond county": "5",
    "si": "5",
})


def borough_code_to_name(code: str) -> str:
    """Convert a borough code (1-5) to its full name.

    Args:
        code: Single-digit string "1" through "5".

    Returns:
        Borough name, e.g. "Manhattan".

    Raises:
        ValueError: If code is not 1-5.
    """
    name = BOROUGH_CODE_TO_NAME.get(code)
    if name is None:
        raise ValueError(f"Invalid borough code: {code!r}. Must be 1-5.")
    return name


def borough_name_to_code(name: str) -> str:
    """Convert a borough name (or alias) to its numeric code.

    Args:
        name: Borough name like "Brooklyn", "BK", "Kings County".

    Returns:
        Single-digit string "1" through "5".

    Raises:
        ValueError: If the name is not recognized.
    """
    code = BOROUGH_NAME_TO_CODE.get(name.strip().lower())
    if code is None:
        raise ValueError(
            f"Unrecognized borough: {name!r}. "
            f"Expected one of: {', '.join(BOROUGH_CODE_TO_NAME.values())}."
        )
    return code


# ── BBL validation and parsing ────────────────────────────────────────

_BBL_PATTERN = re.compile(r"^[1-5]\d{9}$")


def validate_bbl(bbl: str) -> tuple[str, str, str]:
    """Validate a 10-digit BBL string and split it into components.

    A BBL (Borough-Block-Lot) is a 10-digit identifier used by NYC:
      - Digit 1:    borough code (1-5)
      - Digits 2-6: tax block (5 digits, zero-padded)
      - Digits 7-10: tax lot (4 digits, zero-padded)

    Args:
        bbl: A 10-character numeric string, e.g. "1008350001".

    Returns:
        Tuple of (borough, block, lot) as strings:
        ("1", "00835", "0001").

    Raises:
        ValueError: If the BBL is malformed.
    """
    cleaned = bbl.strip().replace("-", "")
    if not _BBL_PATTERN.match(cleaned):
        raise ValueError(
            f"Invalid BBL: {bbl!r}. Must be exactly 10 digits, "
            f"starting with a borough code 1-5."
        )
    borough = cleaned[0]
    block = cleaned[1:6]
    lot = cleaned[6:10]
    return borough, block, lot


def parse_bbl(bbl: str) -> dict[str, str]:
    """Parse a BBL string into a labeled dictionary.

    Args:
        bbl: A 10-digit BBL string.

    Returns:
        Dict with keys: borough, block, lot, borough_name, bbl_formatted.

    Raises:
        ValueError: If the BBL is malformed.
    """
    borough, block, lot = validate_bbl(bbl)
    return {
        "borough": borough,
        "block": block,
        "lot": lot,
        "borough_name": borough_code_to_name(borough),
        "bbl_formatted": f"{borough}-{block}-{lot}",
    }


# ── Currency formatting ──────────────────────────────────────────────

def format_currency(amount: int | float | None) -> str:
    """Format a numeric amount as a US dollar string.

    Args:
        amount: The dollar amount, or None.

    Returns:
        Formatted string like "$1,250,000" or "$0" or "N/A".
    """
    if amount is None:
        return "N/A"
    if amount < 0:
        if isinstance(amount, float) and amount != int(amount):
            return f"-${abs(amount):,.2f}"
        return f"-${abs(int(amount)):,}"
    if isinstance(amount, float) and amount != int(amount):
        return f"${amount:,.2f}"
    return f"${int(amount):,}"


# ── Data freshness ───────────────────────────────────────────────────

# Map table names to their typical data sources and update cadence.
_DATA_SOURCES: dict[str, dict[str, str]] = {
    "pluto": {
        "source": "NYC DCP PLUTO",
        "cadence": "updated quarterly",
    },
    "rpad": {
        "source": "NYC DOF RPAD",
        "cadence": "updated annually (tentative roll in January, final in May)",
    },
    "acris_legals": {
        "source": "NYC DOF ACRIS",
        "cadence": "updated daily with ~2 week recording lag",
    },
    "acris_real_property_parties": {
        "source": "NYC DOF ACRIS",
        "cadence": "updated daily with ~2 week recording lag",
    },
    "dob_violations": {
        "source": "NYC DOB BIS",
        "cadence": "updated daily",
    },
    "hpd_violations": {
        "source": "NYC HPD",
        "cadence": "updated daily",
    },
    "dob_permits": {
        "source": "NYC DOB NOW / BIS",
        "cadence": "updated daily",
    },
    "ecb_violations": {
        "source": "NYC OATH/ECB",
        "cadence": "updated daily",
    },
    "pad": {
        "source": "NYC DCP PAD (Property Address Directory)",
        "cadence": "updated quarterly",
    },
    "rentstab": {
        "source": "Rent Stabilization Unit Counts (taxbills.nyc)",
        "cadence": "updated annually",
    },
    "hpd_registrations": {
        "source": "NYC HPD Building Registrations",
        "cadence": "updated daily",
    },
    "hpd_complaints": {
        "source": "NYC HPD Complaints and Problems",
        "cadence": "updated daily",
    },
    "hpd_litigations": {
        "source": "NYC HPD Litigations",
        "cadence": "updated monthly",
    },
    "dof_tax_liens": {
        "source": "NYC DOF Tax Lien Sale List",
        "cadence": "updated annually (prior to lien sale)",
    },
    "dof_valuation": {
        "source": "NYC DOF Property Valuation & Assessments (RPAD)",
        "cadence": "updated annually (tentative roll in January, final in May)",
    },
    "dof_exemptions": {
        "source": "NYC DOF Tax Exemptions",
        "cadence": "updated annually with assessment roll",
    },
}


def escape_like(value: str) -> str:
    """Escape LIKE/ILIKE metacharacters in a user-supplied string.

    Replaces backslash, ``%``, and ``_`` with their escaped equivalents so
    that they are treated as literals rather than wildcards in PostgreSQL
    LIKE/ILIKE patterns. PostgreSQL uses backslash as the default LIKE escape.

    Args:
        value: Raw user-supplied string.

    Returns:
        String safe for use as a LIKE pattern fragment.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def data_freshness_note(table_name: str) -> str:
    """Return a human-readable note about the freshness of a data table.

    Args:
        table_name: Internal table/dataset name (e.g., "pluto", "rpad").

    Returns:
        A string like "Source: NYC DCP PLUTO, updated quarterly."
    """
    info = _DATA_SOURCES.get(table_name.lower())
    if info is None:
        return f"Source: NYC Open Data ({table_name})."
    return f"Source: {info['source']}, {info['cadence']}."
