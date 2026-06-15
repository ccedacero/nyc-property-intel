-- 017_watch_confirmation.sql
-- Double-opt-in for "watch this building" (1.9 abuse hardening).
--
-- A watch is only alerted once its email is CONFIRMED. The first watch on a new
-- email creates an unconfirmed row and triggers a confirmation email; clicking
-- the link confirms every watch for that email. This closes third-party
-- watch-bombing (you can't sign up a victim's address and have them spammed).
--
-- Graceful degradation: if LOOPS_WATCH_CONFIRM_TRANSACTIONAL_ID is not set, the
-- app auto-confirms on registration so the feature keeps working.
--
-- Idempotent ADD COLUMNs so this is safe on the already-live table. watch.py's
-- _ensure_watch_table runs the same ALTERs at runtime (mounted sub-app lifespan
-- caveat).

ALTER TABLE watched_buildings
    ADD COLUMN IF NOT EXISTS confirmed    BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS confirmed_at TIMESTAMPTZ;

-- Grandfather any watches that predate double-opt-in: they were created before
-- confirmation existed, so confirm them rather than silently dropping their
-- alerts under the new "WHERE confirmed" cron filter. Runs once at migration
-- time. (At deploy there are 0 active watches, so this is currently a no-op —
-- but it's the correct behavior if that ever changes.)
UPDATE watched_buildings
   SET confirmed = TRUE, confirmed_at = COALESCE(confirmed_at, created_at)
 WHERE NOT confirmed;

-- The cron only sweeps confirmed, active watches.
CREATE INDEX IF NOT EXISTS idx_watched_buildings_confirmed_active
    ON watched_buildings (bbl) WHERE active AND confirmed;
