-- =============================================================================
-- 03_views_procedures.sql - summary maintenance and reporting views
-- =============================================================================

-- -----------------------------------------------------------------------------
-- refresh_scenario_summary
--
-- Recalculates scenario_summary from scenario_runs.
--   CALL refresh_scenario_summary();                         -- every scenario
--   CALL refresh_scenario_summary(ARRAY['SCN-001','SCN-002']); -- only these
--
-- The import pipeline calls it with just the scenarios touched by the import,
-- so the cost of an import grows with the size of the file, not the table.
-- Aggregation happens over full history, which keeps the numbers correct when
-- runs are re-imported or corrected.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE refresh_scenario_summary(p_scenario_ids TEXT[] DEFAULT NULL)
LANGUAGE plpgsql
AS $$
BEGIN
    -- A scenario whose runs have all been removed must not keep a stale summary.
    DELETE FROM scenario_summary ss
    WHERE (p_scenario_ids IS NULL OR ss.scenario_id = ANY (p_scenario_ids))
      AND NOT EXISTS (SELECT 1 FROM scenario_runs r WHERE r.scenario_id = ss.scenario_id);

    INSERT INTO scenario_summary (
        scenario_id, total_runs, successful_runs, failed_runs, success_rate,
        total_duration_seconds, avg_duration_seconds, min_duration_seconds,
        max_duration_seconds, p95_duration_seconds, total_records_processed,
        throughput_records_per_sec, first_run_at, last_run_at, refreshed_at
    )
    SELECT
        r.scenario_id,
        COUNT(*),
        COUNT(*) FILTER (WHERE r.status = 'SUCCESS'),
        COUNT(*) FILTER (WHERE r.status = 'FAILED'),
        ROUND(100.0 * COUNT(*) FILTER (WHERE r.status = 'SUCCESS') / COUNT(*), 2),
        SUM(r.duration_seconds),
        ROUND(AVG(r.duration_seconds), 3),
        MIN(r.duration_seconds),
        MAX(r.duration_seconds),
        -- percentile_cont interpolates linearly, the same as pandas' default quantile.
        ROUND(percentile_cont(0.95) WITHIN GROUP (ORDER BY r.duration_seconds)::NUMERIC, 3),
        SUM(r.records_processed),
        CASE WHEN SUM(r.duration_seconds) > 0
             THEN ROUND(SUM(r.records_processed) / SUM(r.duration_seconds), 3)
             ELSE 0
        END,
        MIN(r.start_time),
        MAX(r.end_time),
        now()
    FROM scenario_runs r
    WHERE p_scenario_ids IS NULL OR r.scenario_id = ANY (p_scenario_ids)
    GROUP BY r.scenario_id
    ON CONFLICT (scenario_id) DO UPDATE SET
        total_runs                 = EXCLUDED.total_runs,
        successful_runs            = EXCLUDED.successful_runs,
        failed_runs                = EXCLUDED.failed_runs,
        success_rate               = EXCLUDED.success_rate,
        total_duration_seconds     = EXCLUDED.total_duration_seconds,
        avg_duration_seconds       = EXCLUDED.avg_duration_seconds,
        min_duration_seconds       = EXCLUDED.min_duration_seconds,
        max_duration_seconds       = EXCLUDED.max_duration_seconds,
        p95_duration_seconds       = EXCLUDED.p95_duration_seconds,
        total_records_processed    = EXCLUDED.total_records_processed,
        throughput_records_per_sec = EXCLUDED.throughput_records_per_sec,
        first_run_at               = EXCLUDED.first_run_at,
        last_run_at                = EXCLUDED.last_run_at,
        refreshed_at               = EXCLUDED.refreshed_at;
END;
$$;


-- -----------------------------------------------------------------------------
-- v_scenario_overview: scenario master data joined with its KPIs.
-- Scenarios without runs are included with NULL KPIs.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_scenario_overview AS
SELECT
    s.scenario_id,
    s.name,
    s.category,
    COALESCE(ss.total_runs, 0)       AS total_runs,
    COALESCE(ss.successful_runs, 0)  AS successful_runs,
    COALESCE(ss.failed_runs, 0)      AS failed_runs,
    ss.success_rate,
    ss.avg_duration_seconds,
    ss.p95_duration_seconds,
    ss.total_records_processed,
    ss.last_run_at
FROM scenarios s
LEFT JOIN scenario_summary ss ON ss.scenario_id = s.scenario_id;


-- -----------------------------------------------------------------------------
-- v_category_summary: KPIs rolled up per scenario category.
-- Success rate is recalculated from the counts, not averaged per scenario,
-- so large scenarios weigh more than small ones.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_category_summary AS
SELECT
    s.category,
    COUNT(DISTINCT s.scenario_id)                     AS scenario_count,
    COALESCE(SUM(ss.total_runs), 0)                   AS total_runs,
    COALESCE(SUM(ss.successful_runs), 0)              AS successful_runs,
    COALESCE(SUM(ss.failed_runs), 0)                  AS failed_runs,
    ROUND(100.0 * SUM(ss.successful_runs) / NULLIF(SUM(ss.total_runs), 0), 2) AS success_rate,
    ROUND(SUM(ss.total_duration_seconds) / NULLIF(SUM(ss.total_runs), 0), 3)  AS avg_duration_seconds,
    COALESCE(SUM(ss.total_records_processed), 0)      AS total_records_processed
FROM scenarios s
LEFT JOIN scenario_summary ss ON ss.scenario_id = s.scenario_id
GROUP BY s.category;


-- -----------------------------------------------------------------------------
-- v_daily_run_stats: execution volume and quality per calendar day (UTC).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_daily_run_stats AS
SELECT
    (r.start_time AT TIME ZONE 'UTC')::DATE          AS run_date,
    COUNT(*)                                         AS total_runs,
    COUNT(*) FILTER (WHERE r.status = 'SUCCESS')     AS successful_runs,
    COUNT(*) FILTER (WHERE r.status = 'FAILED')      AS failed_runs,
    ROUND(100.0 * COUNT(*) FILTER (WHERE r.status = 'SUCCESS') / COUNT(*), 2) AS success_rate,
    ROUND(AVG(r.duration_seconds), 3)                AS avg_duration_seconds,
    SUM(r.records_processed)                         AS total_records_processed
FROM scenario_runs r
GROUP BY (r.start_time AT TIME ZONE 'UTC')::DATE;
