-- Migration 015: shared_reports — public, shareable permalinks for completed
-- full property analyses (feature 1.8, the /r/<id> referral loop).
--
-- Context: when a chat turn runs the full analyze_property report, we persist
-- the rendered markdown + the resolved BBL/address/query so it can be served
-- back at a permanent, auth-free URL (/r/<id>). The shareable link is the free
-- referral mechanism — an investor forwards the report to a partner or lender,
-- who lands on a cold-rendering page with a "watch this building" prompt.
--
-- `id` is a short URL-safe random slug (secrets.token_urlsafe(8), ~11 chars),
-- generated in the app, not a sequence — so the URL leaks no volume/order
-- signal and is unguessable. report_md is capped app-side (~60k chars).
--
-- Additive, idempotent: safe to re-run. Mirrored in db.db_lifespan() startup
-- DDL so a fresh deploy provisions the table without a manual migration step.

BEGIN;

CREATE TABLE IF NOT EXISTS shared_reports (
    id          TEXT PRIMARY KEY,
    bbl         TEXT,
    address     TEXT,
    query       TEXT,
    report_md   TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_shared_reports_bbl        ON shared_reports (bbl);
CREATE INDEX IF NOT EXISTS idx_shared_reports_created_at ON shared_reports (created_at DESC);

COMMENT ON TABLE shared_reports IS
    'Public shareable permalinks for completed analyze_property reports. Served auth-free at /r/<id>. See migration 015 and feature 1.8.';
COMMENT ON COLUMN shared_reports.id IS
    'Short URL-safe random slug (secrets.token_urlsafe(8)); app-generated, unguessable, leaks no ordering.';
COMMENT ON COLUMN shared_reports.report_md IS
    'The full assistant report text (markdown) the user saw. Capped app-side (~60k chars).';

COMMIT;
