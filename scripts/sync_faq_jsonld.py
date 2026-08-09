#!/usr/bin/env python3
"""Regenerate FAQPage JSON-LD from the *visible* on-page FAQ content.

Google's FAQPage policy requires the marked-up Q&A to match the visible page
content; hand-maintaining the two in parallel drifted on every page (an audit
found divergent answers on all 6 pages and 3 questions that didn't exist on
their pages at all). This script makes the visible DOM the single source of
truth: run it after editing any FAQ and commit the result.

    python3 scripts/sync_faq_jsonld.py

Handles both FAQ patterns used on the site:
  - <details class="faq-item"><summary class="faq-q">Q</summary>
      <div class="faq-a">A</div></details>          (homepage)
  - <h2 id="faq-...">…</h2> followed by <h3>Q</h3><p>A</p> pairs
      (article/tool pages; answer = all <p> until the next h2/h3/hr/aside)
"""

from __future__ import annotations

import html
import json
import pathlib
import re

SITE = pathlib.Path(__file__).resolve().parent.parent / "site"

PAGES = [
    "index.html",
    "nyc-property-due-diligence.html",
    "nyc-eviction-history-search.html",
    "nyc-eviction-record-check.html",
    "hpd-violations.html",
    "dob-violations.html",
    "property-owner-lookup.html",
]


def strip_tags(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    # Inline tags leave stray spaces around punctuation ("( wvxf-dwi5 )",
    # "Class I ,") — AI engines quote this text verbatim, so tidy it.
    text = re.sub(r"\s+([,.;:!?)\]])", r"\1", text)
    text = re.sub(r"([(\[])\s+", r"\1", text)
    return text


def extract_details_faq(doc: str) -> list[tuple[str, str]]:
    out = []
    for m in re.finditer(
        r'<details class="faq-item">\s*<summary class="faq-q">(.*?)</summary>'
        r'\s*<div class="faq-a">(.*?)</div>\s*</details>',
        doc,
        re.S,
    ):
        out.append((strip_tags(m.group(1)), strip_tags(m.group(2))))
    return out


def extract_h3_faq(doc: str) -> list[tuple[str, str]]:
    faq_h2 = re.search(r'<h2[^>]*id="faq[^"]*"[^>]*>|<h2[^>]*>\s*FAQ', doc)
    if not faq_h2:
        return []
    section = doc[faq_h2.end():]
    # FAQ section ends at the next h2, closing aside, or footer
    end = re.search(r"<h2[\s>]|<aside|<footer", section)
    if end:
        section = section[: end.start()]
    out = []
    parts = re.split(r"<h3[^>]*>", section)
    for part in parts[1:]:
        qm = re.match(r"(.*?)</h3>", part, re.S)
        if not qm:
            continue
        question = strip_tags(qm.group(1))
        body = part[qm.end():]
        # Answer = the <p> blocks before the next structural break
        stop = re.search(r"<hr\s*/?>|<blockquote|<aside", body)
        if stop:
            body = body[: stop.start()]
        paras = re.findall(r"<p[^>]*>(.*?)</p>", body, re.S)
        answer = " ".join(strip_tags(p) for p in paras).strip()
        if question and answer:
            out.append((question, answer))
    return out


def sync_page(path: pathlib.Path) -> None:
    doc = path.read_text()
    qas = extract_details_faq(doc) or extract_h3_faq(doc)
    if not qas:
        print(f"  {path.name}: no visible FAQ found — skipped")
        return

    changed = False

    def rebuild(match: re.Match) -> str:
        nonlocal changed
        block = match.group(1)
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            return match.group(0)
        graph = data.get("@graph", [data])
        touched = False
        for node in graph:
            if node.get("@type") == "FAQPage":
                node["mainEntity"] = [
                    {
                        "@type": "Question",
                        "name": q,
                        "acceptedAnswer": {"@type": "Answer", "text": a},
                    }
                    for q, a in qas
                ]
                touched = True
        if not touched:
            return match.group(0)
        changed = True
        rendered = json.dumps(data, indent=2, ensure_ascii=False)
        return f'<script type="application/ld+json">\n{rendered}\n  </script>'

    new_doc = re.sub(
        r'<script type="application/ld\+json">(.*?)</script>',
        rebuild,
        doc,
        flags=re.S,
    )
    if changed:
        path.write_text(new_doc)
        print(f"  {path.name}: FAQPage rebuilt from {len(qas)} visible Q&As")
    else:
        print(f"  {path.name}: no FAQPage JSON-LD block found")


def main() -> None:
    for name in PAGES:
        p = SITE / name
        if p.exists():
            sync_page(p)
        else:
            print(f"  {name}: missing — skipped")


if __name__ == "__main__":
    main()
