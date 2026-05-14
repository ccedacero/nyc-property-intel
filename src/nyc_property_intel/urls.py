"""Deep-link helpers for official NYC property data portals.

Used by tool functions to attach a `verify_url` to their responses so the
LLM can render a "Verify on HPD Online ↗" link next to the data. Builds
user trust — every claim is one click from the source-of-truth city site.

URL patterns verified 2026-05-14 against BBL 3-00275-0022 (123 Atlantic
Ave, Brooklyn). Two portals (ACRIS, DOF Property Tax) are session-gated
and cannot be reliably deep-linked; their helpers return None and the
caller renders a copy-paste hint instead.
"""

from __future__ import annotations


def _split_bbl(bbl: str | None) -> tuple[int, int, int] | None:
    """Return (boro, block, lot) ints, or None if the BBL is malformed.

    Accepts BBLs with or without dashes (e.g. "3002750022", "3-00275-0022").
    """
    if bbl is None:
        return None
    s = "".join(c for c in str(bbl) if c.isdigit())
    if len(s) != 10:
        return None
    try:
        return int(s[0]), int(s[1:6]), int(s[6:10])
    except ValueError:
        return None


def hpd_violations_url(bbl: str | None) -> str | None:
    """HPD Online building page — HPD + ECB violations, complaints, litigation.

    Pattern: https://hpdonline.nyc.gov/hpdonline/building/{bbl10}
    """
    parts = _split_bbl(bbl)
    if not parts:
        return None
    boro, block, lot = parts
    bbl10 = f"{boro}{block:05d}{lot:04d}"
    return f"https://hpdonline.nyc.gov/hpdonline/building/{bbl10}"


def dob_bis_url(bbl: str | None) -> str | None:
    """DOB BIS Property Profile — DOB violations, permits, job filings.

    Note: BIS blocks curl with a 403 (Akamai bot protection) but loads
    fine in real browsers. The URL is the documented canonical form used
    by HPD's cross-links and JustFix.
    """
    parts = _split_bbl(bbl)
    if not parts:
        return None
    boro, block, lot = parts
    return (
        "https://a810-bisweb.nyc.gov/bisweb/PropertyProfileOverviewServlet"
        f"?boro={boro}&block={block}&lot={lot}"
    )


def zola_url(bbl: str | None) -> str | None:
    """NYC ZoLa lot page — zoning, lot dimensions, FAR, landmark status.

    Pattern: https://zola.planning.nyc.gov/lot/{boro}/{block}/{lot}
    """
    parts = _split_bbl(bbl)
    if not parts:
        return None
    boro, block, lot = parts
    return f"https://zola.planning.nyc.gov/lot/{boro}/{block}/{lot}"


def acris_url(bbl: str | None) -> str | None:
    """ACRIS does not support deep-linking.

    Every URL on `a836-acris.nyc.gov` redirects to a bandwidth-policy
    interstitial for cold sessions. Callers should omit the URL or
    render a copy-paste hint via :func:`acris_lookup_hint`.
    """
    return None


def acris_lookup_hint(bbl: str | None) -> str | None:
    """Plain-text hint instructing the user how to look up the BBL on ACRIS."""
    parts = _split_bbl(bbl)
    if not parts:
        return None
    boro_names = {1: "Manhattan", 2: "Bronx", 3: "Brooklyn", 4: "Queens", 5: "Staten Island"}
    boro, block, lot = parts
    return (
        f"To verify on ACRIS: open a836-acris.nyc.gov/CP/ → "
        f"Search Property Records → Borough: {boro_names[boro]} → "
        f"Block: {block} → Lot: {lot}"
    )


def dof_tax_url(bbl: str | None) -> str | None:
    """DOF Property Tax does not support deep-linking.

    The portal forces an ASP.NET disclaimer page that strips query
    parameters. Callers should render a copy-paste hint via
    :func:`dof_tax_lookup_hint`.
    """
    return None


def dof_tax_lookup_hint(bbl: str | None) -> str | None:
    """Plain-text hint instructing the user how to look up the BBL on DOF."""
    parts = _split_bbl(bbl)
    if not parts:
        return None
    boro, block, lot = parts
    return (
        f"To view the property tax bill: open a836-pts-access.nyc.gov → "
        f"accept the disclaimer → enter BBL {boro}-{block:05d}-{lot:04d}"
    )
