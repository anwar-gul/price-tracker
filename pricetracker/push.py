"""Push the tracked catalog to remote tables: Google Sheets and Airtable.

Both targets are optional and configured via environment variables:

    PRICETRACKER_AIRTABLE_TOKEN, PRICETRACKER_AIRTABLE_BASE, PRICETRACKER_AIRTABLE_TABLE
    (PRICETRACKER_AIRTABLE_API_BASE overrides api.airtable.com, e.g. for tests)
    PRICETRACKER_GSHEETS_CREDENTIALS (service-account JSON path or JSON text),
    PRICETRACKER_GSHEETS_SPREADSHEET (name or key), PRICETRACKER_GSHEETS_WORKSHEET

Airtable uses its REST API directly (upsert on the "URL" field, batches of
10). Google Sheets uses gspread (pip install gspread); the service account
must be shared on the spreadsheet. `pricetracker push` runs both configured
targets, `watch --push` pushes after every successful run.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import db, metrics
from .config import Settings

AIRTABLE_BATCH = 10
FIELDS = ("Name", "URL", "Price", "Rating", "In stock", "First seen", "Last seen")


class PushError(RuntimeError):
    """A remote push failed (the message is user-facing)."""


def catalog_records(conn) -> list[dict]:
    """Snapshot the latest catalog as remote-table records."""
    return [
        {
            "Name": row["title"],
            "URL": row["url"],
            "Price": row["price"],
            "Rating": row["rating"],
            "In stock": bool(row["in_stock"]),
            "First seen": row["first_seen"],
            "Last seen": row["last_seen"],
        }
        for row in db.fetch_catalog(conn)
    ]


def _post_json(
    url: str,
    payload: dict,
    headers: dict,
    settings: Settings,
    opener=urllib.request.urlopen,
) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": settings.user_agent, **headers},
        method="POST",
    )
    attempts = max(1, settings.push_retries)
    last_error = "unknown error"
    for attempt in range(attempts):
        try:
            with opener(request, timeout=settings.push_timeout_seconds) as response:
                body = response.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:200].decode("utf-8", "replace")
            last_error = f"HTTP {exc.code}: {detail}"
            if exc.code < 500 and exc.code != 429:
                break  # auth/validation errors will not heal by retrying
        except Exception as exc:  # noqa: BLE001 - network errors of many shapes
            last_error = str(exc)
        if attempt < attempts - 1:
            time.sleep(settings.push_backoff_seconds * (2**attempt))
    raise PushError(f"POST {url} failed: {last_error}")


def push_airtable(records: list[dict], settings: Settings) -> int:
    """Upsert records into Airtable, merging on the URL field (10/request)."""
    api_base = settings.airtable_api_base.rstrip("/")
    url = f"{api_base}/v0/{settings.airtable_base}/{settings.airtable_table}"
    headers = {"Authorization": f"Bearer {settings.airtable_token}"}
    pushed = 0
    for start in range(0, len(records), AIRTABLE_BATCH):
        batch = records[start : start + AIRTABLE_BATCH]
        payload = {
            "performUpsert": {"fieldsToMergeOn": ["URL"]},
            "typecast": True,
            "records": [{"fields": record} for record in batch],
        }
        _post_json(url, payload, headers, settings)
        pushed += len(batch)
    return pushed


def _open_worksheet(settings: Settings, rows: int):
    try:
        import gspread
    except ImportError as exc:
        raise PushError(
            "Google Sheets push needs gspread: pip install gspread (or pip install -e '.[sheets]')"
        ) from exc
    credentials = settings.gsheets_credentials
    try:
        if Path(credentials).is_file():
            client = gspread.service_account(filename=credentials)
        else:
            client = gspread.service_account_from_dict(json.loads(credentials))
    except (OSError, ValueError) as exc:
        raise PushError(
            "PRICETRACKER_GSHEETS_CREDENTIALS must be a service-account JSON file "
            "path or the JSON text itself"
        ) from exc
    try:
        book = client.open_by_key(settings.gsheets_spreadsheet)
    except Exception:  # noqa: BLE001 - gspread raises different errors per version
        book = client.open(settings.gsheets_spreadsheet)
    try:
        return book.worksheet(settings.gsheets_worksheet)
    except Exception:  # noqa: BLE001 - missing worksheet
        return book.add_worksheet(
            title=settings.gsheets_worksheet, rows=max(rows + 1, 100), cols=len(FIELDS)
        )


def push_sheets(records: list[dict], settings: Settings) -> int:
    """Replace the worksheet content with the current catalog (header + rows)."""
    worksheet = _open_worksheet(settings, rows=len(records))
    rows = [list(FIELDS)] + [[record[field] for field in FIELDS] for record in records]
    try:
        worksheet.clear()
        worksheet.update(values=rows, range_name="A1")
    except Exception as exc:  # noqa: BLE001 - surface gspread errors with context
        raise PushError(f"Google Sheets update failed: {exc}") from exc
    return len(records)


PUSHERS = {"airtable": push_airtable, "sheets": push_sheets}


def configured_targets(settings: Settings) -> list[str]:
    targets = []
    if settings.airtable_configured:
        targets.append("airtable")
    if settings.gsheets_configured:
        targets.append("sheets")
    return targets


def push_all(
    conn, settings: Settings, targets: list[str] | None = None, logger=None
) -> dict[str, int]:
    """Push the catalog to each target; raises PushError if any target fails."""
    available = configured_targets(settings)
    if not available:
        raise PushError(
            "no push targets configured (set PRICETRACKER_AIRTABLE_* and/or PRICETRACKER_GSHEETS_*)"
        )
    requested = targets or available
    selected = [target for target in requested if target in available]
    if not selected:
        missing = ", ".join(target for target in requested if target not in available)
        raise PushError(f"target(s) not configured: {missing}")

    records = catalog_records(conn)
    results: dict[str, int] = {}
    failures: list[str] = []
    for target in selected:
        try:
            results[target] = PUSHERS[target](records, settings)
        except PushError as exc:
            failures.append(f"{target}: {exc}")
            metrics.record_push(target, "failed")
            continue
        metrics.record_push(target, "success")
        if logger:
            logger.info("catalog pushed", target=target, records=results[target])
    if failures:
        raise PushError("; ".join(failures))
    return results
