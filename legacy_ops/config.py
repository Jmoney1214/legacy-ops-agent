from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    """Runtime settings loaded from environment variables.

    The platform intentionally starts with SQLite so the service can run in any
    cloud container without requiring external infrastructure. DATABASE_URL is
    retained for a future Postgres/Supabase adapter.
    """

    environment: str
    database_path: Path
    database_url: str | None
    app_api_token: str | None
    approval_required: bool
    default_timezone: str

    @classmethod
    def from_env(cls) -> "Settings":
        db_path = Path(os.getenv("LEGACY_OPS_DB_PATH", "data/legacy_ops.db"))
        return cls(
            environment=os.getenv("LEGACY_OPS_ENV", "development"),
            database_path=db_path,
            database_url=os.getenv("DATABASE_URL"),
            app_api_token=os.getenv("LEGACY_OPS_API_TOKEN"),
            approval_required=os.getenv("LEGACY_OPS_APPROVAL_REQUIRED", "true").lower()
            not in {"0", "false", "no"},
            default_timezone=os.getenv("LEGACY_OPS_TIMEZONE", "America/New_York"),
        )
