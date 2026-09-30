# Architecture

## Component view

```mermaid
flowchart LR
    subgraph Sources
        CSV[Scenario run CSV files]
        User[Analyst / dashboard]
    end

    subgraph App["Python application (Docker container)"]
        CLI["CLI<br/>python -m app.cli"]
        API["FastAPI<br/>/api/v1/*"]
        subgraph Engine["Processing engine (app/processing)"]
            Loader["loader.py<br/>parse + validate"]
            KPI["kpi.py<br/>pandas KPIs"]
            Proc["engine.py<br/>ScenarioProcessor"]
        end
        Repo["repository.py<br/>SQL access"]
    end

    subgraph DB["PostgreSQL"]
        T1[(scenarios)]
        T2[(scenario_runs)]
        T3[(scenario_summary)]
        SP{{"refresh_scenario_summary()"}}
        V["views: v_scenario_overview,<br/>v_category_summary, v_daily_run_stats"]
    end

    CPP["C++ resource_planner<br/>(standalone console tool)"]

    CSV --> CLI
    CSV -->|POST /imports| API
    User -->|GET /scenarios, /summary| API
    CLI --> Proc
    API --> Proc
    Proc --> Loader --> KPI
    Proc --> Repo
    API --> Repo
    Repo -->|upsert| T1 & T2
    Repo -->|CALL| SP
    SP -->|aggregate| T2
    SP -->|upsert| T3
    Repo -->|SELECT| V
    V --- T1 & T2 & T3
```

## Import sequence

```mermaid
sequenceDiagram
    participant C as Client
    participant A as FastAPI
    participant P as ScenarioProcessor
    participant L as loader / kpi
    participant R as Repository
    participant D as PostgreSQL

    C->>A: POST /api/v1/imports (multipart CSV, X-API-Key)
    A->>A: check key, extension, size limit
    A->>P: process(file)
    P->>L: load_runs() - parse, validate, type
    L-->>P: valid runs + rejected rows
    P->>L: scenario_kpis(), overall_kpis()
    P->>R: save_runs(scenarios, runs)
    R->>D: BEGIN
    R->>D: upsert scenarios, upsert scenario_runs
    R->>D: CALL refresh_scenario_summary(affected ids)
    R->>D: COMMIT
    P-->>A: ProcessingReport
    A-->>C: 201 JSON (KPIs + rejected rows with reasons)
```

## Key decisions

| Decision | Reason |
|---|---|
| Bad rows are skipped and reported instead of failing the file | One typo should not block thousands of good runs; the caller still gets line numbers and reasons. |
| Whole import runs in one transaction | Readers never see a half-loaded file; a failure leaves the DB unchanged. |
| Runs are upserted on `run_id` | Re-sending a file is safe (idempotent), and corrected data overwrites the old values. |
| `scenario_summary` is recalculated in SQL over full history for affected scenarios only | KPIs stay correct across many imports; the cost scales with the file, not the table. |
| KPIs are also computed in pandas per file | Gives immediate feedback for dry runs and the import response; an integration test checks both implementations agree. |
| Repository layer holds all SQL | The API and the engine can be tested without a database. |
| Sync endpoints for DB/pandas work | psycopg2 and pandas are blocking; FastAPI runs sync endpoints in a thread pool so the event loop is never blocked. |
