-- Migration 010: seed sync_state rows for the 8 ACRIS sub-tables.
--
-- These tables have no per-row PK. They use sync_mode='refresh_by_documentid'
-- in scripts/sync_delta.py: each page deletes existing rows whose documentid
-- appears in the page, then inserts. The cursor column is goodthroughdate;
-- incremental sync filters with `good_through_date > cursor_value` and
-- paginates via $offset within that window.
--
-- Cursor is initialised to MAX(goodthroughdate) so the first run picks up only
-- the next batch (typically one new monthly snapshot). For a full re-sync,
-- run with --reset.
--
-- Idempotent: ON CONFLICT DO NOTHING.

BEGIN;

INSERT INTO sync_state (dataset_key, socrata_id, table_name, cursor_column, cursor_value)
VALUES
    ('real_property_legals',         '8h5j-fqxa', 'real_property_legals',         'goodthroughdate',
        (SELECT MAX(goodthroughdate)::text FROM real_property_legals)),
    ('real_property_parties',        '636b-3b5g', 'real_property_parties',        'goodthroughdate',
        (SELECT MAX(goodthroughdate)::text FROM real_property_parties)),
    ('real_property_references',     'pwkr-dpni', 'real_property_references',     'goodthroughdate',
        (SELECT MAX(goodthroughdate)::text FROM real_property_references)),
    ('real_property_remarks',        '9p4w-7npp', 'real_property_remarks',        'goodthroughdate',
        (SELECT MAX(goodthroughdate)::text FROM real_property_remarks)),
    ('personal_property_legals',     'uqqa-hym2', 'personal_property_legals',     'goodthroughdate',
        (SELECT MAX(goodthroughdate)::text FROM personal_property_legals)),
    ('personal_property_parties',    'nbbg-wtuz', 'personal_property_parties',    'goodthroughdate',
        (SELECT MAX(goodthroughdate)::text FROM personal_property_parties)),
    ('personal_property_references', '6y3e-jcrc', 'personal_property_references', 'goodthroughdate',
        (SELECT MAX(goodthroughdate)::text FROM personal_property_references)),
    ('personal_property_remarks',    'fuzi-5ks9', 'personal_property_remarks',    'goodthroughdate',
        (SELECT MAX(goodthroughdate)::text FROM personal_property_remarks))
ON CONFLICT (dataset_key) DO NOTHING;

COMMIT;
