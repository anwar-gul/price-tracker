"""Throughput checks: SQLite ingest micro-benchmark runs everywhere; the full
crawl benchmark is marked `load` (run explicitly: pytest -m load) and also runs
in a dedicated CI job.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

import pytest

from pricetracker import db
from pricetracker.loadgen import create_server


def test_sqlite_ingest_throughput(tmp_path):
    conn = db.connect(tmp_path / "ingest.db")
    db.init_schema(conn)

    items = 1500
    start = time.perf_counter()
    for i in range(items):
        db.upsert_book(
            conn,
            url=f"https://example.com/product/{i}",
            title=f"Load Book {i}",
            price=10.0 + i % 50,
            rating=(i % 5) + 1,
            in_stock=True,
        )
    elapsed = time.perf_counter() - start
    rate = items / elapsed
    print(f"\n[load] sqlite ingest: {items} inserts in {elapsed:.2f}s ({rate:,.0f}/s)")

    stats = db.stats(conn)
    conn.close()
    assert stats["books"] == items
    assert rate > 100, f"sqlite ingest too slow: {rate:.0f}/s"


@pytest.mark.load
def test_crawl_throughput(tmp_path):
    total, page_size = 1000, 20
    server = create_server(port=0, total=total, page_size=page_size)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    db_path = tmp_path / "load.db"
    env = {
        **os.environ,
        "PRICETRACKER_DOWNLOAD_DELAY": "0",
        "PRICETRACKER_CONCURRENT_REQUESTS_PER_DOMAIN": "16",
        "PRICETRACKER_AUTOTHROTTLE_TARGET": "16",
        "PRICETRACKER_RETRY_TIMES": "1",
    }
    command = [
        sys.executable, "-m", "pricetracker", "crawl",
        "--spider", "books",
        "--start-url", f"http://127.0.0.1:{port}/page-1.html",
        "--db", str(db_path),
        "-L", "ERROR",
    ]
    start = time.perf_counter()
    try:
        result = subprocess.run(command, env=env, capture_output=True, text=True)
    finally:
        server.shutdown()
        server.server_close()
    elapsed = time.perf_counter() - start

    assert result.returncode == 0, result.stderr
    conn = db.connect(db_path)
    run = db.latest_successful_run(conn)
    stats = db.stats(conn)
    conn.close()

    assert run is not None and run["status"] == "success"
    rate = run["items"] / elapsed
    print(
        f"\n[load] crawl: {run['items']} items / {total // page_size} pages "
        f"in {elapsed:.2f}s ({rate:,.1f} items/s end-to-end incl. startup)"
    )
    assert stats["books"] == total
    assert rate > 10, f"end-to-end crawl too slow: {rate:.1f} items/s"
