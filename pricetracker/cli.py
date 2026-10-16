from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from . import db, metrics, notify, push
from .config import get_settings
from .log import get_logger
from .log import setup as setup_logging
from .report import export_all, print_report
from .sites import SitesError, resolve_site

SPIDERS = ("books", "jscatalog", "logincatalog", "generic")

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


LOGIN_KEYS = {
    "csrf_selector": "csrf-selector",
    "csrf_input": "csrf-input",
    "csrf_attribute": "csrf-attribute",
    "next_selector": "next-selector",
    "submit_selector": "submit-selector",
    "error_selector": "error-selector",
}


def _site_crawl_args(args) -> tuple[str, dict]:
    """Resolve --site from the sites file into spider kwargs.

    Explicit command-line flags override values from the file (the caller
    merges them afterwards).
    """
    site = resolve_site(args.sites_file, args.site)
    kwargs: dict = {
        "run_name": site.name,
        "start_url": site.start_url,
        "max_pages": site.max_pages,
        "wait_selector": site.wait_selector,
    }
    if site.spider == "generic":
        kwargs.update(
            selectors=site.selectors.model_dump() if site.selectors else None,
            pagination=site.pagination,
            render="1" if site.render else "0",
        )
    elif site.spider == "logincatalog":
        login = site.login
        if login is None:
            raise SitesError(f"site {site.name!r} uses logincatalog but has no login section")
        kwargs.update(
            username=args.username or os.environ.get(login.username_env),
            password=args.password or os.environ.get(login.password_env),
            username_selector=login.username_selector,
            password_selector=login.password_selector,
            submit_selector=login.submit_selector,
            csrf_selector=login.csrf_selector,
            csrf_input=login.csrf_input,
            csrf_attribute=login.csrf_attribute,
            next_selector=login.next_selector,
            error_selector=login.error_selector,
            wait_selector=login.wait_selector or site.wait_selector,
        )
        if not kwargs["username"] or not kwargs["password"]:
            raise SitesError(
                f"site {site.name!r}: set {login.username_env} and {login.password_env} "
                "environment variables (or pass --username/--password)"
            )
    return site.spider, {key: value for key, value in kwargs.items() if value is not None}


def cmd_crawl(args) -> int:
    from scrapy.crawler import CrawlerProcess
    from scrapy.utils.project import get_project_settings

    settings = get_settings()
    target = _db_target(args)

    scrapy_settings = get_project_settings()
    scrapy_settings.set("PRICETRACKER_DB", target, priority="cmdline")
    scrapy_settings.set("ALERT_LOG", settings.alert_log, priority="cmdline")
    scrapy_settings.set("LOG_LEVEL", args.loglevel or settings.log_level, priority="cmdline")

    spider = args.spider
    kwargs = {"max_pages": args.max_pages, "start_url": args.start_url}
    if args.site:
        try:
            spider, site_kwargs = _site_crawl_args(args)
        except SitesError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        for key, value in kwargs.items():
            if value is not None:
                site_kwargs[key] = value
        kwargs = site_kwargs
    if args.username:
        kwargs["username"] = args.username
    if args.password:
        kwargs["password"] = args.password
    for key in LOGIN_KEYS:
        value = getattr(args, key)
        if value:
            kwargs[key] = value

    process = CrawlerProcess(scrapy_settings)
    process.crawl(spider, **kwargs)
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
    if args.site:
        command += ["--site", args.site, "--sites-file", args.sites_file]
    for key, flag in LOGIN_KEYS.items():
        value = getattr(args, key)
        if value:
            command += [f"--{flag}", value]
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


def _lease_step(conn, owner: str, ttl_seconds: float, holding: bool, logger) -> bool:
    """Renew the watch lease when held, otherwise try to acquire it."""
    if holding and db.renew_lease(conn, db.WATCH_LEASE, owner, ttl_seconds):
        return True
    if holding:
        logger.warning("lease lost mid-cycle; trying to re-acquire")
    return db.acquire_lease(conn, db.WATCH_LEASE, owner, ttl_seconds)


def cmd_watch(args) -> int:
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_format)
    logger = get_logger("watch")
    metrics.start(args.metrics_port or settings.metrics_port)

    lease_seconds = args.lease_seconds if args.lease_seconds is not None else settings.lease_seconds
    owner = settings.instance_id or f"{socket.gethostname()}:{os.getpid()}"
    lease_enabled = lease_seconds > 0

    if lease_enabled:
        # Multi-replica mode: leadership is decided by a database lease, so
        # standby replicas stay up and take over when the leader dies.
        lock = None
    else:
        lock = _acquire_lock(args.lock)
        if lock is None:
            logger.error("another watch is already running, exiting", lock=args.lock)
            return 1

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    interval = max(args.interval_minutes, 0.01) * 60
    runs = 0
    holding = False
    last_prune = time.monotonic()  # prune is checked on later cycles
    logger.info(
        "watch starting",
        lock=args.lock,
        interval_minutes=args.interval_minutes,
        lease_seconds=lease_seconds if lease_enabled else None,
        owner=owner if lease_enabled else None,
    )
    try:
        while not _STOP:
            if lease_enabled:
                conn = db.connect(_db_target(args))
                db.init_schema(conn)
                holding = _lease_step(conn, owner, lease_seconds, holding, logger)
                conn.close()
                metrics.LEASE.set(1 if holding else 0)
                if not holding:
                    logger.info("standing by: another instance holds the lease")
                    _sleep_interruptible(interval)
                    continue

            runs += 1
            logger.info("run starting", run=runs)

            conn = db.connect(_db_target(args))
            db.init_schema(conn)
            notify.retry_due(conn, settings, logger=logger)

            if settings.prune_every_hours > 0 and (
                time.monotonic() - last_prune >= settings.prune_every_hours * 3600
            ):
                deleted = db.prune(conn, keep_days=settings.prune_keep_days)
                last_prune = time.monotonic()
                logger.info(
                    "retention prune ran",
                    keep_days=settings.prune_keep_days,
                    deleted=deleted,
                )

            code = _run_crawl_subprocess(args)

            export_all(conn, args.out)
            print_report(conn, limit=5)

            if args.push:
                if settings.push_configured:
                    try:
                        results = push.push_all(conn, settings, logger=logger)
                        logger.info(
                            "catalog pushed",
                            targets=", ".join(f"{k}={v}" for k, v in results.items()),
                        )
                    except push.PushError as exc:
                        logger.warning("push failed", error=str(exc))
                else:
                    logger.warning("push requested but no targets configured")

            latest = db.fetch_runs(conn, limit=1)

            if lease_enabled:
                holding = db.renew_lease(conn, db.WATCH_LEASE, owner, lease_seconds)
                if not holding:
                    logger.warning("lease lost during run; another instance took over")
                metrics.LEASE.set(1 if holding else 0)

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
        if lease_enabled and holding:
            conn = db.connect(_db_target(args))
            db.release_lease(conn, db.WATCH_LEASE, owner)
            conn.close()
        if lock is not None:
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
    lease = db.fetch_lease(conn, db.WATCH_LEASE)
    now = db.now_iso()
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

    if lease is not None:
        expired = lease["expires_at"] < now
        state = "expired" if expired else "active"
        print()
        print(f"Watch lease ({state}): owner={lease['owner']} until={lease['expires_at']}")

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


def cmd_retry_alerts(args) -> int:
    settings = get_settings()
    conn = db.connect(_db_target(args))
    db.init_schema(conn)
    counts = notify.retry_due(conn, settings, limit=args.limit)
    counts_by_status = db.alert_counts(conn)
    conn.close()
    print(
        f"Retried {counts['retried']} due alert(s): "
        f"{counts['sent']} sent, {counts['failed']} still failing, {counts['dead']} dead"
    )
    if counts_by_status:
        summary = ", ".join(f"{k}={v}" for k, v in sorted(counts_by_status.items()))
        print(f"Alert rows by status: {summary}")
    return 0


def cmd_push(args) -> int:
    settings = get_settings()
    conn = db.connect(_db_target(args))
    db.init_schema(conn)
    targets = None if args.target == "all" else [args.target]
    try:
        results = push.push_all(conn, settings, targets=targets)
    except push.PushError as exc:
        print(f"push failed: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    for target, count in results.items():
        print(f"{target}: pushed {count} record(s)")
    return 0


def cmd_migrate(args) -> int:
    from . import migrate as migrations

    target = _db_target(args)
    migrations.upgrade(target)
    print(f"Schema upgraded to head at {db.normalize_url(target)}")
    return 0


def cmd_demo_shop(args) -> int:
    from .demo_shop import serve

    serve(
        port=args.port,
        delay=args.delay,
        sale=args.sale,
        user=args.user,
        password=args.password,
        csrf=args.csrf,
        two_step=args.two_step,
    )
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
                        help="books = static, jscatalog = Playwright-rendered, "
                             "logincatalog = behind login, generic = selector-driven")
    parser.add_argument("--site", default=None,
                        help="Got a sites.yaml? Track a named site: --site NAME "
                             "(sets spider, start URL, selectors, login)")
    parser.add_argument("--sites-file", default="sites.yaml",
                        help="Sites config used by --site (YAML or JSON)")
    parser.add_argument("--start-url", default=None, help="Override the spider's start URL")
    parser.add_argument("--max-pages", type=int, default=None, help="Stop after N catalogue pages")
    parser.add_argument("-L", "--loglevel", default=None, help="Scrapy log level")
    parser.add_argument("--username", default=None, help="Login user (logincatalog spider)")
    parser.add_argument("--password", default=None, help="Login password (logincatalog spider)")
    parser.add_argument("--csrf-selector", default=None,
                        help="Read a CSRF token from this selector before submitting")
    parser.add_argument("--csrf-input", default=None,
                        help="Form field to fill with the CSRF token (default: input[name=csrf_token])")
    parser.add_argument("--csrf-attribute", default=None,
                        help="Attribute holding the token (default: content, use value for hidden inputs)")
    parser.add_argument("--next-selector", default=None,
                        help="Multi-step login: click this to advance from the username step")
    parser.add_argument("--submit-selector", default=None,
                        help="Login submit button (default: button[type=submit])")
    parser.add_argument("--error-selector", default=None,
                        help="Login error element; when it appears the flow fails fast")


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
    watch.add_argument("--lease-seconds", type=int, default=None,
                       help="Multi-replica mode: lead via a database lease with this TTL "
                            "(standby replicas take over when the leader stops renewing)")
    watch.add_argument("--push", action="store_true",
                       help="Push the catalog to configured remote targets (Sheets/Airtable) "
                            "after every run")
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

    init_db = sub.add_parser("init-db", help="Create the schema (idempotent, for tests/demos)")
    _add_db_option(init_db)
    init_db.set_defaults(func=cmd_init_db)

    migrate = sub.add_parser("migrate", help="Apply Alembic migrations (use this in production)")
    _add_db_option(migrate)
    migrate.set_defaults(func=cmd_migrate)

    retry_alerts = sub.add_parser(
        "retry-alerts", help="Re-deliver failed alerts whose retry backoff has elapsed"
    )
    _add_db_option(retry_alerts)
    retry_alerts.add_argument("--limit", type=int, default=20)
    retry_alerts.set_defaults(func=cmd_retry_alerts)

    push_cmd = sub.add_parser(
        "push", help="Push the catalog to Google Sheets / Airtable"
    )
    _add_db_option(push_cmd)
    push_cmd.add_argument("--target", choices=("all", "airtable", "sheets"), default="all")
    push_cmd.set_defaults(func=cmd_push)

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
    shop.add_argument("--csrf", action="store_true",
                      help="CSRF-protect the login form (token in a meta tag, empty hidden field)")
    shop.add_argument("--two-step", action="store_true",
                      help="Username page followed by a password page")
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
