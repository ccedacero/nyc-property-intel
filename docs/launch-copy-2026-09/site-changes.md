# Site changes — APPLIED (single $49 "Pro" tier)

Decision (2026-09-26): **one paid tier, Pro $49/mo.** The $19 "Watch" tier was dropped (it charged for free monitoring and lost to RegWatch's $15). Live Stripe link: `https://buy.stripe.com/fZubJ1df070efe92bq4Ja00`.

These edits are **already made to the working tree** — they just need review + deploy (`vercel --prod` from `site/`). Nothing is live until you deploy.

## What was changed

1. **`site/js/chat.js`** — `appendChatProProbe` is now a real **Get Pro → $49/mo** CTA linking to the Stripe checkout (was a "$19 … Notify me at launch" painted-door probe). Fires a `pro_checkout_click` PostHog event.
2. **`site/js/report.js`** — `appendProProbe` — same swap, same link.
3. **`site/index.html`**:
   - New **Pricing section** (`#pricing`) before the final CTA: Free vs **Pro $49/mo**, headline "Underwrite deals — and watch your collateral — inside your own Claude," Get Pro button → Stripe link. Uses the site's own `--border`/`--accent` tokens + `btn`/`btn-accent` classes.
   - FAQ "Is NYC Property Intel free?" updated in **both** the JSON-LD (`text`) and the visible `<details>` copy — the "Professional and team plans … in development" line is now "Pro ($49/mo) removes the daily cap and the 30-day expiry …", kept verbatim-matched.

## Guardrails honored
- Canonical coverage phrasing ("20+ datasets from 9 city agencies"). ✅
- Free monitoring ("Watch this building", 25 buildings) is UNTOUCHED — it stays the free funnel. ✅
- No foreclosure/lis-pendens or rent-stabilized claims added. ✅

## Before you deploy / send outreach — the remaining must-haves
1. **Stripe customer portal** on (Settings → Billing → Customer portal → allow cancellation) → paste that portal link into `welcome-email.md`'s `[STRIPE CUSTOMER PORTAL LINK]`.
2. **Postal address** for the email footer (CAN-SPAM) → fill `[POSTAL ADDRESS]` in `welcome-email.md`.
3. **Delete the unused $19 "Watch" product** in Stripe so it can't be reached.
4. `vercel --prod` from `site/` to publish the pricing section + buttons.

## To review the diff before deploying
```bash
cd ~/dev/nyc-property-intel
git diff -- site/js/chat.js site/js/report.js site/index.html
```
