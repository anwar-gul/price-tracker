from __future__ import annotations

from scrapy import signals

from . import db, notify
from .config import get_settings
from .log import get_logger

SUCCESS_REASONS = ("finished",)


def _is_success(reason: str) -> bool:
    return reason in SUCCESS_REASONS or reason.startswith("closespider")


class RunStatsExtension:
    """Records every crawl in the `runs` table and alerts on zero-item runs.

    Lives on the spider_closed signal, so run history works no matter the
    entrypoint (our CLI, `scrapy crawl`, a future scheduler).
    """

    def __init__(self, db_target: str, alert_log: str, stats, settings):
        self.db_target = db_target
        self.alert_log = alert_log
        self.stats = stats
        self.settings = settings
        self.conn = None
        self.run_id = None
        self.previous = None

    @classmethod
    def from_crawler(cls, crawler):
        settings = get_settings()
        extension = cls(
            db_target=crawler.settings.get("PRICETRACKER_DB") or settings.database_url,
            alert_log=crawler.settings.get("ALERT_LOG") or settings.alert_log,
            stats=crawler.stats,
            settings=settings,
        )
        crawler.signals.connect(extension.spider_opened, signal=signals.spider_opened)
        crawler.signals.connect(extension.spider_closed, signal=signals.spider_closed)
        return extension

    def spider_opened(self, spider):
        self.conn = db.connect(self.db_target)
        db.init_schema(self.conn)
        self.run_name = getattr(spider, "run_name", None) or spider.name
        self.run_id = db.start_run(self.conn, self.run_name)
        self.previous = db.latest_successful_run(self.conn, before_id=self.run_id)

    def spider_closed(self, spider, reason):
        if self.conn is None:
            return
        collected = self.stats.get_stats()
        items = int(collected.get("item_scraped_count", 0))
        drops = int(collected.get("pricetracker/drops", 0))
        rises = int(collected.get("pricetracker/rises", 0))
        status = "success" if _is_success(str(reason)) else "failed"

        db.finish_run(
            self.conn,
            self.run_id,
            status=status,
            items=items,
            changes=drops + rises,
            drops=drops,
            message=str(reason),
        )
        if (
            status == "success"
            and items == 0
            and self.previous is not None
            and self.previous["items"] > 0
        ):
            notify.notify_zero_items(
                self.conn, self.settings, self.run_name, self.previous["items"], logger=get_logger()
            )
        self.conn.close()
        self.conn = None
