#!/usr/bin/env bash
# indexnow-ping.sh — Notify Bing (and other IndexNow engines) that one or more
# pages were published or updated, so they get crawled/indexed immediately
# instead of waiting for the next crawl.
#
# Usage:
#   ./scripts/indexnow-ping.sh /nyc-property-due-diligence
#   ./scripts/indexnow-ping.sh / /chat /legal
#   ./scripts/indexnow-ping.sh https://nycpropertyintel.com/some-page
#
# Accepts either root-relative paths (/foo) or full URLs. Paths are resolved
# against HOST below.
#
# Setup (already done as of 2026-06-06): an IndexNow key file lives at the site
# root — https://nycpropertyintel.com/<KEY>.txt containing exactly <KEY>.
set -euo pipefail

HOST="nycpropertyintel.com"
KEY="89dd24c11dd2376e3d12c759bc1ffe34"
KEY_LOCATION="https://${HOST}/${KEY}.txt"
ENDPOINT="https://www.bing.com/indexnow"

if [ "$#" -eq 0 ]; then
  echo "Usage: $0 <path-or-url> [more-paths-or-urls...]" >&2
  echo "Example: $0 / /chat /nyc-property-due-diligence" >&2
  exit 1
fi

# Build the JSON urlList from the arguments.
urls=""
for arg in "$@"; do
  case "$arg" in
    http://*|https://*) url="$arg" ;;
    /*)                 url="https://${HOST}${arg}" ;;
    *)                  url="https://${HOST}/${arg}" ;;
  esac
  if [ -z "$urls" ]; then
    urls="\"$url\""
  else
    urls="$urls,\"$url\""
  fi
done

payload="{\"host\":\"${HOST}\",\"key\":\"${KEY}\",\"keyLocation\":\"${KEY_LOCATION}\",\"urlList\":[${urls}]}"

echo "Submitting to IndexNow:"
for arg in "$@"; do echo "  - $arg"; done

http_code=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$ENDPOINT" \
  -H "Content-Type: application/json; charset=utf-8" \
  -d "$payload")

echo "IndexNow response: HTTP ${http_code}"
case "$http_code" in
  200|202) echo "✅ Accepted."; exit 0 ;;
  400) echo "❌ Bad request — check the JSON/URL format." >&2; exit 1 ;;
  403) echo "❌ Forbidden — key file not found or doesn't match. Verify ${KEY_LOCATION}" >&2; exit 1 ;;
  422) echo "❌ Unprocessable — URLs don't belong to ${HOST}, or key mismatch." >&2; exit 1 ;;
  429) echo "❌ Too many requests — slow down." >&2; exit 1 ;;
  *)   echo "⚠️  Unexpected status ${http_code}." >&2; exit 1 ;;
esac
