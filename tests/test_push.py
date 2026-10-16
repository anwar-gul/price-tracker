from __future__ import annotations

import json
import sys

import pytest

from pricetracker import db, push
from pricetracker.config import Settings

FIELDS = ["Name", "URL", "Price", "Rating", "In stock", "First seen", "Last seen"]


def make_settings(**overrides) -> Settings:
    base = {
        "database_url": "sqlite:///:memory:",
        "push_backoff_seconds": 0.01,
        "push_timeout_seconds": 2.0,
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "push.db")
    db.init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def seeded_conn(conn):
    db.upsert_book(conn, "https://shop.test/a", "Chair", 10.0, rating=4, in_stock=True)
    db.upsert_book(conn, "https://shop.test/b", "Desk", 20.0, rating=5, in_stock=False)
    return conn


def test_catalog_records_snapshot(seeded_conn):
    records = push.catalog_records(seeded_conn)
    assert [record["Name"] for record in records] == ["Chair", "Desk"]
    record = records[0]
    assert record["URL"] == "https://shop.test/a"
    assert record["Price"] == 10.0
    assert record["Rating"] == 4
    assert record["In stock"] is True
    assert record["First seen"] and record["Last seen"]


def records(count: int) -> list[dict]:
    return [
        {"Name": f"item {i}", "URL": f"https://shop.test/{i}", "Price": float(i),
         "Rating": 3, "In stock": True, "First seen": "2026-01-01T00:00:00+00:00",
         "Last seen": "2026-01-01T00:00:00+00:00"}
        for i in range(count)
    ]


def test_airtable_upserts_in_batches(monkeypatch):
    calls = []

    def fake_post(url, payload, headers, settings, opener=None):
        calls.append({"url": url, "payload": payload, "headers": headers})
        return {}

    monkeypatch.setattr(push, "_post_json", fake_post)
    settings = make_settings(
        airtable_token="pat123", airtable_base="appXYZ", airtable_table="Prices"
    )
    pushed = push.push_airtable(records(25), settings)

    assert pushed == 25
    assert [len(call["payload"]["records"]) for call in calls] == [10, 10, 5]
    assert calls[0]["url"] == "https://api.airtable.com/v0/appXYZ/Prices"
    assert calls[0]["headers"]["Authorization"] == "Bearer pat123"
    assert calls[0]["payload"]["performUpsert"] == {"fieldsToMergeOn": ["URL"]}
    assert calls[0]["payload"]["typecast"] is True
    assert calls[0]["payload"]["records"][0]["fields"]["Name"] == "item 0"


def test_airtable_empty_catalog_makes_no_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(push, "_post_json", lambda *a, **k: calls.append(a))
    settings = make_settings(airtable_token="x", airtable_base="y")
    assert push.push_airtable([], settings) == 0
    assert calls == []


class FlakyServer:
    """Records POSTs; returns 500 until ok_after requests have failed."""

    def __init__(self, ok_after: int, code: int = 500):
        import http.server

        calls = self.calls = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                calls.append(json.loads(self.rfile.read(length).decode()))
                if len(calls) <= ok_after:
                    self.send_response(code)
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)

    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/v0/base/table"

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def __enter__(self):
        import threading

        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def test_post_json_retries_transient_failures():
    with FlakyServer(ok_after=2) as server:
        settings = make_settings(push_retries=3)
        result = push._post_json(server.url(), {"a": 1}, {}, settings)
    assert result == {}
    assert len(server.calls) == 3


def test_post_json_gives_up_after_retries():
    with FlakyServer(ok_after=99) as server:
        settings = make_settings(push_retries=2)
        with pytest.raises(push.PushError, match="HTTP 500"):
            push._post_json(server.url(), {"a": 1}, {}, settings)
    assert len(server.calls) == 2


def test_post_json_does_not_retry_client_errors():
    with FlakyServer(ok_after=99, code=401) as server:
        settings = make_settings(push_retries=3)
        with pytest.raises(push.PushError, match="HTTP 401"):
            push._post_json(server.url(), {"a": 1}, {}, settings)
    assert len(server.calls) == 1


class FakeWorksheet:
    def __init__(self):
        self.cleared = False
        self.updated: tuple[str | None, list | None] = (None, None)

    def clear(self):
        self.cleared = True

    def update(self, values=None, range_name=None):
        self.updated = (range_name, values)


class FakeBook:
    def __init__(self, worksheet):
        self.worksheet_obj = worksheet
        self.added = None

    def worksheet(self, title):
        return self.worksheet_obj

    def add_worksheet(self, title, rows, cols):
        self.added = (title, rows, cols)
        return self.worksheet_obj


class FakeGspread:
    def __init__(self, worksheet):
        self.book = FakeBook(worksheet)
        self.service_account_kwargs = None
        self.opened = None

    def service_account(self, filename=None, **kwargs):
        self.service_account_kwargs = {"filename": filename, **kwargs}
        return self

    def service_account_from_dict(self, info):
        self.service_account_kwargs = {"info": info}
        return self

    def open_by_key(self, key):
        self.opened = ("key", key)
        return self.book

    def open(self, name):
        self.opened = ("name", name)
        return self.book


def install_gspread(monkeypatch, worksheet=None):
    worksheet = worksheet or FakeWorksheet()
    module = FakeGspread(worksheet)
    monkeypatch.setitem(sys.modules, "gspread", module)
    return module, worksheet


def sheets_settings(tmp_path, **overrides):
    creds = tmp_path / "sa.json"
    creds.write_text('{"type": "service_account"}', encoding="utf-8")
    return make_settings(
        gsheets_credentials=str(creds),
        gsheets_spreadsheet="TrackerSheet",
        **overrides,
    )


def test_sheets_replaces_worksheet_with_header_and_rows(monkeypatch, tmp_path):
    module, worksheet = install_gspread(monkeypatch)
    settings = sheets_settings(tmp_path)
    count = push.push_sheets(records(3), settings)

    assert count == 3
    assert module.service_account_kwargs["filename"].endswith("sa.json")
    assert module.opened == ("key", "TrackerSheet")
    range_name, values = worksheet.updated
    assert range_name == "A1"
    assert values[0] == FIELDS
    assert [row[1] for row in values[1:]] == [
        "https://shop.test/0", "https://shop.test/1", "https://shop.test/2",
    ]
    assert worksheet.cleared is True


def test_sheets_accepts_json_credentials_text(monkeypatch, tmp_path):
    module, _ = install_gspread(monkeypatch)
    settings = make_settings(
        gsheets_credentials='{"type": "service_account", "client_email": "sa@x"}',
        gsheets_spreadsheet="SheetKey123",
    )
    push.push_sheets(records(1), settings)
    assert module.service_account_kwargs["info"]["client_email"] == "sa@x"


def test_sheets_without_gspread_gives_install_hint(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "gspread", None)
    settings = sheets_settings(tmp_path)
    with pytest.raises(push.PushError, match="pip install"):
        push.push_sheets(records(1), settings)


def test_sheets_falls_back_to_name_lookup(monkeypatch, tmp_path):
    module, _ = install_gspread(monkeypatch)

    def fail_by_key(key):
        raise Exception("not found")

    module.open_by_key = fail_by_key
    settings = sheets_settings(tmp_path)
    push.push_sheets(records(1), settings)
    assert module.opened == ("name", "TrackerSheet")


def test_sheets_bad_credentials_error(monkeypatch, tmp_path):
    install_gspread(monkeypatch)
    settings = make_settings(gsheets_credentials="not-json", gsheets_spreadsheet="x")
    with pytest.raises(push.PushError, match="service-account JSON"):
        push.push_sheets(records(1), settings)


def test_push_all_requires_configured_targets(conn):
    settings = make_settings()
    with pytest.raises(push.PushError, match="no push targets configured"):
        push.push_all(conn, settings)


def test_push_all_reports_requested_unconfigured_target(conn):
    settings = make_settings(airtable_token="x", airtable_base="y")
    with pytest.raises(push.PushError, match="not configured: sheets"):
        push.push_all(conn, settings, targets=["sheets"])


def test_push_all_runs_configured_targets_only(monkeypatch, seeded_conn, tmp_path):
    module, worksheet = install_gspread(monkeypatch)
    settings = sheets_settings(tmp_path, airtable_token="pat", airtable_base="app")
    monkeypatch.setitem(push.PUSHERS, "airtable", lambda records, settings: len(records))
    results = push.push_all(seeded_conn, settings)
    assert results == {"airtable": 2, "sheets": 2}
    assert worksheet.updated[1][0] == FIELDS


def test_push_all_collects_failures(monkeypatch, seeded_conn):
    settings = make_settings(airtable_token="x", airtable_base="y")
    logs: list[tuple] = []

    class Logger:
        def info(self, *args, **kwargs):
            logs.append((args, kwargs))

    def boom(records, settings):
        raise push.PushError("boom")

    monkeypatch.setitem(push.PUSHERS, "airtable", boom)
    monkeypatch.setattr(
        push, "configured_targets", lambda settings: ["airtable", "sheets"]
    )
    with pytest.raises(push.PushError, match="airtable: boom"):
        push.push_all(seeded_conn, settings, targets=["airtable"], logger=Logger())
    assert logs == []


def test_cli_push_end_to_end(monkeypatch, tmp_path, capsys):
    from types import SimpleNamespace

    from pricetracker import cli

    db_path = tmp_path / "push.db"
    connection = db.connect(db_path)
    db.init_schema(connection)
    db.upsert_book(connection, "https://shop.test/a", "Chair", 10.0, rating=4, in_stock=True)
    db.upsert_book(connection, "https://shop.test/b", "Desk", 20.0, rating=5, in_stock=False)
    connection.close()

    with FlakyServer(ok_after=0) as server:
        settings = make_settings(
            airtable_token="pat", airtable_base="app", airtable_api_base=server.base_url
        )
        monkeypatch.setattr(cli, "get_settings", lambda: settings)
        args = SimpleNamespace(db=str(db_path), target="all")
        assert cli.cmd_push(args) == 0
    out = capsys.readouterr().out
    assert "airtable: pushed 2 record(s)" in out
    assert len(server.calls) == 1
    assert len(server.calls[0]["records"]) == 2


def test_cli_push_unconfigured_target_fails(monkeypatch, tmp_path, capsys):
    from types import SimpleNamespace

    from pricetracker import cli

    settings = make_settings()
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    args = SimpleNamespace(db=str(tmp_path / "push.db"), target="sheets")
    assert cli.cmd_push(args) == 1
    assert "no push targets configured" in capsys.readouterr().err
