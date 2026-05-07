# Signup stopgap defenses — plan (2026-05-06)

Cheap, reversible bot defenses on the existing Loops signup pipeline. Targets
the verified attack: 80 signups in 14 days, only 19 firing the frontend
`signup_form_submitted` event, 15/19 from two AWS IPs (`52.9.251.8`,
`54.183.143.153`), ~76/80 POSTing directly to the Loops form endpoint with no
JS execution.

## Scope

We do NOT control the Loops form endpoint (`app.loops.so/api/newsletter-form/...`).
All defenses must run on:

1. The HTML form on `nycpropertyintel.com` (catches naive HTML scrapers)
2. The `/webhook/loops` handler in `loops_webhook.py` (catches things visible
   in the contact payload Loops forwards us)

## Decisions per stopgap item

### 1. Honeypot field — SHIP

- **What**: Add `<input type="text" name="phone" tabindex="-1" autocomplete="off" style="position:absolute;left:-9999px;" aria-hidden="true">` to the hero signup form.
- **Webhook reject**: in `loops_webhook.py`, after parsing payload, look at the
  contact dict for a `phone` property. If it's a non-empty string, reject as
  bot (event `signup_rejected_honeypot`, 200 OK so Loops doesn't retry).
- **Why this works**: Loops contact properties accept arbitrary form fields.
  When a user submits the form with `email=...&phone=...`, Loops creates a
  contact with both. A naive scraper that POSTs all visible form fields will
  fill `phone`. A real human can't see the field.
- **What it misses**: bots that POST only `email=...` directly to the Loops
  endpoint (the dominant attack pattern today). Still worth doing — it's free,
  catches the naive subset, and gives us a positive bot-fingerprint signal in
  PostHog so we can size the rest of the threat.
- **False positive risk**: near zero. The field is off-screen and `aria-hidden`,
  so screen readers skip it; `tabindex="-1"` keeps it out of keyboard nav;
  `autocomplete="off"` stops password managers from auto-filling it. Real
  users only fill it if they have very aggressive form-fill addons that
  ignore `aria-hidden` AND `tabindex=-1` AND off-screen positioning — vanishingly
  rare. We log/PostHog every reject so we can spot a false-positive spike.

### 2. Source-check / payload audit — PARTIAL SHIP (with caveat)

- **User's framing**: "reject if Loops contact `source ≠ Form`".
- **Audit result**: `grep -rn "api/v1/contacts" --include='*.py'` → ZERO hits
  in this repo. We never create Loops contacts via API. The only outbound
  Loops API calls are:
    - `loops_webhook.py:225` — `PUT /api/v1/contacts/update` (sets `mcp_token`
      on an existing contact; not a contact-create)
    - `chat.py:228`        — `POST /api/v1/transactional` (sends activation
      email; not a contact-create)
  So no risk of self-inflicted rejection if we filter on `source`.
- **What we ship**: Read `contact.source` from the payload and:
    - Always log it at INFO so we can see the field over a few days of
      production traffic before we tighten the rule.
    - Capture a PostHog property `loops_source` on `signup_form_submitted`
      and the `signup_provisioned` event so we can pivot rejection rate by
      source from the dashboard.
    - Reject with a NEW event `signup_rejected_unexpected_source` ONLY when
      source is one of the explicitly-not-expected values: `API`, `Import`,
      `CSV`, `Manual`. We do NOT reject on missing/empty source — Loops
      docs are inconsistent on whether the field is always present, so
      fail-open on the unknown.
- **What this catches**: signups created via Loops' contacts API (which we
  ourselves never use) or via Loops' CSV/import/manual paths. Probably narrow
  but free.
- **Risk**: if Loops ever changes the constant value of `source` (e.g. sends
  `"form"` lower-case), our `Form` allow-list would falsely pass nothing
  through the new reject and fail-closed nothing — we only reject on a
  known-bad set, not allow-list. So the failure mode is "stops catching
  bots", not "blocks real users". Acceptable.

### 3. Per-IP rate limit — SKIP, substitute time-on-page heuristic

- **Why skipping per-IP rate limit**: We don't control the Loops form
  endpoint. Rate-limiting our own `/webhook/loops` doesn't help because Loops
  servers proxy from a small AWS pool — every signup looks the same. The
  duplicate-email guard (`auth.create_token` returning `created=False`) is
  already the per-email rate limit at the webhook layer.
- **Substitute (per the user's instruction)**: time-since-page-load heuristic.
    - Add a hidden `<input type="hidden" name="form_loaded_at" value="">` to
      the form.
    - In `main.js`, on page load, set `form_loaded_at` to
      `Date.now().toString()`.
    - In the webhook, parse the contact's `formLoadedAt` / `form_loaded_at`
      property as an integer (ms-since-epoch). If it's present AND
      `now - form_loaded_at < 2000ms`, reject as bot
      (`signup_rejected_too_fast`).
    - **Critical**: if the field is missing or unparsable, FALL THROUGH (do
      not reject). This preserves backward-compat for users on cached
      pages, browsers that drop hidden fields, etc. This means the heuristic
      catches bots that scrape and submit our HTML in <2s but doesn't trip
      on direct-API bots — we're trading false-positive risk for no
      coverage of the dominant attack. Acceptable for a stopgap.
- **Why the threshold is 2000ms**: a real human filling one email field
  takes 5+ seconds typically. 2s is conservative — it catches obvious
  scripts while leaving room for fast power-users on broadband.

## Order of checks in the webhook

After signature verification and payload parse, in order (each returns 200
on reject so Loops doesn't retry):

1. Missing email → 400 (existing).
2. `signup_form_submitted` PostHog event (existing, with new `loops_source` prop).
3. **NEW**: honeypot `phone` non-empty → reject (`signup_rejected_honeypot`).
4. **NEW**: `form_loaded_at` present AND fresh < 2s → reject (`signup_rejected_too_fast`).
5. **NEW**: `source` in {`API`,`Import`,`CSV`,`Manual`} → reject (`signup_rejected_unexpected_source`).
6. Existing layer 1: disposable domain.
7. Existing layer 2: MX record.
8. Existing layer 3: brand-prefix.
9. Existing token provisioning + duplicate guard.

The new checks run BEFORE the DNS lookup (which is the slowest existing step)
so we save MX work on rejected signups.

## Test plan

Add to `tests/test_loops_webhook_hardening.py` (reuse existing fixtures):

1. `test_honeypot_field_present_rejects` — payload with `contact.phone="+15551234"` rejected.
2. `test_honeypot_field_empty_allowed` — payload with `contact.phone=""` allowed.
3. `test_honeypot_field_absent_allowed` — payload without `phone` allowed (back-compat).
4. `test_too_fast_form_load_rejected` — `formLoadedAt` set to ~now, rejected as too-fast.
5. `test_form_load_time_old_enough_allowed` — `formLoadedAt` 5s ago, allowed.
6. `test_form_load_time_missing_allowed` — back-compat for no field.
7. `test_form_load_time_unparsable_allowed` — back-compat for garbage value.
8. `test_unexpected_source_api_rejected` — `source="API"` rejected.
9. `test_unexpected_source_import_rejected` — `source="Import"` rejected.
10. `test_form_source_allowed` — `source="Form"` provisioned (positive case).
11. `test_missing_source_allowed` — no source field, fall-through to allow.

Total: 11 new test cases. Each new reject branch covered by both the
positive (rejects bot) and negative (allows real user) test.

## Files touched

- `site/index.html` — add 2 hidden inputs (`phone`, `form_loaded_at`)
- `site/js/main.js` — set `form_loaded_at` on page load; submit it via Loops form POST body
- `src/nyc_property_intel/loops_webhook.py` — 3 new reject branches + log `source` + new PostHog props
- `tests/test_loops_webhook_hardening.py` — 11 new tests

## Reversibility

Each change can be reverted independently:

- **Honeypot**: removing `phone` field from HTML makes the field empty in all
  future Loops contacts → webhook check evaluates falsy → allows. Removing
  the webhook check has no effect on real users (they never had the field
  populated). Safe to revert either side first.
- **Time heuristic**: removing the JS that sets `form_loaded_at` makes the
  field empty/missing → webhook falls through to allow. Removing the
  webhook check makes the JS field a no-op. Safe to revert either side first.
- **Source check**: removing the reject re-allows everything except for the
  3 existing layers. Reversibility = trivial.

## Backwards compatibility evidence

- All new reject branches return 200 OK with `{"ok":true,"skipped":...}` — same shape as existing rejects (see `_reject_200` in `loops_webhook.py:239`). Loops dashboard / monitoring sees no new error rate.
- All new reject branches fail OPEN (allow) on absent/unparsable fields, so existing in-flight Loops payloads — which don't have `phone` or `form_loaded_at` properties yet — continue to provision normally. Verified by tests #3, #6, #7, #11.
- Honeypot field is invisible to humans (off-screen + `tabindex=-1` + `aria-hidden`), so the form looks identical on the live site.
- Source check only rejects on a known-bad allow-list of 4 strings (`API`, `Import`, `CSV`, `Manual`). Loops normal Form-source signups continue through.

## Out of scope (intentionally)

- Server-side per-IP rate limit at our webhook (won't help — see "Decisions" §3).
- Frontend captcha (heavy UX cost, out of scope per "cheap stopgap").
- Migrating off the Loops form endpoint (bigger project; tracked separately).
- Rate limit by email — already covered by `auth.create_token` idempotency
  (`loops_webhook.py:357-370`).

## Branch + PR

- Branch: `feat/signup-stopgap-defenses`
- PR target: `main`
- DO NOT MERGE — review first.
