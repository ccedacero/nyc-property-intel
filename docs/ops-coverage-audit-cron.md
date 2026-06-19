# Ops — Weekly data coverage audit (Railway cron)

Makes the landing page's "automated weekly audit" claim actually true. The audit
(`scripts/coverage_audit.py`) compares every local Postgres dataset against the
Socrata source-of-truth and flags drift (DEFICIT / NEVER_SYNCED / FROZEN_SOURCE).

## What ships in the repo

`railway.toml` dispatches the same image to different services by name. A new
case runs the audit:

```
*audit*) PYTHONUNBUFFERED=1 uv run python scripts/coverage_audit.py ;;
```

The audit prints per-dataset JSON lines plus a final greppable summary, e.g.:

```
[coverage-audit] 2026-06-19 ALIGNED=14 MINOR_DRIFT=3 DEFICIT=1 | RED_FLAGS: dob_violations
```

It always exits 0 (repo convention — never crash-loop a cron). The markdown
report it writes to `docs/` is **ephemeral** on a cron container; the retained
**Railway logs are the durable record.** Alert on the `RED_FLAGS:` marker.

## One-time Railway setup (dashboard)

1. **New service** in the same project, from the same repo/image.
2. **Name it so it contains `audit` and NOT `cron`** — e.g. `nyc-property-intel-audit`.
   (Patterns match in order; `*audit*` is checked before `*cron*`. A name with both
   would wrongly run the sync.)
3. **Cron schedule:** weekly — e.g. `0 8 * * 1` (Mondays 08:00 UTC).
   Set it as the service's Cron Schedule (Settings → Cron Schedule).
4. **Env vars** (or shared from the project):
   - `RAILWAY_DB` or `DATABASE_URL` — the production Postgres DSN (required).
   - `SOCRATA_APP_TOKEN` — recommended, avoids Socrata rate limits during the audit.
5. Deploy. Trigger once manually (Deploy → Run now) and confirm the
   `[coverage-audit] … RED_FLAGS:` line appears in the logs.

## Optional follow-ups

- **Alerting:** pipe the `RED_FLAGS:` log line to a Railway log alert / webhook so
  a deficit pages you instead of waiting to be read.
- **Durable history:** if you want each audit kept, have the job write a row to a
  `coverage_audit_runs` table (status counts + timestamp) instead of relying on the
  ephemeral markdown. Small follow-up; not required for the weekly claim to hold.

## Run it ad hoc (no cron)

```bash
RAILWAY_DB=postgresql://… SOCRATA_APP_TOKEN=… uv run python scripts/coverage_audit.py
# or, with the Railway project linked:
railway run --service nyc-property-intel-audit python scripts/coverage_audit.py
```
