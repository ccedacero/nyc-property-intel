-- Migration 013: anon_chat_queries.ran_analyze — track free-tier full-DD use.
--
-- Context: an anonymous visitor is entitled to one free full due-diligence
-- report (the analyze_property tool) before the email gate. That allowance
-- was gated via a signed-cookie flag pre-marked on EVERY anonymous request
-- (`max(anon_analyze_count, 1)`), so any non-analyze query — e.g. a plain
-- address lookup — burned the allowance, and the visitor's first real
-- full-DD request was wrongly blocked. The Set-Cookie header is sent before
-- the SSE body, so the handler can never know mid-stream whether analyze ran.
--
-- The fix moves the gate to an authoritative server-side count, mirroring
-- the existing query-count limit. This column records whether
-- analyze_property actually ran on a given anonymous request; the gate
-- counts ran_analyze rows per ip_hash within the last 24h.
--
-- Backwards-compatible: additive column with a constant default — no table
-- rewrite on Postgres 11+. Idempotent: safe to re-run.

BEGIN;

ALTER TABLE anon_chat_queries
    ADD COLUMN IF NOT EXISTS ran_analyze BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN anon_chat_queries.ran_analyze IS
    'TRUE when analyze_property (a full DD report) ran on this anonymous request. Gates the one-free-DD anon allowance. See migration 013.';

COMMIT;
