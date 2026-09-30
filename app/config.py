"""
Application settings.

Everything is read from environment variables so the same image can run
locally, in CI and on the hosting platform without code changes. For local
development a ``.env`` file in the project root is picked up as well; values
already present in the environment always win over the file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/scenario_analytics"


def _load_dotenv(path: Path) -> None:
    """Minimal .env reader: KEY=VALUE lines, '#' comments, optional quotes."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Settings:
    database_url: str
    log_level: str
    import_api_key: str | None
    max_upload_bytes: int
    db_pool_min: int
    db_pool_max: int


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    _load_dotenv(PROJECT_ROOT / ".env")

    return Settings(
        database_url=os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        # An empty string is treated the same as "not configured".
        import_api_key=os.getenv("IMPORT_API_KEY") or None,
        max_upload_bytes=int(os.getenv("MAX_UPLOAD_MB", "10")) * 1024 * 1024,
        db_pool_min=int(os.getenv("DB_POOL_MIN", "1")),
        db_pool_max=int(os.getenv("DB_POOL_MAX", "5")),
    )
