# Security Policy

## Supported versions

This project is pre-1.0. The `main` branch is the only supported version. Security fixes will be applied to `main` and released as a patch version.

## Reporting a vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Email **security@nycpropertyintel.com** with:

- A description of the vulnerability
- Steps to reproduce (or a proof of concept)
- The version / commit SHA you tested against
- Your assessment of impact (auth bypass, token leak, RCE, data exfiltration, etc.)

I aim to acknowledge reports within 72 hours and to ship a fix or mitigation within 30 days for high-severity issues.

## Scope

In scope:
- The MCP server (`src/nyc_property_intel/`)
- The auth / token-issuance logic (`auth.py`, `manage_tokens.py`, `loops_webhook.py`)
- The hosted endpoint at `nyc-property-intel-production.up.railway.app`
- The marketing site at `nycpropertyintel.com`

Out of scope:
- Findings on third-party services (Railway, PostHog, Loops, Anthropic) — report directly to those vendors
- Data-accuracy issues in upstream NYC datasets — these are upstream agency concerns
- DoS via expensive queries (the rate limiter is the intended control; report bypasses, not load tests)
- Social engineering of the maintainer

## Coordinated disclosure

If you'd like to publish a writeup, I'm happy to coordinate disclosure timing and credit. I do not currently run a bug bounty.
