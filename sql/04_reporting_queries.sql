-- =============================================================================
-- 04_reporting_queries.sql - ad-hoc reporting queries
--
-- Not executed by init-db; run them manually, e.g.
--     psql "$DATABASE_URL" -f sql/04_reporting_queries.sql
-- =============================================================================

-- 1. Portfolio overview: headline numbers across all scenarios.
SELECT
    COUNT(*)                                                       AS scenarios,
    SUM(total_runs)                                                AS total_runs,
    SUM(failed_runs)                                               AS failed_runs,
    ROUND(100.0 * SUM(successful_runs) / NULLIF(SUM(total_runs), 0), 2) AS success_rate,
    ROUND(SUM(total_duration_seconds) / 3600.0, 2)                 AS total_runtime_hours,
    SUM(total_records_processed)                                   AS total_records
FROM scenario_summary;


-- 2. Scenarios needing attention: lowest success rate first
--    (ignoring scenarios with too few runs to be meaningful).
SELECT s.scenario_id, s.name, ss.total_runs, ss.failed_runs, ss.success_rate
FROM scenario_summary ss
JOIN scenarios s USING (scenario_id)
WHERE ss.total_runs >= 5
ORDER BY ss.success_rate ASC, ss.failed_runs DESC
LIMIT 10;


-- 3. Most common failure reasons in the last 7 days of data.
SELECT
    r.scenario_id,
    COALESCE(NULLIF(r.error_message, ''), '(no message)') AS error_message,
    COUNT(*)                                              AS occurrences,
    MAX(r.start_time)                                     AS last_seen
FROM scenario_runs r
WHERE r.status = 'FAILED'
  AND r.start_time >= (SELECT MAX(start_time) FROM scenario_runs) - INTERVAL '7 days'
GROUP BY r.scenario_id, 2
ORDER BY occurrences DESC, last_seen DESC;


-- 4. Daily trend with a 7-day moving success rate.
SELECT
    run_date,
    total_runs,
    failed_runs,
    success_rate,
    ROUND(
        100.0 * SUM(successful_runs) OVER w / NULLIF(SUM(total_runs) OVER w, 0), 2
    ) AS success_rate_7d
FROM v_daily_run_stats
WINDOW w AS (ORDER BY run_date ROWS BETWEEN 6 PRECEDING AND CURRENT ROW)
ORDER BY run_date;


-- 5. Outlier runs: successful runs slower than their scenario's p95.
SELECT r.run_id, r.scenario_id, r.start_time, r.duration_seconds, ss.p95_duration_seconds
FROM scenario_runs r
JOIN scenario_summary ss USING (scenario_id)
WHERE r.status = 'SUCCESS'
  AND r.duration_seconds > ss.p95_duration_seconds
ORDER BY r.duration_seconds / NULLIF(ss.p95_duration_seconds, 0) DESC
LIMIT 20;


-- 6. Category roll-up (see v_category_summary).
SELECT * FROM v_category_summary ORDER BY total_runs DESC;


-- 7. Consistency check: summary table vs. live aggregation.
--    Should return no rows; anything listed means a refresh was missed.
SELECT ss.scenario_id, ss.total_runs AS summary_runs, live.total_runs AS actual_runs
FROM scenario_summary ss
JOIN (
    SELECT scenario_id, COUNT(*) AS total_runs
    FROM scenario_runs
    GROUP BY scenario_id
) live USING (scenario_id)
WHERE ss.total_runs <> live.total_runs;
