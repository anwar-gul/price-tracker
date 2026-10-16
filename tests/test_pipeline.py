from __future__ import annotations

import pytest
from scrapy.exceptions import DropItem

from pricetracker import db
from pricetracker.config import Settings
from pricetracker.pipelines import PriceTrackerPipeline


def make_item(price: float = 10.0) -> dict:
    return {
        "url": "https://example.com/book-1",
        "title": "A Book",
        "price": price,
        "rating": 4,
        "in_stock": True,
    }


def make_pipeline(tmp_path) -> PriceTrackerPipeline:
    return PriceTrackerPipeline(
        db_target=str(tmp_path / "p.db"),
        alert_log=str(tmp_path / "alerts.log"),
        settings=Settings(_env_file=None, database_url=str(tmp_path / "p.db")),
    )


def test_pipeline_stores_items_and_writes_drop_alerts(tmp_path):
    alert_log = tmp_path / "alerts.log"
    pipeline = make_pipeline(tmp_path)
    pipeline.open_spider()

    pipeline.process_item(make_item(10.0))
    pipeline.process_item(make_item(10.0))  # unchanged
    pipeline.process_item(make_item(7.5))   # drop

    pipeline.close_spider()

    conn = db.connect(tmp_path / "p.db")
    counts = db.stats(conn)
    conn.close()
    assert counts == {"books": 1, "history": 2, "changes": 1}

    log_text = alert_log.read_text(encoding="utf-8")
    assert "[DROP]" in log_text
    assert "10.00 -> 7.50" in log_text
    assert "-25.0%" in log_text


def test_pipeline_writes_no_alert_for_rises(tmp_path):
    alert_log = tmp_path / "alerts.log"
    pipeline = make_pipeline(tmp_path)
    pipeline.open_spider()
    pipeline.process_item(make_item(10.0))
    pipeline.process_item(make_item(12.0))
    pipeline.close_spider()
    assert not alert_log.exists()


def test_pipeline_drops_unparseable_price(tmp_path):
    pipeline = make_pipeline(tmp_path)
    pipeline.open_spider()
    item = make_item()
    item["price"] = None
    with pytest.raises(DropItem):
        pipeline.process_item(item)
    pipeline.close_spider()
