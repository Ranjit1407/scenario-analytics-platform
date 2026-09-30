# Scenario Analytics Platform

Processes operational scenario runs from CSV, calculates KPIs, stores the results in PostgreSQL and
exposes them through a REST API. A small C++ console tool estimates resource utilisation and
completion times for a set of tasks.

| | |
|---|---|
| **Live API** | `https://<your-service>.onrender.com/docs` *(replace after deploying)* |
| **Health check** | `https://<your-service>.onrender.com/health` |
| **Stack** | Python 3.12 · pandas · FastAPI · PostgreSQL 16 · C++17 · Docker |

## Contents

- [Project structure](#project-structure)
- [Quick start (Docker)](#quick-start-docker)
- [Local setup without Docker](#local-setup-without-docker)
- [Task 1 - Processing engine](#task-1---scenario-processing-engine)
- [Task 2 - Database](#task-2---postgresql-database)
- [Task 3 - REST API](#task-3---rest-api)
- [Task 4 - C++ resource planner](#task-4---c-resource-planner)
- [Task 5 - System design](#task-5---system-design)
- [Task 6 - Deployment](#task-6---deployment)
- [Testing](#testing)

Architecture diagrams are in [docs/architecture.md](docs/architecture.md).

## Project structure

```
app/
  processing/     loader.py (CSV + validation), kpi.py (pandas KPIs), engine.py (pipeline)
  db/             connection.py (pool), repository.py (all SQL)
  api/            main.py (app + error handling), routes.py, schemas.py, dependencies.py
  cli.py          init-db / import / seed commands
  config.py       settings from environment variables
sql/              01_schema, 02_indexes, 03_views_procedures, 04_reporting_queries
data/             sample_scenarios.csv (240 runs), sample_with_errors.csv
cpp/resource_planner/   C++17 console app, tests, CMake
docs/             architecture.md, system_design.md
tests/            pytest suite (unit, API, PostgreSQL integration)
scripts/          generate_sample_data.py
```

## Quick start (Docker)

```bash
docker compose up --build
```

This starts PostgreSQL and the API, creates the schema and loads the sample data.
Open http://localhost:8000/docs for the interactive API documentation.

## Local setup without Docker

Requirements: Python 3.11+ and a PostgreSQL 14+ server.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

createdb scenario_analytics         # or: psql -c "CREATE DATABASE scenario_analytics"
cp .env.example .env                # then set DATABASE_URL for your server

python -m app.cli init-db                           # create tables, indexes, views, procedure
python -m app.cli import data/sample_scenarios.csv  # process and store the sample data
uvicorn app.api.main:app --reload                   # http://localhost:8000/docs
```

Configuration (environment variables or `.env`):

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql://postgres:postgres@localhost:5432/scenario_analytics` | PostgreSQL connection |
| `IMPORT_API_KEY` | *(unset)* | If set, `POST /api/v1/imports` requires header `X-API-Key` |
| `MAX_UPLOAD_MB` | `10` | Upload size limit |
| `LOG_LEVEL` | `INFO` | Logging level |
| `DB_POOL_MIN` / `DB_POOL_MAX` | `1` / `5` | Connection pool size |

## Task 1 - Scenario processing engine

`app/processing` is independent of the API and database. The same code path is used by the CLI and
the upload endpoint.

**Input format**

```csv
run_id,scenario_id,scenario_name,category,start_time,end_time,status,records_processed,error_message
RUN-000001,SCN-003,Offshore vessel weather window,installation,2026-09-01T00:49:00Z,2026-09-01T01:52:13Z,SUCCESS,5065,
```

Required: `run_id`, `scenario_id`, `start_time`, `end_time`, `status` (`SUCCESS`/`FAILED`,
case-insensitive). Timestamps are ISO 8601; values without an offset are treated as UTC.

**KPIs per scenario:** total runs, successful/failed runs, success rate, total/avg/min/max/p95 duration,
total records processed, throughput (records/s), first/last run. Portfolio totals are calculated as well.

**Error handling**

- File-level problems (unreadable file, missing columns) raise `CsvFormatError`. The API returns 400 and
  the CLI exits with code 2.
- Row-level problems (bad timestamp, end before start, unknown status, negative count, duplicate
  `run_id`, invalid id characters) skip only that row. Every reason is logged and returned with its line number.
- `--strict` makes the CLI exit with code 1 if any row was rejected, which is useful in a scheduled pipeline.

```bash
python -m app.cli import data/sample_with_errors.csv --dry-run   # shows 6 rejected rows and why
python -m app.cli import data/sample_scenarios.csv --json         # machine-readable report
```

## Task 2 - PostgreSQL database

| Script | Contents |
|---|---|
| [sql/01_schema.sql](sql/01_schema.sql) | `scenarios`, `scenario_runs`, `scenario_summary` with PK/FK and CHECK constraints |
| [sql/02_indexes.sql](sql/02_indexes.sql) | composite index for "latest runs of a scenario", BRIN on `start_time`, partial index on failures, category index |
| [sql/03_views_procedures.sql](sql/03_views_procedures.sql) | `refresh_scenario_summary()` procedure; views `v_scenario_overview`, `v_category_summary`, `v_daily_run_stats` |
| [sql/04_reporting_queries.sql](sql/04_reporting_queries.sql) | portfolio overview, worst scenarios, top failure reasons, 7-day moving success rate, p95 outliers, consistency check |

All schema scripts are idempotent. `python -m app.cli init-db` applies 01-03. They can also be run
with `psql -f`.

Each import is a single transaction: upsert scenarios, upsert runs, then
`CALL refresh_scenario_summary(<affected scenario ids>)`. The summary is recalculated from the full run
history of those scenarios only. It therefore stays correct across repeated imports, and the cost scales
with the file size rather than the table size.

## Task 3 - REST API

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/scenarios` | List scenarios with KPIs. Query: `category`, `sort` (`scenario_id`, `name`, `total_runs`, `success_rate`, `failed_runs`, `avg_duration_seconds`, `last_run_at`), `order` (`asc`/`desc`), `limit` (1-200), `offset` |
| GET | `/api/v1/scenarios/{scenario_id}` | Scenario details and the full KPI set (404 if unknown) |
| GET | `/api/v1/scenarios/{scenario_id}/runs` | Runs, newest first. Query: `status`, `limit`, `offset` |
| GET | `/api/v1/summary` | Portfolio KPIs and breakdown by category |
| GET | `/api/v1/summary/daily?days=14` | Daily run count and success rate |
| POST | `/api/v1/imports` | Upload a CSV (multipart field `file`). `?dry_run=true` validates without storing |
| GET | `/health` | Liveness and database status (503 when the DB is down) |

Validation is done with typed query/path parameters and Pydantic models. Invalid input returns
`422` with details, unknown resources return `404`, and all errors are JSON (`{"detail": "..."}`).

```bash
curl "http://localhost:8000/api/v1/scenarios?sort=success_rate&order=asc&limit=5"
curl "http://localhost:8000/api/v1/scenarios/SCN-003"
curl "http://localhost:8000/api/v1/scenarios/SCN-003/runs?status=FAILED"
curl "http://localhost:8000/api/v1/summary"
curl -X POST "http://localhost:8000/api/v1/imports" \
     -H "X-API-Key: local-dev-key" -F "file=@data/sample_with_errors.csv"
```

## Task 4 - C++ resource planner

`cpp/resource_planner` is a C++17 console application. Given tasks (`task_id,volume,rate_per_hour`)
and a number of identical resources, it:

- converts each task into effort hours (`volume / rate_per_hour`)
- schedules the tasks with the **LPT heuristic**: the longest task goes to the resource that becomes free
  first, tracked with a `std::priority_queue` (O(n log n), guaranteed within 4/3 of optimal)
- reports the estimated completion time (hours and working days), the theoretical lower bound, per-resource
  and overall utilisation, and the finish time of each task
- with `--sweep N`, compares 1..N resources in parallel (`std::async`) to answer "how many crews do we need?"

```bash
cd cpp/resource_planner
cmake -S . -B build && cmake --build build
ctest --test-dir build                     # unit tests
./build/resource_planner data/tasks.csv --resources 3 --hours-per-day 8 --sweep 6
```

Without CMake:

```bash
g++ -std=c++17 -O2 -Wall -Wextra -Iinclude src/planner.cpp src/main.cpp -o resource_planner -pthread
```

> On Windows with MinGW, add `-static` if the program exits silently. That happens when an older
> `libstdc++-6.dll` from another program comes earlier on `PATH`.

Input is validated (field count, numeric values, positive rate, duplicate ids), and errors report the
line number.

## Task 5 - System design

See [docs/system_design.md](docs/system_design.md) for scaling from 100 to 10,000 scenarios per
day: queue-based ingestion, autoscaled workers, table partitioning, COPY-based loading, read replicas
and connection pooling.

## Task 6 - Deployment

The app ships as one Docker image. On start it applies the schema, seeds the sample data if the
database is empty, and serves on `$PORT`.

**Render (free tier, used for the live URL)**

1. Push this repository to GitHub.
2. In Render: **New → Blueprint** and select the repository. [render.yaml](render.yaml) creates the
   PostgreSQL database and the web service, and generates `IMPORT_API_KEY`.
3. When the deploy is green, open `https://<service>.onrender.com/docs`.

Free Render services sleep when idle, so the first request after a pause can take about 30-60 s.

**Azure Container Apps (alternative)**

```bash
az group create -n rg-scenario -l westeurope
az postgres flexible-server create -g rg-scenario -n <pg-name> --tier Burstable --sku-name Standard_B1ms \
   --admin-user pgadmin --admin-password <password> --public-access 0.0.0.0
az postgres flexible-server db create -g rg-scenario -s <pg-name> -d scenario_analytics
az containerapp up -g rg-scenario -n scenario-analytics --source . --ingress external --target-port 8000 \
   --env-vars DATABASE_URL="postgresql://pgadmin:<password>@<pg-name>.postgres.database.azure.com:5432/scenario_analytics?sslmode=require" \
              IMPORT_API_KEY=<key>
```

## Testing

```bash
pytest                      # unit + API tests, no database needed
```

PostgreSQL integration tests run when `TEST_DATABASE_URL` points at a **disposable** database. They
drop and recreate the tables. They also check that the SQL procedure and the pandas code produce
identical KPIs.

```bash
createdb scenario_analytics_test
TEST_DATABASE_URL=postgresql://postgres:<password>@localhost:5432/scenario_analytics_test pytest
```

GitHub Actions ([.github/workflows/ci.yml](.github/workflows/ci.yml)) runs the full Python suite
against a PostgreSQL service container, builds and tests the C++ project, and builds the Docker image.

Regenerate the sample data (deterministic seed):

```bash
python scripts/generate_sample_data.py --runs-per-scenario 30 --days 21
```
