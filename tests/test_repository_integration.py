"""
End-to-end tests against a real PostgreSQL database.

Skipped unless TEST_DATABASE_URL points at a database that may be wiped,
e.g. postgresql://postgres:postgres@localhost:5432/scenario_analytics_test
CI provides one through a service container.
"""

from __future__ import annotations

import os
from datetime import date

import pytest

from app.cli import SCHEMA_SCRIPTS, SQL_DIR
from app.processing.engine import ScenarioProcessor
from tests.conftest import csv_source

DSN = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")


@pytest.fixture
def db():
    from app.db.connection import Database

    database = Database(DSN)
    with database.cursor() as cur:
        cur.execute(
            "DROP TABLE IF EXISTS scenario_summary, scenario_runs, scenarios CASCADE;"
            "DROP PROCEDURE IF EXISTS refresh_scenario_summary(TEXT[]);"
        )
    for name in SCHEMA_SCRIPTS:
        database.run_script(SQL_DIR / name)
    yield database
    database.close()


@pytest.fixture
def repo(db):
    from app.db.repository import ScenarioRepository

    return ScenarioRepository(db)


def test_import_and_read_back(repo, valid_csv):
    ScenarioProcessor(repo).process(csv_source(valid_csv), source_name="t.csv")

    items, total = repo.list_scenarios(None, "scenario_id", False, 50, 0)
    assert total == 2
    assert [i["scenario_id"] for i in items] == ["SCN-A", "SCN-B"]

    detail = repo.get_scenario("SCN-A")
    assert detail["total_runs"] == 3
    assert detail["failed_runs"] == 1
    assert float(detail["success_rate"]) == 66.67
    assert float(detail["p95_duration_seconds"]) == 290.0
    assert float(detail["throughput_records_per_sec"]) == 5.0


def test_sql_kpis_match_python_kpis(repo, valid_csv):
    """The stored procedure and kpi.py must agree on every number."""
    report = ScenarioProcessor(repo).process(csv_source(valid_csv))

    for py_row in report.scenario_summary.to_dict("records"):
        db_row = repo.get_scenario(py_row["scenario_id"])
        for column in (
            "total_runs", "successful_runs", "failed_runs", "success_rate",
            "total_duration_seconds", "avg_duration_seconds", "min_duration_seconds",
            "max_duration_seconds", "p95_duration_seconds", "total_records_processed",
            "throughput_records_per_sec",
        ):
            assert float(db_row[column]) == pytest.approx(float(py_row[column])), column


def test_reimport_is_idempotent(repo, valid_csv):
    processor = ScenarioProcessor(repo)
    processor.process(csv_source(valid_csv))
    processor.process(csv_source(valid_csv))

    assert repo.get_scenario("SCN-A")["total_runs"] == 3


def test_summary_accumulates_across_imports(repo, valid_csv):
    processor = ScenarioProcessor(repo)
    processor.process(csv_source(valid_csv))
    processor.process(csv_source("""
        run_id,scenario_id,start_time,end_time,status
        R9,SCN-A,2026-09-03T10:00:00Z,2026-09-03T10:00:40Z,SUCCESS
    """))

    detail = repo.get_scenario("SCN-A")
    assert detail["total_runs"] == 4
    assert float(detail["success_rate"]) == 75.0


def test_run_moved_to_other_scenario_refreshes_both(repo, valid_csv):
    processor = ScenarioProcessor(repo)
    processor.process(csv_source(valid_csv))
    # R4 used to belong to SCN-B; after this import SCN-B has no runs left.
    processor.process(csv_source("""
        run_id,scenario_id,start_time,end_time,status
        R4,SCN-A,2026-09-02T12:00:00Z,2026-09-02T12:01:00Z,SUCCESS
    """))

    assert repo.get_scenario("SCN-A")["total_runs"] == 4
    assert repo.get_scenario("SCN-B")["total_runs"] is None


def test_runs_paging_and_filter(repo, valid_csv):
    ScenarioProcessor(repo).process(csv_source(valid_csv))

    runs, total = repo.list_runs("SCN-A", None, 2, 0)
    assert total == 3
    assert [r["run_id"] for r in runs] == ["R3", "R2"]  # newest first

    failed, failed_total = repo.list_runs("SCN-A", "FAILED", 50, 0)
    assert failed_total == 1 and failed[0]["error_message"] == "Solver timeout"


def test_reporting_views(repo, valid_csv):
    ScenarioProcessor(repo).process(csv_source(valid_csv))

    overall = repo.overall_summary()
    assert overall["total_runs"] == 4
    assert float(overall["success_rate"]) == 75.0

    categories = {c["category"]: c for c in repo.category_summary()}
    assert categories["logistics"]["total_runs"] == 3

    daily = repo.daily_stats(days=30)
    assert [d["run_date"] for d in daily] == [date(2026, 9, 1), date(2026, 9, 2)]


def test_list_scenarios_filter_and_sort(repo, valid_csv):
    ScenarioProcessor(repo).process(csv_source(valid_csv))

    items, total = repo.list_scenarios("equipment", "scenario_id", False, 50, 0)
    assert total == 1 and items[0]["scenario_id"] == "SCN-B"

    items, _ = repo.list_scenarios(None, "success_rate", False, 50, 0)
    assert [i["scenario_id"] for i in items] == ["SCN-A", "SCN-B"]
