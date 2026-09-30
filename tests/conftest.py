from __future__ import annotations

import io
import textwrap

import pytest

from app.processing.loader import load_runs


def csv_source(text: str) -> io.StringIO:
    """Turn an indented triple-quoted CSV literal into a file-like object."""
    return io.StringIO(textwrap.dedent(text).lstrip())


@pytest.fixture
def valid_csv() -> str:
    # Two scenarios; durations chosen so the expected KPIs are easy to verify by hand.
    #   SCN-A: 100s OK, 200s OK, 300s FAILED  -> 3 runs, 66.67% success
    #   SCN-B: 60s OK                         -> 1 run, 100% success
    return """
        run_id,scenario_id,scenario_name,category,start_time,end_time,status,records_processed,error_message
        R1,SCN-A,Alpha,logistics,2026-09-01T10:00:00Z,2026-09-01T10:01:40Z,SUCCESS,1000,
        R2,SCN-A,Alpha,logistics,2026-09-01T11:00:00Z,2026-09-01T11:03:20Z,SUCCESS,2000,
        R3,SCN-A,Alpha,logistics,2026-09-02T09:00:00Z,2026-09-02T09:05:00Z,failed,0,Solver timeout
        R4,SCN-B,Bravo,equipment,2026-09-02T12:00:00Z,2026-09-02T12:01:00Z,SUCCESS,600,
    """


@pytest.fixture
def valid_runs(valid_csv):
    return load_runs(csv_source(valid_csv)).runs
