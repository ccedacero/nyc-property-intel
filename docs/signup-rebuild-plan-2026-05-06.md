# Signup Rebuild — Implementation Plan

**Status:** implementation in progress (branch `feat/signup-rebuild`).
**Date:** 2026-05-06
**Author:** Claude (implementation agent)
**Scope:** ship Phase A + Phase B + Phase C of the signup-bot architecture
in `signup-bot-architecture-2026-05-06.md`. Closes the door on the public
Loops form ID being the load-bearing ingress for new signups, while keeping
the legacy webhook live as a forensic tripwire for the transition window.

This document is the **plan only** — the actual implementation lives in the
PR opened from this branch. If you find yourself reading this without the
PR open, the PR is the source of truth.

---

## 1. Goals & non-goals

### Goals

1. The `Get Access Token` form on the homepage stops POSTing to
   `https://app.loops.so/api/newsletter-form/cmntqdkqy00y20iycvyyxby0m` and
   instead POSTs to a backend endpoint we own (`POST /api/signup`).
2. The homepage hero leads with **`Try It Now — Free →`** going to `/chat`,
   not the Loops form. The Loops form (now backed by `/api/signup`) is
   demoted below the fold under a `For Claude Desktop power-users` heading.
3. The new `/api/signup` endpoint runs **all existing anti-bot checks**
   (disposable domain, MX, brand-prefix heuristic, IP rate limit) before
   issuing any token, and uses the **chat-style magic-link pattern** for
   double-opt-in activation.
4. The legacy `/webhook/loops` route stays live and continues to honour
   real Loops payloads — but every hit is now logged as a forensic event
   (`signup_via_legacy_webhook`) so the user can see exactly who is still
   POSTing directly to Loops.
5. Every existing test continues to pass, every new code path has a test,
   and the deploy is fully reversible (form action is a one-character env
   change away from rolling back).

### Non-goals (deferred)

- Cloudflare Turnstile enforcement. The new endpoint is **Turnstile-ready**
  (env-var stub `SIGNUP_REQUIRE_TURNSTILE` defaulting to `false`) but does
  not validate the token. Cloudflare account setup is a separate step.
- Deleting `/webhook/loops`. Per the architecture doc, week 3 of the
  rollout is when we delete it; this PR only covers weeks 1-2 of that
  rollout.
- Changing `mcp_tokens.status` semantics or the chat magic-link flow.
  `chat.py:signup_handler` and `/api/activate` continue to work exactly
  as today.
- Email-template content / Loops dashboard automation rewiring. We reuse
  the existing `LOOPS_CHAT_TRANSACTIONAL_ID` template for the activation
  email so no Loops-side changes are required to ship Phase A.
- Deploying the cutover. The user reviews this PR and merges when ready.

---

## 2. Phase-by-phase plan

Each phase is its own commit. Tests in each phase MUST pass before the
next phase is started. The PR is opened only after all three phases pass.

### Phase A — `POST /api/signup` backend endpoint

**Files touched:**

| File | Change |
|---|---|
| `src/nyc_property_intel/loops_webhook.py` | Helpers (`_split_email`, `domain_has_mx`, `is_brand_prefix_suspicious`, `is_disposable_domain`) imported by the new endpoint — no extraction needed; they are already top-level functions. |
| `src/nyc_property_intel/chat.py` | Add a new `make_signup_endpoint_handler(auth)` factory next to `make_chat_handlers`. Reuses `_create_magic_link`, `_send_activation_email`, `_get_client_ip`, `_check_signup_ip_rate_limit`, `_normalize_email`, and `_EMAIL_RE`. Returns a Starlette handler. |
| `src/nyc_property_intel/server.py` | Mount `Route("/api/signup", api_signup_handler, methods=["POST"])` in the streamable + sse branches. Wire it next to the existing `/webhook/loops` route. |
| `src/nyc_property_intel/config.py` | Add `signup_require_turnstile: bool = False` and `signup_turnstile_secret: str = ""` env-var stubs (NOT enforced yet). Documented in the PR description. |
| `tests/test_signup_endpoint.py` (new) | Cover happy path, every rejection branch, IP rate-limit, malformed JSON, missing email, MX failures (mocked), disposable domain, brand-prefix heuristic, duplicate email, DB error. |

**Endpoint contract** (`POST /api/signup`):

- **Request:** JSON `{ "email": "<addr>", "hp_field"?: "", "started_at_ms"?: 1234 }`
  - `hp_field` and `started_at_ms` are stub fields (Turnstile-ready); we
    accept and (mostly) ignore them in this phase. `hp_field` IS enforced
    today: a non-empty value silently drops the request. Documented in
    code so Phase D can wire `started_at_ms` and Turnstile properly.
- **Response (success):** `200 {"ok": true}` — same shape that the
  homepage form's success branch already expects.
- **Response (validation failure):** `400 {"error": "Invalid email"}`
- **Response (rate limit):** `429 {"error": "Too many requests"}`
- **Response (server error):** `500 {"error": "Service error"}`

**Why this contract:**

- We standardize on `data.ok === true` (matches `/api/chat/signup`,
  `/api/activate`, `/webhook/loops`). The frontend diff is one line.
- We keep `200 OK` as the success response (not `202`) so the frontend's
  `res.ok` check works.
- Validation errors return `400` with descriptive `error` so the user can
  see what went wrong (per spec: "descriptive error").

**Anti-bot stack (executed in order, fail-fast):**

1. Validate JSON parses → `400` if not
2. Honeypot stub: if `hp_field` is non-empty → silent `200 ok:true`
3. Per-IP rate limit (`_check_signup_ip_rate_limit`, 3/hour, reused
   verbatim from `chat.py`) → `429` if hit
4. Validate email shape (`_EMAIL_RE`, length ≤ 254) → `400` if invalid
5. Disposable domain check (`is_disposable_domain`) → `200 {"ok": true}`
   silently dropped + PostHog event `signup_rejected_disposable`
6. MX lookup (`domain_has_mx`, fail-open on transient) → silently dropped
   + PostHog event `signup_rejected_mx`
7. Brand-prefix heuristic (`is_brand_prefix_suspicious`) → silently
   dropped + PostHog event `signup_rejected_heuristic`
8. Duplicate-detection: if `auth.create_token` returns `created=False`,
   fire fresh magic link to a freshly issued token (matches `chat.py`
   re-signup behaviour: revoke existing tokens, issue a new one)
9. Issue token + magic link + send activation email
10. PostHog event `signup_provisioned` (with `source="api_signup"`)

**Why "silently dropped" with `200 ok:true` for the bot rejections:**

- Aligns with the existing `/webhook/loops` handler's `_reject_200`
  pattern — bots can't oracle success vs. failure from response shape
  alone.
- Aligns with the architecture doc §1.6: "A `/api/signup` POST that
  returns 200 OK regardless of outcome (so success vs. failure isn't
  oracle-able)".
- A real user who happens to type a disposable address sees "Check your
  email" and won't get one. They re-submit with a real address.
  False-positive rate on these checks is documented as low.

**Why we DON'T extract a shared helper module:**

The architecture doc Appendix A suggests extracting helpers into a new
`signup.py`. After re-reading the existing code, the cleaner cut is:

- Helpers that are **pure functions** of email/domain
  (`is_disposable_domain`, `is_brand_prefix_suspicious`, `domain_has_mx`,
  `_split_email`) **stay in `loops_webhook.py`** and are imported by the
  new endpoint. They're already module-level functions — no extraction
  needed, just import.
- Helpers that are **chat-flow specific** (`_create_magic_link`,
  `_send_activation_email`, `_check_signup_ip_rate_limit`, `_get_client_ip`,
  `_normalize_email`, `_EMAIL_RE`) **stay in `chat.py`**. The new
  signup handler is a *peer* of the chat signup handler, not a refactor
  of it.
- The new `make_signup_endpoint_handler(auth)` factory lives in `chat.py`
  alongside `make_chat_handlers`. This keeps the magic-link issuance and
  email-sending code colocated with the path that already uses it.

This avoids creating `signup.py` for what would be ~30 lines of glue
code, and avoids touching `loops_webhook.py`'s tested code paths.

### Phase B — homepage hero rewrite

**Files touched:**

| File | Change |
|---|---|
| `site/index.html` | Restructure hero. Lead with `Try It Now — Free →` linking to `/chat` (already there as `chat.html`, route via `vercel.json` cleanUrls). Demote the Loops form to a `For Claude Desktop power-users` collapsible section under the hero CTA. Form `action` becomes the Railway `/api/signup` URL. |
| `site/js/main.js` | Replace direct fetch to `https://app.loops.so/api/newsletter-form/...` with fetch to the Railway backend `/api/signup`. Send JSON, not form-urlencoded. Read `data.ok` (not `data.success`). Delete `LOOPS_FORM_ID` constant. |
| `vercel.json` | Update CSP `form-action` to allow the Railway backend (`https://nyc-property-intel-production.up.railway.app`) instead of (or in addition to) `https://app.loops.so`. `connect-src` already lists Railway, so the JS fetch already works. |

**Markup sketch (rough):**

```html
<section class="hero">
  <div class="container">
    <h1>NYC Property Due Diligence in Minutes, Not Hours</h1>
    <p class="hero-sub">
      Ask about any NYC property in plain English. Get violations, sales,
      liens, permits, rent stabilization, and ownership records from
      <strong>20+ official NYC databases</strong> — no spreadsheets, no
      browser tabs.
    </p>

    <!-- DOMINANT primary CTA — single button -->
    <div class="hero-actions hero-actions-primary">
      <a href="/chat" class="btn btn-primary btn-xl">
        Try It Now — Free →
      </a>
    </div>
    <p class="hero-note">
      No credit card · No signup for first 3 queries · Open source (MIT)
    </p>

    <div class="hero-actions-secondary">
      <a href="#how-it-works" class="link-subtle">See how it works ↓</a>
      <span class="hero-note-sep">·</span>
      <a href="https://github.com/ccedacero/nyc-property-intel"
         class="link-subtle" target="_blank" rel="noopener noreferrer">
         View source on GitHub
      </a>
    </div>

    <!-- DEMOTED — power-user path -->
    <details class="hero-power-user" id="hero-signup-form-wrapper">
      <summary class="hero-power-user-summary">
        For Claude Desktop / Claude Code power-users →
      </summary>
      <div class="hero-power-user-body">
        <p>
          Connect via the MCP protocol from your local Claude Desktop or
          Claude Code install. Get a token by email, paste it into your
          MCP config, you're done.
        </p>
        <form
          class="hero-signup"
          id="hero-signup-form"
          action="https://nyc-property-intel-production.up.railway.app/api/signup"
          method="POST"
          novalidate
        >
          <div class="hero-signup-fields">
            <input type="email" name="email" id="hero-email"
                   class="signup-input" placeholder="your@email.com"
                   autocomplete="email" required
                   aria-label="Email address">
            <button type="submit" class="btn btn-primary signup-btn">
              Email me a token
            </button>
          </div>
          <p class="signup-error" id="hero-signup-error"
             role="alert" aria-live="polite"></p>
        </form>
        <div class="hero-signup-success" id="hero-signup-success" hidden>
          <p class="signup-success-msg">✓ Check your inbox — your token
             and setup instructions are on the way.</p>
        </div>
      </div>
    </details>
  </div>
</section>
```

**Rationale:**

- Single dominant primary button — no "two roughly equal CTAs" tension.
- `<details>` collapsible keeps the Loops/MCP path discoverable to
  power-users who scroll without dominating the hero for the casual
  visitor whose journey is one click → web chat.
- Form `action` URL is the absolute Railway URL. We do *not* use a
  same-origin path because the homepage is on Vercel
  (`nycpropertyintel.com`) and the backend is on Railway. No new infra
  needed.
- `chat.html` is already linked and `/chat` resolves via `cleanUrls` in
  `vercel.json` — no router change.
- `final-cta` section at the bottom of the page also gets the same
  treatment: primary CTA = `/chat`, the existing "Get Your Access
  Token →" link still anchors to the (now-collapsed)
  `#hero-signup-form-wrapper`.

### Phase C — instrument legacy `/webhook/loops`

**Files touched:**

| File | Change |
|---|---|
| `src/nyc_property_intel/loops_webhook.py` | At the top of the handler (after signature + payload validation), fire a PostHog event `signup_via_legacy_webhook` with `{"email": email, "ts_ms": <unix-ms>, "user_agent": "<UA>"}`. This is the forensic tripwire promised in the architecture doc §3. |
| `tests/test_loops_webhook_hardening.py` | Add a test that asserts the new forensic event fires on a successful Loops webhook hit. |

**What we explicitly do NOT do:**

- We do NOT delete the route. The architecture doc is clear: that's a
  Week 3 step, after telemetry confirms zero legitimate traffic.
- We do NOT add "tripwire mode" (return 200 OK, write nothing). That's
  a Week 2 step. For now, the webhook continues to provision tokens for
  whatever's still hitting it, AND fires the new forensic event so the
  user can see who.

**Why it's safe to keep the webhook fully functional in this PR:**

- The bots that POST directly to the Loops form ID still arrive via
  Loops's `contact.created` webhook signed with our `LOOPS_WEBHOOK_SECRET`.
  Our webhook applies all hardening (disposable / MX / brand-prefix) and
  logs the rejection. No new attack surface from leaving it on.
- A 7-day grace window per the user's spec is satisfied by leaving the
  webhook intact and deferring removal to a follow-up PR. The forensic
  event tells us when it's safe to remove.

---

## 3. What gets reused vs. duplicated

| Capability | Source of truth | Reused by `/api/signup`? |
|---|---|---|
| Disposable-domain blocklist | `loops_webhook._CUSTOM_DISPOSABLE` + `disposable_email_domains` lib | Yes — direct import of `is_disposable_domain` |
| MX lookup with timeout | `loops_webhook.domain_has_mx` | Yes — direct import |
| Brand-prefix heuristic | `loops_webhook.is_brand_prefix_suspicious` | Yes — direct import |
| Email split | `loops_webhook._split_email` | Yes — direct import (leading underscore but module-level; safe to import) |
| Magic-link DB row | `chat._create_magic_link` | Yes — direct call |
| Activation email send | `chat._send_activation_email` | Yes — direct call. **Reuses `LOOPS_CHAT_TRANSACTIONAL_ID`.** Email goes to `/chat?t=<uuid>` (the chat magic-link page), which already correctly stores the token and unlocks the chat. |
| IP rate limit (3/hr) | `chat._check_signup_ip_rate_limit` | Yes — direct call |
| Client IP extraction | `chat._get_client_ip` | Yes — direct call |
| Email validation regex | `chat._EMAIL_RE` | Yes — direct import |
| Email normalization | `chat._normalize_email` (re-export of `auth.normalize_email`) | Yes — direct call |
| Token issuance | `auth.TokenAuth.create_token` | Yes — passed in via factory |
| Re-signup token rotation | `chat.signup_handler` lines 489-516 | **Pattern duplicated** with comment marking it as such; both flows need the same revoke-then-issue behaviour, but extracting a shared helper would force a refactor of `chat.signup_handler` mid-PR. Documented as a follow-up cleanup. |

**The one thing that's duplicated**: the re-signup "revoke existing tokens
+ issue fresh one" code from `chat.py:signup_handler`. This is ~25 lines.
We accept the duplication in this PR and flag it as a follow-up because:

1. Extracting it cleanly requires touching `chat.py:signup_handler`,
   which would make the diff harder to review and increase blast radius.
2. The duplicated code is purely DB-shape glue, not business logic that
   could drift.
3. The follow-up extraction is mechanical (`_create_or_rotate_token(pool, email)`).

---

## 4. What gets deleted vs. deprecated

| Item | This PR's action |
|---|---|
| `LOOPS_FORM_ID` constant in `site/js/main.js` | **Deleted** — no longer referenced |
| Direct `fetch("https://app.loops.so/api/newsletter-form/...")` | **Deleted** — replaced by Railway backend fetch |
| Hero hero-actions-secondary `Connect to Claude Desktop →` button that anchored to `#hero-signup-form` | **Replaced** — inside the `<details>` element instead |
| `/webhook/loops` route | **Kept fully functional** — only addition is the new `signup_via_legacy_webhook` PostHog event for forensics |
| `loops_chat_transactional_id` env var usage | **Kept** — `/api/signup` reuses the same template ID. No new env var needed for activation email. |
| `LOOPS_API_KEY` requirement | **Kept** — needed by both `/webhook/loops` and `/api/signup` for the activation transactional |

---

## 5. Test plan

### Phase A: `tests/test_signup_endpoint.py`

| Test | Asserts |
|---|---|
| `test_e2e_signup_flow_token_issued_and_email_sent` | The "critical safety check": 200 `{ok:true}`, `auth.create_token` called once for canonical email, `_create_magic_link` called once with the issued plaintext token, `_send_activation_email` called once with `/chat?t=<uuid>` URL, PostHog `signup_form_submitted` + `signup_provisioned` events both tagged `source=api_signup`. |
| `test_response_shape_is_ok_true` | Frontend contract: response is exactly `{"ok": true}` |
| `test_invalid_json_returns_400` | 400, no DB hit |
| `test_missing_email_returns_400` | 400 |
| `test_malformed_email_returns_400` | 400 |
| `test_email_too_long_returns_400` | 400 (>254 chars) |
| `test_empty_email_returns_400` | 400 (whitespace-only email) |
| `test_fourth_signup_from_same_ip_blocked` | 4th from same IP returns 429 |
| `test_different_ips_have_independent_budgets` | IP B works after IP A's budget burned |
| `test_disposable_domain_silently_dropped` | 200 `{ok:true}`, NO token issued, PostHog `signup_rejected_disposable` fires |
| `test_no_mx_silently_dropped` | 200 `{ok:true}`, NO token issued, PostHog `signup_rejected_mx` fires |
| `test_brand_prefix_silently_dropped` | 200 `{ok:true}`, NO token issued, PostHog `signup_rejected_heuristic` fires |
| `test_brand_prefix_on_gmail_allowed` | `info@gmail.com` MUST issue a token (free providers exempt) |
| `test_transient_dns_does_not_block` | MX transient → token IS issued (fail-open) |
| `test_honeypot_filled_silently_dropped` | `hp_field` non-empty → silent 200 + PostHog `signup_rejected_honeypot` |
| `test_duplicate_email_rotates_token` | Re-signup revokes old + inserts new token + still issues magic link |
| `test_create_token_db_error_returns_500` | `auth.create_token` raises → 500, no token leak |
| `test_pool_execute_error_returns_500` | post-create UPDATE error → 500 |
| `test_email_send_failure_does_not_break_signup` | Loops API error → still 200 (token in DB; matches `chat.signup_handler`) |

### Phase B: no new tests (presentation-only changes)

The frontend changes are pure HTML/JS. The behaviour change (POST to
backend instead of Loops) is verified by Phase A's contract test —
the `/api/signup` endpoint is the new contract surface.

### Phase C: extension to `tests/test_loops_webhook_hardening.py`

| Test | Asserts |
|---|---|
| `test_legacy_webhook_fires_forensic_event` | A successful Loops webhook hit fires `signup_via_legacy_webhook` (in addition to the existing `signup_form_submitted`/`signup_provisioned` events). |

### Pre-existing test suite

Every existing test must continue to pass. `tests/test_security.py` is
skipped per the spec (pre-existing import error, unrelated). The full
test command:

```bash
uv run pytest tests/ --ignore=tests/test_security.py -q
```

---

## 6. Migration / rollback

Per-phase rollback playbook:

### Phase A rollback (backend endpoint broken)

- Revert the `server.py` mount diff (one route line).
- The frontend continues posting to Loops because Phase B hasn't shipped
  yet. **No user impact.**
- If Phase A is broken AND already deployed AND Phase B has shipped:
  revert the `site/index.html` form `action` attribute back to
  `https://app.loops.so/api/newsletter-form/cmntqdkqy00y20iycvyyxby0m`.
  Vercel deploys in <30s. Loops continues to work. **<2 min revert.**

### Phase B rollback (homepage broken)

- One Vercel rollback click — Vercel keeps every prior deploy.
- The Loops form ID is unchanged in the production Loops account, so
  the rolled-back HTML continues to work without any backend revert.
- No DB migration is involved.

### Phase C rollback (forensic event noise)

- The new PostHog event is purely additive — nothing depends on it.
- Revert the one-line `ph_capture` call in `loops_webhook.py`.
- No user impact at any point.

### Single biggest blast risk

If `/api/signup` ships with a bug AND we cut the homepage form to it
in the same deploy, new MCP-token signups stop working until either
(a) we revert the form `action`, or (b) we fix the endpoint. Mitigation:

- Phase A's e2e test catches token-not-issued bugs pre-merge.
- Phase A's tests cover every rejection branch and the happy path.
- The form `action` revert is a single character diff. Vercel deploys
  in <30s.
- The `/chat` flow does **not** depend on `/api/signup` — chat works
  via `/api/chat/signup`. So even if `/api/signup` is fully broken, the
  primary CTA still works.

This is why the Phase B form-action flip is **a separate commit** from
Phase A — so a quick revert is one commit, not two.

---

## 7. Acceptance criteria

This PR is mergeable when ALL of:

1. `uv run pytest tests/ --ignore=tests/test_security.py -q` passes
   (all existing + new tests green).
2. The end-to-end test in Phase A proves token issuance + activation
   email pipeline works (mocked Loops API).
3. The homepage hero leads with the `Try It Now — Free →` CTA to `/chat`
   and the Loops form is below the fold under a `<details>` element.
4. The `<form action>` URL points to the Railway backend, not Loops.
5. The legacy `/webhook/loops` route still accepts and processes Loops
   payloads (the existing `test_loops_webhook_hardening.py` suite passes
   unchanged plus the new forensic-event test passes).
6. CSP `form-action` directive in `vercel.json` is updated.
7. Plan doc (this file) committed.
8. PR opened, NOT merged. User reviews and merges.

---

## 8. What ships in a follow-up

- Cloudflare Turnstile enforcement (env vars are present but unread).
- Per-IP rate-limit tuning + Slack alerting on burst.
- Deletion of `/webhook/loops` after 7-day forensic window confirms
  zero legitimate Loops-form traffic.
- Refactor: extract `_create_or_rotate_token(pool, email)` so
  `chat.signup_handler` and the new `/api/signup` handler share the
  re-signup token rotation logic.
- `mcp_tokens.status` column + `pending_verification` state. The
  current chat magic-link path doesn't need this column to work — the
  token sits in `mcp_tokens` already, and the magic link gates access
  to it. Adding a `status` column is a bigger refactor that doesn't
  add security on top of "you must click the link to see the token".
