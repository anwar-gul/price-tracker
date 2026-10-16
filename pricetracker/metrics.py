from __future__ import annotations

from prometheus_client import Counter, Gauge, start_http_server

RUNS = Counter(
    "pricetracker_runs_total", "Crawls finished, by status", ["status"]
)
ITEMS = Counter("pricetracker_items_total", "Items stored across all crawls")
DROPS = Counter("pricetracker_price_drops_total", "Price drops detected")
ALERTS = Counter(
    "pricetracker_alerts_total", "Alert deliveries, by channel and status", ["channel", "status"]
)
LAST_SUCCESS = Gauge(
    "pricetracker_last_success_timestamp_seconds",
    "Unix time of the last successful crawl",
)
LEASE = Gauge(
    "pricetracker_lease_held",
    "1 when this instance currently leads the watch lease, 0 when standing by",
)
PUSHES = Counter(
    "pricetracker_push_total", "Catalog pushes to remote targets", ["target", "status"]
)


def start(port: int) -> None:
    if port > 0:
        start_http_server(port)


def record_run(status: str, items: int, drops: int, finished_at_epoch: float | None = None) -> None:
    RUNS.labels(status=status).inc()
    ITEMS.inc(items)
    DROPS.inc(drops)
    if status == "success" and finished_at_epoch is not None:
        LAST_SUCCESS.set(finished_at_epoch)


def record_alert(channel: str, status: str) -> None:
    ALERTS.labels(channel=channel, status=status).inc()


def record_push(target: str, status: str) -> None:
    PUSHES.labels(target=target, status=status).inc()
