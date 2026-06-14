#!/usr/bin/env python3
"""Guard against the /r/<id> relative-asset bug (2026-06-14).

A page served on a MULTI-SEGMENT Vercel rewrite must use ROOT-RELATIVE asset
paths (/css/…, /js/…, /assets/…). Relative paths (css/…) resolve against the
nested directory (/r/) and 404 in the browser — the page renders unstyled and
its JS never loads, even though curl of the HTML source and the API both look
fine.

Keep PAGES in sync with the multi-segment "rewrites" in vercel.json. Today the
only one is /r/:id → /report (report.html). Add any future nested-route page.

Run pre-deploy:  python scripts/check_nested_route_assets.py
Exits non-zero (and prints offending lines) if a nested-route page regresses.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Pages whose rewrite destination is served under a multi-segment source.
PAGES = ["site/report.html"]

# href/src starting with css/ js/ assets/ (relative) — NOT /css, NOT https://, NOT #.
_RELATIVE = re.compile(r'(?:href|src)="(?:css|js|assets)/')


def main() -> int:
    failed = False
    for rel in PAGES:
        path = ROOT / rel
        lines = path.read_text().splitlines()
        hits = [(i + 1, ln.strip()) for i, ln in enumerate(lines) if _RELATIVE.search(ln)]
        if hits:
            failed = True
            print(
                f"ERROR: {rel} is served on a nested route but uses RELATIVE asset paths.\n"
                f"       These 404 on /r/<id> (resolve to /r/css/…). "
                f"Use root-relative: /css/…, /js/…, /assets/…"
            )
            for n, ln in hits:
                print(f"  {rel}:{n}: {ln}")
    if not failed:
        print("✓ nested-route pages use root-relative asset paths")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
