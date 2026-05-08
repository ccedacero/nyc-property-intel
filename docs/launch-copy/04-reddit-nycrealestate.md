# r/nycrealestate post draft

**Where:** https://reddit.com/r/nycrealestate
**Audience:** NYC investors, agents, landlords, prospective buyers
**Format:** "Free tool I built that helps with X" — not a launch post.
**Why this works:** They actively share tools they use. The BiggerPockets crowd reads here too.

## Title (pick one)

Best (problem-led):
> Free tool: check 22 NYC city databases for any address in one query (built it because manual due diligence was killing me)

Alternates:
> Built a free AI tool that pulls DOB / HPD / ACRIS / 311 / sales for any NYC address
> Free: ask AI about any NYC building — open violations, sale history, owner info, complaints

## Body

```
Hey r/nycrealestate — I built this because I was tired of bouncing between
DOB, HPD, ECB, ACRIS, 311, the assessment roll, and PLUTO every time I looked
at a building.

It's a free tool that lets you ask plain-English questions about any NYC
property and pulls the answer from 22+ city databases at once:

  "Open violations on 123 Atlantic Ave Brooklyn?"
  "Has anyone been evicted from 170 W 85th?"
  "Show me sales over $5M in zip 11215 last 12 months"
  "Who owns the building at 350 5th Ave Manhattan?"

Try it without signing up: https://nycpropertyintel.com/chat (3 free queries)

If it's useful, you can sign up for a free trial token (10 queries/day for
30 days) — your token gets emailed automatically.

What it currently surfaces (open-source, MIT licensed):
  - DOB violations + permits + complaints
  - HPD complaints, violations, registrations, litigations
  - ECB violations + penalties
  - ACRIS deeds, mortgages, liens
  - DOF rolling sales + annual sales + assessments
  - 311 complaints (200+ types)
  - FDNY fire incident history
  - NYPD complaints by geo radius
  - Marshal eviction records
  - PLUTO (zoning, FAR, lot area, building class)
  - Rent stabilization registry
  - …and a few more

A few honest things:
  - This is for due diligence, NOT appraisal. It pulls public data; it doesn't
    tell you what a building is worth.
  - Sales data is via DOF (so monthly cadence). Violations are daily.
  - Doesn't do tenant screening — Fair Housing compliant by design.
  - Free for now. Paid tiers (higher daily limits) are coming for pros.

Looking for feedback from active investors / agents / landlords — what would
make this more useful? What data am I missing? What would you pay for?

Happy to demo specific addresses in the comments if anyone wants to test it
on a real building.
```

## Mod-friendly notes

- This sub allows tool/resource posts but they distrust spam.
- Frame this as "I built this for myself, sharing in case it helps."
- DO NOT post the same content in r/AskNYC (auto-removed) or r/realestateinvesting (more general, less NYC).

## Reply templates ready

**"Tried it on [my address]" — good engagement signal:**
> Awesome — happy to walk through what it pulled vs what's missing. DM the address (or BBL) if you want me to run it manually too.

**"Is this just X tool?" / "How is this different from [PropertyShark/NYC Scope/etc.]?"**
> Honestly the closest thing is NYC Scope — they're dashboard-style and fast. This is an AI tool that lets you write "any red flags?" instead of clicking through 8 tabs. It's also free + open-source + you can self-host. Different use case than PropertyShark (which is paid + much more comprehensive but built around a UI).

**"What about Brooklyn / Bronx coverage?"**
> All 5 boroughs, same datasets. The data is from NYC Open Data so wherever NYC tracks something, it's in there.

**"Privacy / who sees my queries?"**
> Email is the only PII collected and only for sending you the trial token. Queries are logged by hashed token (not email) for rate-limiting. Full privacy policy at /legal.html. Self-host has zero telemetry.

## Things to NOT do

- Don't post in 3 NYC subs the same day — pick one
- Don't argue with mods or other users; if it gets removed, message the mods politely
- Don't promote this as a paid tool. Lead with "free."
