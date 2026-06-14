-- 016_watched_buildings.sql
-- Feature 1.9: "watch this building" email alerts.
--
-- A user viewing a report subscribes (email, BBL). The daily tier-1 sync diffs
-- each building's open-risk snapshot against the last alerted snapshot and emails
-- on an increase (new open violation / litigation / lien), capped to one email
-- per building per week.
--
-- The table also self-provisions at runtime (watch._ensure_watch_table) since
-- Starlette doesn't run the mounted sub-app's lifespan — this .sql is the
-- canonical definition and for environments that run migrations explicitly.

CREATE TABLE IF NOT EXISTS watched_buildings (
    id               TEXT PRIMARY KEY,        -- secrets.token_urlsafe(8)
    email            TEXT NOT NULL,
    bbl              TEXT NOT NULL,
    address          TEXT,
    baseline         JSONB NOT NULL,          -- snapshot at registration (audit/reference)
    last_seen        JSONB NOT NULL,          -- snapshot the cron last reconciled against
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_notified_at TIMESTAMPTZ,
    active           BOOLEAN NOT NULL DEFAULT TRUE
);

-- One active watch per (email, building); re-subscribing reactivates.
CREATE UNIQUE INDEX IF NOT EXISTS idx_watched_buildings_email_bbl
    ON watched_buildings (email, bbl);

-- The cron sweeps active rows by building.
CREATE INDEX IF NOT EXISTS idx_watched_buildings_active
    ON watched_buildings (bbl) WHERE active;
