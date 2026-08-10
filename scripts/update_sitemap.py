#!/usr/bin/env python3
"""Regenerate site/sitemap.xml with <lastmod> derived from git history.

The sitemap was hand-maintained and drifted weeks behind actual page edits
(the flagship pillar advertised a lastmod ~8 weeks stale), which reads as
dormancy to crawlers. Run this before deploying the site:

    python3 scripts/update_sitemap.py

lastmod = the source file's last git commit date, or today if the file has
uncommitted changes. Only indexable pages belong here — /reports, /report,
/watch-confirm, /watch-unsubscribe are noindex by design and must stay out.
"""

from __future__ import annotations

import datetime
import pathlib
import subprocess

REPO = pathlib.Path(__file__).resolve().parent.parent
SITE = REPO / "site"

# (url path, source file, changefreq, priority) — priority/changefreq omitted
# when None, matching the shape the sitemap has always had.
PAGES = [
    ("/", "index.html", "weekly", "1.0"),
    ("/nyc-property-due-diligence", "nyc-property-due-diligence.html", "monthly", "0.9"),
    ("/nyc-eviction-history-search", "nyc-eviction-history-search.html", "monthly", "0.8"),
    ("/nyc-eviction-record-check", "nyc-eviction-record-check.html", "monthly", "0.8"),
    ("/nyc-pied-a-terre-tax", "nyc-pied-a-terre-tax.html", "monthly", "0.8"),
    ("/hpd-violations", "hpd-violations.html", "monthly", "0.8"),
    ("/dob-violations", "dob-violations.html", "monthly", "0.8"),
    ("/property-owner-lookup", "property-owner-lookup.html", "monthly", "0.8"),
    ("/chat", "chat.html", "monthly", "0.9"),
    ("/legal", "legal.html", "yearly", "0.3"),
]

BASE = "https://nycpropertyintel.com"


def lastmod(rel: str) -> str:
    path = SITE / rel
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--", str(path)],
        cwd=REPO, capture_output=True, text=True,
    ).stdout.strip()
    if dirty:
        return datetime.date.today().isoformat()
    committed = subprocess.run(
        ["git", "log", "-1", "--format=%cs", "--", str(path)],
        cwd=REPO, capture_output=True, text=True,
    ).stdout.strip()
    return committed or datetime.date.today().isoformat()


def main() -> None:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for url, rel, changefreq, priority in PAGES:
        lines.append("  <url>")
        lines.append(f"    <loc>{BASE}{url}</loc>")
        lines.append(f"    <lastmod>{lastmod(rel)}</lastmod>")
        if changefreq:
            lines.append(f"    <changefreq>{changefreq}</changefreq>")
        if priority:
            lines.append(f"    <priority>{priority}</priority>")
        lines.append("  </url>")
    lines.append("</urlset>")
    out = SITE / "sitemap.xml"
    out.write_text("\n".join(lines) + "\n")
    print(f"wrote {out} ({len(PAGES)} urls)")


if __name__ == "__main__":
    main()
