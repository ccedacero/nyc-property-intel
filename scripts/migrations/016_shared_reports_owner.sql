-- Migration 016: shared_reports.owner_token_hash — tie each saved report to
-- the authenticated user who created it, so signed-up users get a private
-- "Your Reports" history (the retention surface, GTM Phase 0 §3b #6).
--
-- Context: migration 015 made reports public, shareable, and anonymous — a
-- great *referral* loop (forward a /r/<id> link) but not a *retention* one:
-- a returning user had no way to find the reports they ran. This adds a
-- nullable owner column keyed on the caller's token_hash (the same hash used
-- across mcp_tokens / usage tables). Anonymous reports keep owner_token_hash
-- NULL and remain anonymous shareable permalinks — unchanged behaviour.
--
-- Nullable + IF NOT EXISTS so it is additive and idempotent: existing rows
-- (pre-016) simply have no owner and stay out of every "mine" list. Mirrored
-- in db.db_lifespan() startup DDL and chat._ensure_reports_table so a fresh
-- deploy provisions the column without a manual migration step.

BEGIN;

ALTER TABLE shared_reports
    ADD COLUMN IF NOT EXISTS owner_token_hash TEXT;

-- Serves the "Your Reports" list query: WHERE owner_token_hash = $1
-- ORDER BY created_at DESC. Partial index — anonymous (NULL owner) rows are
-- never listed by owner, so they don't belong in this index.
CREATE INDEX IF NOT EXISTS idx_shared_reports_owner
    ON shared_reports (owner_token_hash, created_at DESC)
    WHERE owner_token_hash IS NOT NULL;

COMMENT ON COLUMN shared_reports.owner_token_hash IS
    'SHA-256 token_hash of the authenticated creator (NULL for anonymous reports). Drives the private "Your Reports" history at /api/reports/mine. See migration 016.';

COMMIT;
