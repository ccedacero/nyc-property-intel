#!/usr/bin/env bash
# Verify AI crawlers can still reach the site — guards against Cloudflare's
# "Block AI Scrapers" managed rule silently rewriting robots.txt at the edge
# or challenging crawler user-agents (robots.txt itself documents this risk;
# until now nothing monitored it).
#
# Run manually or from cron/CI:  bash scripts/check_ai_crawlers.sh
# Exit 0 = all good; exit 1 = at least one check failed (prints which).

set -u

SITE="https://nycpropertyintel.com"
FAILED=0

check() {
  local ua="$1" url="$2"
  local code
  code=$(curl -s -o /dev/null -w "%{http_code}" -A "$ua" --max-time 15 "$url")
  if [ "$code" != "200" ]; then
    echo "FAIL: $ua -> $url returned $code"
    FAILED=1
  else
    echo "ok:   $ua -> $url ($code)"
  fi
}

for ua in \
  "GPTBot/1.0 (+https://openai.com/gptbot)" \
  "ClaudeBot/1.0 (+claudebot@anthropic.com)" \
  "PerplexityBot/1.0 (+https://perplexity.ai/perplexitybot)" \
  "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"; do
  check "$ua" "$SITE/robots.txt"
  check "$ua" "$SITE/"
done

# robots.txt must still be OUR file, not a Cloudflare-injected replacement.
if ! curl -s --max-time 15 "$SITE/robots.txt" | grep -q "Content-Signal"; then
  echo "FAIL: robots.txt no longer contains our Content-Signal line — possibly rewritten at the edge"
  FAILED=1
else
  echo "ok:   robots.txt content intact (Content-Signal present)"
fi

# llms.txt and llms-full.txt must stay reachable.
for path in /llms.txt /llms-full.txt /sitemap.xml; do
  check "llms-txt-monitor" "$SITE$path"
done

exit $FAILED
