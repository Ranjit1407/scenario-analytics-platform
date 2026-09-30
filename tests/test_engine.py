from __future__ import annotations

import json

import pandas as pd

from app.processing.engine import ScenarioProcessor, build_scenario_catalogue
from tests.conftest import csv_source


class RecordingRepository:
    """Stands in for ScenarioRepository and remembers what it was asked to save."""

    def __init__(self):
        self.calls = []

    def save_runs(self, scenarios, runs, source_file):
        self.calls.append((scenarios, runs, source_file))


def test_process_persists_through_repository(valid_csv):
    repo = RecordingRepository()
    report = ScenarioProcessor(repo).process(csv_source(valid_csv), source_name="runs.csv")

    assert report.persisted
    assert report.rows_loaded == 4
    assert len(repo.calls) == 1
    scenarios, runs, source_file = repo.calls[0]
    assert list(scenarios["scenario_id"]) == ["SCN-A", "SCN-B"]
    assert len(runs) == 4
    assert source_file == "runs.csv"


def test_dry_run_does_not_touch_repository(valid_csv):
    repo = RecordingRepository()
    report = ScenarioProcessor(repo).process(csv_source(valid_csv), persist=False)

    assert not report.persisted
    assert repo.calls == []


def test_file_without_valid_rows_is_not_persisted():
    repo = RecordingRepository()
    report = ScenarioProcessor(repo).process(csv_source("""
        run_id,scenario_id,start_time,end_time,status
        R1,S1,bad,bad,SUCCESS
    """))

    assert not report.persisted
    assert repo.calls == []
    assert report.rows_rejected == 1
    assert report.warnings


def test_report_is_json_serialisable(valid_csv):
    report = ScenarioProcessor().process(csv_source(valid_csv), source_name="runs.csv")
    payload = json.loads(json.dumps(report.to_dict()))

    assert payload["rows_loaded"] == 4
    assert payload["overall"]["period_start"].startswith("2026-09-01T10:00:00")
    assert {s["scenario_id"] for s in payload["scenarios"]} == {"SCN-A", "SCN-B"}


def test_catalogue_uses_most_recent_name():
    runs = pd.DataFrame(
        {
            "scenario_id": ["S1", "S1"],
            "scenario_name": ["New name", "Old name"],
            "category": ["b", "a"],
            "start_time": pd.to_datetime(["2026-09-02", "2026-09-01"], utc=True),
        }
    )
    catalogue = build_scenario_catalogue(runs)

    assert catalogue.to_dict("records") == [{"scenario_id": "S1", "name": "New name", "category": "b"}]
