"""
KPI calculations over validated scenario runs.

Input is the ``runs`` frame produced by ``loader.load_runs``. The formulas
match the ``refresh_scenario_summary`` procedure in
sql/03_views_procedures.sql, so a file processed here reports the same
numbers the database stores:

    success_rate  = successful_runs / total_runs * 100
    p95 duration  = linear interpolation (same as PostgreSQL percentile_cont)
    throughput    = total records processed / total duration in seconds
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

SUMMARY_COLUMNS = [
    "scenario_id",
    "total_runs",
    "successful_runs",
    "failed_runs",
    "success_rate",
    "total_duration_seconds",
    "avg_duration_seconds",
    "min_duration_seconds",
    "max_duration_seconds",
    "p95_duration_seconds",
    "total_records_processed",
    "throughput_records_per_sec",
    "first_run_at",
    "last_run_at",
]

DURATION_DECIMALS = 3
RATE_DECIMALS = 2


def scenario_kpis(runs: pd.DataFrame) -> pd.DataFrame:
    """One row of KPIs per scenario, ordered by scenario_id."""
    if runs.empty:
        return pd.DataFrame(columns=SUMMARY_COLUMNS)

    flagged = runs.assign(
        is_success=runs["status"].eq("SUCCESS"),
        is_failed=runs["status"].eq("FAILED"),
    )
    grouped = flagged.groupby("scenario_id", sort=True)

    summary = grouped.agg(
        total_runs=("run_id", "count"),
        successful_runs=("is_success", "sum"),
        failed_runs=("is_failed", "sum"),
        total_duration_seconds=("duration_seconds", "sum"),
        avg_duration_seconds=("duration_seconds", "mean"),
        min_duration_seconds=("duration_seconds", "min"),
        max_duration_seconds=("duration_seconds", "max"),
        total_records_processed=("records_processed", "sum"),
        first_run_at=("start_time", "min"),
        last_run_at=("end_time", "max"),
    )
    summary["p95_duration_seconds"] = grouped["duration_seconds"].quantile(0.95)
    summary["success_rate"] = (summary["successful_runs"] / summary["total_runs"] * 100).round(
        RATE_DECIMALS
    )
    summary["throughput_records_per_sec"] = _safe_divide(
        summary["total_records_processed"], summary["total_duration_seconds"]
    )

    duration_cols = [col for col in SUMMARY_COLUMNS if col.endswith("_seconds")]
    summary[duration_cols] = summary[duration_cols].round(DURATION_DECIMALS)
    summary["throughput_records_per_sec"] = summary["throughput_records_per_sec"].round(
        DURATION_DECIMALS
    )

    return summary.reset_index()[SUMMARY_COLUMNS]


def overall_kpis(runs: pd.DataFrame) -> dict[str, Any]:
    """Portfolio-level KPIs across every scenario in ``runs``."""
    total = len(runs)
    if total == 0:
        return {
            "scenario_count": 0,
            "total_runs": 0,
            "successful_runs": 0,
            "failed_runs": 0,
            "success_rate": None,
            "total_duration_seconds": 0.0,
            "avg_duration_seconds": None,
            "p95_duration_seconds": None,
            "total_records_processed": 0,
            "period_start": None,
            "period_end": None,
        }

    successful = int(runs["status"].eq("SUCCESS").sum())
    durations = runs["duration_seconds"]

    return {
        "scenario_count": int(runs["scenario_id"].nunique()),
        "total_runs": total,
        "successful_runs": successful,
        "failed_runs": int(runs["status"].eq("FAILED").sum()),
        "success_rate": round(successful / total * 100, RATE_DECIMALS),
        "total_duration_seconds": round(float(durations.sum()), DURATION_DECIMALS),
        "avg_duration_seconds": round(float(durations.mean()), DURATION_DECIMALS),
        "p95_duration_seconds": round(float(durations.quantile(0.95)), DURATION_DECIMALS),
        "total_records_processed": int(runs["records_processed"].sum()),
        "period_start": runs["start_time"].min(),
        "period_end": runs["end_time"].max(),
    }


def _safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    """Element-wise division that yields 0 instead of inf/NaN when dividing by zero."""
    result = np.where(denominator > 0, numerator / denominator.where(denominator > 0, 1), 0.0)
    return pd.Series(result, index=numerator.index, dtype="float64")
