# Observability Setup

This doc covers the monitoring stack: **Sentry** (errors), **Better Stack** (uptime), and the in-app **`/healthz`** endpoint.

---

## 1. `/healthz` endpoint (already deployed)

Lives at `https://nyc-property-intel-production.up.railway.app/healthz`. Returns:

| Status | Meaning |
|---|---|
| **200 OK** | App up + DB reachable + every tier-1 dataset synced within 48h |
| **503 Service Unavailable** | DB unreachable OR one or more tier-1 datasets are stale (sync cron broken) |

Response body (success):
```json
{ "status": "ok", "db": "ok", "stale_datasets": [] }
```

Response body (degraded):
```json
{ "status": "degraded", "stale_datasets": ["hpd_violations"] }
```

There's also a simpler `/health` endpoint that only pings the DB — use that for Railway internal health checks if you ever wire those up; use `/healthz` for external uptime monitoring.

---

## 2. Sentry — error tracking

### Setup (one-time)

1. Create a free account at https://sentry.io
2. Create a new project → Platform: **Python** → Framework: **Starlette**
3. Copy the DSN (looks like `https://abc123@o123456.ingest.us.sentry.io/789012`)
4. In Railway → `nyc-property-intel` service → **Variables**, add:
   ```
   SENTRY_DSN=<paste DSN here>
   SENTRY_ENVIRONMENT=production
   SENTRY_TRACES_SAMPLE_RATE=0.1
   ```
5. Railway will auto-redeploy. The Sentry SDK no-ops while `SENTRY_DSN` is empty, so this is safe to roll out before the env var is set.

### What gets captured automatically

- **All unhandled exceptions** in any HTTP handler (chat, signup, MCP tool, webhook).
- **Performance traces** for 10% of requests (`SENTRY_TRACES_SAMPLE_RATE=0.1`). Crank up if you need to debug latency; drop to 0.01 if you blow through the free quota.
- **PII**: emails, IPs, request bodies are **not** sent (`send_default_pii=False`).

### Free tier limits

- 5,000 errors/month
- 10,000 performance units/month
- 30-day retention

At current traffic (~5 customer calls/day) you'll never come close. If you do, set `traces_sample_rate=0.01` first before paying.

### Recommended Sentry config (in their UI)

1. **Alerts → Create Alert Rule** → "Issue alerts" → `WHEN a new issue is created` → notify Slack/email.
2. **Alerts → Create Alert Rule** → "Metric alerts" → `error count > 10 in 5min` → notify.
3. **Settings → Inbound Filters** → enable "Filter out localhost events" and "Filter out browser extensions".

---

## 3. Better Stack — uptime monitoring + status page

### Setup (one-time)

1. Sign up at https://betterstack.com/uptime (free tier)
2. **Monitors → Create monitor** for each row below.
3. **Status pages → Create status page** → name it `NYC Property Intel`, attach all monitors.
4. **Notifications** → add Slack/email/SMS webhook. Default escalation is fine for a solo project.

### Monitor specs

| Name | URL | Method | Expected | Frequency | Notes |
|---|---|---|---|---|---|
| **Landing page** | `https://nycpropertyintel.com/` | GET | `200` | 3 min | Vercel static |
| **Chat page** | `https://nycpropertyintel.com/chat` | GET | `200` | 3 min | Vercel static |
| **API healthz (deep)** | `https://nyc-property-intel-production.up.railway.app/healthz` | GET | `200` AND body contains `"status":"ok"` | 3 min | Pages on stale cron |
| **API health (light)** | `https://nyc-property-intel-production.up.railway.app/health` | GET | `200` | 1 min | Cheap liveness, pages first |
| **Chat signup** | `https://nyc-property-intel-production.up.railway.app/api/chat/signup` | POST | `200` AND body contains `"ok":true` | 5 min | See "POST monitor" below |
| **Loops webhook live** | `https://nyc-property-intel-production.up.railway.app/webhook/loops` | POST | `400` (rejects malformed payload) | 10 min | Confirms route is wired |
| **MCP endpoint** | `https://nyc-property-intel-production.up.railway.app/mcp` | POST | `406` or `200` | 5 min | Without proper Accept header MCP returns 406; that's fine — we just want "responding" |

### POST monitor for `/api/chat/signup`

Better Stack supports POST monitors. Configure:

- **Method:** POST
- **Headers:** `Content-Type: application/json`
- **Request body:**
  ```json
  {"email":"uptime+betterstack@nycpropertyintel.com"}
  ```
- **Expected:** Response body contains `"ok":true`
- **Important:** This will issue a real trial token every 5 minutes. Add `uptime+betterstack@nycpropertyintel.com` to a Loops segment that won't trigger emails, OR rotate the email occasionally.

Alternative: use a HEAD or OPTIONS check on the endpoint — proves routing works without creating tokens.

### Status page

- Add a status page banner: `Status: status.nycpropertyintel.com` → CNAME to Better Stack
- Group monitors: "Web" (landing, chat), "API" (healthz, signup), "Data" (cron freshness via healthz)
- Public URL becomes a trust signal — link from the footer of nycpropertyintel.com

---

## 4. Alerting hierarchy (recommended)

| Severity | Trigger | Notify |
|---|---|---|
| 🔴 **P0** | `/healthz` 503 for >5 min OR `/` returns 5xx | Slack + SMS + email |
| 🟡 **P1** | Sentry: new error type spotted in production | Slack + email |
| 🟡 **P1** | `/healthz` reports `stale_datasets` | Slack + email |
| 🟢 **P2** | Sentry error count > 50/hr | Email only |

---

## 5. Quick verification checklist

After deploying this commit and configuring the dashboards:

- [ ] `curl https://nyc-property-intel-production.up.railway.app/healthz` → 200 with `"status":"ok"`
- [ ] Set `SENTRY_DSN` in Railway → wait for redeploy → trigger a test error (e.g., `curl …/api/chat/signup -d 'not-json'` returns 400, NOT a 500; force a 500 by hitting an unauthed admin route if any)
- [ ] Check Sentry dashboard for the test event
- [ ] All Better Stack monitors green
- [ ] Status page loads at the public URL
