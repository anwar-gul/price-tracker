from __future__ import annotations

import json

import scrapy
from scrapy_playwright.page import PageMethod

from ..extractors import extract_with_selectors
from ..sites import SelectorConfig
from .base import CatalogSpider
from .jscatalog import JsCatalogSpider

DEFAULT_PAGINATION = "li.next a::attr(href)"


class GenericCatalogSpider(CatalogSpider):
    """Configurable spider: extract any shop with CSS selectors.

    Everything is passed in (usually by `crawl --site NAME` from a sites
    file, see pricetracker/sites.py):

        -a selectors='{"product": "div.card", "title": "h2::text", ...}'
        -a pagination='a.next::attr(href)' -a render=1
        -a wait_selector='div.card' -a run_name='my-shop'

    `render=1` routes pages through Playwright first (for JS catalogs and
    for logins that need a real browser), then extracts with the selectors.
    """

    name = "generic"
    custom_settings = JsCatalogSpider.custom_settings

    def __init__(
        self,
        selectors=None,
        pagination=None,
        render="0",
        wait_selector=None,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        if isinstance(selectors, str):
            selectors = json.loads(selectors) if selectors else {}
        self.selectors = {**SelectorConfig().model_dump(), **(selectors or {})}
        self.pagination = pagination or DEFAULT_PAGINATION
        self.render = str(render).lower() in ("1", "true", "yes", "on")
        self.wait_selector = wait_selector or self.selectors["product"]

    async def start(self):
        yield self._page_request(self.start_urls[0], page=1)

    def _page_request(self, url: str, page: int) -> scrapy.Request:
        meta: dict = {}
        if self.render:
            meta = {
                "playwright": True,
                "playwright_page_methods": [
                    PageMethod("wait_for_selector", self.wait_selector, timeout=15000),
                ],
            }
        return scrapy.Request(url, callback=self.parse, cb_kwargs={"page": page}, meta=meta)

    def parse(self, response, page=1):
        for pod in response.css(self.selectors["product"]):
            yield extract_with_selectors(pod, response, self.selectors)

        if self.max_pages and page >= self.max_pages:
            return

        next_href = response.css(self.pagination).get()
        if next_href:
            yield self._page_request(response.urljoin(next_href), page + 1)
