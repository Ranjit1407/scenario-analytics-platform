"""
Command-line entry point.

    python -m app.cli init-db                      create/upgrade the schema
    python -m app.cli import data/sample.csv       process a file and store it
    python -m app.cli import data/sample.csv --dry-run --json
    python -m app.cli seed                         load the sample data if the DB is empty
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import pandas as pd

from app.config import PROJECT_ROOT, get_settings
from app.logging_config import setup_logging
from app.processing.engine import ProcessingReport, ScenarioProcessor
from app.processing.loader import CsvFormatError

logger = logging.getLogger("app.cli")

SQL_DIR = PROJECT_ROOT / "sql"
# Order matters: indexes and procedures depend on the tables.
# 04_reporting_queries.sql is for manual use only and is not applied here.
SCHEMA_SCRIPTS = ("01_schema.sql", "02_indexes.sql", "03_views_procedures.sql")
SAMPLE_CSV = PROJECT_ROOT / "data" / "sample_scenarios.csv"


def _database():
    # Imported lazily so `import --dry-run` never touches the database layer.
    from app.db.connection import Database

    settings = get_settings()
    return Database(settings.database_url)


def cmd_init_db(_: argparse.Namespace) -> int:
    db = _database()
    try:
        for name in SCHEMA_SCRIPTS:
            db.run_script(SQL_DIR / name)
    finally:
        db.close()
    logger.info("Database schema is up to date")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    path = Path(args.csv)
    if not path.is_file():
        logger.error("File not found: %s", path)
        return 2

    db = None
    repository = None
    if not args.dry_run:
        from app.db.repository import ScenarioRepository

        db = _database()
        repository = ScenarioRepository(db)

    try:
        report = ScenarioProcessor(repository).process(
            path, source_name=path.name, persist=not args.dry_run
        )
    except CsvFormatError as exc:
        logger.error("Cannot process %s: %s", path, exc)
        return 2
    finally:
        if db is not None:
            db.close()

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        _print_report(report)

    # Non-zero exit when rows were rejected lets a scheduler/pipeline flag the file.
    return 1 if report.rows_rejected and args.strict else 0


def cmd_seed(args: argparse.Namespace) -> int:
    from app.db.repository import ScenarioRepository

    db = _database()
    try:
        repository = ScenarioRepository(db)
        if repository.has_runs():
            logger.info("Database already contains runs - skipping seed")
            return 0
        ScenarioProcessor(repository).process(Path(args.csv), source_name=Path(args.csv).name)
    finally:
        db.close()
    return 0


def _print_report(report: ProcessingReport) -> None:
    overall = report.overall
    print()
    print(f"Source           : {report.source}")
    print(
        f"Rows             : {report.rows_read} read, {report.rows_loaded} loaded, "
        f"{report.rows_rejected} rejected"
    )
    print(f"Stored in DB     : {'yes' if report.persisted else 'no (dry run)'}")
    if overall["total_runs"]:
        print(f"Scenarios        : {overall['scenario_count']}")
        print(
            f"Success rate     : {overall['success_rate']:.2f}% "
            f"({overall['failed_runs']} failed of {overall['total_runs']})"
        )
        print(f"Total duration   : {overall['total_duration_seconds'] / 3600:.2f} h")
        print(f"Period           : {overall['period_start']} -> {overall['period_end']}")

    if not report.scenario_summary.empty:
        columns = [
            "scenario_id",
            "total_runs",
            "failed_runs",
            "success_rate",
            "avg_duration_seconds",
            "p95_duration_seconds",
            "throughput_records_per_sec",
        ]
        print("\nPer-scenario KPIs:")
        with pd.option_context("display.width", 160, "display.max_columns", None):
            print(report.scenario_summary[columns].to_string(index=False))

    if report.rejected:
        print("\nRejected rows:")
        for row in report.rejected:
            print(f"  line {row.line_number:>5} {row.run_id or '<no id>'}: {'; '.join(row.reasons)}")

    for warning in report.warnings:
        print(f"\nWarning: {warning}")
    print()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init-db", help="create or upgrade the database schema")
    init.set_defaults(func=cmd_init_db)

    imp = sub.add_parser("import", help="process a scenario-run CSV file")
    imp.add_argument("csv", help="path to the CSV file")
    imp.add_argument("--dry-run", action="store_true", help="validate and report only, do not store")
    imp.add_argument("--json", action="store_true", help="print the report as JSON")
    imp.add_argument("--strict", action="store_true", help="exit with code 1 if any row is rejected")
    imp.set_defaults(func=cmd_import)

    seed = sub.add_parser("seed", help="import the sample data if the database has no runs")
    seed.add_argument("--csv", default=str(SAMPLE_CSV), help="CSV to seed from")
    seed.set_defaults(func=cmd_seed)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(get_settings().log_level)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
