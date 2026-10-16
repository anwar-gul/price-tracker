import os

from pricetracker.config import get_settings

_cfg = get_settings()

BOT_NAME = "pricetracker"

SPIDER_MODULES = ["pricetracker.spiders"]
NEWSPIDER_MODULE = "pricetracker.spiders"

# Crawl politely: respect robots.txt; AutoThrottle adapts to the target's latency.
ROBOTSTXT_OBEY = True
DOWNLOAD_DELAY = _cfg.download_delay
CONCURRENT_REQUESTS_PER_DOMAIN = _cfg.concurrent_requests_per_domain
AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = _cfg.download_delay
AUTOTHROTTLE_MAX_DELAY = 30.0
AUTOTHROTTLE_TARGET_CONCURRENCY = _cfg.autothrottle_target

# Bounded retries on transient errors (timeouts, 429, 5xx)
RETRY_ENABLED = True
RETRY_TIMES = _cfg.retry_times
RETRY_HTTP_CODES = [408, 429, 500, 502, 503, 504]
DOWNLOAD_TIMEOUT = _cfg.download_timeout

USER_AGENT = _cfg.user_agent

if _cfg.proxy_url:
    # HttpProxyMiddleware and scrapy-playwright both read these env vars
    os.environ.setdefault("http_proxy", _cfg.proxy_url)
    os.environ.setdefault("https_proxy", _cfg.proxy_url)

ITEM_PIPELINES = {
    "pricetracker.pipelines.PriceTrackerPipeline": 300,
}

EXTENSIONS = {
    "pricetracker.extensions.RunStatsExtension": 100,
}

# Local state / storage (overridable per invocation by the CLI)
PRICETRACKER_DB = _cfg.database_url
ALERT_LOG = _cfg.alert_log

FEED_EXPORT_ENCODING = "utf-8"
LOG_LEVEL = _cfg.log_level
LOG_FORMAT = "%(levelname)s: %(message)s"

# --- Playwright (JavaScript catalogs) ---
# Required by scrapy-playwright; must be set before the reactor starts.
TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT = 30000

if _cfg.playwright_connect_url:
    # Remote browser (e.g. browserless) instead of a local Chromium
    PLAYWRIGHT_CONNECT_URL = _cfg.playwright_connect_url

if _cfg.proxy_url:
    PLAYWRIGHT_LAUNCH_OPTIONS = {"proxy": {"server": _cfg.proxy_url}}


def _abort_heavy_resources(request) -> bool:
    """Skip images/fonts/media when rendering pages in a browser."""
    return request.resource_type in ("image", "font", "media")


PLAYWRIGHT_ABORT_REQUEST = _abort_heavy_resources
