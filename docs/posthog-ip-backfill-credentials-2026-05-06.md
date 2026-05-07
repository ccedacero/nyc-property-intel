# PostHog IP Backfill — Credentials Setup

**Status:** Script (`scripts/posthog_ip_backfill.py`) and tests are merged on
the branch. The script cannot be run yet because PostHog query credentials
are missing from the local shell. This doc lists the exact env vars the
script needs and how to obtain them.

## What the script needs

```bash
export POSTHOG_API_KEY="phx_..."           # Personal API key, NOT phc_*
export POSTHOG_PROJECT_ID="12345"          # Numeric project ID
export POSTHOG_HOST="https://us.i.posthog.com"
# RAILWAY_DB is already in the user's shell.
```

## Why the existing `POSTHOG_API_KEY` does not work

What's in Railway today (`railway variables --service nyc-property-intel`):

```
POSTHOG_API_KEY = phc_yAcxu2vtzDRHvPXDY2UE3VhrMSfQLcv77kLURqzYu72C
```

This is the **client/team key** (always starts `phc_`). It's used for
ingestion (writing events from the SDK) and for the JS snippet in
`site/js/posthog-init.js`. It **cannot read events back** through the
HogQL `/query/` endpoint — the API returns:

```
HTTP 403  authentication_failed
"Personal API key found in request Authorization header is invalid."
```

(Verified by smoke-testing the script with this key on 2026-05-06.)

## How to mint the right key

1. Open https://us.posthog.com/ → sign in.
2. Top-left org/project switcher → confirm you're in the `nyc-property-intel`
   project (the one with the `phc_yAcxu2vtzDRHvPXDY2UE3VhrMSfQLcv...` token).
3. Click your avatar → **Personal API keys** → **Create personal API key**.
4. Scope it down to the minimum:
   - **Scopes:** `query:read` (and `project:read` if PostHog asks).
   - **Projects:** restrict to the `nyc-property-intel` project only.
5. Copy the key. It starts with `phx_`.
6. Project ID: in the same UI, **Settings → Project → Project ID** (a
   small integer, e.g. `12345`).

## Run the script

```bash
export POSTHOG_API_KEY="phx_..."
export POSTHOG_PROJECT_ID="12345"
export POSTHOG_HOST="https://us.i.posthog.com"
# RAILWAY_DB is already set in your shell.

uv run python scripts/posthog_ip_backfill.py --days 14 \
    > /tmp/posthog_ip_backfill_$(date +%Y-%m-%d).txt 2>&1
```

The summary prints to stderr; the CSV body to stdout. The redirect above
captures both into one file for review.

## PII handling

- The CSV contains email + IP per signup. **Do not commit it to git.**
- Keep it in `/tmp/` or delete after review.
- `.gitignore` already excludes `/tmp/`; the repo never touches that path.

## Why a fresh personal key (vs. reusing the `phc_` one)

- Personal API keys are user-scoped and revocable independently of the
  project token.
- Scoping to `query:read` only means a leaked key can read but not
  ingest, modify, or delete.
- We never need this in Railway env — it's a one-shot local script.
