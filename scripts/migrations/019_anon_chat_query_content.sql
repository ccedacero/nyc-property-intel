-- Migration 019: anon_chat_queries content columns — de-identified in-product
-- intent logging on the anonymous (free-tier) chat funnel.
--
-- Context: we already log one anon_chat_queries row per free query, but only
-- ip_hash + counts + ran_analyze — i.e. HOW MANY lookups happen, never WHAT
-- people ask or WHICH property. That left us blind on real product intent
-- (the data needed to decide what, if anything, to monetize). These additive,
-- nullable columns capture the query text and the resolved property.
--
-- Privacy: content is de-identified (tied only to the existing sha256(ip||secret)
-- hash + an opaque session id, never a name), truncated (query_text<=1000,
-- address<=160), retained <=24 months then purged, and disclosed in the Privacy
-- Policy (site/legal.html). Signed-in traffic is NOT captured here.
--
-- Applied inline + idempotently in db.py db_lifespan on startup; this file is the
-- repo-of-record copy. Backwards-compatible: old code paths ignore the columns.

BEGIN;

ALTER TABLE anon_chat_queries ADD COLUMN IF NOT EXISTS query_text TEXT;
ALTER TABLE anon_chat_queries ADD COLUMN IF NOT EXISTS bbl        TEXT;
ALTER TABLE anon_chat_queries ADD COLUMN IF NOT EXISTS address    TEXT;

COMMENT ON COLUMN anon_chat_queries.query_text IS 'De-identified: the free-text question the anon visitor asked (<=1000 chars). Retained <=24mo then nulled. See migration 019 + Privacy Policy.';
COMMENT ON COLUMN anon_chat_queries.bbl        IS 'Resolved 10-digit BBL for this query, if a property was resolved. NULL otherwise.';
COMMENT ON COLUMN anon_chat_queries.address    IS 'Resolved property address for this query (<=160 chars), if any. NULL otherwise.';

COMMIT;
