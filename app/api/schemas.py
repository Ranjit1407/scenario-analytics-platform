"""Request/response models. These define the JSON contract shown in /docs."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RunStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class ScenarioSort(str, Enum):
    scenario_id = "scenario_id"
    name = "name"
    total_runs = "total_runs"
    success_rate = "success_rate"
    failed_runs = "failed_runs"
    avg_duration_seconds = "avg_duration_seconds"
    last_run_at = "last_run_at"


class SortOrder(str, Enum):
    asc = "asc"
    desc = "desc"


class _Model(BaseModel):
    # Repository rows are plain dicts with Decimal/datetime values;
    # Pydantic coerces them into the declared types.
    model_config = ConfigDict(from_attributes=True)


class Page(_Model):
    total: int = Field(description="Number of items matching the filter, across all pages")
    limit: int
    offset: int


class ScenarioListItem(_Model):
    scenario_id: str
    name: str
    category: str
    total_runs: int
    failed_runs: int
    success_rate: float | None = Field(None, description="Percentage of successful runs, 0-100")
    avg_duration_seconds: float | None = None
    last_run_at: datetime | None = None


class ScenarioListResponse(Page):
    items: list[ScenarioListItem]


class ScenarioKpis(_Model):
    total_runs: int
    successful_runs: int
    failed_runs: int
    success_rate: float
    total_duration_seconds: float
    avg_duration_seconds: float
    min_duration_seconds: float
    max_duration_seconds: float
    p95_duration_seconds: float
    total_records_processed: int
    throughput_records_per_sec: float
    first_run_at: datetime
    last_run_at: datetime
    refreshed_at: datetime


class ScenarioDetail(_Model):
    scenario_id: str
    name: str
    category: str
    created_at: datetime
    updated_at: datetime
    kpis: ScenarioKpis | None = Field(None, description="Null when the scenario has no runs yet")


class RunItem(_Model):
    run_id: str
    status: RunStatus
    start_time: datetime
    end_time: datetime
    duration_seconds: float
    records_processed: int
    error_message: str | None = None


class RunListResponse(Page):
    scenario_id: str
    items: list[RunItem]


class OverallSummary(_Model):
    scenario_count: int
    total_runs: int
    successful_runs: int
    failed_runs: int
    success_rate: float | None
    total_duration_seconds: float
    avg_duration_seconds: float | None
    total_records_processed: int
    period_start: datetime | None
    period_end: datetime | None


class CategorySummary(_Model):
    category: str
    scenario_count: int
    total_runs: int
    successful_runs: int
    failed_runs: int
    success_rate: float | None
    avg_duration_seconds: float | None
    total_records_processed: int


class SummaryResponse(_Model):
    overall: OverallSummary
    by_category: list[CategorySummary]


class DailyStat(_Model):
    run_date: date
    total_runs: int
    successful_runs: int
    failed_runs: int
    success_rate: float
    avg_duration_seconds: float
    total_records_processed: int


class RejectedRowOut(_Model):
    line_number: int
    run_id: str
    reasons: list[str]


class ImportResponse(_Model):
    source: str
    rows_read: int
    rows_loaded: int
    rows_rejected: int
    persisted: bool
    elapsed_seconds: float
    overall: dict[str, Any]
    scenarios: list[dict[str, Any]]
    rejected: list[RejectedRowOut]
    warnings: list[str]


class HealthResponse(_Model):
    status: str
    database: str
    version: str


class ErrorResponse(_Model):
    detail: str
