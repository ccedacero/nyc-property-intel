# Spec: Passwordless Auth, Magic Links, and Watch Alerts

Status: written 2026-08-13 during the feature-status audit. Codifies the
*intended* contracts for subsystems that were built without written specs.
Where the implementation currently diverges, the divergence is called out and
tracked in `docs/qa-audit-2026-08-13.md`.

## 1. Identity model (passwordless)

- **Identity = canonical email.** `normalize_email` strips `+`-aliases for all
  providers and, for Gmail/Googlemail, strips dots and folds the domain. There
  is **one active token per canonical email**, enforced by the partial unique
  index `mcp_tokens_email_active (customer_email) WHERE revoked_at IS NULL`.
- **Token** = `nyprop_` + 128-bit hex, shown once at issue; only its SHA-256
  hash is stored. Validation is a by-hash lookup; invalid / revoked / expired
  all return an **identical generic 401** (no oracle).
- **Trust boundary:** possession of a valid token = full API identity for that
  email. Therefore issuing a token that can read email-keyed data MUST require
  proof the requester controls the email. (See divergence M-4.)
- **Rate/usage limits** are intended to be **email-scoped**, so token rotation
  cannot reset a user's daily allowance. (See divergence M-5 — currently
  token-hash-scoped.)

## 2. Magic-link lifecycle

- Row: `web_magic_links(id uuid, token_hash, encrypted_token, created_at,
  expires_at = created_at + 24h, used_at, created_by_ip)`.
- **Single-use, TTL 24h.** Consumption is one atomic
  `UPDATE ... SET used_at=NOW() WHERE id=$1 AND used_at IS NULL AND
  expires_at > NOW() RETURNING encrypted_token`; a miss → 410. (Implemented
  correctly.)
- **All user-facing copy must say "24 hours" and "used once."** The in-repo
  templates (`signin-link.mjml`) comply; the Loops-hosted new-signup
  activation template must be corrected from "15 minutes" (tracked).
- Magic-link URLs are **credentials**: never logged, never sent to analytics.
- Activation returns the plaintext token in JSON **and** sets an HttpOnly,
  Secure, SameSite=None cookie. The cookie `path` must cover every
  authenticated read endpoint for cookie-auth to work there (currently
  `/api/chat` only — the `/reports`/`/watch` cookie fallback is inert; JS uses
  the Bearer header, so it works in practice).

## 3. Sign-in from anywhere + `next` redirect

- The signed-out `/watches` and `/reports` pages present an email form that
  hits `POST /api/chat/signup` with an optional `next`.
- **`next` whitelist = the exact set `{"/watches", "/reports"}`.** Anything
  else — protocol-relative `//host`, `javascript:`, absolute URLs, paths with a
  query/fragment, any other internal path — collapses to no redirect.
- Enforced **server-side** (both signup handlers, before building the
  activation URL) **and** client-side (before `window.location.assign`). Use
  exact-match only; never `startsWith`/prefix (that reopens `//evil.com`,
  `/watches.evil`). Implemented correctly on both ends today.
- New email → activation email (trial pitch). Returning email → sign-in email
  (`LOOPS_SIGNIN_TRANSACTIONAL_ID`, falls back to the activation template when
  unset). The magic link carries `&next=` and returns the user to the
  originating page.

## 4. Watch / building-alert contract

- **Subscription** is `(email, bbl)`, created from the watch box in chat / on a
  report page / on `/r/<id>`. No token required to subscribe — email only.
- **Double opt-in:** a new email's first watch is `pending` until it clicks the
  confirm link (`LOOPS_WATCH_CONFIRM_TRANSACTIONAL_ID` is set in prod, so this
  path is live). An email that already has a confirmed watch auto-confirms
  further watches. Repeat submits for an already-pending `(email, bbl)` do NOT
  resend the confirm email.
- **Alert-firing rules** (`process_watches`, on the daily tier-1 sync after the
  MV refresh):
  1. For each `active AND confirmed` watch, recompute the open-risk snapshot:
     `hpd_open`, `dob_open`, `ecb_active`, `litigations`.
  2. Fire **only on an INCREASE** vs `last_seen`. Closures / flat counts never
     alert (they silently re-baseline downward so a future re-increase is
     caught).
  3. **Cooldown:** at most one alert per watch per 7 days. A change during
     cooldown stays pending (last_seen not advanced) and fires when the
     cooldown expires.
  4. On a successful send, advance `last_seen` and `last_notified_at`. On send
     failure, leave `last_seen` so the delta retries next run.
- **Identity join:** watches are email-keyed; `/api/watch/mine` joins on the
  authenticated token's `customer_email`. A user sees a watch on `/watches`
  iff they are signed in with the same email they watched under.
- **Unsubscribe:** the watch row id doubles as the unsubscribe token, carried
  in every alert email's `unsubscribeUrl`. `?all=1` deactivates every watch for
  that email. The `/watch-unsubscribe` page is confirm-button-first so mail
  scanners can't silently unsubscribe.
- **Pro-monitoring interest** ("Notify me at launch", $19/mo painted door) is
  recorded durably in `pro_monitoring_interest (email, bbl, source, created_at)`
  in addition to the PostHog event — that table is the launch list.

## 5. Route inventory (must stay in sync across both Starlette branches)

`/health` `/healthz` `/webhook/loops` `/api/signup` `/api/chat/signup`
`/api/activate` `/api/chat` `/api/report/{id}` `/api/reports/mine`
`/api/watch` `/api/watch/mine` `/api/watch/confirm` `/api/watch/unsubscribe`
`/api/pro-interest`, then `Mount("/", mcp_app)`.

The server builds routes on two branches (streamable vs not). **Any route added
to one branch must be added to the other** — a mismatch is a latent 404 that
only appears if the transport config flips. (This exact bug was found and fixed
2026-08-13: `/api/pro-interest` was doubled on one branch and missing from the
other.)
