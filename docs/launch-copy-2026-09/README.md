# Paid launch — concierge playbook (2026-09-25)

Synthesis of a 4-Opus-agent review (offer architecture · demand validation · launch mechanics · competitive positioning). Goal: **charge a real card this week with zero new code, then build billing only if it validates.**

## The decision in one paragraph

Kill the old $19 "Watch" tier — it charges for monitoring, which is free today and is our best funnel, and $19 reads as a consumer toy to a lender. Don't compete on "AI chat" ($15 RegWatch's game we can't win). Sell **"underwrite NYC deals + monitor your collateral inside your own Claude"** — MCP-native, 25 buildings vs. their 1. Launch by **hand-selling at the real price** to the warmest users; build the Stripe integration only after ≥3 real cards.

## The offer

| Tier | Price | DB plan slug | Limit | Who |
|---|---|---|---|---|
| Free | $0 | `trial` | 10/day, expires 30d | the SEO funnel — unchanged, never gated |
| **Pro** | **$49/mo** | `pro` | 500/day, no expiry | solo underwriter / broker |

**Single paid tier at launch** (decided 2026-09-26): one price, lowest decision friction, just under PropertyShark's $59.95. Monitoring (25 buildings) stays free for everyone — it's the funnel, not the product. The paid product is *continuous, unmetered, professional access inside Claude*. Seats/team can be a manual upsell later (mint `--plan team`); not a public tier yet.

**Live Stripe Payment Link:** `https://buy.stripe.com/fZubJ1df070efe92bq4Ja00`

## Why a lender pays us (true today — no foreclosure/lis-pendens claims)

- **MCP-native** — the only NYC DD tool that runs *inside the Claude they already use*. No new portal, no new login. (Headline. Nobody else does this at a sane price.)
- **Monitor 25 buildings** for new violations/litigation — RegWatch caps at **1**. (The proof-point that justifies the price gap.)
- **Plain-English risk narration** across 20+ datasets from 9 city agencies.

Do **not** put foreclosure or lis-pendens anywhere — we don't have that data. Do not claim any unit is rent-stabilized.

## Your steps this week (~3–5 founder-hours)

1. **Stripe account** under your legal entity. Statement descriptor `NYCPROPINTEL`. Settings → Customer emails → turn on receipts for successful payments + refunds.
2. **Payment Link** — Pro $49/mo recurring. ✅ DONE: `https://buy.stripe.com/fZubJ1df070efe92bq4Ja00`
   - On each: **collect customer email** (on by default for subs); add a custom field "Email for your access token"; set the after-payment message to *"You're in — your access token arrives by email within 24h (usually much faster)."*
   - Settings → Billing → **Customer portal** → allow cancellation. Paste that portal link into the welcome email = self-serve cancel, no code.
3. **Notify me on every payment**: Stripe Settings → notifications → successful payments to your inbox.
4. **Get me the two link URLs** → I wire them into the site (button + pricing section) and you `vercel --prod` from `site/`.
5. **Send the outreach** (`outreach-emails.md`) from your address. Pull the recipient list with the query below.

## Manual fulfillment loop (until Phase 2)

On each Stripe payment notification:
```bash
cd ~/dev/nyc-property-intel
# ⚠️ MUST point at prod — manage_tokens.py defaults DATABASE_URL to localhost,
# and a token minted locally will NOT work in the hosted MCP (buyer pays, dead token).
export DATABASE_URL="$RAILWAY_DB"

# ⚠️ If the buyer already has an active token (all 8 watchers + any prior signup do),
# revoke it first — a partial unique index blocks a second active token per email.
uv run python scripts/manage_tokens.py revoke --email BUYER@x.com   # safe if none exists

# Pro ($49/mo):
uv run python scripts/manage_tokens.py create --email BUYER@x.com --plan pro --notes "Pro \$49/mo — Stripe sub_XXXX"
```
Then send the welcome email (`welcome-email.md`) with the token. Log every sale in a Google Sheet: `email · date · plan · token minted Y/N · Stripe sub_id`. This sheet is the source of truth until the webhook exists.

**Weekly reconcile:** check Stripe dashboard vs. sheet; if a subscription was canceled/refunded, revoke:
```bash
uv run python scripts/manage_tokens.py revoke --email BUYER@x.com
```

## Pull the outreach recipient list (run at send time — keeps PII out of git)

```bash
cd ~/dev/nyc-property-intel
# The 8 free building-watchers (highest-intent): email + #buildings
psql "$RAILWAY_DB" -c "SELECT email, COUNT(*) AS buildings, MIN(created_at)::date AS since
  FROM watched_buildings WHERE active AND confirmed GROUP BY email ORDER BY buildings DESC;"
# The 1 real waitlist lead
psql "$RAILWAY_DB" -c "SELECT email, bbl, created_at::date FROM pro_monitoring_interest
  WHERE email NOT LIKE '%test%' AND email NOT LIKE '%example.com%';"
```

## Go / no-go bar (21 days from first send)

- **GO — build the real Stripe webhook + expiry enforcement:** ≥3 unaffiliated cards.
- **HOLD — keep hand-selling, don't build billing:** 1–2 yeses.
- **NO — paid tier is premature:** 0 cards after asking ~12 warm users. Stay free, keep growing SEO/MCP, revisit at ~50 signups / ~20 active watchers.

**Watch for the false positive:** the warm list may say yes out of rapport with you, then cold traffic converts at 0%. Weight an *unaffiliated* report-generator's yes far more than a friendly watcher's.

## Phase 2 — only after ~3 paying cards (see `site-changes.md` for the paywall UX)

Automate on the existing Starlette/Postgres/Railway stack: one `POST /stripe/webhook` (verify signature) → on `invoice.paid` mint/refresh token; on `customer.subscription.deleted` / `charge.refunded` **revoke**; on `invoice.payment_failed` → grace + dunning. Add `stripe_customer_id`, `stripe_subscription_id`, `subscription_status` to `mcp_tokens`; gate token validity on status, not mere existence. Make provisioning idempotent on (email, sub_id) and dedupe webhook events by Stripe event id.

**Top 3 leaks to guard:** (1) paid-but-never-provisioned → reconciliation sheet + payment notifications; (2) token outlives a canceled sub → gate on `subscription_status`; (3) double-charge/duplicate token → idempotent upsert.
