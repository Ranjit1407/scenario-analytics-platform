-- =============================================================================
-- 02_indexes.sql - indexes chosen for the API and reporting access paths
-- =============================================================================

-- Scenario detail page and GET /scenarios/{id}/runs: "latest runs of scenario X".
-- Also covers the FK lookup when a scenario is deleted, and the per-scenario
-- GROUP BY in refresh_scenario_summary().
CREATE INDEX IF NOT EXISTS idx_runs_scenario_start
    ON scenario_runs (scenario_id, start_time DESC);

-- Date-range reports (daily trend, "last N days").
-- Runs are inserted roughly in time order, so a BRIN index gives range
-- pruning at a fraction of the size of a B-tree on a large fact table.
CREATE INDEX IF NOT EXISTS idx_runs_start_time_brin
    ON scenario_runs USING BRIN (start_time);

-- Failure investigation ("recent failures for scenario X"). Partial index:
-- failures are a small share of all runs, so this stays small.
CREATE INDEX IF NOT EXISTS idx_runs_failed
    ON scenario_runs (scenario_id, start_time DESC)
    WHERE status = 'FAILED';

-- Filtering the scenario list by category.
CREATE INDEX IF NOT EXISTS idx_scenarios_category
    ON scenarios (category);
