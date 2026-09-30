"""
FastAPI application entry point.

Run locally with:
    uvicorn app.api.main:app --reload
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import psycopg2
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse, RedirectResponse

from app import __version__
from app.api.routes import router
from app.api.schemas import HealthResponse
from app.config import get_settings
from app.db.connection import Database
from app.db.repository import ScenarioRepository
from app.logging_config import setup_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    setup_logging(settings.log_level)

    db = Database(settings.database_url, settings.db_pool_min, settings.db_pool_max)
    app.state.db = db
    app.state.repository = ScenarioRepository(db)

    if settings.import_api_key is None:
        logger.warning("IMPORT_API_KEY is not set - POST /api/v1/imports is open to anyone")

    logger.info("Scenario Analytics API %s started", __version__)
    yield
    db.close()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Scenario Analytics API",
        version=__version__,
        description=(
            "Processes operational scenario runs from CSV, stores them in PostgreSQL "
            "and exposes scenario KPIs over REST."
        ),
        lifespan=lifespan,
    )
    app.include_router(router)

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse(url="/docs")

    @app.get("/health", response_model=HealthResponse, tags=["health"])
    def health(request: Request) -> JSONResponse:
        """Liveness/readiness probe used by the hosting platform."""
        db_ok = request.app.state.db.ping()
        body = HealthResponse(
            status="ok" if db_ok else "degraded",
            database="up" if db_ok else "down",
            version=__version__,
        )
        return JSONResponse(
            status_code=status.HTTP_200_OK if db_ok else status.HTTP_503_SERVICE_UNAVAILABLE,
            content=body.model_dump(),
        )

    @app.exception_handler(psycopg2.OperationalError)
    async def database_unavailable(_: Request, exc: psycopg2.OperationalError) -> JSONResponse:
        logger.error("Database unavailable: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "Database is currently unavailable, please retry shortly"},
        )

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        # Log the full traceback server-side but keep internals out of the response.
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal server error"},
        )

    return app


app = create_app()
