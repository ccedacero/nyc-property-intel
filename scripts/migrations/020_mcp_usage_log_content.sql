-- Migration 020: mcp_usage_log content columns — intent capture on the
-- AUTHENTICATED path (MCP tool calls + signed-in web chat).
--
-- Context: migration 019 captured WHAT anonymous web visitors ask, but the
-- paid/ICP segment (hard-money lenders) use MCP, not the web chat, and their
-- tool calls logged only token_hash + tool_name + duration + status — never
-- WHICH property. These additive, nullable columns capture the resolved
-- property (bbl/address, parsed from the MCP tools/call arguments) and, for
-- signed-in web chat, the query text. This is the data that actually informs
-- monetization — what the paying segment researches.
--
-- Privacy: PSEUDONYMOUS (tied to the sha256 token hash, which maps to an
-- account — unlike the fully de-identified anon_chat_queries), truncated
-- (query_text<=1000, address<=160, bbl<=10), retained <=24 months then purged,
-- disclosed in the Privacy Policy (site/legal.html, "API usage logs").
--
-- Applied inline + idempotently in db.py db_lifespan on startup; this file is
-- the repo-of-record copy. Backwards-compatible: old code paths ignore the cols.

BEGIN;

ALTER TABLE mcp_usage_log ADD COLUMN IF NOT EXISTS query_text TEXT;
ALTER TABLE mcp_usage_log ADD COLUMN IF NOT EXISTS bbl        TEXT;
ALTER TABLE mcp_usage_log ADD COLUMN IF NOT EXISTS address    TEXT;

COMMENT ON COLUMN mcp_usage_log.query_text IS 'Signed-in web-chat question (<=1000 chars); NULL for pure MCP tool calls. Retained <=24mo then nulled. See migration 020 + Privacy Policy.';
COMMENT ON COLUMN mcp_usage_log.bbl        IS 'Property BBL looked up on this call, from the MCP tool arguments or resolved web query. NULL if none.';
COMMENT ON COLUMN mcp_usage_log.address    IS 'Property address looked up on this call (<=160 chars). NULL if none.';

COMMIT;
