#!/usr/bin/env python3
"""PostHog IP backfill — recover signup IPs and ASN-class hints.

Pulls `signup_submitted` events from PostHog for the last N days, joins them
against `mcp_tokens` in the production DB, classifies each signup against
`mcp_usage_log`, and emits a CSV to stdout for human review.

Usage:
    POSTHOG_API_KEY=phx_... POSTHOG_PROJECT_ID=12345 \
    POSTHOG_HOST=https://us.i.posthog.com \
    RAILWAY_DB=postgres://... \
    uv run python scripts/posthog_ip_backfill.py [--days 14]

Required env:
    POSTHOG_API_KEY     — Personal API key with project read scope.
                          (NOT the public client key `phc_*` — this must
                          start with `phx_*` and be created at
                          PostHog → Settings → Personal API Keys.)
    POSTHOG_PROJECT_ID  — Numeric project ID (NOT the team token).
    POSTHOG_HOST        — e.g. https://us.i.posthog.com
    RAILWAY_DB          — Production DB connection string.

Notes:
    - PII: never commit the CSV output to git. Pipe to /tmp/.
    - This is a one-shot script, NOT a cron. Re-run manually as needed.
    - The `asn_hint` column is a coarse heuristic only (CIDR prefix
      check). It is NOT an authoritative ASN lookup. Treat as a "smell
      test", not as ground truth.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import ipaddress
import os
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

# ── Heuristic CIDR ranges for "datacenter" detection ──────────────────
#
# This is a deliberately incomplete list of common cloud provider IPv4
# prefixes (AWS, GCP, Azure, DigitalOcean, OVH, Hetzner). We do NOT try
# to be authoritative — this is a free-tier sniff test for the question
# "did this signup probably come from a residential ISP or a VPS?".
#
# A real ASN lookup would require IPinfo/MaxMind. Out of scope here.
DATACENTER_CIDRS: tuple[str, ...] = (
    # AWS — public ranges sampled from ip-ranges.amazonaws.com
    "3.0.0.0/8",
    "13.32.0.0/15",
    "13.224.0.0/14",
    "15.230.0.0/16",
    "18.130.0.0/16",
    "18.140.0.0/15",
    "18.144.0.0/15",
    "18.156.0.0/14",
    "18.160.0.0/13",
    "18.184.0.0/13",
    "18.192.0.0/11",
    "34.192.0.0/10",
    "35.71.64.0/18",
    "35.152.0.0/13",
    "35.160.0.0/13",
    "35.180.0.0/14",
    "44.192.0.0/10",
    "52.0.0.0/11",
    "52.32.0.0/11",
    "52.64.0.0/12",
    "52.84.0.0/14",
    "52.88.0.0/13",
    "52.94.0.0/22",
    "52.95.0.0/16",
    "52.119.128.0/17",
    "52.144.192.0/19",
    "52.192.0.0/11",
    "52.222.128.0/17",
    "54.64.0.0/11",
    "54.144.0.0/12",
    "54.160.0.0/11",
    "54.192.0.0/12",
    "54.208.0.0/13",
    "54.216.0.0/14",
    "54.220.0.0/15",
    "54.222.0.0/15",
    "54.224.0.0/11",
    "100.20.0.0/14",
    "100.24.0.0/13",
    "107.20.0.0/14",
    "174.129.0.0/16",
    "184.72.0.0/15",
    "184.169.128.0/17",
    "204.236.128.0/17",
    # GCP
    "34.64.0.0/10",
    "34.128.0.0/10",
    "35.184.0.0/13",
    "35.192.0.0/14",
    "35.196.0.0/15",
    "35.198.0.0/16",
    "35.199.0.0/17",
    "35.200.0.0/13",
    "35.208.0.0/12",
    "35.224.0.0/12",
    "35.240.0.0/13",
    "104.154.0.0/15",
    "104.196.0.0/14",
    "146.148.0.0/17",
    "192.158.28.0/22",
    "199.192.112.0/22",
    "199.223.232.0/21",
    "208.68.108.0/22",
    # Azure
    "13.64.0.0/11",
    "13.96.0.0/13",
    "13.104.0.0/14",
    "20.0.0.0/8",
    "23.96.0.0/13",
    "40.64.0.0/10",
    "51.4.0.0/15",
    "51.8.0.0/16",
    "51.10.0.0/15",
    "51.103.0.0/16",
    "51.104.0.0/15",
    "51.107.0.0/16",
    "51.116.0.0/16",
    "51.124.0.0/16",
    "51.132.0.0/16",
    "51.136.0.0/15",
    "51.140.0.0/14",
    "51.144.0.0/15",
    "65.52.0.0/14",
    "70.37.0.0/17",
    "104.40.0.0/13",
    "104.146.0.0/15",
    "104.208.0.0/13",
    "131.253.0.0/16",
    "138.91.0.0/16",
    "157.55.0.0/16",
    "168.61.0.0/16",
    "168.62.0.0/15",
    "191.232.0.0/13",
    "207.46.0.0/16",
    "213.199.128.0/18",
    # DigitalOcean
    "45.55.0.0/16",
    "104.131.0.0/16",
    "104.236.0.0/16",
    "138.68.0.0/16",
    "138.197.0.0/16",
    "139.59.0.0/16",
    "157.245.0.0/16",
    "159.65.0.0/16",
    "159.89.0.0/16",
    "165.227.0.0/16",
    "167.71.0.0/16",
    "167.99.0.0/16",
    "178.62.0.0/16",
    "188.166.0.0/16",
    "192.241.128.0/17",
    "198.199.64.0/18",
    "206.189.0.0/16",
    # OVH
    "51.68.0.0/16",
    "51.75.0.0/16",
    "51.83.0.0/16",
    "51.89.0.0/16",
    "51.91.0.0/16",
    "51.158.0.0/15",
    "51.255.0.0/16",
    "54.36.0.0/14",
    "92.222.0.0/15",
    "176.31.0.0/16",
    "176.31.224.0/19",
    "178.32.0.0/15",
    "188.165.0.0/16",
    "192.95.0.0/18",
    "198.27.64.0/18",
    "213.32.0.0/16",
    "213.186.32.0/19",
    "213.251.128.0/18",
    "217.182.0.0/16",
    # Hetzner
    "5.9.0.0/16",
    "78.46.0.0/15",
    "88.99.0.0/16",
    "94.130.0.0/16",
    "116.202.0.0/16",
    "136.243.0.0/16",
    "138.201.0.0/16",
    "144.76.0.0/16",
    "148.251.0.0/16",
    "159.69.0.0/16",
    "176.9.0.0/16",
    "178.63.0.0/16",
    "188.40.0.0/16",
    "213.133.96.0/19",
    "213.239.192.0/18",
)

_DATACENTER_NETS = tuple(ipaddress.ip_network(c) for c in DATACENTER_CIDRS)

JOIN_WINDOW_SECONDS = 10


# ── ASN-hint heuristic ────────────────────────────────────────────────


def asn_hint(ip: str | None) -> str:
    """Return "datacenter" if the IP falls in a known cloud CIDR, else
    "residential". Returns "" for missing/invalid IPs.

    This is a *heuristic*, not an authoritative lookup. False positives
    and false negatives are both expected. Use it as a sniff test only.
    """
    if not ip:
        return ""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ""
    if addr.version != 4:
        # We don't ship IPv6 datacenter ranges. Default to residential
        # rather than mis-classify. Caller can sharpen later if needed.
        return "residential"
    for net in _DATACENTER_NETS:
        if addr in net:
            return "datacenter"
    return "residential"


# ── Classification (mirrors signup_dashboard.py heuristic) ────────────


def classify(real_calls: int, null_tool_calls: int, distinct_active_days: int,
             is_in_burst: bool, is_disposable: bool) -> str:
    """REAL / LIGHT / ZERO / BOT_LIKELY (INIT_ONLY collapsed into ZERO
    for the IP-backfill use case — we only care if there's any real
    tool usage)."""
    if real_calls >= 3 and distinct_active_days >= 2:
        return "REAL"
    if real_calls >= 1:
        return "LIGHT"
    if is_disposable or is_in_burst:
        return "BOT_LIKELY"
    return "ZERO"


# ── PostHog HogQL query ───────────────────────────────────────────────


def fetch_posthog_events(*, api_key: str, project_id: str, host: str,
                         days: int) -> list[dict[str, Any]]:
    """Query PostHog for `signup_submitted` events over the window.

    Uses the HogQL `/query` endpoint. Returns a list of dicts:
        {timestamp, ip, country, city, continent, email}

    Raises RuntimeError on any non-2xx, with the response body included
    so the user can debug auth/scope issues.
    """
    import httpx  # local import keeps tests light

    sql = f"""
    SELECT
        toString(timestamp)                                 AS ts,
        properties.$ip                                      AS ip,
        properties.$geoip_country_code                      AS country,
        properties.$geoip_city_name                         AS city,
        properties.$geoip_continent_code                    AS continent,
        properties.email                                    AS email,
        distinct_id                                         AS distinct_id
    FROM events
    WHERE event = 'signup_submitted'
      AND timestamp >= now() - INTERVAL {int(days)} DAY
    ORDER BY timestamp DESC
    LIMIT 5000
    """
    url = host.rstrip("/") + f"/api/projects/{project_id}/query/"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {"query": {"kind": "HogQLQuery", "query": sql}}
    with httpx.Client(timeout=60.0) as client:
        resp = client.post(url, headers=headers, json=body)
    if resp.status_code >= 300:
        raise RuntimeError(
            f"PostHog query failed: HTTP {resp.status_code}\n"
            f"URL: {url}\n"
            f"Body: {resp.text[:500]}"
        )
    data = resp.json()
    cols = data.get("columns") or []
    rows = data.get("results") or []
    out: list[dict[str, Any]] = []
    for r in rows:
        rec = dict(zip(cols, r, strict=False))
        # Normalize timestamp to a tz-aware datetime.
        ts_raw = rec.get("ts")
        ts: datetime | None
        if isinstance(ts_raw, str) and ts_raw:
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            except ValueError:
                ts = None
        elif isinstance(ts_raw, datetime):
            ts = ts_raw
        else:
            ts = None
        if ts is not None and ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        out.append({
            "timestamp": ts,
            "ip": (rec.get("ip") or None),
            "country": (rec.get("country") or None),
            "city": (rec.get("city") or None),
            "continent": (rec.get("continent") or None),
            "email": (rec.get("email") or None),
            "distinct_id": (rec.get("distinct_id") or None),
        })
    return out


# ── DB queries ────────────────────────────────────────────────────────


async def fetch_db(db_url: str, days: int) -> tuple[list[dict[str, Any]],
                                                     dict[str, dict]]:
    """Return (signups, usage_by_token).

    signups rows: token_hash, customer_email, source, created_at,
                  real_calls, null_tool_calls, distinct_active_days,
                  burst_cluster_size.

    usage_by_token: just real_calls per token, since we collapse
    INIT_ONLY into ZERO for this report.
    """
    import asyncpg

    conn = await asyncpg.connect(db_url, command_timeout=60)
    try:
        sql = f"""
        WITH burst AS (
            SELECT t1.token_hash, COUNT(*) AS cluster_size
              FROM mcp_tokens t1
              JOIN mcp_tokens t2
                ON t2.source = 'cli'
               AND t2.created_at BETWEEN
                       t1.created_at - INTERVAL '30 minutes'
                   AND t1.created_at + INTERVAL '30 minutes'
             WHERE t1.source = 'cli'
             GROUP BY t1.token_hash
        ),
        agg AS (
            SELECT u.token_hash,
                   COUNT(*) FILTER (WHERE u.tool_name IS NOT NULL)
                       AS real_calls,
                   COUNT(*) FILTER (WHERE u.tool_name IS NULL)
                       AS null_tool_calls,
                   COUNT(DISTINCT (u.called_at AT TIME ZONE 'UTC')::date)
                       FILTER (WHERE u.tool_name IS NOT NULL)
                       AS distinct_active_days
              FROM mcp_usage_log u
             GROUP BY u.token_hash
        )
        SELECT t.token_hash,
               t.customer_email,
               t.source,
               t.created_at,
               COALESCE(a.real_calls, 0)            AS real_calls,
               COALESCE(a.null_tool_calls, 0)       AS null_tool_calls,
               COALESCE(a.distinct_active_days, 0)  AS distinct_active_days,
               COALESCE(b.cluster_size, 0)          AS burst_cluster_size
          FROM mcp_tokens t
          LEFT JOIN agg   a ON a.token_hash = t.token_hash
          LEFT JOIN burst b ON b.token_hash = t.token_hash
         WHERE t.created_at > NOW() - INTERVAL '{int(days)} days'
         ORDER BY t.created_at DESC
        """
        rows = await conn.fetch(sql)
    finally:
        await conn.close()

    signups: list[dict[str, Any]] = []
    for r in rows:
        ca = r["created_at"]
        if ca is not None and ca.tzinfo is None:
            ca = ca.replace(tzinfo=UTC)
        signups.append({
            "token_hash": r["token_hash"],
            "email": r["customer_email"],
            "source": r["source"],
            "created_at": ca,
            "real_calls": r["real_calls"],
            "null_tool_calls": r["null_tool_calls"],
            "distinct_active_days": r["distinct_active_days"],
            "burst_cluster_size": r["burst_cluster_size"],
        })
    return signups, {}


# ── Join: PostHog events × mcp_tokens ─────────────────────────────────


def join_events_to_signups(
    *,
    posthog_events: list[dict[str, Any]],
    db_signups: list[dict[str, Any]],
    join_window_seconds: int = JOIN_WINDOW_SECONDS,
) -> list[dict[str, Any]]:
    """Return one row per db signup, augmented with the best matching
    PostHog event (or None).

    Match priority:
      1. Exact email match (case-insensitive). Among ties, pick the
         event whose timestamp is closest to the signup's created_at.
      2. Heuristic timestamp match: the signup must have NO exact
         match, AND there must be a PostHog event without an email
         (anonymous, pre-Part-1) within ±join_window_seconds. Same
         tie-breaker (closest ts wins).

    The returned dicts have all db_signup fields plus:
        ip, country, city, continent, asn_hint, match_kind
    where match_kind is "exact", "heuristic", or "none".
    """
    # Index PostHog events by email (lowercased) and by timestamp.
    by_email: dict[str, list[dict]] = defaultdict(list)
    anonymous: list[dict] = []
    for ev in posthog_events:
        em = (ev.get("email") or "").strip().lower()
        if em:
            by_email[em].append(ev)
        else:
            anonymous.append(ev)
    anonymous.sort(key=lambda e: e["timestamp"] or datetime.min.replace(tzinfo=UTC))

    used_anon: set[int] = set()
    out: list[dict[str, Any]] = []
    for s in db_signups:
        em = (s.get("email") or "").strip().lower()
        match: dict | None = None
        match_kind = "none"
        if em and em in by_email:
            cands = by_email[em]
            # Closest to the signup's created_at.
            ca = s["created_at"]
            if ca is not None:
                cands_with_ts = [c for c in cands if c.get("timestamp")]
                if cands_with_ts:
                    match = min(
                        cands_with_ts,
                        key=lambda c: abs(
                            (c["timestamp"] - ca).total_seconds()
                        ),
                    )
                    match_kind = "exact"
                elif cands:
                    match = cands[0]
                    match_kind = "exact"
            elif cands:
                match = cands[0]
                match_kind = "exact"
        if match is None:
            ca = s["created_at"]
            if ca is not None:
                best_idx: int | None = None
                best_delta = float("inf")
                for i, ev in enumerate(anonymous):
                    if i in used_anon:
                        continue
                    ts = ev.get("timestamp")
                    if ts is None:
                        continue
                    delta = abs((ts - ca).total_seconds())
                    if delta <= join_window_seconds and delta < best_delta:
                        best_idx = i
                        best_delta = delta
                if best_idx is not None:
                    match = anonymous[best_idx]
                    match_kind = "heuristic"
                    used_anon.add(best_idx)

        ip = match.get("ip") if match else None
        out.append({
            **s,
            "ip": ip,
            "country": match.get("country") if match else None,
            "city": match.get("city") if match else None,
            "continent": match.get("continent") if match else None,
            "asn_hint": asn_hint(ip),
            "match_kind": match_kind,
        })
    return out


# ── CSV emit + summary ────────────────────────────────────────────────


def emit_csv_rows(joined: list[dict[str, Any]], out_stream) -> None:
    writer = csv.writer(out_stream)
    writer.writerow([
        "email", "signup_at", "ip", "country", "city", "continent",
        "asn_hint", "source", "made_real_call", "classification",
        "match_kind",
    ])
    for r in joined:
        made_real = bool(r.get("real_calls", 0) >= 1)
        is_disp = _is_disposable(r.get("email"))
        is_burst = (r.get("source") == "cli") and (
            r.get("burst_cluster_size", 0) >= 3
        )
        cls = classify(
            real_calls=r.get("real_calls", 0),
            null_tool_calls=r.get("null_tool_calls", 0),
            distinct_active_days=r.get("distinct_active_days", 0),
            is_in_burst=is_burst,
            is_disposable=is_disp,
        )
        ts = r.get("created_at")
        ts_str = ts.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S") if ts else ""
        writer.writerow([
            r.get("email") or "",
            ts_str,
            r.get("ip") or "",
            r.get("country") or "",
            r.get("city") or "",
            r.get("continent") or "",
            r.get("asn_hint") or "",
            r.get("source") or "",
            "true" if made_real else "false",
            cls,
            r.get("match_kind") or "none",
        ])


# Curated disposable list (kept in sync with signup_dashboard.py).
_DISPOSABLE_DOMAINS = frozenset({
    "lohinja.com", "immenseignite.info", "web-ster.com", "meyer-alpers.de",
    "mailinator.com", "tempmail.com", "throwaway.email",
    "guerrillamail.com", "10minutemail.com", "yopmail.com",
    "trashmail.com", "getnada.com", "dispostable.com",
})


def _is_disposable(email: str | None) -> bool:
    if not email or "@" not in email:
        return False
    return email.lower().rsplit("@", 1)[1] in _DISPOSABLE_DOMAINS


def summarize(joined: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(joined)
    matched = sum(1 for r in joined if r["match_kind"] != "none")
    countries: Counter[str] = Counter()
    asn_hints: Counter[str] = Counter()
    for r in joined:
        if r.get("country"):
            countries[r["country"]] += 1
        h = r.get("asn_hint") or "unknown"
        asn_hints[h] += 1

    # Burst clusters by /24 within 1 hour.
    by_slash24_hour: dict[tuple[str, str], list[str]] = defaultdict(list)
    for r in joined:
        ip = r.get("ip")
        ts = r.get("created_at")
        if not ip or ts is None:
            continue
        try:
            net = str(ipaddress.ip_network(f"{ip}/24", strict=False))
        except ValueError:
            continue
        hour = ts.astimezone(UTC).strftime("%Y-%m-%d %H")
        by_slash24_hour[(net, hour)].append(r.get("email") or "(no-email)")

    burst_clusters = [
        {"net": net, "hour": hour, "count": len(emails),
         "emails": emails}
        for (net, hour), emails in by_slash24_hour.items()
        if len(emails) >= 2
    ]
    burst_clusters.sort(key=lambda c: -c["count"])

    return {
        "total_signups": total,
        "matched_to_posthog": matched,
        "match_rate_pct": round(matched / total * 100, 1) if total else 0.0,
        "top_countries": countries.most_common(5),
        "asn_hint_breakdown": dict(asn_hints),
        "burst_clusters_24_hour": burst_clusters,
    }


def print_summary(summary: dict[str, Any], out=sys.stderr) -> None:
    out.write("\n" + "=" * 78 + "\n")
    out.write("BACKFILL SUMMARY\n")
    out.write("=" * 78 + "\n")
    out.write(f"Total signups (DB):            {summary['total_signups']}\n")
    out.write(
        f"Matched to PostHog event:      {summary['matched_to_posthog']} "
        f"({summary['match_rate_pct']}%)\n"
    )
    out.write("\nTop source countries (PostHog $geoip):\n")
    if not summary["top_countries"]:
        out.write("  (none — no PostHog matches yet)\n")
    for cc, n in summary["top_countries"]:
        out.write(f"  {cc:<6} {n}\n")
    out.write("\nASN-hint breakdown (heuristic CIDR check):\n")
    total_with_hint = sum(
        v for k, v in summary["asn_hint_breakdown"].items()
        if k in ("datacenter", "residential")
    )
    for k, v in sorted(summary["asn_hint_breakdown"].items()):
        pct = (v / total_with_hint * 100) if total_with_hint and k in (
            "datacenter", "residential") else None
        if pct is not None:
            out.write(f"  {k:<14} {v:>4}  ({pct:>4.1f}%)\n")
        else:
            out.write(f"  {k:<14} {v:>4}\n")
    out.write("\nBurst clusters (≥2 signups from same /24 in same hour):\n")
    if not summary["burst_clusters_24_hour"]:
        out.write("  (none)\n")
    for c in summary["burst_clusters_24_hour"][:10]:
        out.write(f"  {c['hour']}  {c['net']:<20}  count={c['count']}\n")
        for em in c["emails"][:5]:
            out.write(f"      └ {em}\n")
        if len(c["emails"]) > 5:
            out.write(f"      └ ... +{len(c['emails']) - 5} more\n")
    out.write("=" * 78 + "\n")


# ── Main ──────────────────────────────────────────────────────────────


def _require_env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        sys.stderr.write(
            f"ERROR: env var {name} is required but not set.\n"
            f"See the module docstring for the full list of required vars.\n"
        )
        sys.exit(2)
    return v


async def _amain(args: argparse.Namespace) -> int:
    api_key = _require_env("POSTHOG_API_KEY")
    project_id = _require_env("POSTHOG_PROJECT_ID")
    host = _require_env("POSTHOG_HOST")
    db_url = os.environ.get("RAILWAY_DB") or os.environ.get("DATABASE_URL")
    if not db_url:
        sys.stderr.write("ERROR: set RAILWAY_DB (or DATABASE_URL).\n")
        return 2

    sys.stderr.write(f"Querying PostHog ({host}) for last {args.days}d...\n")
    try:
        events = fetch_posthog_events(
            api_key=api_key, project_id=project_id, host=host, days=args.days,
        )
    except Exception as exc:
        sys.stderr.write(f"PostHog fetch failed: {exc}\n")
        return 3
    sys.stderr.write(f"  → {len(events)} signup_submitted events\n")

    sys.stderr.write("Querying RAILWAY_DB for signups + usage...\n")
    db_signups, _ = await fetch_db(db_url, args.days)
    sys.stderr.write(f"  → {len(db_signups)} mcp_tokens rows\n")

    joined = join_events_to_signups(
        posthog_events=events, db_signups=db_signups,
    )

    emit_csv_rows(joined, sys.stdout)

    summary = summarize(joined)
    print_summary(summary)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__.strip().splitlines()[0] if __doc__ else "",
    )
    p.add_argument("--days", type=int, default=14,
                   help="Lookback window in days (default 14)")
    args = p.parse_args()
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
