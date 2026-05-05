#!/usr/bin/env python3
"""Signup analytics dashboard — real-vs-bot signup funnel monitoring.

Run anytime against the production DB:
    RAILWAY_DB=... uv run python scripts/signup_dashboard.py

Sections:
  1. Funnel summary (last N days)
  2. Per-signup classification (most recent N external signups)
  3. Bot-signal detail (disposable domains, burst windows)
  4. Time-series (signups per day for last 14 days, ASCII bar chart)
  5. Top users by real activity (calls with non-NULL tool_name)

Flags:
  --days N       Window for funnel summary       (default 30)
  --limit N      How many signups to list        (default 50)
  --json         Emit structured JSON instead of human-readable text
  --internal     Include internal accounts (default: hide them)
  --no-chart     Skip the ASCII time-series chart
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime

import asyncpg

# ── Constants ─────────────────────────────────────────────────────────

# Curated list of throwaway / temp / disposable-mail domains we've seen
# (or expect to see) hitting the signup form.
DISPOSABLE_DOMAINS: frozenset[str] = frozenset({
    "lohinja.com",
    "immenseignite.info",
    "web-ster.com",
    "meyer-alpers.de",
    "mailinator.com",
    "tempmail.com",
    "throwaway.email",
    "guerrillamail.com",
    "10minutemail.com",
    "yopmail.com",
    "trashmail.com",
    "getnada.com",
    "dispostable.com",
})

INTERNAL_EMAILS: frozenset[str] = frozenset({
    "cristiancedacero@gmail.com",
    "devtzitest@gmail.com",
    "launchhero.test@gmail.com",
})

INTERNAL_DOMAIN_SUFFIX = "@nycpropertyintel.com"

# Burst window: ≥ this many cli signups inside BURST_WINDOW_MINUTES => burst
BURST_THRESHOLD = 3
BURST_WINDOW_MINUTES = 60


# ── Helpers ───────────────────────────────────────────────────────────

def _is_internal(email: str | None) -> bool:
    if not email:
        return False
    e = email.lower().strip()
    if e in INTERNAL_EMAILS:
        return True
    return e.endswith(INTERNAL_DOMAIN_SUFFIX)


def _domain_of(email: str | None) -> str:
    if not email or "@" not in email:
        return ""
    return email.lower().rsplit("@", 1)[1]


def _is_disposable(email: str | None) -> bool:
    return _domain_of(email) in DISPOSABLE_DOMAINS


def _classify(
    *,
    real_calls: int,
    null_tool_calls: int,
    distinct_active_days: int,
    is_in_burst: bool,
    is_disposable: bool,
) -> tuple[str, list[str]]:
    """Return (classification, reasons).

    Precedence (highest first):
      REAL       — real_calls >= 3 across distinct_active_days >= 2
      LIGHT      — any real_calls (i.e. non-NULL tool_name) but not REAL
      INIT_ONLY  — only NULL-tool calls (handshake noise, no real query)
      BOT_LIKELY — no calls at all AND (disposable OR burst-signup)
      ZERO       — no calls at all, no bot signals

    `reasons` is a list of short tags useful in the bot-signal section
    and the JSON output.
    """
    reasons: list[str] = []
    if real_calls >= 3 and distinct_active_days >= 2:
        return "REAL", reasons
    if real_calls >= 1:
        return "LIGHT", reasons
    if null_tool_calls > 0:
        return "INIT_ONLY", reasons
    # zero calls at all
    if is_disposable:
        reasons.append("disposable_domain")
    if is_in_burst:
        reasons.append("burst_signup")
    if reasons:
        return "BOT_LIKELY", reasons
    return "ZERO", reasons


def _fmt_dt(ts: datetime | None) -> str:
    if ts is None:
        return ""
    return ts.astimezone(UTC).strftime("%Y-%m-%d %H:%M")


# ── SQL ───────────────────────────────────────────────────────────────

# Pull every signup in the window plus aggregates we need for classification.
# We compute:
#   real_calls           — non-NULL tool_name calls
#   null_tool_calls      — NULL tool_name calls (init handshakes)
#   distinct_tools       — distinct non-NULL tool names
#   distinct_active_days — distinct calendar dates (UTC) with non-NULL tool_name
#
# Burst detection runs in a CTE: for each cli signup, count how many other cli
# signups landed within ±BURST_WINDOW_MINUTES/2 of it. We use a symmetric window
# centered on each signup so any member of a burst cluster gets flagged.
SIGNUP_QUERY = """
WITH burst AS (
    SELECT t1.token_hash,
           COUNT(*) AS cluster_size
      FROM mcp_tokens t1
      JOIN mcp_tokens t2
        ON t2.source = 'cli'
       AND t2.created_at BETWEEN t1.created_at - INTERVAL '{half_min} minutes'
                             AND t1.created_at + INTERVAL '{half_min} minutes'
     WHERE t1.source = 'cli'
     GROUP BY t1.token_hash
),
agg AS (
    SELECT u.token_hash,
           COUNT(*) FILTER (WHERE u.tool_name IS NOT NULL)         AS real_calls,
           COUNT(*) FILTER (WHERE u.tool_name IS NULL)             AS null_tool_calls,
           COUNT(DISTINCT u.tool_name)                             AS distinct_tools,
           COUNT(DISTINCT (u.called_at AT TIME ZONE 'UTC')::date)
               FILTER (WHERE u.tool_name IS NOT NULL)              AS distinct_active_days
      FROM mcp_usage_log u
     GROUP BY u.token_hash
)
SELECT t.token_hash,
       t.customer_email,
       t.plan,
       t.source,
       t.created_at,
       t.revoked_at,
       COALESCE(a.real_calls, 0)            AS real_calls,
       COALESCE(a.null_tool_calls, 0)       AS null_tool_calls,
       COALESCE(a.distinct_tools, 0)        AS distinct_tools,
       COALESCE(a.distinct_active_days, 0)  AS distinct_active_days,
       COALESCE(b.cluster_size, 0)          AS burst_cluster_size
  FROM mcp_tokens t
  LEFT JOIN agg   a ON a.token_hash = t.token_hash
  LEFT JOIN burst b ON b.token_hash = t.token_hash
 WHERE t.created_at > NOW() - INTERVAL '{days} days'
 ORDER BY t.created_at DESC
"""


async def _fetch_signups(conn: asyncpg.Connection, days: int) -> list[dict]:
    rows = await conn.fetch(
        SIGNUP_QUERY.format(days=days, half_min=BURST_WINDOW_MINUTES // 2)
    )
    out: list[dict] = []
    for r in rows:
        email = r["customer_email"]
        burst_size = r["burst_cluster_size"] or 0
        is_in_burst = (r["source"] == "cli") and burst_size >= BURST_THRESHOLD
        disposable = _is_disposable(email)
        cls, reasons = _classify(
            real_calls=r["real_calls"],
            null_tool_calls=r["null_tool_calls"],
            distinct_active_days=r["distinct_active_days"],
            is_in_burst=is_in_burst,
            is_disposable=disposable,
        )
        out.append({
            "token_hash": r["token_hash"],
            "email": email,
            "plan": r["plan"],
            "source": r["source"],
            "created_at": r["created_at"],
            "revoked_at": r["revoked_at"],
            "real_calls": r["real_calls"],
            "null_tool_calls": r["null_tool_calls"],
            "distinct_tools": r["distinct_tools"],
            "distinct_active_days": r["distinct_active_days"],
            "burst_cluster_size": burst_size,
            "is_in_burst": is_in_burst,
            "is_disposable": disposable,
            "is_internal": _is_internal(email),
            "classification": cls,
            "bot_reasons": reasons,
        })
    return out


async def _fetch_daily_signups(conn: asyncpg.Connection, days: int) -> list[tuple[str, int, int]]:
    """Return list of (date_iso, total_count, cli_count) for the last N days."""
    rows = await conn.fetch(
        f"""
        SELECT (created_at AT TIME ZONE 'UTC')::date AS d,
               COUNT(*)                              AS total,
               COUNT(*) FILTER (WHERE source = 'cli') AS cli_count
          FROM mcp_tokens
         WHERE created_at > NOW() - INTERVAL '{days} days'
         GROUP BY d
         ORDER BY d
        """
    )
    return [(r["d"].isoformat(), r["total"], r["cli_count"]) for r in rows]


async def _fetch_top_real_users(
    conn: asyncpg.Connection, days: int, limit: int = 10
) -> list[dict]:
    rows = await conn.fetch(
        f"""
        SELECT t.customer_email,
               t.plan,
               t.source,
               COUNT(*)                          AS calls,
               COUNT(DISTINCT u.tool_name)       AS distinct_tools,
               COUNT(DISTINCT (u.called_at AT TIME ZONE 'UTC')::date)
                                                 AS active_days
          FROM mcp_usage_log u
          JOIN mcp_tokens t USING (token_hash)
         WHERE u.called_at > NOW() - INTERVAL '{days} days'
           AND u.tool_name IS NOT NULL
         GROUP BY t.customer_email, t.plan, t.source
         ORDER BY calls DESC
         LIMIT {limit}
        """
    )
    return [dict(r) for r in rows]


# ── Rendering ─────────────────────────────────────────────────────────

def _render_funnel(signups: list[dict], days: int) -> dict:
    total = len(signups)
    real = sum(1 for s in signups if s["classification"] == "REAL")
    light = sum(1 for s in signups if s["classification"] == "LIGHT")
    init_only = sum(1 for s in signups if s["classification"] == "INIT_ONLY")
    bot_likely = sum(1 for s in signups if s["classification"] == "BOT_LIKELY")
    zero = sum(1 for s in signups if s["classification"] == "ZERO")

    real_pct = (real / total * 100) if total else 0.0
    any_real_pct = ((real + light) / total * 100) if total else 0.0

    # reason breakdown
    disposable_n = sum(
        1 for s in signups
        if s["classification"] == "BOT_LIKELY" and "disposable_domain" in s["bot_reasons"]
    )
    burst_n = sum(
        1 for s in signups
        if s["classification"] == "BOT_LIKELY" and "burst_signup" in s["bot_reasons"]
    )

    return {
        "window_days": days,
        "total_signups": total,
        "real": real,
        "light": light,
        "init_only": init_only,
        "bot_likely": bot_likely,
        "zero": zero,
        "real_pct": round(real_pct, 1),
        "any_activity_pct": round(any_real_pct, 1),
        "bot_reasons": {
            "disposable_domain": disposable_n,
            "burst_signup": burst_n,
        },
    }


def _print_funnel(funnel: dict) -> None:
    print(f"\n┌─ Funnel summary (last {funnel['window_days']}d) "
          f"{'─' * (78 - 32 - len(str(funnel['window_days'])))}")
    print(f"  Total signups:           {funnel['total_signups']}")
    print(f"  REAL (≥3 calls, ≥2 days):{funnel['real']:>4}   "
          f"({funnel['real_pct']:>4.1f}% of total)")
    print(f"  LIGHT (1–2 day usage):   {funnel['light']:>4}")
    print(f"  Any real activity:       "
          f"{funnel['real'] + funnel['light']:>4}   "
          f"({funnel['any_activity_pct']:>4.1f}% of total)")
    print(f"  INIT_ONLY (handshake):   {funnel['init_only']:>4}")
    print(f"  ZERO (no calls):         {funnel['zero']:>4}")
    print(f"  BOT_LIKELY:              {funnel['bot_likely']:>4}")
    print(f"    └ disposable_domain:   {funnel['bot_reasons']['disposable_domain']:>4}")
    print(f"    └ burst_signup:        {funnel['bot_reasons']['burst_signup']:>4}")


def _render_signup_row(s: dict) -> dict:
    return {
        "email": s["email"],
        "source": s["source"],
        "created_at": _fmt_dt(s["created_at"]),
        "real_calls": s["real_calls"],
        "null_tool_calls": s["null_tool_calls"],
        "distinct_tools": s["distinct_tools"],
        "distinct_active_days": s["distinct_active_days"],
        "classification": s["classification"],
        "bot_reasons": s["bot_reasons"],
    }


def _print_signups_table(signups: list[dict], limit: int) -> None:
    rows = signups[:limit]
    print(f"\n┌─ Per-signup classification (most recent {len(rows)}) "
          f"{'─' * max(0, 78 - 47 - len(str(len(rows))))}")
    if not rows:
        print("  (no external signups in window)")
        return
    print(f"  {'EMAIL':<38} {'SRC':<4} {'WHEN':<17} "
          f"{'REAL':>4} {'TOOLS':>5} {'DAYS':>4}  CLASS")
    for s in rows:
        email = (s["email"] or "(null)")[:38]
        src = (s["source"] or "?")[:4]
        when = _fmt_dt(s["created_at"])
        cls = s["classification"]
        if s["bot_reasons"]:
            cls = f"{cls} [{','.join(s['bot_reasons'])}]"
        print(f"  {email:<38} {src:<4} {when:<17} "
              f"{s['real_calls']:>4} {s['distinct_tools']:>5} "
              f"{s['distinct_active_days']:>4}  {cls}")


def _render_bot_detail(signups: list[dict]) -> dict:
    bot_zero = [s for s in signups if s["classification"] in ("ZERO", "BOT_LIKELY")]
    domain_hits: dict[str, int] = {}
    burst_emails: list[dict] = []
    for s in bot_zero:
        if s["is_disposable"]:
            d = _domain_of(s["email"])
            domain_hits[d] = domain_hits.get(d, 0) + 1
        if s["is_in_burst"]:
            burst_emails.append({
                "email": s["email"],
                "created_at": _fmt_dt(s["created_at"]),
                "cluster_size": s["burst_cluster_size"],
            })
    return {
        "zero_or_bot_count": len(bot_zero),
        "disposable_domain_hits": domain_hits,
        "burst_signups": burst_emails,
    }


def _print_bot_detail(detail: dict) -> None:
    print(f"\n┌─ Bot-signal detail (ZERO + BOT_LIKELY cohort: "
          f"{detail['zero_or_bot_count']}) {'─' * 16}")
    if detail["disposable_domain_hits"]:
        print("  Disposable domains hit:")
        for d, n in sorted(
            detail["disposable_domain_hits"].items(), key=lambda kv: -kv[1]
        ):
            print(f"    {d:<28} {n}")
    else:
        print("  Disposable domains:     none observed")
    if detail["burst_signups"]:
        print(f"  Burst windows (≥{BURST_THRESHOLD} cli signups in "
              f"{BURST_WINDOW_MINUTES}min):")
        for b in detail["burst_signups"]:
            print(f"    {b['created_at']:<17} cluster={b['cluster_size']:<3} {b['email']}")
    else:
        print("  Burst windows:          none observed")


def _render_chart_data(daily: list[tuple[str, int, int]], days: int = 14) -> list[dict]:
    """Return last `days` daily counts (filling zeros for missing days)."""
    from datetime import timedelta
    by_day = {d: (total, cli) for d, total, cli in daily}
    today = datetime.now(UTC).date()
    out = []
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        total, cli = by_day.get(d.isoformat(), (0, 0))
        out.append({"date": d.isoformat(), "total": total, "cli": cli})
    return out


def _print_chart(chart: list[dict]) -> None:
    print(f"\n┌─ Signups per day (last {len(chart)}d) "
          f"{'─' * (78 - 28 - len(str(len(chart))))}")
    if not chart:
        print("  (no data)")
        return
    max_total = max(c["total"] for c in chart) or 1
    bar_width = 40
    print(f"  {'DATE':<12} {'TOTAL':>5} {'CLI':>4}  CHART")
    for c in chart:
        bar = "█" * int(round(c["total"] / max_total * bar_width))
        print(f"  {c['date']:<12} {c['total']:>5} {c['cli']:>4}  {bar}")


def _print_top_users(top: list[dict]) -> None:
    print(f"\n┌─ Top users by real activity ({len(top)}) "
          f"{'─' * (78 - 33 - len(str(len(top))))}")
    if not top:
        print("  (no real activity in window)")
        return
    print(f"  {'EMAIL':<42} {'PLAN':<6} {'SRC':<4} "
          f"{'CALLS':>6} {'TOOLS':>6} {'DAYS':>4}")
    for r in top:
        email = (r["customer_email"] or "(null)")[:42]
        print(f"  {email:<42} {(r['plan'] or '?'):<6} "
              f"{(r['source'] or '?'):<4} "
              f"{r['calls']:>6} {r['distinct_tools']:>6} {r['active_days']:>4}")


# ── Main ──────────────────────────────────────────────────────────────

async def run(args: argparse.Namespace) -> None:
    db_url = os.environ.get("RAILWAY_DB") or os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: set RAILWAY_DB or DATABASE_URL", file=sys.stderr)
        sys.exit(1)

    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=4, command_timeout=60)
    try:
        async with pool.acquire() as conn:
            all_signups = await _fetch_signups(conn, args.days)
            daily = await _fetch_daily_signups(conn, max(args.days, 14))
            top = await _fetch_top_real_users(conn, args.days)
    finally:
        await pool.close()

    # Filter internal accounts unless --internal
    if args.internal:
        signups = all_signups
    else:
        signups = [s for s in all_signups if not s["is_internal"]]
        top = [r for r in top if not _is_internal(r["customer_email"])]

    funnel = _render_funnel(signups, args.days)
    chart = _render_chart_data(daily, days=14)
    bot_detail = _render_bot_detail(signups)

    if args.json:
        payload = {
            "generated_at": datetime.now(UTC).isoformat(),
            "window_days": args.days,
            "internal_included": bool(args.internal),
            "funnel": funnel,
            "signups": [_render_signup_row(s) for s in signups[: args.limit]],
            "bot_detail": bot_detail,
            "time_series": chart if not args.no_chart else None,
            "top_real_users": [
                {
                    "email": r["customer_email"],
                    "plan": r["plan"],
                    "source": r["source"],
                    "calls": r["calls"],
                    "distinct_tools": r["distinct_tools"],
                    "active_days": r["active_days"],
                }
                for r in top
            ],
        }
        print(json.dumps(payload, indent=2, default=str))
        return

    now = datetime.now(UTC)
    print("\n" + "=" * 78)
    label = "INCLUDING INTERNAL" if args.internal else "EXTERNAL ONLY"
    print(f"NYC PROPERTY INTEL — SIGNUP DASHBOARD  ({label})")
    print(f"Run at: {now.strftime('%Y-%m-%d %H:%M:%S UTC')}  "
          f"window={args.days}d  signup_rows={len(signups)}")
    print("=" * 78)

    _print_funnel(funnel)
    _print_signups_table(signups, args.limit)
    _print_bot_detail(bot_detail)
    if not args.no_chart:
        _print_chart(chart)
    _print_top_users(top)
    print("\n" + "=" * 78 + "\n")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("--days", type=int, default=30,
                   help="Window for funnel summary (default 30)")
    p.add_argument("--limit", type=int, default=50,
                   help="How many signups to list in section 2 (default 50)")
    p.add_argument("--json", action="store_true",
                   help="Emit structured JSON instead of human-readable text")
    p.add_argument("--internal", action="store_true",
                   help="Include internal accounts (default: hide them)")
    p.add_argument("--no-chart", action="store_true",
                   help="Skip the ASCII time-series chart")
    return p


if __name__ == "__main__":
    asyncio.run(run(_build_parser().parse_args()))
