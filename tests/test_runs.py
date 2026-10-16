from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import insert

from pricetracker import db
from pricetracker.cli import cmd_prune, cmd_status


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "runs.db")
    db.init_schema(connection)
    yield connection
    connection.close()


def test_normalize_url_paths_and_urls():
    assert db.normalize_url("data/x.db") == "sqlite:///data/x.db"
    assert db.normalize_url("sqlite:///data/x.db") == "sqlite:///data/x.db"
    assert db.normalize_url("postgresql+psycopg://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    with pytest.raises(ValueError):
        db.normalize_url("mysql://nope")


def test_run_lifecycle(conn):
    run_id = db.start_run(conn, "books")
    assert run_id > 0
    db.finish_run(conn, run_id, status="success", items=20, changes=1, drops=1,
                  message="finished")

    runs = db.fetch_runs(conn)
    assert runs[0]["spider"] == "books"
    assert runs[0]["status"] == "success"
    assert runs[0]["items"] == 20

    latest = db.latest_successful_run(conn)
    assert latest["id"] == run_id


def test_latest_successful_run_respects_before_id(conn):
    first = db.start_run(conn, "books")
    db.finish_run(conn, first, "success", 10, 0, 0, "finished")
    second = db.start_run(conn, "books")
    db.finish_run(conn, second, "success", 0, 0, 0, "finished")

    assert db.latest_successful_run(conn)["id"] == second
    assert db.latest_successful_run(conn, before_id=second)["id"] == first


def test_failed_runs_are_not_latest_success(conn):
    bad = db.start_run(conn, "books")
    db.finish_run(conn, bad, "failed", 0, 0, 0, "shutdown")
    assert db.latest_successful_run(conn) is None


def test_alert_records_and_cooldown_lookup(conn):
    db.record_alert(conn, "https://x/1", "drop", "T", "detail", "sent")
    db.record_alert(conn, "https://x/1", "drop", "T", "detail", "suppressed")
    db.record_alert(conn, "https://x/2", "drop", "T", "detail", "logged")

    assert db.last_alert_at(conn, "https://x/1", "drop") is not None
    assert db.last_alert_at(conn, "https://x/2", "drop") is None  # logged ≠ delivered


def test_prune_removes_only_old_rows(conn, tmp_path):
    old = (datetime.now(UTC) - timedelta(days=120)).isoformat(timespec="seconds")
    fresh = db.now_iso()

    db.upsert_book(conn, url="o", title="old", price=1.0, rating=None, in_stock=True,
                   scraped_at=old)
    db.upsert_book(conn, url="n", title="new", price=2.0, rating=None, in_stock=True,
                   scraped_at=fresh)
    conn.execute(insert(db.alerts).values(
        url="o", kind="drop", title="t", detail="d", status="sent", sent_at=old
    ))
    conn.commit()

    deleted = db.prune(conn, keep_days=90)
    assert deleted["price_history"] == 1
    assert deleted["alerts"] == 1
    assert db.stats(conn)["history"] == 1


def test_status_command_reports_and_checks_freshness(tmp_path, capsys):
    target = str(tmp_path / "s.db")
    conn = db.connect(target)
    db.init_schema(conn)
    run_id = db.start_run(conn, "books")
    db.finish_run(conn, run_id, "success", 5, 0, 0, "finished")
    db.upsert_book(conn, url="u", title="T", price=1.0, rating=None, in_stock=True)
    conn.close()

    args = SimpleNamespace(db=target, check_max_age_minutes=None)
    assert cmd_status(args) == 0
    out = capsys.readouterr().out
    assert "Books tracked: 1" in out
    assert "Last success:" in out
    assert "books" in out

    args = SimpleNamespace(db=target, check_max_age_minutes=60)
    assert cmd_status(args) == 0  # just written → fresh

    stale_args = SimpleNamespace(db=str(tmp_path / "empty.db"), check_max_age_minutes=60)
    assert cmd_status(stale_args) == 1  # no runs at all


def test_prune_command_output(tmp_path, capsys):
    target = str(tmp_path / "p.db")
    conn = db.connect(target)
    db.init_schema(conn)
    conn.close()

    args = SimpleNamespace(db=target, keep_days=30)
    assert cmd_prune(args) == 0
    assert "Pruned 0 row(s) older than 30 day(s)" in capsys.readouterr().out


@pytest.mark.pg
def test_postgres_roundtrip():
    url = os.environ.get("PRICETRACKER_TEST_PG_URL")
    if not url:
        pytest.skip("set PRICETRACKER_TEST_PG_URL to run the Postgres test")

    conn = db.connect(url)
    db.init_schema(conn)
    conn.execute(db.books.delete())
    db.upsert_book(conn, url="pg-1", title="Postgres Book", price=10.0, rating=4,
                   in_stock=True, scraped_at="2026-03-01T00:00:00+00:00")
    update = db.upsert_book(conn, url="pg-1", title="Postgres Book", price=8.0, rating=4,
                            in_stock=True, scraped_at="2026-03-02T00:00:00+00:00")
    assert update.changed is True
    assert update.direction == "DROP"

    run_id = db.start_run(conn, "books")
    db.finish_run(conn, run_id, "success", 1, 1, 1, "finished")
    assert db.latest_successful_run(conn)["items"] == 1

    change = db.fetch_changes(conn)[0]
    assert change["old_price"] == 10.0
    conn.execute(db.price_history.delete())
    conn.execute(db.price_changes.delete())
    conn.execute(db.books.delete())
    conn.execute(db.runs.delete())
    conn.commit()
    conn.close()
