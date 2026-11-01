-- "Is the data fresh and does it reconcile?"
-- Reads meta.load_log, written by a verification sweep
-- (fabric/nb_meta_health.Notebook) that COUNT(*)s every gold table and
-- records the result with a timestamp -- not a per-load history table
-- (this project's ingest notebooks were run as a one-time historical
-- backfill, not on an incremental schedule yet; see
-- docs/decisions/0020-phase-8-hardening-scope.md for why a full
-- meta.source_registry watermark table wasn't built alongside this).
-- Re-run nb_meta_health to refresh before trusting "latest".

SELECT source, table_name, row_count, checked_at_utc, status, notes
FROM meta.load_log
WHERE checked_at_utc = (SELECT MAX(checked_at_utc) FROM meta.load_log)
ORDER BY source, table_name;

-- One row per source, so a thin/missing source is obvious at a glance.
SELECT
    source,
    COUNT(*) AS tables_tracked,
    SUM(CASE WHEN status = 'OK' THEN 1 ELSE 0 END) AS tables_ok,
    MIN(row_count) AS min_row_count
FROM meta.load_log
WHERE checked_at_utc = (SELECT MAX(checked_at_utc) FROM meta.load_log)
GROUP BY source
ORDER BY source;
