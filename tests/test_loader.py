from __future__ import annotations

import pandas as pd
import pytest

from app.processing.loader import CsvFormatError, load_runs
from tests.conftest import csv_source

HEADER = "run_id,scenario_id,start_time,end_time,status,records_processed"


def test_loads_valid_file(valid_csv):
    result = load_runs(csv_source(valid_csv))

    assert result.rows_read == 4
    assert result.rows_valid == 4
    assert result.rejected == []
    assert list(result.runs["duration_seconds"]) == [100.0, 200.0, 300.0, 60.0]


def test_status_is_case_insensitive(valid_runs):
    assert valid_runs.loc[valid_runs["run_id"] == "R3", "status"].item() == "FAILED"


def test_timestamps_are_utc(valid_runs):
    assert str(valid_runs["start_time"].dt.tz) == "UTC"


def test_naive_timestamps_are_treated_as_utc():
    result = load_runs(csv_source(f"""
        {HEADER}
        R1,S1,2026-09-01 10:00:00,2026-09-01 10:00:30,SUCCESS,5
    """))
    assert result.runs["start_time"].iloc[0] == pd.Timestamp("2026-09-01T10:00:00Z")
    assert result.runs["duration_seconds"].iloc[0] == 30.0


def test_optional_columns_get_defaults():
    result = load_runs(csv_source("""
        run_id,scenario_id,start_time,end_time,status
        R1,S1,2026-09-01T10:00:00Z,2026-09-01T10:00:10Z,SUCCESS
    """))
    row = result.runs.iloc[0]
    assert row["scenario_name"] == "S1"  # falls back to the id
    assert row["category"] == "uncategorised"
    assert row["records_processed"] == 0


def test_header_is_case_and_whitespace_insensitive():
    result = load_runs(csv_source("""
        Run_ID , Scenario_ID,START_TIME,End_Time,Status
        R1,S1,2026-09-01T10:00:00Z,2026-09-01T10:00:10Z,success
    """))
    assert result.rows_valid == 1


@pytest.mark.parametrize(
    ("row", "expected_reason"),
    [
        (",S1,2026-09-01T10:00:00Z,2026-09-01T10:01:00Z,SUCCESS,1", "run_id is empty"),
        ("R1,,2026-09-01T10:00:00Z,2026-09-01T10:01:00Z,SUCCESS,1", "scenario_id is empty"),
        ("R1,S 1,2026-09-01T10:00:00Z,2026-09-01T10:01:00Z,SUCCESS,1", "scenario_id may only contain"),
        ("R1,S1,yesterday,2026-09-01T10:01:00Z,SUCCESS,1", "start_time is missing"),
        ("R1,S1,2026-09-01T10:00:00Z,,SUCCESS,1", "end_time is missing"),
        ("R1,S1,2026-09-01T10:00:00Z,2026-09-01T09:00:00Z,SUCCESS,1", "end_time is before start_time"),
        ("R1,S1,2026-09-01T10:00:00Z,2026-09-01T10:01:00Z,RUNNING,1", "status must be one of"),
        ("R1,S1,2026-09-01T10:00:00Z,2026-09-01T10:01:00Z,SUCCESS,-5", "records_processed"),
        ("R1,S1,2026-09-01T10:00:00Z,2026-09-01T10:01:00Z,SUCCESS,2.5", "records_processed"),
        ("R1,S1,2026-09-01T10:00:00Z,2026-09-01T10:01:00Z,SUCCESS,lots", "records_processed"),
        ("R1," + "S" * 51 + ",2026-09-01T10:00:00Z,2026-09-01T10:01:00Z,SUCCESS,1", "longer than 50"),
    ],
)
def test_invalid_rows_are_rejected_with_reason(row, expected_reason):
    result = load_runs(csv_source(f"{HEADER}\n{row}\n"))

    assert result.rows_valid == 0
    assert len(result.rejected) == 1
    rejected = result.rejected[0]
    assert rejected.line_number == 2
    assert any(expected_reason in reason for reason in rejected.reasons), rejected.reasons


def test_bad_rows_do_not_affect_good_rows():
    result = load_runs(csv_source(f"""
        {HEADER}
        R1,S1,2026-09-01T10:00:00Z,2026-09-01T10:01:00Z,SUCCESS,1
        R2,S1,garbage,2026-09-01T10:01:00Z,SUCCESS,1
        R3,S1,2026-09-01T11:00:00Z,2026-09-01T11:01:00Z,FAILED,0
    """))
    assert list(result.runs["run_id"]) == ["R1", "R3"]
    assert [r.line_number for r in result.rejected] == [3]


def test_row_with_several_problems_reports_all_of_them():
    result = load_runs(csv_source(f"""
        {HEADER}
        R1,,not-a-date,2026-09-01T10:01:00Z,MAYBE,-1
    """))
    assert len(result.rejected[0].reasons) == 4


def test_duplicate_run_id_keeps_first_occurrence():
    result = load_runs(csv_source(f"""
        {HEADER}
        R1,S1,2026-09-01T10:00:00Z,2026-09-01T10:01:00Z,SUCCESS,1
        R1,S1,2026-09-01T12:00:00Z,2026-09-01T12:01:00Z,FAILED,1
    """))
    assert result.rows_valid == 1
    assert result.runs["status"].item() == "SUCCESS"
    assert "duplicate run_id" in result.rejected[0].reasons[0]


def test_missing_required_column_raises():
    with pytest.raises(CsvFormatError, match="status"):
        load_runs(csv_source("""
            run_id,scenario_id,start_time,end_time
            R1,S1,2026-09-01T10:00:00Z,2026-09-01T10:01:00Z
        """))


def test_empty_file_raises():
    with pytest.raises(CsvFormatError, match="empty"):
        load_runs(csv_source(""))


def test_header_only_file_gives_empty_result():
    result = load_runs(csv_source(f"{HEADER}\n"))
    assert result.rows_read == 0
    assert result.runs.empty


def test_utf8_bom_is_ignored(tmp_path):
    path = tmp_path / "bom.csv"
    path.write_bytes(
        "﻿".encode() + f"{HEADER}\nR1,S1,2026-09-01T10:00:00Z,2026-09-01T10:01:00Z,SUCCESS,1\n".encode()
    )
    assert load_runs(path).rows_valid == 1
