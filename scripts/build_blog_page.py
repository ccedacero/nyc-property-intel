#!/usr/bin/env python3
"""Build the static blog/article HTML page from the pillar markdown, matching
the existing NYC Property Intel site template (head meta, nav, footer, schema).

One-off build helper. Requires `markdown` (install into the project venv:
  uv pip install markdown
The rendered output is committed static HTML; markdown is NOT a runtime dep.
"""
import re
import sys
import json
import math
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "docs/content-marketing/pillar-blog-nyc-due-diligence.md"
OUT = ROOT / "site/nyc-property-due-diligence.html"
CANONICAL = "https://nycpropertyintel.com/nyc-property-due-diligence"

raw = SRC.read_text()

# ── 1. Split YAML front-matter ───────────────────────────────────────────────
fm = {}
body = raw
if raw.startswith("---"):
    end = raw.index("\n---", 3)
    fm_block = raw[3:end].strip()
    body = raw[end + 4 :].lstrip("\n")
    key = None
    for line in fm_block.splitlines():
        if re.match(r"^\s*-\s+", line) and key:  # list item
            fm.setdefault(key, [])
            if isinstance(fm[key], list):
                fm[key].append(line.strip()[2:].strip())
        else:
            m = re.match(r"^([A-Za-z_]+):\s*(.*)$", line)
            if m:
                key, val = m.group(1), m.group(2).strip()
                if val == "":
                    fm[key] = []  # a list follows
                else:
                    fm[key] = val.strip('"')

def strip_quotes(s):
    return s.strip().strip('"').strip()

title = strip_quotes(fm.get("title", "NYC Property Due Diligence"))
meta_desc = strip_quotes(fm.get("meta_description", ""))
primary_kw = strip_quotes(fm.get("primary_keyword", ""))
secondary = fm.get("secondary_keywords", [])
if isinstance(secondary, str):
    secondary = [secondary]
keywords = ", ".join([primary_kw] + secondary).strip(", ")
pub_date = strip_quotes(str(fm.get("date", "2026-06-03")))

# ── 2. Extract JSON-LD fenced block → head; remove from body ─────────────────
jsonld = ""
m = re.search(r"```json\s*(\{.*?\})\s*```", body, re.DOTALL)
if m:
    candidate = m.group(1)
    json.loads(candidate)  # validate (raises on bad JSON)
    jsonld = candidate
    body = body[: m.start()] + body[m.end() :]
else:
    print("WARNING: no JSON-LD block found in markdown", file=sys.stderr)

# ── 3. Render markdown → HTML ────────────────────────────────────────────────
md = markdown.Markdown(
    extensions=["extra", "toc", "sane_lists", "smarty"],
    extension_configs={"toc": {"permalink": False}},
)
html_body = md.convert(body)

# ── 4. Post-process HTML ─────────────────────────────────────────────────────
# 4a. CTA anchors (#scan) → the free tool at /chat
html_body = html_body.replace('href="#scan"', 'href="/chat"')

# 4a-bis. Lead-magnet/pricing anchors that have no target yet.
#   The downloadable PDF, sample-report, and pricing pages are future assets;
#   point the checklist CTA at the on-page checklist, the rest at the free tool.
CHECKLIST_ID = "the-complete-nyc-pre-offer-due-diligence-checklist-run-it-as-one-query"
html_body = html_body.replace('href="#checklist-pdf"', f'href="#{CHECKLIST_ID}"')
for dead in ("#sample-report", "#pricing", "#1031-sheet", "#checklist-pdf"):
    html_body = html_body.replace(f'href="{dead}"', 'href="/chat"')

# 4b. Style the primary in-callout CTA links as buttons
#     (markdown rendered **[→ Scan ...](/chat)** as <strong><a ...>)
def buttonize(m):
    href, text = m.group(1), m.group(2)
    return f'<a href="{href}" class="btn btn-primary article-cta-btn">{text}</a>'

html_body = re.sub(
    r'<strong><a href="(/chat)">([^<]*?(?:Scan|scan)[^<]*?)</a></strong>',
    buttonize,
    html_body,
)

# 4c. External links open in a new tab safely
def ext_links(m):
    attrs, href = m.group(1), m.group(2)
    if "nycpropertyintel.com" in href or href.startswith("/") or href.startswith("#"):
        return m.group(0)
    if "target=" in attrs:
        return m.group(0)
    return f'<a {attrs}href="{href}" target="_blank" rel="nofollow noopener noreferrer">'

html_body = re.sub(r'<a ([^>]*?)href="(https?://[^"]+)"', ext_links, html_body)

# 4d. Byline / meta line right after the first </h1>
words = len(re.findall(r"\w+", body))
read_min = max(1, math.ceil(words / 225))
meta_line = (
    f'<p class="article-meta">By <strong>NYC Property Intel</strong> · '
    f'Updated June 3, 2026 · {read_min} min read</p>'
)
html_body = html_body.replace("</h1>", "</h1>\n" + meta_line, 1)

# 4e. Make data tables horizontally scrollable on mobile
html_body = html_body.replace(
    "<table>", '<div class="table-wrap"><table>'
).replace("</table>", "</table></div>")

# ── 5. Assemble full document ────────────────────────────────────────────────
def esc(s):
    return s.replace("&", "&amp;").replace('"', "&quot;")

doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">

  <title>{esc(title)}</title>
  <meta name="description" content="{esc(meta_desc)}">
  <meta name="keywords" content="{esc(keywords)}">
  <meta name="robots" content="index, follow">
  <meta name="author" content="NYC Property Intel">
  <meta name="theme-color" content="#0f172a">

  <!-- Open Graph -->
  <meta property="og:title" content="{esc(title)}">
  <meta property="og:description" content="{esc(meta_desc)}">
  <meta property="og:type" content="article">
  <meta property="og:site_name" content="NYC Property Intel">
  <meta property="og:url" content="{CANONICAL}">
  <meta property="og:locale" content="en_US">
  <meta property="og:image" content="https://nycpropertyintel.com/assets/og-card.png">
  <meta property="og:image:width" content="1200">
  <meta property="og:image:height" content="630">
  <meta property="article:published_time" content="{pub_date}">
  <meta property="article:modified_time" content="{pub_date}">

  <!-- Twitter Card -->
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="{esc(title)}">
  <meta name="twitter:description" content="{esc(meta_desc)}">
  <meta name="twitter:image" content="https://nycpropertyintel.com/assets/og-card.png">
  <meta name="twitter:creator" content="@nycpropertyintel">
  <meta name="twitter:site" content="@nycpropertyintel">

  <link rel="canonical" href="{CANONICAL}">
  <link rel="icon" href="assets/favicon.svg" type="image/svg+xml">
  <link rel="apple-touch-icon" href="assets/apple-touch-icon.png">
  <link rel="stylesheet" href="css/style.css?v=20260603">
  <script src="js/posthog-init.js?v=20260522" defer></script>

  <!-- Article + FAQPage structured data -->
  <script type="application/ld+json">
{jsonld}
  </script>
  <!-- WebPage + Breadcrumb -->
  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@graph": [
      {{
        "@type": "WebPage",
        "@id": "{CANONICAL}#webpage",
        "url": "{CANONICAL}",
        "name": {json.dumps(title)},
        "isPartOf": {{ "@id": "https://nycpropertyintel.com/#website" }},
        "inLanguage": "en-US",
        "datePublished": "{pub_date}",
        "dateModified": "{pub_date}",
        "breadcrumb": {{ "@id": "{CANONICAL}#breadcrumb" }}
      }},
      {{
        "@type": "BreadcrumbList",
        "@id": "{CANONICAL}#breadcrumb",
        "itemListElement": [
          {{ "@type": "ListItem", "position": 1, "name": "Home", "item": "https://nycpropertyintel.com/" }},
          {{ "@type": "ListItem", "position": 2, "name": "NYC Property Due Diligence", "item": "{CANONICAL}" }}
        ]
      }}
    ]
  }}
  </script>
</head>
<body>
  <a href="#article-content" class="skip-link">Skip to main content</a>

  <header class="site-header">
    <nav class="container nav" aria-label="Main navigation">
      <a href="/" class="nav-logo">NYC Property Intel</a>
      <button class="nav-toggle" aria-label="Toggle navigation" aria-expanded="false">
        <span></span><span></span><span></span>
      </button>
      <ul class="nav-links">
        <li><a href="/">Home</a></li>
        <li><a href="#the-complete-nyc-pre-offer-due-diligence-checklist-run-it-as-one-query">Checklist</a></li>
        <li><a href="#faq-nyc-property-due-diligence">FAQ</a></li>
        <li><a href="/chat" class="btn btn-sm btn-accent">Try It Free &rarr;</a></li>
      </ul>
    </nav>
  </header>

  <main class="legal-page article-page" id="article-content">
    <article class="container">
{html_body}
      <aside class="article-cta-box" id="scan">
        <h2>Run a free Risk Scan on any NYC address</h2>
        <p>Skip the 22+ city logins. Enter one address and NYC Property Intel returns a normalized risk profile &mdash; violations, liens, rent-stabilization signals, and compliance exposure &mdash; in one query, with honest data-vintage stamps. First queries are free, no signup required.</p>
        <p><a href="/chat" class="btn btn-primary">Scan an address free &rarr;</a></p>
        <p class="article-cta-fine">Due diligence, not a title search, appraisal, or legal advice. Always verify with the source agency and your professionals before you transact.</p>
      </aside>
    </article>
  </main>

  <footer class="site-footer">
    <div class="container">
      <div class="footer-disclaimer">
        <p>
          <strong>Disclaimer:</strong> NYC Property Intel is provided for
          <strong>educational and informational purposes only</strong>. It does not constitute
          legal, tax, financial, or investment advice. The information presented is sourced
          from NYC public records and may not be current, complete, or accurate. This tool is
          <strong>not a substitute</strong> for a professional title search, property appraisal,
          environmental assessment, or building inspection. Always consult qualified professionals
          (attorneys, licensed appraisers, engineers) before making real estate decisions. NYC Property Intel
          is not affiliated with the City of New York or any city agency.
        </p>
        <p>
          This tool must not be used for property valuations in connection with credit or lending decisions.
          It is not an appraisal tool.
        </p>
        <p>
          All real estate data is subject to the
          <a href="https://www.hud.gov/program_offices/fair_housing_equal_opp/fair_housing_act_overview" target="_blank" rel="noopener noreferrer">Federal Fair Housing Act</a>
          and to New York State and New York City Human Rights Laws.
        </p>
      </div>
      <div class="footer-links">
        <a href="/nyc-property-due-diligence">Due Diligence Guide</a>
        <a href="/legal">Terms of Use &amp; Privacy Policy</a>
        <a href="/legal#fair-housing-policy">Fair Housing Policy</a>
        <a href="https://github.com/ccedacero/nyc-property-intel" target="_blank" rel="noopener noreferrer">GitHub</a>
        <span class="footer-license">MIT License</span>
      </div>
      <p class="footer-copy">&copy; 2026 NYC Property Intel. Not affiliated with the City of New York.</p>
    </div>
  </footer>

  <script src="js/main.js?v=20260530" defer></script>
  <script defer src="/_vercel/insights/script.js"></script>
</body>
</html>
"""

OUT.write_text(doc)
print(f"Wrote {OUT}  ({len(doc):,} bytes)")
print(f"  title:    {title}")
print(f"  words:    {words}  (~{read_min} min read)")
print(f"  keywords: {keywords[:90]}...")
print(f"  json-ld:  {'OK' if jsonld else 'MISSING'} ({len(jsonld)} chars)")
