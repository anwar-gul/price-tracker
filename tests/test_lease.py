from __future__ import annotations

import os

import pytest

from pricetracker import db
from pricetracker.cli import _lease_step
from pricetracker.log import get_logger


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "lease.db")
    db.init_schema(connection)
    yield connection
    connection.close()


def test_lease_acquire_blocks_second_owner(conn):
    assert db.acquire_lease(conn, db.WATCH_LEASE, "a", 60) is True
    assert db.acquire_lease(conn, db.WATCH_LEASE, "b", 60) is False

    lease = db.fetch_lease(conn, db.WATCH_LEASE)
    assert lease["owner"] == "a"


def test_lease_same_owner_can_reacquire(conn):
    assert db.acquire_lease(conn, db.WATCH_LEASE, "a", 60) is True
    assert db.acquire_lease(conn, db.WATCH_LEASE, "a", 60) is True


def test_expired_lease_can_be_stolen(conn):
    assert db.acquire_lease(conn, db.WATCH_LEASE, "a", 60) is True
    db.expire_lease(conn, db.WATCH_LEASE)

    assert db.acquire_lease(conn, db.WATCH_LEASE, "b", 60) is True
    assert db.fetch_lease(conn, db.WATCH_LEASE)["owner"] == "b"


def test_renew_only_by_owner(conn):
    db.acquire_lease(conn, db.WATCH_LEASE, "a", 60)

    assert db.renew_lease(conn, db.WATCH_LEASE, "b", 60) is False
    assert db.renew_lease(conn, db.WATCH_LEASE, "a", 60) is True


def test_release_allows_takeover(conn):
    db.acquire_lease(conn, db.WATCH_LEASE, "a", 60)

    assert db.release_lease(conn, db.WATCH_LEASE, "b") is False
    assert db.release_lease(conn, db.WATCH_LEASE, "a") is True
    assert db.acquire_lease(conn, db.WATCH_LEASE, "b", 60) is True


def test_lease_step_follows_leadership(conn):
    logger = get_logger("test")

    assert _lease_step(conn, "a", 60, False, logger) is True   # acquire
    assert _lease_step(conn, "a", 60, True, logger) is True    # renew
    assert _lease_step(conn, "b", 60, False, logger) is False  # standby
    assert _lease_step(conn, "b", 60, True, logger) is False   # lost mid-cycle, cannot re-acquire


@pytest.mark.pg
def test_postgres_lease_semantics():
    url = os.environ.get("PRICETRACKER_TEST_PG_URL")
    if not url:
        pytest.skip("set PRICETRACKER_TEST_PG_URL to run the Postgres test")

    conn = db.connect(url)
    db.init_schema(conn)
    conn.execute(db.locks.delete())
    conn.commit()

    assert db.acquire_lease(conn, db.WATCH_LEASE, "node-a", 60) is True
    assert db.acquire_lease(conn, db.WATCH_LEASE, "node-b", 60) is False

    db.expire_lease(conn, db.WATCH_LEASE)
    assert db.acquire_lease(conn, db.WATCH_LEASE, "node-b", 60) is True
    assert db.renew_lease(conn, db.WATCH_LEASE, "node-a", 60) is False
    assert db.renew_lease(conn, db.WATCH_LEASE, "node-b", 60) is True
    assert db.release_lease(conn, db.WATCH_LEASE, "node-b") is True

    conn.execute(db.locks.delete())
    conn.commit()
    conn.close()
