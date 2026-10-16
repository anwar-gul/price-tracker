from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from pricetracker import db

ROOT = Path(__file__).resolve().parents[1]


def _config(database_url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", db.normalize_url(database_url))
    return config


def upgrade(database_url: str, revision: str = "head") -> None:
    command.upgrade(_config(database_url), revision)


def current(database_url: str) -> str | None:
    """Return the current revision of the database, or None if unmanaged."""
    from alembic.runtime.migration import MigrationContext

    conn = db.connect(database_url)
    try:
        context = MigrationContext.configure(conn)
        return context.get_current_revision()
    finally:
        conn.close()
