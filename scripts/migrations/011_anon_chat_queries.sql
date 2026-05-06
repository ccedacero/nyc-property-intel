-- Migration 011: anon_chat_queries — observability for the anonymous /api/chat path.
--
-- Context: the 3 free queries that an anonymous visitor can make before the email
-- gate are not currently logged anywhere (per docs/usage-tracking-audit-2026-05-06.md).
-- The signed `nyprop_sess` cookie lives only on the client, so we have zero
-- server-side data on top-of-funnel behaviour.
--
-- This migration adds a lightweight log table. One row is inserted per anonymous
-- chat request after a successful query. We never store raw IP — only an HMAC-style
-- sha256(ip || ANON_IP_HASH_SECRET) truncated to 32 hex chars, so the column is
-- not directly reversible without the env-var secret.
--
-- Backwards-compatible: adding a brand-new table; old code paths ignore it.
-- Idempotent: safe to re-run.

BEGIN;

CREATE TABLE IF NOT EXISTS anon_chat_queries (
    id              BIGSERIAL PRIMARY KEY,
    ip_hash         TEXT,
    anon_session_id TEXT,
    called_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    query_count     INT
);

-- Funnel queries ("how many anon queries today?" / "by IP-hash") are the
-- expected access pattern. Two cheap indexes keep them fast without
-- adding write overhead worth worrying about at our volume.
CREATE INDEX IF NOT EXISTS anon_chat_queries_called_at_idx
    ON anon_chat_queries (called_at DESC);

CREATE INDEX IF NOT EXISTS anon_chat_queries_ip_hash_idx
    ON anon_chat_queries (ip_hash)
    WHERE ip_hash IS NOT NULL;

COMMENT ON TABLE  anon_chat_queries          IS 'One row per anonymous /api/chat request (pre-email-gate, free tier). See migration 011.';
COMMENT ON COLUMN anon_chat_queries.ip_hash         IS 'sha256(ip || ANON_IP_HASH_SECRET)[:32]. NULL when IP unavailable.';
COMMENT ON COLUMN anon_chat_queries.anon_session_id IS 'Optional opaque session identifier (currently unused; reserved for future).';
COMMENT ON COLUMN anon_chat_queries.query_count     IS 'Anon query_count from the signed cookie at the time of this request (1-based).';

COMMIT;
