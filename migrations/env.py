from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool

from pricetracker import db as db_module
from pricetracker.config import get_settings

config = context.config
target_metadata = db_module.metadata


def _database_url() -> str:
    x_args = context.get_x_argument(as_dictionary=True)
    override = x_args.get("db") or config.get_main_option("sqlalchemy.url")
    if override:
        return db_module.normalize_url(override)
    return db_module.normalize_url(get_settings().database_url)


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
