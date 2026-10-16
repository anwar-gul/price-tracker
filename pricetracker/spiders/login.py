from __future__ import annotations

import os

import scrapy
from scrapy_playwright.page import PageMethod

from .jscatalog import JsCatalogSpider


class LoginCatalogSpider(JsCatalogSpider):
    """Crawls catalogs behind a login form.

    Renders the login page, fills the form, submits it, waits for the
    catalog, then reuses the browser session (cookies) for pagination —
    exactly what a logged-in human would do.

    Credentials: -a username=... -a password=... (or SHOP_USER /
    SHOP_PASSWORD env vars). Demo:
        python -m pricetracker demo-shop --user demo --password demo
        python -m pricetracker crawl --spider logincatalog \\
            --start-url http://localhost:8000/login \\
            --username demo --password demo
    """

    name = "logincatalog"
    default_start_url = "http://localhost:8000/login"

    def __init__(self, username=None, password=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.username = username or os.environ.get("SHOP_USER", "demo")
        self.password = password or os.environ.get("SHOP_PASSWORD", "demo")

    async def start(self):
        yield scrapy.Request(
            self.start_urls[0],
            callback=self.parse_after_login,
            meta={
                "playwright": True,
                "playwright_page_methods": [
                    PageMethod("fill", "input[name=username]", self.username),
                    PageMethod("fill", "input[name=password]", self.password),
                    PageMethod("click", "button[type=submit]"),
                    PageMethod("wait_for_selector", self.wait_selector, timeout=15000),
                ],
            },
        )

    def parse_after_login(self, response):
        yield from self.parse(response, page=1)
