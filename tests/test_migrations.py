from __future__ import annotations

import os

import pytest
from sqlalchemy import inspect

from pricetracker import db, migrate


def _table_names(conn) -> set[str]:
    return set(inspect(conn).get_table_names())


def test_upgrade_creates_full_schema(tmp_path):
    target = str(tmp_path / "mig.db")
    migrate.upgrade(target)

    conn = db.connect(target)
    names = _table_names(conn)
    conn.close()

    assert {"books", "price_history", "price_changes", "alerts", "runs"} <= names
    assert "alembic_version" in names
    assert migrate.current(target) is not None


def test_upgrade_is_idempotent_and_stamps_revision(tmp_path):
    target = str(tmp_path / "mig.db")
    migrate.upgrade(target)
    first = migrate.current(target)

    migrate.upgrade(target)  # second run is a no-op
    assert migrate.current(target) == first


def test_migrated_schema_accepts_writes(tmp_path):
    target = str(tmp_path / "mig.db")
    migrate.upgrade(target)

    conn = db.connect(target)
    db.upsert_book(conn, url="m1", title="Migrated", price=5.0, rating=3, in_stock=True)

    run_id = db.start_run(conn, "books")
    db.finish_run(conn, run_id, "success", 1, 0, 0, "finished")
    assert db.latest_successful_run(conn)["items"] == 1

    assert db.fetch_catalog(conn)[0]["title"] == "Migrated"
    conn.close()


def test_unmanaged_database_has_no_revision(tmp_path):
    target = str(tmp_path / "plain.db")
    conn = db.connect(target)
    db.init_schema(conn)
    conn.close()

    assert migrate.current(target) is None


@pytest.mark.pg
def test_postgres_migration_roundtrip():
    url = os.environ.get("PRICETRACKER_TEST_PG_URL")
    if not url:
        pytest.skip("set PRICETRACKER_TEST_PG_URL to run the Postgres test")

    conn = db.connect(url)
    db.metadata.drop_all(conn)
    conn.commit()
    conn.close()

    migrate.upgrade(url)
    conn = db.connect(url)
    names = _table_names(conn)
    assert {"books", "price_history", "price_changes", "alerts", "runs"} <= names
    assert migrate.current(url) is not None
    conn.close()
