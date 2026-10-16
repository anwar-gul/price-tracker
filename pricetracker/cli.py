from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from . import db, metrics
from .config import get_settings
from .log import get_logger
from .log import setup as setup_logging
from .report import export_all, print_report

SPIDERS = ("books", "jscatalog", "logincatalog")

_STOP = False


def _handle_signal(_signum, _frame):
    global _STOP
    _STOP = True


def _db_target(args) -> str:
    return args.db or get_settings().database_url


def _epoch(iso: str | None) -> float | None:
    if not iso:
        return None
    value = datetime.fromisoformat(iso)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.timestamp()


def _age_minutes(iso: str | None) -> float | None:
    epoch = _epoch(iso)
    if epoch is None:
        return None
    return (time.time() - epoch) / 60.0


def cmd_crawl(args) -> int:
    from scrapy.crawler import CrawlerProcess
    from scrapy.utils.project import get_project_settings

    settings = get_settings()
    target = _db_target(args)

    scrapy_settings = get_project_settings()
    scrapy_settings.set("PRICETRACKER_DB", target, priority="cmdline")
    scrapy_settings.set("ALERT_LOG", settings.alert_log, priority="cmdline")
    scrapy_settings.set("LOG_LEVEL", args.loglevel or settings.log_level, priority="cmdline")

    kwargs = {"max_pages": args.max_pages, "start_url": args.start_url}
    if args.username:
        kwargs["username"] = args.username
    if args.password:
        kwargs["password"] = args.password

    process = CrawlerProcess(scrapy_settings)
    process.crawl(args.spider, **kwargs)
    process.start()

    conn = db.connect(target)
    db.init_schema(conn)
    latest = db.fetch_runs(conn, limit=1)
    conn.close()
    status = latest[0]["status"] if latest else "failed"
    return 0 if status == "success" else 1


def _run_crawl_subprocess(args) -> int:
    command = [
        sys.executable, "-m", "pricetracker", "crawl",
        "--spider", args.spider,
    ]
    if args.loglevel:
        command += ["-L", args.loglevel]
    if args.db:
        command += ["--db", args.db]
    if args.max_pages:
        command += ["--max-pages", str(args.max_pages)]
    if args.start_url:
        command += ["--start-url", args.start_url]
    if args.username:
        command += ["--username", args.username]
    if args.password:
        command += ["--password", args.password]
    return subprocess.run(command).returncode


def _sleep_interruptible(seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while not _STOP and time.monotonic() < deadline:
        time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))


def _acquire_lock(lock_path: str):
    """Non-blocking exclusive lock; returns None when another process holds it."""
    from filelock import FileLock
    from filelock import Timeout as LockTimeout

    lock = FileLock(lock_path)
    try:
        lock.acquire(timeout=0)
    except LockTimeout:
        return None
    return lock


def cmd_watch(args) -> int:
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_format)
    logger = get_logger("watch")
    metrics.start(args.metrics_port or settings.metrics_port)

    lock = _acquire_lock(args.lock)
    if lock is None:
        logger.error("another watch is already running, exiting", lock=args.lock)
        return 1

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    interval = max(args.interval_minutes, 0.01) * 60
    runs = 0
    logger.info("watch starting", lock=args.lock, interval_minutes=args.interval_minutes)
    try:
        while not _STOP:
            runs += 1
            logger.info("run starting", run=runs)
            code = _run_crawl_subprocess(args)

            conn = db.connect(_db_target(args))
            db.init_schema(conn)
            export_all(conn, args.out)
            print_report(conn, limit=5)
            latest = db.fetch_runs(conn, limit=1)
            conn.close()
            if latest:
                row = latest[0]
                metrics.record_run(
                    row["status"],
                    row["items"],
                    row["drops"],
                    _epoch(row["finished_at"]) if row["status"] == "success" else None,
                )
            logger.info("run finished", run=runs, crawl_exit=code)

            if args.runs and runs >= args.runs:
                break
            logger.info("sleeping", minutes=round(interval / 60, 2))
            _sleep_interruptible(interval)
    finally:
        lock.release()
    logger.info("watch stopped", runs=runs)
    return 0


def cmd_export(args) -> int:
    conn = db.connect(_db_target(args))
    db.init_schema(conn)
    paths = export_all(conn, args.out)
    conn.close()
    print(f"Exported {len(paths)} files to {Path(args.out).resolve()}:")
    for path in paths.values():
        print(f"  {path}")
    return 0


def cmd_report(args) -> int:
    conn = db.connect(_db_target(args))
    db.init_schema(conn)
    print_report(conn, limit=args.limit)
    conn.close()
    return 0


def cmd_status(args) -> int:
    conn = db.connect(_db_target(args))
    db.init_schema(conn)
    counts = db.stats(conn)
    latest_runs = db.fetch_runs(conn, limit=5)
    last_success = db.latest_successful_run(conn)
    conn.close()

    print("price-tracker status")
    print("=" * 62)
    print(f"Database:      {db.normalize_url(_db_target(args))}")
    print(f"Books tracked: {counts['books']}")
    print(f"Price points:  {counts['history']}")
    print(f"Price changes: {counts['changes']}")

    stale = False
    if last_success is not None:
        age = _age_minutes(last_success["finished_at"])
        age_text = f"{age:.1f} min ago" if age is not None else "unknown age"
        print(
            f"Last success:  {last_success['finished_at']} "
            f"({age_text}, {last_success['items']} items, spider {last_success['spider']})"
        )
        if args.check_max_age_minutes is not None and (
            age is None or age > args.check_max_age_minutes
        ):
            stale = True
    else:
        print("Last success:  (none)")
        stale = args.check_max_age_minutes is not None

    if latest_runs:
        print()
        print("Recent runs:")
        for row in latest_runs:
            print(
                f"  {row['started_at']}  {row['spider']:<13} {row['status']:<8} "
                f"items={row['items']} changes={row['changes']} drops={row['drops']}"
            )

    if stale:
        print()
        print(f"STALE: no successful run within {args.check_max_age_minutes} minute(s)")
        return 1
    return 0


def cmd_prune(args) -> int:
    conn = db.connect(_db_target(args))
    db.init_schema(conn)
    deleted = db.prune(conn, keep_days=args.keep_days)
    conn.close()
    print(f"Pruned {sum(deleted.values())} row(s) older than {args.keep_days} day(s):")
    for table, count in deleted.items():
        print(f"  {table}: {count}")
    return 0


def cmd_init_db(args) -> int:
    conn = db.connect(_db_target(args))
    db.init_schema(conn)
    conn.close()
    print(f"Schema ready at {db.normalize_url(_db_target(args))}")
    return 0


def cmd_demo_shop(args) -> int:
    from .demo_shop import serve

    serve(port=args.port, delay=args.delay, sale=args.sale, user=args.user, password=args.password)
    return 0


def _add_db_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--db",
        default=None,
        help="SQLite path or database URL (default: PRICETRACKER_DATABASE_URL or data/prices.db)",
    )


def _add_crawl_options(parser: argparse.ArgumentParser) -> None:
    _add_db_option(parser)
    parser.add_argument("--spider", choices=SPIDERS, default="books",
                        help="books = static, jscatalog = Playwright-rendered, logincatalog = behind login")
    parser.add_argument("--start-url", default=None, help="Override the spider's start URL")
    parser.add_argument("--max-pages", type=int, default=None, help="Stop after N catalogue pages")
    parser.add_argument("-L", "--loglevel", default=None, help="Scrapy log level")
    parser.add_argument("--username", default=None, help="Login user (logincatalog spider)")
    parser.add_argument("--password", default=None, help="Login password (logincatalog spider)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pricetracker",
        description="Scrape a product catalog, track price history, report changes.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    crawl = sub.add_parser("crawl", help="Run one crawl into the database")
    _add_crawl_options(crawl)
    crawl.set_defaults(func=cmd_crawl)

    watch = sub.add_parser("watch", help="Crawl + export on a repeating schedule (file-locked)")
    _add_crawl_options(watch)
    watch.add_argument("--out", default="exports", help="Export folder (default: exports)")
    watch.add_argument("--interval-minutes", type=float, default=60.0)
    watch.add_argument("--runs", type=int, default=0, help="Stop after N runs (0 = forever)")
    watch.add_argument("--lock", default="data/tracker.lock",
                       help="Lock file preventing overlapping watch processes")
    watch.add_argument("--metrics-port", type=int, default=0,
                       help="Expose Prometheus metrics on this port (0 = off)")
    watch.set_defaults(func=cmd_watch)

    export = sub.add_parser("export", help="Export catalog + history to CSV/Excel")
    _add_db_option(export)
    export.add_argument("--out", default="exports")
    export.set_defaults(func=cmd_export)

    report = sub.add_parser("report", help="Print tracking summary + recent changes")
    _add_db_option(report)
    report.add_argument("--limit", type=int, default=20)
    report.set_defaults(func=cmd_report)

    status = sub.add_parser("status", help="Ops status: freshness, recent runs (for health checks)")
    _add_db_option(status)
    status.add_argument("--check-max-age-minutes", type=int, default=None,
                        help="Exit 1 when the last success is older than this")
    status.set_defaults(func=cmd_status)

    prune = sub.add_parser("prune", help="Delete history/changes/alerts older than N days")
    _add_db_option(prune)
    prune.add_argument("--keep-days", type=int, default=90)
    prune.set_defaults(func=cmd_prune)

    init_db = sub.add_parser("init-db", help="Create the schema (idempotent)")
    _add_db_option(init_db)
    init_db.set_defaults(func=cmd_init_db)

    shop = sub.add_parser(
        "demo-shop",
        help="Serve a local JS-rendered catalog to demo the Playwright spider",
    )
    shop.add_argument("--port", type=int, default=8000)
    shop.add_argument("--delay", type=float, default=0.8,
                      help="Seconds the page's JavaScript waits before rendering")
    shop.add_argument("--sale", type=float, default=0.0,
                      help="Discount %% applied to all prices (demo price drops)")
    shop.add_argument("--user", default=None, help="Enable login gate with this username")
    shop.add_argument("--password", default=None, help="Login password (with --user)")
    shop.set_defaults(func=cmd_demo_shop)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
