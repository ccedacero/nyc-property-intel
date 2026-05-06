# Pending TODOs — 2026-05-05 (end of long debug+stabilize session)

Captured here so context isn't lost between Claude sessions or working days.

---

## ⚠️ Open question — trial cap (revisit)

The current trial defaults are `daily_limit=50` / `TRIAL_DAYS=14` (PR #16, merged today). Agents recommended this; the user pushed back at end of day.

**Why agents recommended it**: the launch-pricing playbook + revenue-readiness triage flagged the prior `999999` / 30 days as "no friction stacked" — the diagnosis for the 92% activation drop-off.

**Why it might be wrong**:
- The 92% drop-off is people doing **zero** queries — they never hit any limit. Tightening a cap nobody hit doesn't fix the underlying problem.
- We have **no paid tier** to convert to (no Stripe). A trial cap with no paywall = pure friction.
- The Loops webhook bot-mitigation (PR #11) already catches abusive signups *before* a token is issued.

**Existing 33 trial tokens are unaffected** — `daily_limit` is set at token-creation time. Only NEW signups get the new limits.

**Recommended action**: **Revert to `daily_limit=999999` / `TRIAL_DAYS=30`** until activation > 20% AND there's a paid tier to convert to. Reapply when both conditions hold.

**File**: `src/nyc_property_intel/auth.py:41,47`

---

## Bot-vs-real signups — partial answer, more work needed

As of 2026-05-05 22:07 UTC: 54 external signups in last 7 days (3× baseline). 1.9% activation. The Loops webhook mitigation IS blocking obvious bots (no disposable domains in the new cohort). But:

- **Burst pattern**: 6 windows of 3+ cli signups in 60 min. Could be a traffic source clustering visitors OR sophisticated bots cycling real-looking emails.
- **Email patterns look real** (mixed providers: gmail/yahoo/edu/.de/.aol).
- **All zero-activity** — whether real or bot, they're not using the product.

### Verification work to determine bot share

Currently we can't tell bot from real. To resolve:

1. **IP reputation scoring** at signup. Add to `loops_webhook.py`:
   - Capture client IP from `request.headers.get("x-forwarded-for")`
   - Check against AbuseIPDB / IPQualityScore / IPinfo (free tiers exist)
   - Reject scores >75 OR datacenter-origin IPs
   - Cost: zero (free-tier API calls), effort: ~2 hours
   - Reference: `docs/signup-bot-mitigation-options.md` §7

2. **Email verification (double-opt-in)**. Current flow: token issued instantly. Proposed: token issued in `pending_verification` state, user must click link in email to activate.
   - Catches anyone using a real-looking but non-owned email
   - Trade-off: friction; might worsen activation
   - Cost: 1-2 days dev, requires Loops template + new DB column + activation route
   - Reference: `docs/signup-bot-mitigation-options.md` §5 + `docs/launch-playbook-pricing.md` §6

3. **Per-token first-touch event** in PostHog. Already have `signup_form_submitted` (PR #16 fired) + existing `tool_called`. Once we have ~2 weeks of data, compute "median time to first query" per cohort. Real users typically query within 10 min; bots either never or in seconds.

**Recommended next step**: option 1 (IP reputation, free tier) — biggest signal for least friction. Can ship without paid plan in place.

---

## APPROVE-AND-SHIP queue (waiting on yes/no from user)

These came out of the multi-agent panel earlier today. Each is one binary decision + an opinionated default direction.

### A1. Make `/chat` the primary hero CTA on the marketing site
**Default**: Move "Try It Free" / chat surface above "Connect Claude Desktop" / install. Demote the install instructions to a "Graduation" section.
**Why**: Activation panel found the install flow (Node + Claude Desktop + JSON-edit + token-paste + restart) is selecting for developers and bots over the actual ICP (small/mid NYC investors). The `/chat` surface is the same product without install friction.
**Risk**: Reorganizes the homepage story. May confuse existing signups in flight.
**Files**: `site/index.html` lines 200-330 (hero section), `site/css/style.css`
**Effort**: 1-2 hours
**Source**: `docs/triage-activation-2026-05-05.md` §2.1

### A2. Stripe stub schema (no real Stripe account yet)
**Default**: Add `stripe_customer_id`, `stripe_subscription_id`, `subscription_status`, `current_period_end`, `monthly_limit` columns to `mcp_tokens`. Stubbed `/webhook/stripe` route mirroring the Loops webhook pattern.
**Why**: When the user creates a real Stripe account (separate task A3), we can finish the integration in half a day instead of a week. Decouples database/code work from external signup.
**Risk**: Schema migration on `mcp_tokens` — needs alembic migration (currently empty `alembic/versions/`).
**Files**: New `alembic/versions/xxxx_stripe_schema.py`, `src/nyc_property_intel/server.py` route, new `stripe_webhook.py` similar to `loops_webhook.py`
**Effort**: 4-6 hours
**Source**: `docs/triage-revenue-readiness-2026-05-05.md` §2.1

### A3. Update `site/legal.html` with paid-plan terms
**Default**: Draft refunds / cancellation / limit-of-liability / NYC jurisdiction language from the GTM playbook. Mark as "draft pending lawyer review" — but get the structure in place.
**Why**: Can't take real money on the current "free open-source" ToS without exposure.
**Risk**: Minimal — DRAFT label flags it as not-final.
**Files**: `site/legal.html`
**Effort**: 2-3 hours
**Source**: `docs/launch-playbook-gtm-readiness.md` + `docs/triage-revenue-readiness-2026-05-05.md` §2.3

### A4. Better Stack free-tier status page
**Default**: Set up a status page at `status.nycpropertyintel.com` (or subdomain) integrated with the `/healthz` endpoint we already wired today.
**Why**: Need before charging real money — paying customers expect a public uptime signal. Free tier covers current scale.
**Risk**: External vendor dependency. Subdomain DNS config required.
**Files**: External setup (Better Stack UI). Repo update: `site/index.html` link in footer.
**Effort**: 1-2 hours
**Source**: `docs/launch-playbook-gtm-readiness.md` §3

---

## USER-MUST-DECIDE (no agent can resolve)

Strategic decisions that need real product/business judgment from the operator.

### U1. ICP commitment
The activation panel converged on **small/mid NYC multifamily investors (5-50 units, 5-20 deals/month)**. Cuts: not lawyers, not home buyers, not enterprise.
- Confirm or reject.
- Once committed: site copy, ad targeting, partnership outreach all key off this.
- Reference: `docs/launch-playbook-product-activation.md`

### U2. Wedge commitment
Panel proposes: **"Run a full due-diligence report on this address"** — make `analyze_property` (`src/nyc_property_intel/tools/analysis.py:719`) THE product. Demote 17 atomic tools to power-user mode.
- Tied to U1 (the wedge serves the ICP).
- Implementation work flows from this — homepage rewrite, MCP first-touch sample report, etc.
- Reference: `docs/launch-playbook-product-activation.md` §3

### U3. Pricing tier numbers
Panel proposes: Free 14d/50q → **Pro $79/mo** (500 queries) → **Team $249/mo** (2K queries / 5 seats).
- Need actual Anthropic API cost per chat session to validate margin (see U4).
- Could be $99 / $329 if costs are higher.
- Reference: `docs/launch-playbook-pricing.md` §2

### U4. Pull Anthropic API cost number
- Last 30 days of Anthropic console billing for this project ÷ chat-session count from `mcp_usage_log`
- If <$0.15 avg: $79 Pro tier holds margin
- If $0.20-0.30: bump Pro to $99 or drop included quota
- Pricing decisions can't be locked without this data point.

### U5. Stripe account creation (real one)
- Entity name, tax ID, bank account, Stripe-Tax decision
- Hard blocker: only the operator can do this. No engineering work removes the requirement.
- A2 (stub schema) is the codebase prep for the day this lands.

### U6. Closed-beta cohort
- Hand-recruit 5-10 ICP-fit users for a 90-day Pro-free beta. Not the existing 25+ mixed-quality signups.
- 4-6 week timeline before public paid launch.
- Reduces the existential "stale data caused a wrong decision" reputation risk for due-diligence products.
- Reference: `docs/launch-playbook-gtm-readiness.md` §1

---

## ⏰ Time-sensitive reminders

### Sunday 2026-05-10 (after 04:30 UTC) — delete `nyc-property-intel-cron-cleanup` service

**Why**: PR #20 (merged 2026-05-06) consolidated the idle-token cleanup logic into the weekly tier-2 sync. The standalone `nyc-property-intel-cron-cleanup` service (id `a3ad54c1-ed92-4bed-bfba-dc05c5ee24ce`, schedule `0 4 * * 0`) is kept alive as a safety net until the consolidated path proves itself in production.

**When**: After Sunday morning 2026-05-10:
- 03:00 UTC: weekly cron fires the consolidated sync+cleanup
- 04:00 UTC: standalone cleanup-cron fires (becomes a no-op by design)
- 04:30 UTC onwards: safe to delete the standalone

**Pre-delete verification**:
```
psql "$RAILWAY_DB" -c "
SELECT count(*) FROM mcp_tokens
WHERE notes LIKE '%auto-revoked: 21d idle no real calls%'
  AND revoked_at > NOW() - INTERVAL '24 hours';
"
```
Any non-error response (even 0) = consolidated path worked.

**Delete command**:
```
TOKEN=$(python3 -c "import json; print(json.load(open('/Users/devtzi/.railway/config.json'))['user']['token'])")
curl -X POST https://backboard.railway.com/graphql/v2 \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"query":"mutation { serviceDelete(id: \"a3ad54c1-ed92-4bed-bfba-dc05c5ee24ce\") }"}'
```

**Savings**: <$1/mo (hygiene only — one less service on the dashboard).

### Today/Tomorrow — Resize or delete `postgres-volume` on `nyc-property-intel` via Railway UI

**Why**: 40 GB orphan volume mounted on the MCP server (the actual Postgres has its own separate volume). Production code has zero references. See `docs/operational-changes-log.md` for full diagnosis.

**Where**: Railway dashboard → `nyc-property-intel` service → Settings → Volumes → `postgres-volume` (40 GB / 5 GB used)

**Recommended action**: detach + delete OR resize to 5 GB.

**Savings**: ~$8.75–10/mo.

---

## Other deferred items (not P0/P1 but worth tracking)

- **`scripts/run_backfill.py`** auto-deploy issue: any merge to main redeploys the backfill service and kills in-flight runs. Configure the backfill service in Railway UI to NOT auto-deploy, OR move backfill code to a separate repo. Not urgent now (no scheduled backfills running).
- **Alembic migrations**: configured but unused — real migrations are SQL files in `scripts/migrations/`. Future foot-gun. Should adopt alembic before A2 (Stripe schema) introduces a real migration.
- **`tests/test_security.py`** has a pre-existing import error (`_BearerTokenMiddleware` doesn't exist). Either fix or delete.
- **PR #1 (auth-server signup hardening)** — wrong codebase per investigation; deferred indefinitely. Either close the PR or note the deferral.
- **Weekly automated coverage/null audit cron** (P1 from `docs/roadmap-2026-05-05.md`) — the `_coerce` bug hid for weeks because nothing watched. Recommend a 4th cron service running `coverage_audit.py` + `column_null_audit.py` weekly with email diff.
- **Sentry/PostHog env-var verification** — code is wired but env-var presence in Railway prod was never confirmed. Run a quick smoke check.
- **Cleanup-cron service first-run verification** — created today via GraphQL with cron `0 4 * * 0`. Confirm it actually runs at the scheduled time and emails the summary.

---

## Strategic playbook docs (reference)

These docs were written by agent panels today. Keep them as reference, not as binding decisions:

- `docs/launch-playbook-product-activation.md` — ICP/wedge/activation
- `docs/launch-playbook-pricing.md` — pricing tiers, billing infra, unit economics
- `docs/launch-playbook-gtm-readiness.md` — distribution, launch sequence, SLA, support
- `docs/triage-tactical-2026-05-05.md` — engineering priorities (most done today)
- `docs/triage-activation-2026-05-05.md` — activation funnel priorities
- `docs/triage-revenue-readiness-2026-05-05.md` — revenue-blocker priorities
- `docs/sync-status-audit-2026-05-05.md` — independent DB audit
- `docs/remediation-plans-2026-05-05.md` — fix plans (mostly done today)
- `docs/roadmap-2026-05-05.md` — forward-looking roadmap
- `docs/known-issues.md` — accepted limitations (dobjobs, hpd_registrations)
- `docs/signup-bot-mitigation-options.md` — bot mitigation playbook

---

## Today's wins (so we don't lose track of what's already shipped)

- ✅ `_coerce` date-format bug fixed → 3 datasets recovered (dob_complaints, dobjobs, dob_violations)
- ✅ `column_map` fix → 3 NEVER_SYNCED datasets started ingesting (nyc_311, fdny, nypd)
- ✅ asyncpg timeout 120s → 600s (big-table UPSERT)
- ✅ Per-page cursor in backfill mode + 38-min retry budget (PR #12)
- ✅ Loops webhook bot mitigation (3-layer: disposable + MX + heuristic)
- ✅ Spam token cleanup (5 retroactively revoked)
- ✅ Tier-3 cron service created via GraphQL + cron schedule set
- ✅ Cleanup cron service created via GraphQL + weekly schedule
- ✅ Healthcheck `/healthz` configured → zero-downtime deploys
- ✅ hpd_litigations 3 NULL columns fixed (boro, openjudgement, findingdate)
- ✅ nypd_crime_complaints 94% missing → 0.20% drift (incremental resume)
- ✅ Promoted nyc_311_complaints from tier-2 to tier-1 (PR #6)
- ✅ Recovery script `scripts/recover_by_pk.py` for any future Socrata `$offset` tie issue
- ✅ Coverage audit `KNOWN_FROZEN_SOURCE` set (dobjobs + hpd_registrations) — accepted limitations don't show as red
- ✅ `signup_form_submitted` PostHog event — funnel analytics now possible

**21 of 23 datasets ALIGNED, 2 KNOWN_FROZEN_SOURCE accepted, all sync issues resolved.**
