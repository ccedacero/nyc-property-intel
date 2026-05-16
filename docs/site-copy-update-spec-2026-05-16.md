# Site Copy + Structure Update — Spec (2026-05-16)

Synthesized from 6 parallel research agents (3 site-audit + 3 copy-review) and the ui-ux-pro-max design skill. Applies to `site/index.html` and `site/css/style.css`.

## Why this update

Pre-YouTube launch review surfaced 9 concrete issues across copy, hierarchy, and accessibility. All issues are P0/P1 in scope. Goal: minimum-touch fixes that preserve current visual design while improving conversion, accessibility, and the V1 video positioning alignment.

## Scope (P0 + P1 only)

### P0 — Ship-blockers

| # | Change | Why |
|---|--------|-----|
| 1 | Replace 18 tool card descriptions with user-facing copy | "Always call this first — you need a BBL" is AI/MCP documentation language; end users don't call tools |
| 2 | Add plain-English display names as H3 (snake_case demoted to `<code>` subtitle) | `get_hpd_litigations` as H3 means nothing to a broker; "HPD Lawsuits" does |
| 3 | Update hero H1 to decision-naming tagline | Current "in Minutes, Not Hours" is comparison, not decision-naming |

### P1 — Fix before YouTube traffic

| # | Change | Why |
|---|--------|-----|
| 4 | Restructure personas section into 2 sub-sections | Adds "Renters & Buyers" persona to match V1 video positioning; doesn't dilute pro B2B anchor |
| 5 | Update Hosted install tab copy | Replace "early access" framing (inconsistent with FAQ pricing tiers) |
| 6 | Auto-expand `<details>` on anchor navigation | Hero signup is hidden; Hosted tab anchor link does nothing without manual expand |
| 7 | Add tooltips to NYC RE acronyms on first mention | Bilingual + first-time-buyer audience; teach the vocabulary without dumbing down |
| 8 | Add NY State protected classes to Fair Housing footer | Site claims NYS/NYC compliance but lists federal classes only |
| 9 | Add Glossary footer link (stub) | SEO + bilingual audience signal; supports V1 video viewers becoming power users |

## Voice principles applied

Defined by brand-voice agent. Anchor sentence: *"Check FAR utilization, existing permits, open stop-work orders, and zoning before a letter of intent."*

1. **Name the decision, not the data.** ("before you wire a deposit" / "before you sign a lease")
2. **Second person, present tense, active verbs.**
3. **Specific nouns over category nouns.** (ACRIS deeds, HPD lawsuits — not "regulatory information")
4. **Anti-hype.** Banned: *AI-powered, comprehensive, powerful, seamless, cutting-edge, unlock, empower.*
5. **Source-cite as voice.** "From official NYC public records" used where credibility is load-bearing.

## Acceptance criteria

- [ ] All 18 tool cards have user-facing copy + plain-English H3 + snake_case subtitle
- [ ] No "Always call this first" or "Filter by category, status, and date" language anywhere
- [ ] H1 reads as decision-naming
- [ ] Personas section has 2 sub-headings ("For Professionals" / "For Renters & Buyers") + 5 cards (4 + 1)
- [ ] Hosted install tab does not say "early access"
- [ ] Clicking "Sign up for early access" link from Hosted tab auto-expands the hero signup details
- [ ] First mention of HPD, DOB, ACRIS, BBL, FDNY, DOF, FAR, OATH, PLUTO has `<abbr title="...">` semantic tooltip
- [ ] Footer Fair Housing disclaimer includes sexual orientation, gender identity, source of income (NY State law)
- [ ] Footer contains stub link to NYC Real Estate Glossary
- [ ] No existing functionality broken (forms, tabs, modals, links all still work)
- [ ] Mobile responsive at 375px, 768px, 1024px, 1440px
- [ ] Tab order intact, focus rings visible (WCAG AA)
- [ ] No layout shifts from new tooltip styling

## Out of scope (future)

- Live data freshness badge from API (P2 — needs API endpoint)
- Replace `analyze_property` featured layout with grid integration (P1 — kept featured + added caption instead)
- Replace OG image / meta description hype copy (P2 — separate SEO pass)
- Full glossary page (stubbed link only this pass; create page in follow-up)

## Files changed

- `site/index.html` — tool cards, personas, hosted tab, hero, footer disclaimer, footer links, glossary anchor
- `site/css/style.css` — append: tool-card-pretty-name, tool-name-code, personas-subgroup, acronym tooltip, persona-renters card variant
- Inline `<script>` before `</body>` — `<details>` auto-expand on anchor navigation
- New file: `docs/site-copy-update-spec-2026-05-16.md` (this doc)

## Rollback

`git diff` against the pre-2026-05-16 commit. All changes are content + additive CSS — no destructive structural changes.

## References

- Agent outputs (in conversation log 2026-05-16)
- ui-ux-pro-max skill design intelligence
- `/Users/devtzi/dev/youtube-channel/videos/01-hero-launch.md` (V1 script — positioning alignment)
