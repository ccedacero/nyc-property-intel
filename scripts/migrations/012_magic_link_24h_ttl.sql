-- Migration 012: web_magic_links default TTL 15min → 24h
--
-- Context: a 15-minute activation window is hostile to users who don't check
-- email immediately. Industry standard for magic-link auth (Substack, Notion,
-- Slack) is 24h. We continue to expire links on first use, so the security
-- profile is essentially identical — single-use tokens with a longer time
-- horizon.
--
-- Effect: only NEW inserts pick up the 24h default. Application code in
-- chat.py now also passes the 24h interval explicitly so this column
-- default is belt-and-suspenders. Existing rows are left alone; they'll
-- age out naturally.
--
-- Safe to re-run.

ALTER TABLE web_magic_links
    ALTER COLUMN expires_at SET DEFAULT NOW() + INTERVAL '24 hours';
