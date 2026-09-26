# Outreach — concierge sale (send from your own address)

Plain-text, from Cristian, no marketing template. Lender-framed, low-pressure, explicitly invites the "no" so a soft answer isn't mistaken for interest. Fill the `[bracketed]` bits per recipient (pull the list with the query in `README.md`).

Founding price used below: **$49/mo, locked for life** (i.e. early users keep $49 if you raise it later). The live Stripe link is `https://buy.stripe.com/fZubJ1df070efe92bq4Ja00` — paste it when someone says yes.

---

## Email 1 — to the free building-watchers (send first; highest intent)

**To:** each of the 8 confirmed watchers, individually (not a visible group)
**Subject:** Keeping your building alerts running

> Hi [first name / "there"],
>
> You've been monitoring [N] building[s] on NYC Property Intel — thanks for using it.
>
> I'm turning monitoring into a paid plan, and I want to keep your alerts running without a gap. For people already using it I'm doing a founding rate, locked for life:
>
> **$49/mo** — monitor up to 25 buildings, get an alert the day a new violation or HPD litigation hits public record, and full access to the tool inside Claude Desktop / Claude Code (no more daily query cap, no 30-day expiry).
>
> If you're using this for diligence, it pays for itself the first time it flags a problem on a building before you fund or close.
>
> Want me to keep it on? Reply "yes" and I'll send a payment link and set everything up personally today. And if the free version was all you needed, tell me that too — a one-line "not for me" genuinely helps me figure out what to build.
>
> — Cristian

---

## Email 2 — to the waitlist lead (bespoke)

> Recipient: the one real `pro_monitoring_interest` row (pull it with the query in `README.md` — kept out of git as PII). Fill `[first name]` from the address.

**Subject:** The property you flagged on NYC Property Intel

> Hi [first name],
>
> A while back you asked to be notified when paid monitoring launched — you'd flagged a property in Manhattan. It's ready.
>
> For the folks who raised their hand early I'm doing a founding price, locked for life: **$49/mo** for continuous monitoring — new violations and HPD litigation on any building you track, emailed the day they post — plus full access to the tool inside Claude (no daily cap, no expiry). You can watch up to 25 buildings, not just the one.
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
- **CAN-SPAM:** these are commercial solicitations. 1:1 from your personal address to warm users is low-risk, but if you send at any scale, each must carry a physical postal address, clear sender identification, and a real opt-out (the "not for me" reply is a courtesy, not a compliant unsubscribe).
- Send **individually** or BCC — never expose the list.
- Space them out; reply to every response, including the no's.
- If someone says yes: send the Stripe link, and on payment run the provisioning command in `README.md`, then the welcome email.
- Track each in the Google Sheet.
