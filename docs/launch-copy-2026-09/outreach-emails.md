# Outreach — concierge sale (send from your own address)

Plain-text, from Cristian, no marketing template. Lender-framed, low-pressure, explicitly invites the "no" so a soft answer isn't mistaken for interest. Fill the `[bracketed]` bits per recipient (pull the list with the query in `README.md`).

Founding price used below: **$49/mo, locked for life** (i.e. early users keep $49 if you raise it later). The live Stripe link is `https://buy.stripe.com/fZubJ1df070efe92bq4Ja00` — paste it when someone says yes.

---

## Email 1 — to the free building-watchers (send first; highest intent)

**To:** each of the 8 confirmed watchers, individually (not a visible group)
> ✅ SENT 2026-10-06 (v3, after 3 rounds of 2-expert marketing review; both reviewers → SEND). Fixes across rounds: killed the "keeping your alerts running / turning monitoring into a paid plan" bait-and-switch (monitoring STAYS FREE — Pro is sold as *more*); dropped "figure out what to build" + the double opt-out; softened alert-latency wording to detection-truthful ("as soon as it shows up in public records", not filing-day); gave the first-logo (Michael) a value anchor + confident close.

**Subject:** More buildings on NYC Property Intel (founding rate)

> Hi [first name / "there"],
>
> You've been monitoring [their building] on NYC Property Intel — thanks for using it. That stays free; nothing changes there.
>
> I've built a paid tier for people who want more, and because you were an early user I'm offering it at a founding rate, locked for life: **$49/mo** — monitor up to 25 buildings (not just one), get the alert as soon as a new violation or HPD litigation shows up in public records, plus full access to the tool inside Claude Desktop / Claude Code (no daily query cap, no 30-day expiry).
>
> Want the upgrade? Reply "yes" and I'll send a payment link and have you set up today. If the free version covers you, just say so — your free lookups keep working either way.
>
> — Cristian
> NYC Property Intel

**Lender variant** (e.g. `jadderley@opfunding.com`) — add this line just before the CTA:
> If you're underwriting, one alert on a new violation or HPD case before you fund more than covers the year.

and use the more confident close:
> Want the upgrade? Reply "yes" and I'll send a payment link and have you set up today. If now's not the time, a one-line "not yet" is all I need.

**First-logo variant** (e.g. `michael@codefixsolutionsny.com`) — use a fit-appropriate value anchor (not the lender "underwriting" line) plus the confident close:
> If you're keeping tabs on the buildings you work on, catching a new violation as soon as it posts pays for the month.

---

## Email 2 — to the waitlist lead (bespoke)

> Recipient: the one real `pro_monitoring_interest` row (pull it with the query in `README.md` — kept out of git as PII). Fill `[first name]` from the address.

**Subject:** The property you flagged on NYC Property Intel

> Hi [first name],
>
> A while back you asked to be notified when paid monitoring launched — you'd flagged a property in Manhattan. Good news first: the monitoring you waitlisted for is now free for everyone, including that building.
>
> What I built for the folks who raised their hand early is a step up — **$49/mo**, locked for life: track up to 25 buildings instead of one, plus full access to the tool inside Claude (no daily cap, no 30-day expiry), with an alert as soon as a new violation or HPD case shows up in public records.
>
> Want me to turn it on for you? I'll send a payment link and set it up personally. If it's not useful right now, a one-line "not yet" is honestly just as helpful.
>
> — Cristian

---

## Optional Email 3 — recent report-generators (send last, batch)

Cooler audience, but their "yes" counts *more* toward the go/no-go bar because they're less affected by rapport. Pull distinct emails from `shared_reports` (last 30d). Same body as Email 1, swap the opening line:

> You recently pulled a due-diligence report on NYC Property Intel. I'm launching a paid plan and wanted to offer early users a founding rate…

---

### Sending notes
- **CAN-SPAM:** these are 1:1 personal emails from Cristian's own address to existing users — deliberately no postal-address footer (keeps them personal, not a marketing blast; Stripe receipts carry the address). This is low-risk for genuine 1:1 sends. ⚠️ If you ever move to **bulk/scaled** sending, CAN-SPAM requires a physical postal address + a real opt-out in every message — add them back then.
- Send **individually** or BCC — never expose the list.
- Space them out; reply to every response, including the no's.
- If someone says yes: send the Stripe link, and on payment run the provisioning command in `README.md`, then the welcome email.
- Track each in the Google Sheet.
