"""
Orchestrates one import: load -> validate -> calculate KPIs -> persist.

The processor does not know anything about HTTP or the command line; both
the CLI (app/cli.py) and the upload endpoint call ``ScenarioProcessor.process``
and render the returned ``ProcessingReport`` in their own way.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pandas as pd

from app.processing.kpi import overall_kpis, scenario_kpis
from app.processing.loader import CsvSource, RejectedRow, load_runs

if TYPE_CHECKING:  # avoid importing psycopg2 for callers that only need the engine
    from app.db.repository import ScenarioRepository

logger = logging.getLogger(__name__)


@dataclass
class ProcessingReport:
    source: str
    rows_read: int
    rows_loaded: int
    rejected: list[RejectedRow]
    scenario_summary: pd.DataFrame
    overall: dict[str, Any]
    persisted: bool
    elapsed_seconds: float
    warnings: list[str] = field(default_factory=list)

    @property
    def rows_rejected(self) -> int:
        return len(self.rejected)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable view used by the API response and ``cli import --json``."""
        return {
            "source": self.source,
            "rows_read": self.rows_read,
            "rows_loaded": self.rows_loaded,
            "rows_rejected": self.rows_rejected,
            "persisted": self.persisted,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "overall": {key: _json_value(value) for key, value in self.overall.items()},
            # to_json handles Timestamp -> ISO string and NaN -> null for us.
            "scenarios": json.loads(
                self.scenario_summary.to_json(orient="records", date_format="iso")
            ),
            "rejected": [
                {"line_number": r.line_number, "run_id": r.run_id, "reasons": list(r.reasons)}
                for r in self.rejected
            ],
            "warnings": self.warnings,
        }


class ScenarioProcessor:
    """Runs the processing pipeline, optionally writing the results to PostgreSQL."""

    def __init__(self, repository: ScenarioRepository | None = None) -> None:
        self._repository = repository

    def process(
        self,
        source: CsvSource,
        source_name: str | None = None,
        persist: bool = True,
    ) -> ProcessingReport:
        """
        Process one CSV file.

        ``persist=False`` (or no repository) gives a dry run: the file is
        validated and the KPIs are calculated, but nothing is written.
        Raises ``CsvFormatError`` if the file itself is unusable.
        """
        name = source_name or str(source)
        started = time.perf_counter()
        logger.info("Processing %s", name)

        loaded = load_runs(source)
        summary = scenario_kpis(loaded.runs)
        overall = overall_kpis(loaded.runs)
        warnings: list[str] = []

        persisted = False
        if loaded.runs.empty:
            warnings.append("No valid rows found; nothing was stored.")
        elif persist and self._repository is not None:
            self._repository.save_runs(
                scenarios=build_scenario_catalogue(loaded.runs),
                runs=loaded.runs,
                source_file=name,
            )
            persisted = True

        elapsed = time.perf_counter() - started
        logger.info(
            "Finished %s in %.2fs: %d loaded, %d rejected, persisted=%s",
            name,
            elapsed,
            loaded.rows_valid,
            len(loaded.rejected),
            persisted,
        )
        return ProcessingReport(
            source=name,
            rows_read=loaded.rows_read,
            rows_loaded=loaded.rows_valid,
            rejected=loaded.rejected,
            scenario_summary=summary,
            overall=overall,
            persisted=persisted,
            elapsed_seconds=elapsed,
            warnings=warnings,
        )


def build_scenario_catalogue(runs: pd.DataFrame) -> pd.DataFrame:
    """
    Distinct scenarios referenced by ``runs``.

    If the same scenario appears with different names or categories in one
    file, the values from its most recent run win.
    """
    oldest_first = runs.sort_values("start_time")
    return (
        oldest_first.groupby("scenario_id", as_index=False)
        .agg(name=("scenario_name", "last"), category=("category", "last"))
        .sort_values("scenario_id")
        .reset_index(drop=True)
    )


def _json_value(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value
