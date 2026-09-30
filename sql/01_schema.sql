-- =============================================================================
-- 01_schema.sql - core tables for the Scenario Analytics Platform
--
-- scenarios         one row per scenario definition (master data)
-- scenario_runs     one row per execution of a scenario (fact table, grows fastest)
-- scenario_summary  pre-aggregated KPIs per scenario, maintained by
--                   refresh_scenario_summary() in 03_views_procedures.sql
--
-- The script is idempotent and runs on every deployment.
-- =============================================================================

CREATE TABLE IF NOT EXISTS scenarios (
    scenario_id   VARCHAR(50)   PRIMARY KEY,
    name          VARCHAR(200)  NOT NULL,
    category      VARCHAR(100)  NOT NULL DEFAULT 'uncategorised',
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ   NOT NULL DEFAULT now()
);

COMMENT ON TABLE scenarios IS 'Scenario master data. Upserted from imported CSV files.';


CREATE TABLE IF NOT EXISTS scenario_runs (
    run_id             VARCHAR(50)    PRIMARY KEY,
    scenario_id        VARCHAR(50)    NOT NULL
                                      REFERENCES scenarios (scenario_id) ON DELETE CASCADE,
    status             VARCHAR(10)    NOT NULL,
    start_time         TIMESTAMPTZ    NOT NULL,
    end_time           TIMESTAMPTZ    NOT NULL,
    -- Stored instead of derived on every read: reporting queries aggregate this
    -- column constantly, and it is fixed once a run has finished.
    duration_seconds   NUMERIC(12, 3) NOT NULL,
    records_processed  INTEGER        NOT NULL DEFAULT 0,
    error_message      TEXT,
    source_file        VARCHAR(255),
    loaded_at          TIMESTAMPTZ    NOT NULL DEFAULT now(),

    CONSTRAINT chk_runs_status        CHECK (status IN ('SUCCESS', 'FAILED')),
    CONSTRAINT chk_runs_time_order    CHECK (end_time >= start_time),
    CONSTRAINT chk_runs_duration      CHECK (duration_seconds >= 0),
    CONSTRAINT chk_runs_records       CHECK (records_processed >= 0)
);

COMMENT ON TABLE  scenario_runs             IS 'One row per scenario execution.';
COMMENT ON COLUMN scenario_runs.source_file IS 'Name of the CSV file the row was last imported from.';


CREATE TABLE IF NOT EXISTS scenario_summary (
    scenario_id                 VARCHAR(50)    PRIMARY KEY
                                               REFERENCES scenarios (scenario_id) ON DELETE CASCADE,
    total_runs                  INTEGER        NOT NULL,
    successful_runs             INTEGER        NOT NULL,
    failed_runs                 INTEGER        NOT NULL,
    success_rate                NUMERIC(5, 2)  NOT NULL,   -- percentage 0-100
    total_duration_seconds      NUMERIC(14, 3) NOT NULL,
    avg_duration_seconds        NUMERIC(12, 3) NOT NULL,
    min_duration_seconds        NUMERIC(12, 3) NOT NULL,
    max_duration_seconds        NUMERIC(12, 3) NOT NULL,
    p95_duration_seconds        NUMERIC(12, 3) NOT NULL,
    total_records_processed     BIGINT         NOT NULL,
    throughput_records_per_sec  NUMERIC(14, 3) NOT NULL,
    first_run_at                TIMESTAMPTZ    NOT NULL,
    last_run_at                 TIMESTAMPTZ    NOT NULL,
    refreshed_at                TIMESTAMPTZ    NOT NULL DEFAULT now()
);

COMMENT ON TABLE scenario_summary IS
    'Pre-aggregated KPIs per scenario. Do not write directly - call refresh_scenario_summary().';
