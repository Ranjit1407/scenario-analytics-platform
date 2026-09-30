from __future__ import annotations

import pandas as pd
import pytest

from app.processing.kpi import SUMMARY_COLUMNS, overall_kpis, scenario_kpis
from app.processing.loader import load_runs
from tests.conftest import csv_source


def test_scenario_kpis(valid_runs):
    summary = scenario_kpis(valid_runs).set_index("scenario_id")

    a = summary.loc["SCN-A"]
    assert a["total_runs"] == 3
    assert a["successful_runs"] == 2
    assert a["failed_runs"] == 1
    assert a["success_rate"] == pytest.approx(66.67)
    assert a["total_duration_seconds"] == 600.0
    assert a["avg_duration_seconds"] == 200.0
    assert a["min_duration_seconds"] == 100.0
    assert a["max_duration_seconds"] == 300.0
    # Linear interpolation: 200 + 0.9 * (300 - 200)
    assert a["p95_duration_seconds"] == pytest.approx(290.0)
    assert a["total_records_processed"] == 3000
    assert a["throughput_records_per_sec"] == pytest.approx(5.0)
    assert a["first_run_at"] == pd.Timestamp("2026-09-01T10:00:00Z")
    assert a["last_run_at"] == pd.Timestamp("2026-09-02T09:05:00Z")

    b = summary.loc["SCN-B"]
    assert b["total_runs"] == 1
    assert b["success_rate"] == 100.0
    assert b["p95_duration_seconds"] == 60.0


def test_scenario_kpis_column_order(valid_runs):
    assert list(scenario_kpis(valid_runs).columns) == SUMMARY_COLUMNS


def test_zero_duration_does_not_divide_by_zero():
    runs = load_runs(csv_source("""
        run_id,scenario_id,start_time,end_time,status,records_processed
        R1,S1,2026-09-01T10:00:00Z,2026-09-01T10:00:00Z,SUCCESS,50
    """)).runs
    assert scenario_kpis(runs)["throughput_records_per_sec"].item() == 0.0


def test_empty_input():
    empty = load_runs(csv_source("run_id,scenario_id,start_time,end_time,status\n")).runs

    assert scenario_kpis(empty).empty
    overall = overall_kpis(empty)
    assert overall["total_runs"] == 0
    assert overall["success_rate"] is None


def test_overall_kpis(valid_runs):
    overall = overall_kpis(valid_runs)

    assert overall["scenario_count"] == 2
    assert overall["total_runs"] == 4
    assert overall["successful_runs"] == 3
    assert overall["failed_runs"] == 1
    assert overall["success_rate"] == 75.0
    assert overall["total_duration_seconds"] == 660.0
    assert overall["avg_duration_seconds"] == 165.0
    assert overall["total_records_processed"] == 3600
    assert overall["period_start"] == pd.Timestamp("2026-09-01T10:00:00Z")
    assert overall["period_end"] == pd.Timestamp("2026-09-02T12:01:00Z")
