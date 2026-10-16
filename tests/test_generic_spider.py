from __future__ import annotations

import json
from pathlib import Path

from scrapy import Request
from scrapy.http import HtmlResponse

from pricetracker.items import BookItem
from pricetracker.spiders.generic import GenericCatalogSpider

FIXTURE = Path(__file__).parent / "fixtures" / "shop_rendered.html"

CUSTOM_MARKUP = """
<html><body>
<div class="card">
  <h2>Wool Runner</h2>
  <a class="buy" href="/p/wool-runner">buy</a>
  <span class="price">$95.00</span>
  <span class="stars" data-score="4"></span>
  <span class="stock">In stock</span>
</div>
<div class="card">
  <h2>Tree Dasher</h2>
  <a class="buy" href="/p/tree-dasher">buy</a>
  <span class="price">$135.00</span>
  <span class="stars" data-score="5"></span>
  <span class="stock">Out of stock</span>
</div>
<a class="next" href="?p=2">next</a>
</body></html>
"""

CUSTOM_SELECTORS = {
    "product": "div.card",
    "title": "h2::text",
    "link": "a.buy::attr(href)",
    "price": ".price::text",
    "rating": ".stars::attr(data-score)",
    "stock": ".stock::text",
}


def make_response(html: str, url: str = "https://shop.example.com/") -> HtmlResponse:
    return HtmlResponse(url=url, body=html.encode("utf-8"), encoding="utf-8")


def split_results(results):
    results = list(results)
    return (
        [r for r in results if isinstance(r, BookItem)],
        [r for r in results if isinstance(r, Request)],
    )


def test_custom_selectors_extract_products():
    spider = GenericCatalogSpider(selectors=CUSTOM_SELECTORS, start_url="https://shop.example.com/")
    items, _ = split_results(spider.parse(make_response(CUSTOM_MARKUP)))
    assert len(items) == 2
    first, second = items
    assert first["title"] == "Wool Runner"
    assert first["price"] == 95.0
    assert first["rating"] == 4
    assert first["in_stock"] is True
    assert first["url"] == "https://shop.example.com/p/wool-runner"
    assert second["in_stock"] is False
    assert second["rating"] == 5


def test_custom_selectors_follow_pagination():
    spider = GenericCatalogSpider(
        selectors=CUSTOM_SELECTORS,
        pagination="a.next::attr(href)",
        start_url="https://shop.example.com/",
    )
    _, requests = split_results(spider.parse(make_response(CUSTOM_MARKUP)))
    assert [r.url for r in requests] == ["https://shop.example.com/?p=2"]
    assert requests[0].cb_kwargs == {"page": 2}


def test_defaults_parse_books_compatible_markup():
    html = FIXTURE.read_text(encoding="utf-8")
    response = make_response(html, url="http://localhost:8000/")
    items, requests = split_results(GenericCatalogSpider().parse(response))
    assert len(items) == 6
    assert items[0]["title"] == "Aeron Chair"
    assert [r.url for r in requests] == ["http://localhost:8000/?page=2"]


def test_selectors_accept_json_string():
    spider = GenericCatalogSpider(selectors=json.dumps(CUSTOM_SELECTORS))
    assert spider.selectors["product"] == "div.card"
    assert spider.selectors["stock"] == ".stock::text"


def test_render_flag_sets_playwright_meta():
    spider = GenericCatalogSpider(render="1", wait_selector="div.card")
    request = spider._page_request("https://shop.example.com/", page=1)
    assert request.meta["playwright"] is True
    assert request.meta["playwright_page_methods"][0].args[0] == "div.card"


def test_render_off_uses_plain_requests():
    request = GenericCatalogSpider()._page_request("https://shop.example.com/", page=1)
    assert "playwright" not in request.meta


def test_run_name_is_recorded_from_kwarg():
    spider = GenericCatalogSpider(run_name="my-shop")
    assert spider.run_name == "my-shop"


def test_max_pages_stops_pagination():
    spider = GenericCatalogSpider(selectors=CUSTOM_SELECTORS, max_pages=1)
    items, requests = split_results(spider.parse(make_response(CUSTOM_MARKUP)))
    assert len(items) == 2
    assert requests == []
