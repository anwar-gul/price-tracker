from __future__ import annotations

from pathlib import Path

import pytest
from scrapy.http import HtmlResponse

from pricetracker.spiders.books import BooksSpider

FIXTURES = Path(__file__).parent / "fixtures"
PAGE_URL = "https://books.toscrape.com/catalogue/page-1.html"


@pytest.fixture
def page1_html() -> str:
    return (FIXTURES / "books_page1.html").read_text(encoding="utf-8")


@pytest.fixture
def spider() -> BooksSpider:
    return BooksSpider()


@pytest.fixture
def page1_response(page1_html) -> HtmlResponse:
    return make_response(page1_html)


def make_response(html: str, url: str = PAGE_URL) -> HtmlResponse:
    return HtmlResponse(url=url, body=html.encode("utf-8"), encoding="utf-8")
