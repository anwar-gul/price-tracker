from __future__ import annotations

import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import (
    Column,
    Float,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    delete,
    event,
    func,
    insert,
    or_,
    select,
    update,
)
from sqlalchemy.engine import Connection, Engine

metadata = MetaData()

books = Table(
    "books",
    metadata,
    Column("url", String(1024), primary_key=True),
    Column("title", Text, nullable=False),
    Column("price", Float, nullable=False),
    Column("rating", Integer),
    Column("in_stock", Integer, nullable=False, default=0),
    Column("first_seen", String(40), nullable=False),
    Column("last_seen", String(40), nullable=False),
)

price_history = Table(
    "price_history",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("url", String(1024), nullable=False),
    Column("price", Float, nullable=False),
    Column("scraped_at", String(40), nullable=False),
)
Index("idx_history_url", price_history.c.url)

price_changes = Table(
    "price_changes",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("url", String(1024), nullable=False),
    Column("title", Text, nullable=False),
    Column("old_price", Float, nullable=False),
    Column("new_price", Float, nullable=False),
    Column("changed_at", String(40), nullable=False),
)
Index("idx_changes_time", price_changes.c.changed_at)

alerts = Table(
    "alerts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("url", String(1024), nullable=False),
    Column("kind", String(32), nullable=False),
    Column("title", Text),
    Column("detail", Text),
    Column("status", String(16), nullable=False),
    Column("channel", String(16), nullable=False, default="log"),
    Column("payload", Text),
    Column("attempts", Integer, nullable=False, default=0),
    Column("next_attempt_at", String(40)),
    Column("last_error", Text),
    Column("sent_at", String(40), nullable=False),
)
Index("idx_alerts_url_kind", alerts.c.url, alerts.c.kind, alerts.c.sent_at)
Index("idx_alerts_status", alerts.c.status, alerts.c.next_attempt_at)

runs = Table(
    "runs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("spider", String(64), nullable=False),
    Column("started_at", String(40), nullable=False),
    Column("finished_at", String(40)),
    Column("status", String(16), nullable=False),
    Column("items", Integer, nullable=False, default=0),
    Column("changes", Integer, nullable=False, default=0),
    Column("drops", Integer, nullable=False, default=0),
    Column("message", Text),
)
Index("idx_runs_started", runs.c.started_at)

PRICE_EPSILON = 0.0001


@dataclass
class PriceUpdate:
    url: str
    title: str
    old_price: float | None
    new_price: float
    changed: bool

    @property
    def direction(self) -> str:
        if not self.changed or self.old_price is None:
            return ""
        return "DROP" if self.new_price < self.old_price else "RISE"

    @property
    def pct_change(self) -> float:
        if self.old_price in (None, 0):
            return 0.0
        return (self.new_price - self.old_price) / self.old_price * 100


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def normalize_url(target: str | Path) -> str:
    """Accept a bare path (treated as SQLite) or an explicit database URL."""
    text = str(target)
    if "://" in text:
        scheme = text.split("://", 1)[0]
        if not scheme.startswith(("sqlite", "postgresql", "postgres")):
            raise ValueError(f"unsupported database URL scheme: {scheme}")
        return text
    return f"sqlite:///{text}"


_engines: dict[str, Engine] = {}


def _sqlite_pragmas(dbapi_connection, _record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def get_engine(target: str | Path) -> Engine:
    url = normalize_url(target)
    engine = _engines.get(url)
    if engine is None:
        engine = create_engine(url, pool_pre_ping=True)
        if engine.dialect.name == "sqlite":
            event.listen(engine, "connect", _sqlite_pragmas)
        _engines[url] = engine
    return engine


def connect(target: str | Path) -> Connection:
    url = normalize_url(target)
    if url.startswith("sqlite:///"):
        path = Path(url.removeprefix("sqlite:///"))
        path.parent.mkdir(parents=True, exist_ok=True)
    return get_engine(url).connect()


def init_schema(conn: Connection) -> None:
    metadata.create_all(conn)
    conn.commit()


def upsert_book(
    conn: Connection,
    url: str,
    title: str,
    price: float,
    rating: int | None,
    in_stock: bool,
    scraped_at: str | None = None,
) -> PriceUpdate:
    """Insert or update a book; records history and change events."""
    ts = scraped_at or now_iso()
    row = conn.execute(select(books.c.price).where(books.c.url == url)).fetchone()

    if row is None:
        conn.execute(
            insert(books).values(
                url=url,
                title=title,
                price=price,
                rating=rating,
                in_stock=int(in_stock),
                first_seen=ts,
                last_seen=ts,
            )
        )
        conn.execute(insert(price_history).values(url=url, price=price, scraped_at=ts))
        conn.commit()
        return PriceUpdate(url, title, None, price, changed=False)

    old_price = row[0]
    price_changed = abs(old_price - price) > PRICE_EPSILON
    conn.execute(
        update(books)
        .where(books.c.url == url)
        .values(title=title, price=price, rating=rating, in_stock=int(in_stock), last_seen=ts)
    )
    if price_changed:
        conn.execute(insert(price_history).values(url=url, price=price, scraped_at=ts))
        conn.execute(
            insert(price_changes).values(
                url=url, title=title, old_price=old_price, new_price=price, changed_at=ts
            )
        )
    conn.commit()
    return PriceUpdate(url, title, old_price, price, changed=price_changed)


def fetch_catalog(conn: Connection):
    return conn.execute(select(books).order_by(func.lower(books.c.title))).mappings().all()


def fetch_history(conn: Connection):
    h, b = price_history, books
    return (
        conn.execute(
            select(h.c.url, b.c.title, h.c.price, h.c.scraped_at)
            .select_from(h.outerjoin(b, b.c.url == h.c.url))
            .order_by(h.c.id)
        )
        .mappings()
        .all()
    )


def fetch_changes(conn: Connection, limit: int | None = None):
    query = select(price_changes).order_by(
        price_changes.c.changed_at.desc(), price_changes.c.id.desc()
    )
    if limit:
        query = query.limit(int(limit))
    return conn.execute(query).mappings().all()


def stats(conn: Connection) -> dict[str, int]:
    def count(table) -> int:
        return conn.execute(select(func.count()).select_from(table)).scalar_one()

    return {
        "books": count(books),
        "history": count(price_history),
        "changes": count(price_changes),
    }


def start_run(conn: Connection, spider: str) -> int:
    result = conn.execute(
        insert(runs).values(
            spider=spider, started_at=now_iso(), status="running", items=0, changes=0, drops=0
        )
    )
    conn.commit()
    return result.inserted_primary_key[0]


def finish_run(
    conn: Connection,
    run_id: int,
    status: str,
    items: int,
    changes: int,
    drops: int,
    message: str | None = None,
) -> None:
    conn.execute(
        update(runs)
        .where(runs.c.id == run_id)
        .values(
            finished_at=now_iso(),
            status=status,
            items=items,
            changes=changes,
            drops=drops,
            message=message,
        )
    )
    conn.commit()


def fetch_runs(conn: Connection, limit: int = 10):
    return conn.execute(select(runs).order_by(runs.c.id.desc()).limit(limit)).mappings().all()


def latest_successful_run(conn: Connection, before_id: int | None = None):
    query = select(runs).where(runs.c.status == "success")
    if before_id is not None:
        query = query.where(runs.c.id < before_id)
    row = conn.execute(query.order_by(runs.c.id.desc()).limit(1)).fetchone()
    return row._mapping if row else None


def record_alert(
    conn: Connection,
    url: str,
    kind: str,
    title: str,
    detail: str,
    status: str,
    *,
    channel: str = "log",
    payload: str | None = None,
    attempts: int = 0,
    next_attempt_at: str | None = None,
    last_error: str | None = None,
) -> int:
    result = conn.execute(
        insert(alerts).values(
            url=url,
            kind=kind,
            title=title,
            detail=detail,
            status=status,
            channel=channel,
            payload=payload,
            attempts=attempts,
            next_attempt_at=next_attempt_at,
            last_error=last_error,
            sent_at=now_iso(),
        )
    )
    conn.commit()
    return result.inserted_primary_key[0]


def fetch_due_alerts(
    conn: Connection, limit: int = 20, max_attempts: int = 5
) -> list:
    """Failed alerts whose retry backoff has elapsed, oldest first."""
    now = now_iso()
    rows = conn.execute(
        select(alerts)
        .where(
            alerts.c.status == "failed",
            alerts.c.channel.in_(("webhook", "email")),
            alerts.c.attempts < max_attempts,
            or_(alerts.c.next_attempt_at.is_(None), alerts.c.next_attempt_at <= now),
        )
        .order_by(alerts.c.id)
        .limit(limit)
    ).mappings().all()
    return list(rows)


def update_alert_attempt(
    conn: Connection,
    alert_id: int,
    *,
    status: str,
    attempts: int,
    next_attempt_at: str | None = None,
    last_error: str | None = None,
) -> None:
    conn.execute(
        update(alerts)
        .where(alerts.c.id == alert_id)
        .values(
            status=status,
            attempts=attempts,
            next_attempt_at=next_attempt_at,
            last_error=last_error,
            sent_at=now_iso(),
        )
    )
    conn.commit()


def alert_counts(conn: Connection) -> dict[str, int]:
    rows = conn.execute(
        select(alerts.c.status, func.count()).group_by(alerts.c.status)
    ).all()
    return {status: count for status, count in rows}


ALERT_COOLDOWN_STATUSES = ("sent", "failed")


def last_alert_at(conn: Connection, url: str, kind: str) -> str | None:
    row = conn.execute(
        select(alerts.c.sent_at)
        .where(alerts.c.url == url, alerts.c.kind == kind, alerts.c.status.in_(ALERT_COOLDOWN_STATUSES))
        .order_by(alerts.c.id.desc())
        .limit(1)
    ).fetchone()
    return row[0] if row else None


def prune(conn: Connection, keep_days: int) -> dict[str, int]:
    """Delete history/changes/alerts older than keep_days. Returns per-table counts."""
    cutoff = (datetime.now(UTC) - timedelta(days=keep_days)).isoformat(timespec="seconds")
    targets = {
        "price_history": (price_history, price_history.c.scraped_at),
        "price_changes": (price_changes, price_changes.c.changed_at),
        "alerts": (alerts, alerts.c.sent_at),
    }
    deleted: dict[str, int] = {}
    for name, (table, column) in targets.items():
        result = conn.execute(delete(table).where(column < cutoff))
        deleted[name] = int(result.rowcount or 0)
    conn.commit()
    if conn.dialect.name == "sqlite":
        with contextlib.suppress(Exception):
            conn.exec_driver_sql("VACUUM")
    return deleted
