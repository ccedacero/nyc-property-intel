# Contributing to NYC Property Intel

Thanks for considering a contribution. This project is maintained by one person, so I lean toward small, focused PRs and clear issue reports over big drive-by rewrites. That said — PRs welcome.

## Ground rules

- **Be kind.** Read the [Code of Conduct](CODE_OF_CONDUCT.md).
- **Don't add data sources that aren't public records.** This project's whole posture (legal, ethical, and technical) rests on every byte tracing back to a NYC/NYS public dataset. No scraped private data, no proprietary feeds, no aggregator middlemen.
- **No demographic data, no tenant-screening data, no protected-characteristic analysis.** See [DISCLAIMER.md](DISCLAIMER.md) §5.
- **No statements in code, comments, or docs that assert character, intent, or wrongdoing of named owners/individuals.** Stick to what the record says.

## What's most welcome

- Bug fixes (data-shape regressions, parsing edge cases, address-resolution failures)
- New tool wrappers around existing nycdb tables you've spotted aren't surfaced yet
- Dataset-coverage additions (open a discussion first — adding a dataset is a 3-step process: nycdb support → loader → tool)
- Test coverage, especially for the geocoding fallback paths and the Socrata clients
- Doc fixes, typos, broken links
- Performance work (the materialized views and indexes can probably go further)

## What I'll likely close

- Cosmetic refactors without behavior change
- New deps for things that can be done with the stdlib or existing deps
- Anything that breaks the MIT-only license posture (no GPL/AGPL deps)
- Anything that introduces ranking, scoring, or recommendations about properties or people

## Setup

See [README.md](README.md#self-hosting). Run the unit tests:

```bash
uv run pytest tests/test_utils.py -q
uv run ruff check src/
```

Integration tests need a loaded local DB:

```bash
uv run pytest tests/ -m integration -q
```

## PR checklist

- [ ] Tests pass: `uv run pytest tests/test_utils.py -q`
- [ ] Lint passes: `uv run ruff check src/`
- [ ] New tools have a docstring matching the format used in `src/nyc_property_intel/tools/lookup.py`
- [ ] No secrets or `.env` values in the diff
- [ ] If you added a dataset, you updated the Data Sources table in README.md and added the agency + as-of cadence
- [ ] PR description explains the user-facing change in one sentence

## Reporting issues

For bugs, please include:
- The query/tool call you made
- The full response (redact anything sensitive)
- Your environment (hosted vs. self-hosted; OS; Python version)
- For data-accuracy issues: the source-of-truth URL (HPDOnline, DOB BIS, ACRIS, etc.) and the as-of date

For security issues, see [SECURITY.md](SECURITY.md) — don't open a public issue.

## License

By submitting a contribution you agree it will be licensed under the same [MIT License](LICENSE) as the rest of the project.
