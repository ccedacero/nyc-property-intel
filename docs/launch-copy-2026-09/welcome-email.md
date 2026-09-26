# Welcome / fulfillment email — send after provisioning a paid token

Send this by hand right after `manage_tokens.py create` succeeds. Includes the CAN-SPAM essentials (physical postal address, clear identification) and the self-serve cancel link.

**⚠️ Before first send, fill this placeholder:**
- `[TOKEN]` — the `nyprop_…` token printed by the provisioning script.

Locked in: postal address (CAN-SPAM) ✅ and portal link ✅ — see the footer / billing line below.

---

**Subject:** Your NYC Property Intel access is live

> Hi [first name],
>
> You're set up. Here's your access token:
>
> **`[TOKEN]`**
>
> **Two ways to use it:**
>
> 1. **In the browser** — go to nycpropertyintel.com/chat and paste the token when prompted. Your daily cap and the 30-day expiry are gone; ask away.
> 2. **Inside Claude Desktop / Claude Code (recommended)** — add the hosted MCP server and paste this token in your config. Two-minute setup here: https://nycpropertyintel.com/#install (use the "Hosted" tab). Then you can pull violations, liens, evictions, tax, ownership, and permits on any NYC property by just asking, right in your workflow.
>
> **Monitoring:** look up a building in the chat and hit "Watch this building" — you'll get an email the day it picks up a new violation or HPD litigation. You can watch up to 25 at once.
>
> **Billing & cancellation:** you're on the founding rate ($49/mo), locked for life. Manage or cancel anytime here: https://billing.stripe.com/p/login/fZubJ1df070efe92bq4Ja00. **Full refund within 14 days, no questions** — just reply.
>
> Reply to this email anytime — it comes straight to me.
>
> — Cristian
> NYC Property Intel
> 418 Broadway, STE R, Albany, NY 12207

---

### Seats (manual upsell, not a public tier)
If a buyer needs 2–3 people, provision each seat's email as `--plan team` and set a price by hand (there's no public seats link yet). Change the token line note to *"…and up to 3 people on your team can each use it."* Note it in the sheet.
