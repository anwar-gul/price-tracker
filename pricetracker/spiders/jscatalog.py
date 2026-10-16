from __future__ import annotations

import scrapy
from scrapy_playwright.page import PageMethod

from ..extractors import extract_product
from .base import CatalogSpider

WAIT_SELECTOR = "article.product_pod"


class JsCatalogSpider(CatalogSpider):
    """Renders JavaScript catalogs with a real browser, then extracts.

    Static HTML of a JS catalog typically contains no products at all: this
    spider waits for client-side rendering (wait_for_selector) before
    parsing, and follows pagination one rendered page at a time.

    Try it against the local demo shop:
        python -m pricetracker demo-shop
        python -m pricetracker crawl --spider jscatalog
    """

    name = "jscatalog"
    default_start_url = "http://localhost:8000/"

    custom_settings = {
        "DOWNLOAD_HANDLERS": {
            "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
            "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
        },
        "PLAYWRIGHT_BROWSER_TYPE": "chromium",
        "PLAYWRIGHT_LAUNCH_OPTIONS": {"headless": True},
    }

    def __init__(self, wait_selector=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.wait_selector = wait_selector or WAIT_SELECTOR

    async def start(self):
        # Scrapy 2.13+ start API: yielding here is what makes the first
        # request go through Playwright (start_requests is no longer called).
        yield self._page_request(self.start_urls[0], page=1)

    def _page_request(self, url: str, page: int) -> scrapy.Request:
        return scrapy.Request(
            url,
            callback=self.parse,
            cb_kwargs={"page": page},
            meta={
                "playwright": True,
                "playwright_page_methods": [
                    PageMethod("wait_for_selector", self.wait_selector, timeout=15000),
                ],
            },
        )

    def parse(self, response, page=1):
        for pod in response.css("article.product_pod"):
            yield extract_product(pod, response)

        if self.max_pages and page >= self.max_pages:
            return

        next_href = response.css("li.next a::attr(href)").get()
        if next_href:
            yield self._page_request(response.urljoin(next_href), page + 1)
