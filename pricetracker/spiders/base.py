from __future__ import annotations

from urllib.parse import urlparse

import scrapy


class CatalogSpider(scrapy.Spider):
    """Shared wiring for catalog spiders: start URL, page limit, domain lock."""

    default_start_url: str = ""

    def __init__(self, start_url=None, max_pages=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        url = start_url or self.default_start_url
        self.start_urls = [url]
        host = urlparse(url).hostname
        self.allowed_domains = [host] if host else []
        self.max_pages = int(max_pages) if max_pages else None
