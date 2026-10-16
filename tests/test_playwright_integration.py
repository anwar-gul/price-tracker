"""End-to-end: real browser renders the demo shop, Scrapy pipeline stores it.

Skipped automatically when Chromium is not installed
(install with: .venv/bin/playwright install chromium).
"""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

import pytest

pytest.importorskip("scrapy_playwright")
sync_api = pytest.importorskip("playwright.sync_api")

from pricetracker import db  # noqa: E402 - after importorskip on purpose
from pricetracker.demo_shop import create_server  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def chromium_available() -> bool:
    try:
        with sync_api.sync_playwright() as playwright:
            playwright.chromium.launch(headless=True).close()
        return True
    except Exception:
        return False


@pytest.mark.integration
def test_jscatalog_crawl_end_to_end(tmp_path):
    if not chromium_available():
        pytest.skip("chromium not installed (run: .venv/bin/playwright install chromium)")

    server = create_server(port=0, delay=0.3)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    db_path = tmp_path / "js.db"

    try:
        result = subprocess.run(
            [
                sys.executable, "-m", "pricetracker", "crawl",
                "--spider", "jscatalog",
                "--start-url", f"http://127.0.0.1:{port}/",
                "--db", str(db_path),
                "-L", "WARNING",
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert result.returncode == 0, result.stderr
    finally:
        server.shutdown()
        server.server_close()

    conn = db.connect(db_path)
    stats = db.stats(conn)
    titles = [row["title"] for row in db.fetch_catalog(conn)]
    conn.close()

    assert stats["books"] == 12  # 2 JS-rendered pages x 6 products
    assert stats["history"] == 12
    assert "Aeron Chair" in titles


@pytest.mark.integration
def test_logincatalog_crawl_end_to_end(tmp_path):
    if not chromium_available():
        pytest.skip("chromium not installed (run: .venv/bin/playwright install chromium)")

    server = create_server(port=0, delay=0.3, user="demo", password="demo")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    db_path = tmp_path / "login.db"

    try:
        result = subprocess.run(
            [
                sys.executable, "-m", "pricetracker", "crawl",
                "--spider", "logincatalog",
                "--start-url", f"http://127.0.0.1:{port}/login",
                "--username", "demo", "--password", "demo",
                "--db", str(db_path),
                "-L", "WARNING",
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert result.returncode == 0, result.stderr
    finally:
        server.shutdown()
        server.server_close()

    conn = db.connect(db_path)
    stats = db.stats(conn)
    titles = [row["title"] for row in db.fetch_catalog(conn)]
    conn.close()

    assert stats["books"] == 12  # login -> 2 rendered pages x 6 products
    assert "Standing Desk" in titles
