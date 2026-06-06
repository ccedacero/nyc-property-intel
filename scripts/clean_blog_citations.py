#!/usr/bin/env python3
"""One-time cleanup of the pillar blog markdown:
  1. Reframe unverifiable, competitor-sourced anecdotes as clearly illustrative.
  2. Apply outbound-link policy: keep only authoritative gov/major-news
     citations (relabeled with descriptive anchor text); strip links to
     competitors, vendors, and law-firm content blogs (keeping the claim text).
Run once, then rebuild the HTML with build_blog_page.py.
"""
import re
import sys
from pathlib import Path

MD = Path(__file__).resolve().parent.parent / "docs/content-marketing/pillar-blog-nyc-due-diligence.md"
text = MD.read_text()

# ── 1. Anecdote reframes (regex; .*? spans punctuation-heavy middles) ─────────
ANECDOTES = [
    (r'One Flushing two-family carried \*\*\$87,000 in ECB judgments\*\* and the deal died at title\.',
     "A two-family carrying, say, **$87,000 in unpaid ECB judgments** can die at title — the buyer inherits every dollar."),

    (r'A \$1\.4M Upper East Side co-op nearly died.*?with the buyer out roughly \$15,000\.',
     "An attorney can surface undisclosed FDNY standpipe violations on a $1.4M co-op at the eleventh hour; a buyer can turn up a dozen-plus open DOB/HPD violations days before closing — enough to force a multi-week delay, collapse the deal, and burn five figures in sunk legal and inspection fees."),

    (r'### The \$87,000 ECB judgment that died at closing',
     "### How an inherited ECB judgment dies at closing"),

    (r'A Flushing two-family carried \*\*\$87,000 in outstanding ECB default judgments\*\*\..*?in sunk fees \(\[violationwatch\.nyc\]\([^)]+\)\)\.',
     'Picture a two-family carrying **$87,000 in outstanding ECB default judgments**. The title company will not insure over them, the seller cannot pay them off, and the buyer\'s deposit and months evaporate — a "dead deal." And a dead deal is the *good* outcome: at least the buyer did not close. The worse version is a dozen-plus open DOB/HPD violations surfacing days before closing, forcing a multi-week delay that collapses the deal and burns five figures in sunk legal and inspection fees.'),

    (r'In one documented case, a buyer found \*\*14 open DOB/HPD violations days before closing.*?\$15,000 lost\*\* \(\[violationwatch\.nyc\]\([^)]+\)\)\.',
     "A buyer can easily turn up **a dozen-plus open DOB/HPD violations days before closing — enough to trigger a multi-week delay, a deal collapse, and five figures in sunk fees**."),

    (r'A real Flushing two-family carried \*\*\$87,000 in outstanding ECB default judgments\*\* — .*?the deal died \(\[violationwatch\.nyc\]\([^)]+\)\)\.',
     "A two-family carrying **$87,000 in outstanding ECB default judgments**, for example, can be impossible to close: the title company will not insure over them and the seller cannot pay them off."),

    (r'One Flushing two-family carried \$87,000 in outstanding ECB default judgments; the title company refused to insure, the seller couldn.t pay, and the deal died\.',
     "For example, a two-family carrying $87,000 in outstanding ECB default judgments can be impossible to close, because the title company will not insure over them and the seller cannot pay them off."),
]

errors = []
for pat, new in ANECDOTES:
    text, n = re.subn(pat, new.replace("\\", "\\\\"), text, flags=re.DOTALL)
    tag = pat[:48].replace("\\", "")
    print(f"  anecdote [{tag}...]: {n} replaced")
    if n != 1:
        errors.append(pat)

if errors:
    print("\nERROR: these anecdote patterns did not match exactly once:", file=sys.stderr)
    for e in errors:
        print("   ", e, file=sys.stderr)
    sys.exit(1)

# ── 2. Outbound-link policy ──────────────────────────────────────────────────
KEEP = {"nyc.gov", "rentguidelinesboard.cityofnewyork.us", "cbsnews.com", "reinventalbany.org"}
LABELS = [
    ("nyc.gov/site/buildings", "NYC Dept. of Buildings"),
    ("rentguidelinesboard", "NYC Rent Guidelines Board"),
    ("cbsnews.com", "CBS News"),
    ("reinventalbany.org", "Reinvent Albany"),
]

def domain(url):
    return re.sub(r"^https?://(www\.)?", "", url).split("/")[0]

def label_for(url):
    for frag, lab in LABELS:
        if frag in url:
            return lab
    return domain(url)

# Match an optional leading " (" and trailing ")" so parenthetical citations
# can be removed cleanly; inline links collapse to their anchor text.
LINK = re.compile(r"(\s*\()?\[([^\]]+)\]\((https?://[^)]+)\)(\))?")

counts = {"kept": 0, "stripped_paren": 0, "stripped_inline": 0}

def repl(m):
    openp, anchor, url, closep = m.groups()
    d = domain(url)
    if d in KEEP:
        counts["kept"] += 1
        return (openp or "") + f"[{label_for(url)}]({url})" + (closep or "")
    if openp and closep:
        counts["stripped_paren"] += 1
        return ""               # drop the whole " ([anchor](url))"
    counts["stripped_inline"] += 1
    return anchor               # inline link → keep claim text, drop link

text = LINK.sub(repl, text)
print(f"\n  links kept (relabeled): {counts['kept']}")
print(f"  parenthetical citations removed: {counts['stripped_paren']}")
print(f"  inline links de-linked (text kept): {counts['stripped_inline']}")

MD.write_text(text)
print(f"\nWrote {MD}")
