from __future__ import annotations

import http.server
import json
import threading
from contextlib import contextmanager

import pytest

from pricetracker import db, notify
from pricetracker.config import Settings
from pricetracker.db import PriceUpdate


class WebhookReceiver(http.server.BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.server.requests.append(
            {"path": self.path, "body": json.loads(body.decode()), "headers": dict(self.headers)}
        )
        code = 200
        if len(self.server.requests) <= self.server.fail_first:
            code = 500
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


@contextmanager
def receiver(fail_first: int = 0):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), WebhookReceiver)
    server.requests = []
    server.fail_first = fail_first
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "n.db")
    db.init_schema(connection)
    yield connection
    connection.close()


def make_settings(**overrides) -> Settings:
    base = {
        "database_url": "sqlite:///:memory:",
        "alert_log": "/tmp/pricetracker-test-alerts.log",
        "webhook_backoff_seconds": 0.01,
        "webhook_timeout_seconds": 2.0,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


def drop(url="https://example.com/a", old=10.0, new=8.0) -> PriceUpdate:
    return PriceUpdate(url, "Widget", old, new, changed=True)


def test_log_only_without_remote_channels(conn, tmp_path):
    settings = make_settings(alert_log=str(tmp_path / "alerts.log"))
    counts = notify.notify_drops(conn, [drop()], settings)
    assert counts["logged"] == 1
    assert "[DROP] Widget 10.00 -> 8.00 (-20.0%)" in (tmp_path / "alerts.log").read_text()


def test_webhook_delivery(conn, tmp_path):
    with receiver() as server:
        url = f"http://127.0.0.1:{server.server_address[1]}/hook"
        settings = make_settings(webhook_url=url, alert_log=str(tmp_path / "a.log"))
        counts = notify.notify_drops(conn, [drop()], settings)

    assert counts["sent"] == 1
    payload = server.requests[0]["body"]
    assert payload["event"] == "price_drop"
    assert payload["title"] == "Widget"
    assert payload["old_price"] == 10.0
    assert payload["new_price"] == 8.0
    assert payload["pct_change"] == -20.0


def test_webhook_retries_then_succeeds(conn, tmp_path):
    with receiver(fail_first=2) as server:
        url = f"http://127.0.0.1:{server.server_address[1]}/hook"
        settings = make_settings(webhook_url=url, webhook_retries=3,
                                 alert_log=str(tmp_path / "a.log"))
        counts = notify.notify_drops(conn, [drop()], settings)

    assert counts["sent"] == 1
    assert len(server.requests) == 3


def test_webhook_failure_recorded(conn, tmp_path):
    settings = make_settings(
        webhook_url="http://127.0.0.1:9/nothing", webhook_retries=2,
        alert_log=str(tmp_path / "a.log"),
    )
    counts = notify.notify_drops(conn, [drop()], settings)
    assert counts["failed"] == 1
    assert db.last_alert_at(conn, "https://example.com/a", "drop") is not None


def test_slack_payload_shape(conn, tmp_path):
    with receiver() as server:
        url = f"http://127.0.0.1:{server.server_address[1]}/hooks.slack.com/services/x"
        settings = make_settings(webhook_url=url, alert_log=str(tmp_path / "a.log"))
        notify.notify_drops(conn, [drop()], settings)
    assert set(server.requests[0]["body"].keys()) == {"text"}


def test_cooldown_suppresses_repeat_alerts(conn, tmp_path):
    with receiver() as server:
        url = f"http://127.0.0.1:{server.server_address[1]}/hooks.slack.com/services/x"
        settings = make_settings(webhook_url=url, alert_cooldown_hours=24,
                                 alert_log=str(tmp_path / "a.log"))
        first = notify.notify_drops(conn, [drop()], settings)
        second = notify.notify_drops(conn, [drop(new=7.5)], settings)

    assert first["sent"] == 1
    assert second["suppressed"] == 1
    assert len(server.requests) == 1


def test_cooldown_ignores_log_only_status(conn, tmp_path):
    settings = make_settings(alert_log=str(tmp_path / "a.log"))
    notify.notify_drops(conn, [drop()], settings)
    assert db.last_alert_at(conn, "https://example.com/a", "drop") is None


def test_zero_items_alert(conn, tmp_path):
    with receiver() as server:
        url = f"http://127.0.0.1:{server.server_address[1]}/hook"
        settings = make_settings(webhook_url=url, alert_log=str(tmp_path / "a.log"))
        status = notify.notify_zero_items(conn, settings, "books", previous_items=1000)

    assert status == "sent"
    payload = server.requests[0]["body"]
    assert payload["event"] == "zero_items"
    assert payload["previous_items"] == 1000
    assert "[ZERO ITEMS]" in (tmp_path / "a.log").read_text()


def test_email_delivery_uses_legacy_smtp_config(monkeypatch, conn, tmp_path):
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            sent["host"] = host

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def starttls(self):
            sent["tls"] = True

        def login(self, user, password):
            sent["login"] = (user, password)

        def send_message(self, message):
            sent["subject"] = message["Subject"]

    monkeypatch.setattr(notify.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("ALERT_TO", "ops@example.com")
    settings = Settings(_env_file=None, alert_log=str(tmp_path / "a.log"))
    counts = notify.notify_drops(conn, [drop()], settings)

    assert counts["sent"] == 1
    assert sent["host"] == "smtp.example.com"
    assert sent["tls"] is True
    assert "Widget" in sent["subject"]
