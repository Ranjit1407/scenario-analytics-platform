"""Versioned REST endpoints under /api/v1."""

from __future__ import annotations

import io
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Path, Query, UploadFile, status

from app.api.dependencies import get_processor, get_repository, require_import_key
from app.api.schemas import (
    DailyStat,
    ErrorResponse,
    ImportResponse,
    RunListResponse,
    RunStatus,
    ScenarioDetail,
    ScenarioKpis,
    ScenarioListResponse,
    ScenarioSort,
    SortOrder,
    SummaryResponse,
)
from app.config import get_settings
from app.db.repository import ScenarioRepository
from app.processing.engine import ScenarioProcessor
from app.processing.loader import CsvFormatError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")

# Same character set the import accepts; rejects obviously bad ids before
# they reach the database.
ScenarioIdPath = Annotated[
    str,
    Path(
        pattern=r"^[A-Za-z0-9_.\-]{1,50}$",
        description="Scenario identifier, e.g. SCN-001",
        examples=["SCN-001"],
    ),
]
Limit = Annotated[int, Query(ge=1, le=200, description="Page size (1-200)")]
Offset = Annotated[int, Query(ge=0, le=1_000_000, description="Number of items to skip")]
Repo = Annotated[ScenarioRepository, Depends(get_repository)]

NOT_FOUND = {404: {"model": ErrorResponse, "description": "Scenario not found"}}


@router.get("/scenarios", response_model=ScenarioListResponse, tags=["scenarios"])
def list_scenarios(
    repo: Repo,
    category: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    sort: ScenarioSort = ScenarioSort.scenario_id,
    order: SortOrder = SortOrder.asc,
    limit: Limit = 50,
    offset: Offset = 0,
) -> ScenarioListResponse:
    """List scenarios with their headline KPIs. Supports filtering, sorting and paging."""
    items, total = repo.list_scenarios(
        category=category,
        sort=sort.value,
        descending=order is SortOrder.desc,
        limit=limit,
        offset=offset,
    )
    return ScenarioListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get(
    "/scenarios/{scenario_id}",
    response_model=ScenarioDetail,
    responses=NOT_FOUND,
    tags=["scenarios"],
)
def get_scenario(scenario_id: ScenarioIdPath, repo: Repo) -> ScenarioDetail:
    """Scenario master data plus its full KPI set."""
    row = repo.get_scenario(scenario_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Scenario '{scenario_id}' not found")

    # The LEFT JOIN yields NULL KPI columns for a scenario that has no runs.
    kpis = ScenarioKpis.model_validate(row) if row["total_runs"] is not None else None
    return ScenarioDetail(
        scenario_id=row["scenario_id"],
        name=row["name"],
        category=row["category"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        kpis=kpis,
    )


@router.get(
    "/scenarios/{scenario_id}/runs",
    response_model=RunListResponse,
    responses=NOT_FOUND,
    tags=["scenarios"],
)
def list_scenario_runs(
    scenario_id: ScenarioIdPath,
    repo: Repo,
    status_filter: Annotated[RunStatus | None, Query(alias="status")] = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> RunListResponse:
    """Individual runs of a scenario, newest first."""
    # Distinguish "unknown scenario" (404) from "scenario with no matching runs" (empty list).
    if repo.get_scenario(scenario_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Scenario '{scenario_id}' not found")

    items, total = repo.list_runs(
        scenario_id,
        status=status_filter.value if status_filter else None,
        limit=limit,
        offset=offset,
    )
    return RunListResponse(
        scenario_id=scenario_id, items=items, total=total, limit=limit, offset=offset
    )


@router.get("/summary", response_model=SummaryResponse, tags=["summary"])
def get_summary(repo: Repo) -> SummaryResponse:
    """Portfolio-wide KPIs and a breakdown by scenario category."""
    return SummaryResponse(overall=repo.overall_summary(), by_category=repo.category_summary())


@router.get("/summary/daily", response_model=list[DailyStat], tags=["summary"])
def get_daily_summary(
    repo: Repo,
    days: Annotated[int, Query(ge=1, le=365, description="Number of days to return")] = 14,
) -> list[DailyStat]:
    """Run volume and success rate per day for the most recent ``days`` days with data."""
    return [DailyStat.model_validate(row) for row in repo.daily_stats(days)]


@router.post(
    "/imports",
    response_model=ImportResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_import_key)],
    responses={
        400: {"model": ErrorResponse, "description": "File is not a usable CSV"},
        401: {"model": ErrorResponse, "description": "Missing or invalid API key"},
        413: {"model": ErrorResponse, "description": "File too large"},
    },
    tags=["imports"],
)
def import_csv(
    processor: Annotated[ScenarioProcessor, Depends(get_processor)],
    file: Annotated[UploadFile, File(description="Scenario run CSV")],
    dry_run: Annotated[bool, Query(description="Validate and calculate KPIs without storing")] = False,
) -> ImportResponse:
    """
    Upload a scenario-run CSV. Valid rows are stored and KPIs refreshed;
    invalid rows are skipped and listed in ``rejected``.
    """
    filename = file.filename or "upload.csv"
    if not filename.lower().endswith(".csv"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only .csv files are accepted")

    # Declared as a sync endpoint on purpose: pandas and psycopg2 are blocking,
    # and FastAPI runs sync endpoints in its worker thread pool.
    max_bytes = get_settings().max_upload_bytes
    content = file.file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File exceeds the {max_bytes // (1024 * 1024)} MB upload limit",
        )

    try:
        report = processor.process(io.BytesIO(content), source_name=filename, persist=not dry_run)
    except CsvFormatError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    return ImportResponse(**report.to_dict())
