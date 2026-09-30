"""
All SQL used by the application lives here.

Write path: ``save_runs`` upserts scenarios and runs, then refreshes the
summary rows of the affected scenarios, all in one transaction. Either the
whole file lands or nothing does.

Read path: plain queries against the tables and views from sql/, returning
lists of dicts that the API layer validates with its Pydantic models.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd
from psycopg2.extras import execute_values

from app.db.connection import Database

logger = logging.getLogger(__name__)

# API sort keys -> SQL expressions. Only these values can reach ORDER BY,
# which is why building that clause with an f-string below is safe.
SCENARIO_SORT_COLUMNS = {
    "scenario_id": "o.scenario_id",
    "name": "o.name",
    "total_runs": "o.total_runs",
    "success_rate": "o.success_rate",
    "failed_runs": "o.failed_runs",
    "avg_duration_seconds": "o.avg_duration_seconds",
    "last_run_at": "o.last_run_at",
}

_UPSERT_SCENARIOS = """
    INSERT INTO scenarios (scenario_id, name, category)
    VALUES %s
    ON CONFLICT (scenario_id) DO UPDATE SET
        name       = EXCLUDED.name,
        category   = EXCLUDED.category,
        updated_at = now()
    WHERE (scenarios.name, scenarios.category) IS DISTINCT FROM (EXCLUDED.name, EXCLUDED.category)
"""

_UPSERT_RUNS = """
    INSERT INTO scenario_runs (
        run_id, scenario_id, status, start_time, end_time,
        duration_seconds, records_processed, error_message, source_file
    )
    VALUES %s
    ON CONFLICT (run_id) DO UPDATE SET
        scenario_id       = EXCLUDED.scenario_id,
        status            = EXCLUDED.status,
        start_time        = EXCLUDED.start_time,
        end_time          = EXCLUDED.end_time,
        duration_seconds  = EXCLUDED.duration_seconds,
        records_processed = EXCLUDED.records_processed,
        error_message     = EXCLUDED.error_message,
        source_file       = EXCLUDED.source_file,
        loaded_at         = now()
"""

_BATCH_SIZE = 1000


class ScenarioRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------------ writes

    def save_runs(self, scenarios: pd.DataFrame, runs: pd.DataFrame, source_file: str) -> None:
        """Upsert one import's scenarios and runs and refresh their summaries."""
        scenario_rows = list(
            scenarios[["scenario_id", "name", "category"]].itertuples(index=False, name=None)
        )
        run_rows = [
            (
                row.run_id,
                row.scenario_id,
                row.status,
                row.start_time.to_pydatetime(),
                row.end_time.to_pydatetime(),
                round(float(row.duration_seconds), 3),
                int(row.records_processed),
                row.error_message or None,
                source_file[:255],
            )
            for row in runs.itertuples(index=False)
        ]
        run_ids = [row[0] for row in run_rows]

        with self._db.connection() as conn, conn.cursor() as cur:
            # If a re-imported run moved to another scenario, that other
            # scenario's summary must be recalculated as well.
            cur.execute(
                "SELECT DISTINCT scenario_id FROM scenario_runs WHERE run_id = ANY(%s)",
                (run_ids,),
            )
            affected = {row[0] for row in cur.fetchall()}
            affected.update(scenarios["scenario_id"])

            execute_values(cur, _UPSERT_SCENARIOS, scenario_rows, page_size=_BATCH_SIZE)
            execute_values(cur, _UPSERT_RUNS, run_rows, page_size=_BATCH_SIZE)
            cur.execute("CALL refresh_scenario_summary(%s)", (sorted(affected),))

        logger.info(
            "Stored %d run(s) for %d scenario(s); refreshed %d summary row(s)",
            len(run_rows),
            len(scenario_rows),
            len(affected),
        )

    # ------------------------------------------------------------------- reads

    def list_scenarios(
        self,
        category: str | None,
        sort: str,
        descending: bool,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """One page of scenarios plus the total number matching the filter."""
        sort_expr = SCENARIO_SORT_COLUMNS[sort]
        direction = "DESC" if descending else "ASC"
        params = {"category": category, "limit": limit, "offset": offset}

        with self._db.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS n FROM scenarios "
                "WHERE %(category)s::text IS NULL OR category = %(category)s",
                params,
            )
            total = cur.fetchone()["n"]

            cur.execute(
                f"""
                SELECT o.scenario_id, o.name, o.category, o.total_runs, o.failed_runs,
                       o.success_rate, o.avg_duration_seconds, o.last_run_at
                FROM v_scenario_overview o
                WHERE %(category)s::text IS NULL OR o.category = %(category)s
                ORDER BY {sort_expr} {direction} NULLS LAST, o.scenario_id
                LIMIT %(limit)s OFFSET %(offset)s
                """,
                params,
            )
            return cur.fetchall(), total

    def get_scenario(self, scenario_id: str) -> dict[str, Any] | None:
        with self._db.cursor() as cur:
            cur.execute(
                """
                SELECT s.scenario_id, s.name, s.category, s.created_at, s.updated_at,
                       ss.total_runs, ss.successful_runs, ss.failed_runs, ss.success_rate,
                       ss.total_duration_seconds, ss.avg_duration_seconds,
                       ss.min_duration_seconds, ss.max_duration_seconds,
                       ss.p95_duration_seconds, ss.total_records_processed,
                       ss.throughput_records_per_sec, ss.first_run_at, ss.last_run_at,
                       ss.refreshed_at
                FROM scenarios s
                LEFT JOIN scenario_summary ss ON ss.scenario_id = s.scenario_id
                WHERE s.scenario_id = %s
                """,
                (scenario_id,),
            )
            return cur.fetchone()

    def list_runs(
        self,
        scenario_id: str,
        status: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """Runs of one scenario, newest first."""
        params = {"scenario_id": scenario_id, "status": status, "limit": limit, "offset": offset}
        where = "scenario_id = %(scenario_id)s AND (%(status)s::text IS NULL OR status = %(status)s)"

        with self._db.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS n FROM scenario_runs WHERE {where}", params)
            total = cur.fetchone()["n"]

            cur.execute(
                f"""
                SELECT run_id, status, start_time, end_time, duration_seconds,
                       records_processed, error_message
                FROM scenario_runs
                WHERE {where}
                ORDER BY start_time DESC, run_id
                LIMIT %(limit)s OFFSET %(offset)s
                """,
                params,
            )
            return cur.fetchall(), total

    def overall_summary(self) -> dict[str, Any]:
        with self._db.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*)                                     AS scenario_count,
                    COALESCE(SUM(ss.total_runs), 0)              AS total_runs,
                    COALESCE(SUM(ss.successful_runs), 0)         AS successful_runs,
                    COALESCE(SUM(ss.failed_runs), 0)             AS failed_runs,
                    ROUND(100.0 * SUM(ss.successful_runs) / NULLIF(SUM(ss.total_runs), 0), 2)
                                                                 AS success_rate,
                    COALESCE(SUM(ss.total_duration_seconds), 0)  AS total_duration_seconds,
                    ROUND(SUM(ss.total_duration_seconds) / NULLIF(SUM(ss.total_runs), 0), 3)
                                                                 AS avg_duration_seconds,
                    COALESCE(SUM(ss.total_records_processed), 0) AS total_records_processed,
                    MIN(ss.first_run_at)                         AS period_start,
                    MAX(ss.last_run_at)                          AS period_end
                FROM scenarios s
                LEFT JOIN scenario_summary ss ON ss.scenario_id = s.scenario_id
                """
            )
            return cur.fetchone()

    def category_summary(self) -> list[dict[str, Any]]:
        with self._db.cursor() as cur:
            cur.execute("SELECT * FROM v_category_summary ORDER BY total_runs DESC, category")
            return cur.fetchall()

    def daily_stats(self, days: int) -> list[dict[str, Any]]:
        """
        The last ``days`` days that have data, oldest first.

        The window is anchored on the most recent run rather than today, so
        historical data sets still return something useful.
        """
        with self._db.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM v_daily_run_stats
                WHERE run_date > (SELECT MAX(run_date) FROM v_daily_run_stats) - %s
                ORDER BY run_date
                """,
                (days,),
            )
            return cur.fetchall()

    def has_runs(self) -> bool:
        with self._db.cursor() as cur:
            cur.execute("SELECT EXISTS (SELECT 1 FROM scenario_runs) AS present")
            return cur.fetchone()["present"]
