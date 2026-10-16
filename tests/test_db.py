from __future__ import annotations

import pytest

from pricetracker import db


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "test.db")
    db.init_schema(connection)
    yield connection
    connection.close()


def add(conn, price, title="A Book", scraped_at=None):
    return db.upsert_book(
        conn,
        url="https://example.com/book-1",
        title=title,
        price=price,
        rating=4,
        in_stock=True,
        scraped_at=scraped_at,
    )


def test_new_book_creates_catalog_row_and_history(conn):
    update = add(conn, 10.0)
    assert update.changed is False
    assert update.old_price is None
    assert db.stats(conn) == {"books": 1, "history": 1, "changes": 0}


def test_same_price_adds_no_history(conn):
    add(conn, 10.0)
    add(conn, 10.0)
    assert db.stats(conn) == {"books": 1, "history": 1, "changes": 0}


def test_price_drop_records_history_and_change(conn):
    add(conn, 10.0, scraped_at="2026-03-01T00:00:00+00:00")
    update = add(conn, 8.0, scraped_at="2026-03-02T00:00:00+00:00")

    assert update.changed is True
    assert update.direction == "DROP"
    assert update.pct_change == pytest.approx(-20.0)

    counts = db.stats(conn)
    assert counts["history"] == 2
    assert counts["changes"] == 1

    change = db.fetch_changes(conn)[0]
    assert change["old_price"] == 10.0
    assert change["new_price"] == 8.0


def test_price_rise_direction(conn):
    add(conn, 10.0)
    update = add(conn, 12.5)
    assert update.direction == "RISE"
    assert update.pct_change == pytest.approx(25.0)


def test_tiny_floating_point_noise_is_not_a_change(conn):
    add(conn, 10.0)
    update = add(conn, 10.0000001)
    assert update.changed is False


def test_title_and_last_seen_are_updated(conn):
    add(conn, 10.0, title="Old Title", scraped_at="2026-03-01T00:00:00+00:00")
    add(conn, 10.0, title="New Title", scraped_at="2026-03-05T00:00:00+00:00")
    book = db.fetch_catalog(conn)[0]
    assert book["title"] == "New Title"
    assert book["last_seen"] == "2026-03-05T00:00:00+00:00"
    assert book["first_seen"] == "2026-03-01T00:00:00+00:00"


def test_catalog_sorted_by_title(conn):
    for title, url in [("Zebra", "z"), ("apple", "a"), ("Mango", "m")]:
        db.upsert_book(conn, url=url, title=title, price=1.0, rating=None, in_stock=True)
    titles = [row["title"] for row in db.fetch_catalog(conn)]
    assert titles == ["apple", "Mango", "Zebra"]
