"""
Generate a realistic-looking sample CSV of scenario runs.

The output is deterministic (fixed seed) so the committed sample file and the
numbers quoted in the README stay in sync. Usage:

    python scripts/generate_sample_data.py                 # writes data/sample_scenarios.csv
    python scripts/generate_sample_data.py --runs-per-scenario 500 --out big.csv
"""

from __future__ import annotations

import argparse
import csv
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

# (scenario_id, name, category, typical duration in minutes, failure probability,
#  records processed per minute)
SCENARIOS = [
    ("SCN-001", "Blade transport - port to site", "logistics", 42, 0.06, 120),
    ("SCN-002", "Crane allocation - onshore installation", "installation", 18, 0.04, 300),
    ("SCN-003", "Offshore vessel weather window", "installation", 65, 0.15, 80),
    ("SCN-004", "Lifting yoke fleet utilisation", "equipment", 12, 0.02, 450),
    ("SCN-005", "Port storage layout optimisation", "logistics", 30, 0.08, 200),
    ("SCN-006", "Service crew rostering", "operations", 25, 0.05, 260),
    ("SCN-007", "Tower section delivery sequencing", "logistics", 38, 0.10, 150),
    ("SCN-008", "Spare parts stock forecast", "operations", 8, 0.03, 900),
]

ERRORS = [
    "Solver did not converge within iteration limit",
    "Input dataset missing vessel availability",
    "Timeout waiting for weather forecast service",
    "Constraint violation: crane capacity exceeded",
    "Database connection lost during result write",
]

FIELDS = [
    "run_id",
    "scenario_id",
    "scenario_name",
    "category",
    "start_time",
    "end_time",
    "status",
    "records_processed",
    "error_message",
]


def generate(runs_per_scenario: int, days: int, seed: int) -> list[dict[str, str]]:
    rng = random.Random(seed)
    period_start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    rows = []

    for scenario_id, name, category, minutes, fail_p, rate in SCENARIOS:
        for _ in range(runs_per_scenario):
            start = period_start + timedelta(minutes=rng.randrange(days * 24 * 60))
            # Log-normal-ish spread: most runs near the typical time, a few slow outliers.
            duration_min = max(0.5, rng.lognormvariate(0, 0.35) * minutes)
            failed = rng.random() < fail_p
            if failed:
                # Failed runs usually stop part-way through.
                duration_min *= rng.uniform(0.1, 0.8)
            end = start + timedelta(minutes=duration_min)
            records = int(duration_min * rate * rng.uniform(0.85, 1.15))

            rows.append(
                {
                    "scenario_id": scenario_id,
                    "scenario_name": name,
                    "category": category,
                    "start_time": start.isoformat(timespec="seconds").replace("+00:00", "Z"),
                    "end_time": end.isoformat(timespec="seconds").replace("+00:00", "Z"),
                    "status": "FAILED" if failed else "SUCCESS",
                    "records_processed": str(records),
                    "error_message": rng.choice(ERRORS) if failed else "",
                }
            )

    rows.sort(key=lambda r: r["start_time"])
    for number, row in enumerate(rows, start=1):
        row["run_id"] = f"RUN-{number:06d}"
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--runs-per-scenario", type=int, default=30)
    parser.add_argument("--days", type=int, default=21)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "data" / "sample_scenarios.csv",
    )
    args = parser.parse_args()

    rows = generate(args.runs_per_scenario, args.days, args.seed)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {args.out}")


if __name__ == "__main__":
    main()
