# Operational changes log

Running log of significant infrastructure / Railway / config changes outside the application code. Read this if something seems "off" — the change might be recorded here.

For schema or code changes, the git log is the source of truth. This file captures **out-of-repo state changes** that aren't visible from git.

---

## 2026-05-06 — Cost reductions

Reduced monthly Railway bill from ~$65–75/mo → estimated **~$25–35/mo** via four changes.

### Service: `oauth-auth-server` — PAUSED (not deleted)

- **What**: set `sleepApplication: true` via Railway GraphQL `serviceInstanceUpdate`. The container sleeps when idle and wakes on traffic.
- **Why**: PR #1 from 2026-05-05 was on the wrong codebase (the actual signup flow goes through Loops + `loops_webhook.py`, not OAuth). Service was always-on and not used by any production code path. Verified: zero references to it in `nyc-property-intel`'s `src/` and `scripts/`.
- **Caveat**: domain still attached. External probes can briefly wake the container. If ever needed, additionally `domainDelete(id: "fe3dd3de-4943-4190-9cc4-660513b7a353")` to fully isolate it.
- **State preserved**: env vars (PRIVATE_KEY, PUBLIC_KEY, PORT), domain, latest deployment (`bb61f0df…` from 2026-05-01).
- **Rollback**: `mutation { serviceInstanceUpdate(serviceId: "67a33d78-3fe1-49ab-855d-bc195bb87e21", environmentId: "ef3f6030-e181-44c7-9483-df2ea2a75a9a", input: { sleepApplication: false }) }`
- **Estimated savings**: up to $20/mo (depending on traffic patterns hitting the sleep wake-ups).

### Service: `nyc-property-intel-backfill` — DELETED

- **What**: deleted via `serviceDelete` mutation. Project went from 8 → 7 services.
- **Why**: idle since the last manual backfill at 2026-05-05 19:08 UTC. Recreating via GraphQL takes ~30 seconds (we did it twice yesterday) so deletion is fully reversible.
- **Env vars captured before delete** (recreate recipe — all values were literal strings, no secrets-in-secrets):
  - `DATABASE_URL` (postgres internal URL — same as cron services)
  - `SOCRATA_APP_TOKEN`
  - `RESEND_API_KEY`
  - `ALERT_EMAIL_TO=cristian.cedacero@gmail.com`
  - `ALERT_FROM_EMAIL=alerts@nycpropertyintel.com`
  - `BACKFILL_DATASETS=hpd_registrations` (last value)
  - `BACKFILL_RESET=1`
- **Rollback**: `serviceCreate` mutation (project id `20dddcf3-ee9a-4ca3-95fd-4b9f013dd775`, repo `ccedacero/nyc-property-intel`, branch `main`) + 7 `variableUpsert` calls. Full plan in `docs/cost-cuts-plan-svc-ops-2026-05-06.md`.
- **Estimated savings**: ~$2/mo idle baseline.

### Volume: `nyc-property-intel` MCP server volume — PENDING (you do this in Railway UI)

- **What to do**: Railway dashboard → `nyc-property-intel` service → Settings → Volume → resize from **40 GB → 5 GB**.
- **Why**: actual disk usage is ~5 GB (per `du -sh /app/data` at deploy time). 40 GB was over-allocated. Railway charges $0.25/GB-month for volumes regardless of usage.
- **Estimated savings**: ~$8.75/mo ($10 → $1.25).
- **Risk**: if app data ever exceeds 5 GB the service will fail writes. Current usage is ~12% of new size with no growth pattern (the volume is mostly cache, not append-only data).

### Code: Cleanup cron consolidated into weekly cron — PR #20 merged

- **What**: `sync_all.py` now runs `cleanup_idle_tokens()` after the tier-2 weekly sync. Gated to `--tier 2 and not --only`, wrapped in try/except so cleanup errors don't fail the sync.
- **Standalone `nyc-property-intel-cron-cleanup` service** (id `a3ad54c1-ed92-4bed-bfba-dc05c5ee24ce`) is **still alive** with cron `0 4 * * 0`. Both services will run the cleanup logic on Sunday — the standalone job will be a no-op the second time because tokens were already revoked.
- **What you do AFTER next Sunday's run** (verify both work):
  1. Watch logs for `nyc-property-intel-cron-weekly` Sun 03:00 UTC — confirm `running cleanup_idle_tokens (post-sync, tier=2)` line + `summary: revoked=K skipped_internal=M` line
  2. Cross-check DB: `SELECT count(*) FROM mcp_tokens WHERE notes LIKE '%auto-revoked: 21d idle no real calls%' AND revoked_at > NOW() - INTERVAL '24 hours';`
  3. Once verified, delete the standalone cleanup service:
     ```
     curl -X POST https://backboard.railway.com/graphql/v2 \
       -H "Authorization: Bearer $RAILWAY_TOKEN" \
       -H "Content-Type: application/json" \
       -d '{"query":"mutation { serviceDelete(id: \"a3ad54c1-ed92-4bed-bfba-dc05c5ee24ce\") }"}'
     ```
- **Rollback if PR #20 misbehaves**: `git revert <merge-sha>`. The standalone cleanup service is intentionally left alive so revert immediately restores the prior split-services state.
- **Estimated savings**: <$1/mo (mostly hygiene — fewer services).

### Stale Railway "Running 72:55:10" execution — RESET

- **What**: `nyc-property-intel-cron-weekly` UI showed "Running 72:55:10" — was a Railway-side execution-tracking leak (script exited 0 cleanly Sun 2026-05-03 03:01 UTC, container exited, but the `DeploymentInstanceExecution` row never transitioned out of `RUNNING`).
- **Action taken**: `serviceInstanceRedeploy` mutation forced a fresh deployment (`fe87554e-…` SUCCESS at 2026-05-06 04:04 UTC). Cron schedule `0 3 * * 0` preserved.
- **Diagnosis**: `docs/stuck-service-debug-2026-05-06.md`.

---

## Database — does it matter / are we charged extra?

Asked 2026-05-06.

**You have ONE Postgres service** (id `4bc7f241-293d-4401-aa17-bd24c149ddd4`). Not multiple DBs. Confirmed via `railway list-services`. No extra DB billing.

**Database is 94 GB** with 250 GB volume allocated (42.7% used).

**Should you reduce DB size?** Probably not actively, because:
- ~98% of the 94 GB is real product data (sync'd NYC datasets — this IS the product).
- Top tables by size: `nyc_311_complaints` (22 GB), `real_property_parties` (~10 GB), `hpd_violations` (~9 GB), etc.
- Cheaper managed Postgres alternatives (Neon free 0.5 GB / Pro 10 GB; Supabase free 0.5 GB / Pro 8 GB) are 2 orders of magnitude too small for this dataset. Migration would cost MORE.
- **No meaningful savings here without dropping product features.**

**The only DB cost-cut possible** without changing the product:
- Trim very old data (pre-2015 311 complaints, etc.) → cuts ~5 GB but breaks historical lookups
- Drop unused columns (e.g., `geocoded_column`, `lat_lon` redundant with `latitude`/`longitude`) → cuts ~5 GB
- Both touch product/data integrity. Not recommended unless under serious cost pressure.

The volume itself (250 GB allocated) costs you whatever Railway's Postgres tier includes — not directly tied to DB-size growth until you hit the volume cap.
