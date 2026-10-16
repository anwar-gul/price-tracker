from __future__ import annotations

import json
import smtplib
import time
import urllib.request
from datetime import UTC, datetime, timedelta
from email.mime.text import MIMEText
from pathlib import Path

from . import db, metrics
from .config import Settings
from .db import PriceUpdate


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def format_change(update: PriceUpdate, at: str | None = None) -> str:
    prefix = f"{at} " if at else ""
    return (
        f"{prefix}[{update.direction}] {update.title} "
        f"{update.old_price:.2f} -> {update.new_price:.2f} "
        f"({update.pct_change:+.1f}%) {update.url}"
    )


def log_alerts(path: str | Path, updates: list[PriceUpdate]) -> None:
    """Append every detected change to the audit log (no network involved)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    stamp = _now()
    with target.open("a", encoding="utf-8") as fh:
        for update in updates:
            fh.write(format_change(update, at=stamp) + "\n")


def _post_webhook(webhook_url: str, message: str, details: dict, settings: Settings) -> bool:
    if "slack.com" in webhook_url:
        payload = {"text": message}
    else:
        payload = {"service": "price-tracker", "message": message, **details}
    request = urllib.request.Request(
        webhook_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": settings.user_agent},
    )
    attempts = max(1, settings.webhook_retries)
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=settings.webhook_timeout_seconds) as resp:
                if 200 <= resp.status < 300:
                    return True
        except Exception:
            pass
        if attempt < attempts - 1:
            time.sleep(settings.webhook_backoff_seconds * (2**attempt))
    return False


def _send_email(settings: Settings, subject: str, body: str) -> bool:
    message = MIMEText(body, "plain", "utf-8")
    message["Subject"] = subject
    message["From"] = settings.alert_from or settings.smtp_user or "pricetracker@localhost"
    message["To"] = settings.alert_to
    for attempt in range(2):
        try:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
                smtp.starttls()
                if settings.smtp_user:
                    smtp.login(settings.smtp_user, settings.smtp_pass or "")
                smtp.send_message(message)
            return True
        except Exception:
            if attempt == 0:
                time.sleep(1.0)
    return False


def _deliver(
    message: str, details: dict, settings: Settings, subject: str
) -> tuple[str, str | None, str | None]:
    """Push one alert through the configured remote channels.

    Returns (status, retry_channel, error): status is sent | failed | logged;
    retry_channel names the channel to retry when delivery failed.
    """
    if not (settings.webhook_configured or settings.email_configured):
        metrics.record_alert("log", "sent")
        return "logged", None, None

    delivered = False
    failed: list[str] = []
    if settings.webhook_configured:
        ok = _post_webhook(settings.webhook_url, message, details, settings)
        metrics.record_alert("webhook", "sent" if ok else "failed")
        delivered = delivered or ok
        if not ok:
            failed.append("webhook")
    if settings.email_configured:
        ok = _send_email(settings, subject, message)
        metrics.record_alert("email", "sent" if ok else "failed")
        delivered = delivered or ok
        if not ok:
            failed.append("email")
    if delivered:
        return "sent", None, None
    return "failed", failed[0], f"{failed[0]} delivery failed"


def _backoff_seconds(attempts: int, base: int) -> float:
    return base * (2 ** max(0, attempts - 1))


def _next_attempt_iso(attempts: int, settings: Settings) -> str:
    due = datetime.now(UTC) + timedelta(
        seconds=_backoff_seconds(attempts, settings.alert_retry_base_seconds)
    )
    return due.isoformat(timespec="seconds")


def _record_delivery(
    conn,
    update: PriceUpdate,
    message: str,
    details: dict,
    settings: Settings,
    subject: str,
) -> str:
    status, channel, error = _deliver(message, details, settings, subject)
    record = {
        "channel": channel or "log",
        "attempts": 0,
        "payload": None,
        "next_attempt_at": None,
        "last_error": None,
    }
    if status in ("sent", "failed"):
        record.update(
            attempts=1,
            payload=json.dumps({"message": message, **details}),
            next_attempt_at=_next_attempt_iso(1, settings) if status == "failed" else None,
            last_error=error,
        )
    db.record_alert(
        conn, update.url, "drop", update.title, message, status, **record
    )
    return status


def _in_cooldown(conn, url: str, kind: str, settings: Settings) -> bool:
    if settings.alert_cooldown_hours <= 0:
        return False
    last = db.last_alert_at(conn, url, kind)
    if last is None:
        return False
    last_dt = datetime.fromisoformat(last)
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=UTC)
    return datetime.now(UTC) - last_dt < timedelta(hours=settings.alert_cooldown_hours)


def notify_drops(conn, updates: list[PriceUpdate], settings: Settings, logger=None) -> dict[str, int]:
    """Audit-log every drop; push remote alerts respecting the per-product cooldown."""
    drops = [u for u in updates if u.changed and u.direction == "DROP"]
    counts = {"drops": len(drops), "sent": 0, "suppressed": 0, "failed": 0, "logged": 0}
    if not drops:
        return counts

    log_alerts(settings.alert_log, drops)
    if logger:
        logger.info(f"Alerts appended to {settings.alert_log}")

    for update in drops:
        message = format_change(update)
        details = {
            "event": "price_drop",
            "title": update.title,
            "url": update.url,
            "old_price": update.old_price,
            "new_price": update.new_price,
            "pct_change": round(update.pct_change, 2),
            "at": _now(),
        }
        if _in_cooldown(conn, update.url, "drop", settings):
            db.record_alert(conn, update.url, "drop", update.title, message, "suppressed")
            counts["suppressed"] += 1
            continue
        status = _record_delivery(
            conn, update, message, details, settings,
            f"[price-tracker] price drop: {update.title}",
        )
        counts[status] += 1

    if logger and (settings.webhook_configured or settings.email_configured):
        logger.info(
            f"Alert delivery: {counts['sent']} sent, "
            f"{counts['suppressed']} suppressed, {counts['failed']} failed"
        )
    return counts


def notify_zero_items(
    conn, settings: Settings, spider: str, previous_items: int, logger=None
) -> str | None:
    """Warn when a previously-productive crawl suddenly yields nothing (selector drift)."""
    if not settings.alert_on_zero_items:
        return None
    message = (
        f"[ZERO ITEMS] spider '{spider}' produced 0 items "
        f"(previous successful run: {previous_items}) - probable selector drift or block"
    )
    target = Path(settings.alert_log)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(f"{_now()} {message}\n")

    details = {"event": "zero_items", "spider": spider, "previous_items": previous_items, "at": _now()}
    status, channel, error = _deliver(
        message, details, settings, f"[price-tracker] zero items from {spider}"
    )
    db.record_alert(
        conn, f"spider:{spider}", "zero_items", spider, message, status,
        channel=channel or "log",
        payload=json.dumps({"message": message, **details}) if status in ("sent", "failed") else None,
        attempts=1 if status in ("sent", "failed") else 0,
        next_attempt_at=_next_attempt_iso(1, settings) if status == "failed" else None,
        last_error=error,
    )
    if logger:
        logger.warning(message)
    return status


def retry_due(conn, settings: Settings, limit: int = 20, logger=None) -> dict[str, int]:
    """Re-attempt failed alerts due for retry (outbox pattern).

    Sends on the recorded channel, backs off exponentially, and marks alerts
    'dead' once alert_max_attempts is exhausted.
    """
    counts = {"retried": 0, "sent": 0, "failed": 0, "dead": 0}
    rows = db.fetch_due_alerts(conn, limit=limit, max_attempts=settings.alert_max_attempts)
    for row in rows:
        counts["retried"] += 1
        attempts = row["attempts"] + 1
        channel = row["channel"]
        payload = json.loads(row["payload"]) if row["payload"] else {}
        message = payload.pop("message", row["detail"] or "")
        subject = f"[price-tracker] retry: {row['title'] or row['kind']}"

        if channel == "webhook" and settings.webhook_configured:
            ok = _post_webhook(settings.webhook_url, message, payload, settings)
        elif channel == "email" and settings.email_configured:
            ok = _send_email(settings, subject, message)
        else:
            ok = False

        if ok:
            metrics.record_alert(channel, "retry_sent")
            db.update_alert_attempt(
                conn, row["id"], status="sent", attempts=attempts, next_attempt_at=None
            )
            counts["sent"] += 1
        elif attempts >= settings.alert_max_attempts:
            metrics.record_alert(channel, "dead")
            db.update_alert_attempt(
                conn, row["id"], status="dead", attempts=attempts,
                next_attempt_at=None, last_error="max attempts reached",
            )
            counts["dead"] += 1
        else:
            metrics.record_alert(channel, "retry_failed")
            db.update_alert_attempt(
                conn, row["id"], status="failed", attempts=attempts,
                next_attempt_at=_next_attempt_iso(attempts, settings),
                last_error="retry failed",
            )
            counts["failed"] += 1

    if logger and counts["retried"]:
        logger.info(
            f"Alert retry: {counts['retried']} due, {counts['sent']} sent, "
            f"{counts['failed']} still failing, {counts['dead']} dead"
        )
    return counts
