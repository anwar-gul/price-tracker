from __future__ import annotations

from pathlib import Path

import pytest
from scrapy import Request
from scrapy.http import HtmlResponse

from pricetracker.demo_shop import SHELL
from pricetracker.items import BookItem
from pricetracker.spiders.jscatalog import JsCatalogSpider

FIXTURE = Path(__file__).parent / "fixtures" / "shop_rendered.html"


def split_results(results):
    results = list(results)
    items = [r for r in results if isinstance(r, BookItem)]
    requests = [r for r in results if isinstance(r, Request)]
    return items, requests


def make_response(html: str, url: str = "http://localhost:8000/") -> HtmlResponse:
    return HtmlResponse(url=url, body=html.encode("utf-8"), encoding="utf-8")


@pytest.fixture
def rendered_response() -> HtmlResponse:
    html = FIXTURE.read_text(encoding="utf-8")
    return make_response(html)


def test_parses_js_rendered_page(rendered_response):
    items, _ = split_results(JsCatalogSpider().parse(rendered_response))
    assert len(items) == 6


def test_first_rendered_product_fields(rendered_response):
    items, _ = split_results(JsCatalogSpider().parse(rendered_response))
    first = items[0]
    assert first["title"] == "Aeron Chair"
    assert first["price"] == 1299.00
    assert first["rating"] == 5
    assert first["in_stock"] is True
    assert first["url"] == "http://localhost:8000/product/aeron-chair"


def test_follows_js_pagination(rendered_response):
    _, requests = split_results(JsCatalogSpider().parse(rendered_response))
    assert len(requests) == 1
    assert requests[0].url == "http://localhost:8000/?page=2"
    assert requests[0].cb_kwargs == {"page": 2}
    assert requests[0].meta["playwright"] is True


def test_static_shell_yields_nothing_without_rendering():
    """The un-rendered shell is exactly what a plain HTTP crawler would see."""
    shell = SHELL.replace("__DELAY__", "0")
    items, requests = split_results(JsCatalogSpider().parse(make_response(shell)))
    assert items == []
    assert requests == []


def test_max_pages_stops_pagination(rendered_response):
    spider = JsCatalogSpider(max_pages=1)
    items, requests = split_results(spider.parse(rendered_response))
    assert len(items) == 6
    assert requests == []


def test_allowed_domains_follows_start_url():
    assert JsCatalogSpider().allowed_domains == ["localhost"]
    spider = JsCatalogSpider(start_url="https://shop.example.com/catalog")
    assert spider.allowed_domains == ["shop.example.com"]
    assert spider.start_urls == ["https://shop.example.com/catalog"]
