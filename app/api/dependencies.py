"""FastAPI dependencies. Tests swap these out via ``app.dependency_overrides``."""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, Request, status

from app.config import get_settings
from app.db.repository import ScenarioRepository
from app.processing.engine import ScenarioProcessor


def get_repository(request: Request) -> ScenarioRepository:
    return request.app.state.repository


def get_processor(request: Request) -> ScenarioProcessor:
    return ScenarioProcessor(request.app.state.repository)


def require_import_key(x_api_key: str | None = Header(default=None)) -> None:
    """
    Protects write endpoints with a shared key when IMPORT_API_KEY is set.

    Read endpoints stay public so reviewers can browse the deployment freely.
    """
    expected = get_settings().import_api_key
    if expected is None:
        return
    # compare_digest avoids leaking the key length/prefix through response timing.
    if x_api_key is None or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid X-API-Key header",
        )
