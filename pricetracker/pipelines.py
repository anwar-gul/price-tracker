from __future__ import annotations

import logging

from scrapy.exceptions import DropItem

from . import db, notify
from .config import Settings, get_settings

LOGGER = logging.getLogger("pricetracker.pipeline")


class PriceTrackerPipeline:
    """Stores every scraped item in the database and alerts on price drops."""

    def __init__(self, db_target: str, alert_log: str, settings: Settings):
        self.db_target = db_target
        self.alert_log = alert_log
        self.settings = settings.model_copy(update={"alert_log": alert_log})
        self.conn = None
        self.updates: list[db.PriceUpdate] = []
        self.stats = None

    @classmethod
    def from_crawler(cls, crawler):
        settings = get_settings()
        pipeline = cls(
            db_target=crawler.settings.get("PRICETRACKER_DB") or settings.database_url,
            alert_log=crawler.settings.get("ALERT_LOG") or settings.alert_log,
            settings=settings,
        )
        pipeline.stats = crawler.stats
        return pipeline

    def open_spider(self):
        self.conn = db.connect(self.db_target)
        db.init_schema(self.conn)
        self.updates = []

    def process_item(self, item):
        if item.get("price") is None:
            raise DropItem(f"Unparseable price for {item.get('url')}")

        update = db.upsert_book(
            self.conn,
            url=item["url"],
            title=item["title"],
            price=item["price"],
            rating=item.get("rating"),
            in_stock=bool(item.get("in_stock")),
        )
        self.updates.append(update)

        if update.changed:
            verb = "drop" if update.direction == "DROP" else "rise"
            LOGGER.warning(
                f"PRICE {verb.upper()}: {update.title!r} "
                f"{update.old_price:.2f} -> {update.new_price:.2f} "
                f"({update.pct_change:+.1f}%)"
            )
            if self.stats is not None:
                self.stats.inc_value(f"pricetracker/{verb}s")
        return item

    def close_spider(self):
        drops = [u for u in self.updates if u.changed and u.direction == "DROP"]
        rises = [u for u in self.updates if u.changed and u.direction == "RISE"]
        LOGGER.info(
            f"Tracked {len(self.updates)} books | "
            f"{len(drops)} price drops | {len(rises)} rises -> {self.db_target}"
        )
        if self.updates:
            notify.notify_drops(self.conn, self.updates, self.settings, logger=LOGGER)
        if self.conn:
            self.conn.close()
            self.conn = None
