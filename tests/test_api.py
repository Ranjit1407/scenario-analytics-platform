"""
API tests against an in-memory repository.

These cover routing, validation and response shape. The SQL itself is
exercised by test_repository_integration.py against a real database.
"""

from __future__ import annotations

import textwrap
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_processor, get_repository
from app.api.main import create_app
from app.config import get_settings
from app.processing.engine import ScenarioProcessor

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)

SCENARIO_ROW = {
    "scenario_id": "SCN-001",
    "name": "Blade transport",
    "category": "logistics",
    "created_at": NOW,
    "updated_at": NOW,
    "total_runs": 10,
    "successful_runs": 9,
    "failed_runs": 1,
    "success_rate": Decimal("90.00"),
    "total_duration_seconds": Decimal("1000.000"),
    "avg_duration_seconds": Decimal("100.000"),
    "min_duration_seconds": Decimal("50.000"),
    "max_duration_seconds": Decimal("200.000"),
    "p95_duration_seconds": Decimal("180.000"),
    "total_records_processed": 5000,
    "throughput_records_per_sec": Decimal("5.000"),
    "first_run_at": NOW,
    "last_run_at": NOW,
    "refreshed_at": NOW,
}


class FakeRepository:
    def __init__(self):
        self.list_args = None
        self.saved = []

    def list_scenarios(self, category, sort, descending, limit, offset):
        self.list_args = dict(category=category, sort=sort, descending=descending, limit=limit, offset=offset)
        item = {k: SCENARIO_ROW[k] for k in (
            "scenario_id", "name", "category", "total_runs", "failed_runs",
            "success_rate", "avg_duration_seconds", "last_run_at",
        )}
        return [item], 1

    def get_scenario(self, scenario_id):
        if scenario_id == "SCN-001":
            return dict(SCENARIO_ROW)
        if scenario_id == "SCN-EMPTY":
            row = {k: None for k in SCENARIO_ROW}
            row.update(scenario_id="SCN-EMPTY", name="No runs", category="x", created_at=NOW, updated_at=NOW)
            return row
        return None

    def list_runs(self, scenario_id, status, limit, offset):
        run = {
            "run_id": "RUN-1",
            "status": "FAILED",
            "start_time": NOW,
            "end_time": NOW,
            "duration_seconds": Decimal("12.5"),
            "records_processed": 10,
            "error_message": "boom",
        }
        return ([run] if status in (None, "FAILED") else []), (1 if status in (None, "FAILED") else 0)

    def overall_summary(self):
        return {
            "scenario_count": 1, "total_runs": 10, "successful_runs": 9, "failed_runs": 1,
            "success_rate": Decimal("90.00"), "total_duration_seconds": Decimal("1000"),
            "avg_duration_seconds": Decimal("100"), "total_records_processed": 5000,
            "period_start": NOW, "period_end": NOW,
        }

    def category_summary(self):
        return [{
            "category": "logistics", "scenario_count": 1, "total_runs": 10, "successful_runs": 9,
            "failed_runs": 1, "success_rate": Decimal("90"), "avg_duration_seconds": Decimal("100"),
            "total_records_processed": 5000,
        }]

    def daily_stats(self, days):
        return [{
            "run_date": date(2026, 9, 20), "total_runs": 4, "successful_runs": 3, "failed_runs": 1,
            "success_rate": Decimal("75.00"), "avg_duration_seconds": Decimal("12.000"),
            "total_records_processed": 100,
        }]

    def save_runs(self, scenarios, runs, source_file):
        self.saved.append(source_file)


@pytest.fixture
def repo():
    return FakeRepository()


@pytest.fixture
def client(repo, monkeypatch):
    # Empty string = "not configured"; setenv (rather than delenv) also stops a local .env from re-adding a key.
    monkeypatch.setenv("IMPORT_API_KEY", "")
    get_settings.cache_clear()

    app = create_app()
    app.dependency_overrides[get_repository] = lambda: repo
    app.dependency_overrides[get_processor] = lambda: ScenarioProcessor(repo)
    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()


def test_list_scenarios(client, repo):
    response = client.get("/api/v1/scenarios", params={"sort": "success_rate", "order": "desc", "limit": 10})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["scenario_id"] == "SCN-001"
    assert body["items"][0]["success_rate"] == 90.0
    assert repo.list_args == dict(category=None, sort="success_rate", descending=True, limit=10, offset=0)


@pytest.mark.parametrize(
    "params",
    [{"limit": 0}, {"limit": 201}, {"offset": -1}, {"sort": "password"}, {"order": "sideways"}],
)
def test_list_scenarios_rejects_bad_query(client, params):
    assert client.get("/api/v1/scenarios", params=params).status_code == 422


def test_get_scenario(client):
    response = client.get("/api/v1/scenarios/SCN-001")

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Blade transport"
    assert body["kpis"]["p95_duration_seconds"] == 180.0


def test_get_scenario_without_runs_has_null_kpis(client):
    response = client.get("/api/v1/scenarios/SCN-EMPTY")
    assert response.status_code == 200
    assert response.json()["kpis"] is None


def test_get_unknown_scenario_returns_404(client):
    response = client.get("/api/v1/scenarios/NOPE")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"]


def test_invalid_scenario_id_is_rejected(client):
    assert client.get("/api/v1/scenarios/bad%20id").status_code == 422


def test_list_runs_with_status_filter(client):
    failed = client.get("/api/v1/scenarios/SCN-001/runs", params={"status": "FAILED"}).json()
    succeeded = client.get("/api/v1/scenarios/SCN-001/runs", params={"status": "SUCCESS"}).json()

    assert failed["total"] == 1 and failed["items"][0]["error_message"] == "boom"
    assert succeeded["total"] == 0


def test_list_runs_bad_status(client):
    assert client.get("/api/v1/scenarios/SCN-001/runs", params={"status": "MAYBE"}).status_code == 422


def test_list_runs_unknown_scenario(client):
    assert client.get("/api/v1/scenarios/NOPE/runs").status_code == 404


def test_summary(client):
    body = client.get("/api/v1/summary").json()
    assert body["overall"]["success_rate"] == 90.0
    assert body["by_category"][0]["category"] == "logistics"


def test_daily_summary(client):
    response = client.get("/api/v1/summary/daily", params={"days": 7})
    assert response.status_code == 200
    assert response.json()[0]["run_date"] == "2026-09-20"
    assert client.get("/api/v1/summary/daily", params={"days": 0}).status_code == 422


def test_import_csv(client, repo, valid_csv):
    content = textwrap.dedent(valid_csv).lstrip().encode()
    response = client.post("/api/v1/imports", files={"file": ("runs.csv", content, "text/csv")})

    assert response.status_code == 201
    body = response.json()
    assert body["rows_loaded"] == 4
    assert body["persisted"] is True
    assert repo.saved == ["runs.csv"]


def test_import_dry_run(client, repo, valid_csv):
    content = textwrap.dedent(valid_csv).lstrip().encode()
    response = client.post(
        "/api/v1/imports", params={"dry_run": True}, files={"file": ("runs.csv", content)}
    )
    assert response.status_code == 201
    assert response.json()["persisted"] is False
    assert repo.saved == []


def test_import_rejects_non_csv(client):
    response = client.post("/api/v1/imports", files={"file": ("data.xlsx", b"PK\x03\x04")})
    assert response.status_code == 400


def test_import_rejects_malformed_csv(client):
    response = client.post("/api/v1/imports", files={"file": ("bad.csv", b"just,some,columns\n1,2,3\n")})
    assert response.status_code == 400
    assert "Missing required column" in response.json()["detail"]


def test_import_requires_api_key_when_configured(client, monkeypatch, valid_csv):
    monkeypatch.setenv("IMPORT_API_KEY", "secret")
    get_settings.cache_clear()
    files = {"file": ("runs.csv", b"run_id,scenario_id,start_time,end_time,status\n")}

    assert client.post("/api/v1/imports", files=files).status_code == 401
    assert client.post("/api/v1/imports", files=files, headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.post("/api/v1/imports", files=files, headers={"X-API-Key": "secret"}).status_code == 201


def test_root_redirects_to_docs(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/docs"
