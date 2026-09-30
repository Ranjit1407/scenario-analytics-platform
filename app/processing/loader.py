"""
CSV import for scenario run data.

Expected columns (header names are case-insensitive, extra columns are ignored):

    run_id, scenario_id, start_time, end_time, status          -- required
    scenario_name, category, records_processed, error_message  -- optional

Bad rows are not fatal. Each one is collected in ``LoadResult.rejected``
together with its line number and every reason it failed, and the remaining
rows are processed normally. Only problems with the file as a whole
(unparseable, empty, required columns missing) raise ``CsvFormatError``.

Timestamps are parsed as ISO 8601. Values without an explicit offset are
assumed to be UTC.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Union

import pandas as pd

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = ("run_id", "scenario_id", "start_time", "end_time", "status")

# Optional columns and the value used when the column is absent from the file.
OPTIONAL_COLUMNS = {
    "scenario_name": "",
    "category": "",
    "records_processed": "0",
    "error_message": "",
}

VALID_STATUSES = frozenset({"SUCCESS", "FAILED"})
DEFAULT_CATEGORY = "uncategorised"

# Matches the VARCHAR(50) id columns in sql/01_schema.sql. The character set
# is kept URL-safe because scenario ids appear in API paths.
MAX_ID_LENGTH = 50
ID_PATTERN = r"[A-Za-z0-9_.\-]+"

CsvSource = Union[str, Path, IO[str], IO[bytes]]


class CsvFormatError(ValueError):
    """The file cannot be processed at all (as opposed to individual bad rows)."""


@dataclass(frozen=True)
class RejectedRow:
    line_number: int  # line in the source file, header = line 1
    run_id: str
    reasons: tuple[str, ...]


@dataclass
class LoadResult:
    runs: pd.DataFrame
    rows_read: int
    rejected: list[RejectedRow] = field(default_factory=list)

    @property
    def rows_valid(self) -> int:
        return len(self.runs)


def load_runs(source: CsvSource) -> LoadResult:
    """
    Read, clean and validate a scenario-run CSV.

    ``source`` can be a path or any file-like object pandas understands,
    which lets the API pass an uploaded file straight through.

    The returned ``runs`` frame has typed columns (UTC timestamps, int64
    record counts) plus a derived ``duration_seconds`` column.
    """
    raw = _read_csv(source)
    frame = _normalise(raw)
    problems = _find_problems(frame)

    rejected = [
        RejectedRow(
            # +2: pandas index is 0-based and line 1 is the header. This assumes
            # one physical line per record, which holds for our export format.
            line_number=int(idx) + 2,
            run_id=frame.at[idx, "run_id"],
            reasons=tuple(reasons),
        )
        for idx, reasons in sorted(problems.items())
    ]
    for row in rejected:
        logger.warning(
            "Rejected line %d (run_id=%r): %s", row.line_number, row.run_id, "; ".join(row.reasons)
        )

    runs = frame.drop(index=list(problems)).reset_index(drop=True)
    runs["records_processed"] = runs["records_processed"].astype("int64")
    runs["duration_seconds"] = (runs["end_time"] - runs["start_time"]).dt.total_seconds()

    logger.info(
        "Loaded %d of %d rows (%d rejected) covering %d scenario(s)",
        len(runs),
        len(frame),
        len(rejected),
        runs["scenario_id"].nunique(),
    )
    return LoadResult(runs=runs, rows_read=len(frame), rejected=rejected)


def _read_csv(source: CsvSource) -> pd.DataFrame:
    try:
        # Read everything as text first; type conversion happens in _normalise
        # so that a single bad value flags one row instead of failing the file.
        df = pd.read_csv(source, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    except pd.errors.EmptyDataError as exc:
        raise CsvFormatError("The CSV file is empty") from exc
    except (pd.errors.ParserError, UnicodeDecodeError) as exc:
        raise CsvFormatError(f"The file could not be parsed as CSV: {exc}") from exc

    df.columns = [str(col).strip().lower() for col in df.columns]

    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise CsvFormatError(f"Missing required column(s): {', '.join(missing)}")

    for col, default in OPTIONAL_COLUMNS.items():
        if col not in df.columns:
            df[col] = default

    return df[list(REQUIRED_COLUMNS) + list(OPTIONAL_COLUMNS)].copy()


def _normalise(df: pd.DataFrame) -> pd.DataFrame:
    """Trim text, apply defaults and convert columns to their real types.

    Values that cannot be converted become NaT/NaN and are picked up by
    ``_find_problems``; nothing is dropped here.
    """
    df = df.apply(lambda col: col.str.strip())

    df["status"] = df["status"].str.upper()
    df["scenario_name"] = df["scenario_name"].mask(df["scenario_name"] == "", df["scenario_id"])
    df["category"] = df["category"].mask(df["category"] == "", DEFAULT_CATEGORY)

    for col in ("start_time", "end_time"):
        df[col] = pd.to_datetime(df[col], errors="coerce", utc=True, format="ISO8601")

    # An empty count is treated as zero rather than as an error.
    df["records_processed"] = pd.to_numeric(
        df["records_processed"].replace("", "0"), errors="coerce"
    )
    return df


def _find_problems(df: pd.DataFrame) -> dict[int, list[str]]:
    """Return {row index: [reasons]} for every row that fails validation."""
    records = df["records_processed"]
    run_id, scenario_id = df["run_id"], df["scenario_id"]

    checks = [
        (run_id == "", "run_id is empty"),
        (run_id.str.len() > MAX_ID_LENGTH, f"run_id is longer than {MAX_ID_LENGTH} characters"),
        (
            (run_id != "") & ~run_id.str.fullmatch(ID_PATTERN),
            "run_id may only contain letters, digits, '.', '_' and '-'",
        ),
        (scenario_id == "", "scenario_id is empty"),
        (
            scenario_id.str.len() > MAX_ID_LENGTH,
            f"scenario_id is longer than {MAX_ID_LENGTH} characters",
        ),
        (
            (scenario_id != "") & ~scenario_id.str.fullmatch(ID_PATTERN),
            "scenario_id may only contain letters, digits, '.', '_' and '-'",
        ),
        (df["start_time"].isna(), "start_time is missing or not a valid timestamp"),
        (df["end_time"].isna(), "end_time is missing or not a valid timestamp"),
        # Comparisons involving NaT evaluate to False, so rows with a bad
        # timestamp are only reported once (by the checks above).
        (df["end_time"] < df["start_time"], "end_time is before start_time"),
        (
            ~df["status"].isin(VALID_STATUSES),
            f"status must be one of {', '.join(sorted(VALID_STATUSES))}",
        ),
        (
            records.isna() | (records < 0) | (records % 1 != 0),
            "records_processed must be a non-negative whole number",
        ),
        (
            run_id.duplicated(keep="first") & (run_id != ""),
            "duplicate run_id in file (first occurrence kept)",
        ),
    ]

    problems: dict[int, list[str]] = {}
    for mask, reason in checks:
        for idx in df.index[mask.fillna(False)]:
            problems.setdefault(idx, []).append(reason)
    return problems
