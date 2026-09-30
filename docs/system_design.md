# Scaling from 100 to 10,000 scenarios per day

## Where we start

The current deployment is one container (FastAPI + processing engine) and one PostgreSQL instance.
At 100 scenarios/day, with each scenario producing tens of runs, that is a few thousand rows per day.
One small instance handles this easily; imports take well under a second.

10,000 scenarios/day is roughly **7 per minute on average**, with realistic peaks of 10-20x that
when batches arrive together. Assuming ~50 runs per scenario, that is **500k run rows per day, or about
180M rows per year**. At that point the bottleneck is no longer CPU. It is (1) doing the processing
inside the HTTP request, (2) the size of `scenario_runs`, and (3) contention on shared summary rows.

## Target architecture

```
 Clients ──> API (N replicas, stateless) ──> Blob storage (raw CSV)
                    │                               │
                    └── enqueue job ──> Queue ──> Workers (autoscaled on queue depth)
                                                    │
                          PostgreSQL primary <──────┘   (writes)
                          Read replica(s)    <── API reads / reporting
```

1. **Decouple ingestion from processing.** `POST /imports` stores the file in blob storage
   (Azure Blob Storage) and puts a job on a queue (Azure Service Bus or Storage Queue). It returns
   `202 Accepted` with a job id, and the client polls `GET /imports/{id}`. `ScenarioProcessor` already
   has no HTTP dependency, so it moves into a worker unchanged.
2. **Scale workers horizontally.** Workers are stateless containers (Azure Container Apps with a KEDA
   queue-length scaler). They scale from zero to N when batches arrive. Imports are idempotent (upsert on
   `run_id`), so a retried or duplicated message is harmless.
3. **Scale the API separately.** The read endpoints are stateless. Run 2+ replicas behind the platform
   load balancer, and cache `/summary` responses for 30-60 s, since they change only when an import
   finishes.

## Database

- **Partition `scenario_runs` by month** on `start_time` (declarative partitioning). Recent queries touch
  one or two partitions, old partitions can be detached and archived, and each partition's indexes stay small.
  The BRIN index on `start_time` is already chosen with this growth in mind.
- **Bulk load with `COPY`** into a staging table and then `INSERT ... ON CONFLICT` from there, instead of
  multi-row `INSERT`. This is about 5-10x faster for large files.
- **Summary refresh contention.** Refreshing only the affected scenarios already bounds the work. If many
  workers hit the same scenario, move the refresh to a per-scenario debounced job (for example, at most once
  per minute per scenario) so concurrent imports do not serialise on the same `scenario_summary` rows.
- **Read replicas** for the API and reporting queries, keeping the primary for writes. Use PgBouncer in
  transaction mode so many worker and API replicas do not exhaust PostgreSQL connections.
- For analytics over long history, add **rollup tables** (daily per scenario) maintained by the
  worker. Dashboards then read thousands of rows instead of scanning millions.

## Processing engine

- Stream very large files in chunks (`pd.read_csv(chunksize=...)`) to keep worker memory flat.
- CPU-heavy simulation or optimisation steps (for example the C++ planner) can be exposed to Python
  via pybind11 or run as a separate worker type on the same queue.

## Reliability and operations

- **Dead-letter queue** for files that repeatedly fail, plus a status table (`import_jobs`) recording
  rows read, loaded and rejected for every import. This gives execution tracking and auditability.
- **Observability.** Structured JSON logs with the job id, Azure Monitor / Application Insights metrics
  (queue depth, import latency, rejected-row rate) and alerts on success-rate drops.
- **CI/CD.** The existing pipeline (tests, C++ build, Docker build) extends to push images to a
  container registry and deploy to staging, then production, with schema migrations applied as a gated step.

## Capacity summary

| | 100 / day | 10,000 / day |
|---|---|---|
| Ingestion | synchronous request | blob + queue, 202 Accepted |
| Compute | 1 container | API ×2-3, workers 0-N autoscaled |
| `scenario_runs` growth | ~5k rows/day | ~500k rows/day, monthly partitions |
| Load method | multi-row INSERT | COPY + staging upsert |
| Reads | primary | read replica + short-lived cache |
| Connections | app pool | PgBouncer |
