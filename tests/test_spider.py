from __future__ import annotations

import re

import pytest
from conftest import make_response
from scrapy import Request

from pricetracker.items import BookItem
from pricetracker.parsing import parse_price, parse_rating
from pricetracker.spiders.books import BooksSpider


def split_results(results):
    results = list(results)
    items = [r for r in results if isinstance(r, BookItem)]
    requests = [r for r in results if isinstance(r, Request)]
    return items, requests


def test_extracts_all_20_books_from_page(spider, page1_response):
    items, _ = split_results(spider.parse(page1_response))
    assert len(items) == 20


def test_first_book_fields(spider, page1_response):
    items, _ = split_results(spider.parse(page1_response))
    first = items[0]
    assert first["title"] == "A Light in the Attic"
    assert first["price"] == 51.77
    assert first["rating"] == 3
    assert first["in_stock"] is True
    assert first["url"] == (
        "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"
    )


def test_follows_pagination_link(spider, page1_response):
    _, requests = split_results(spider.parse(page1_response))
    assert len(requests) == 1
    assert requests[0].url == "https://books.toscrape.com/catalogue/page-2.html"
    assert requests[0].cb_kwargs == {"page": 2}


def test_max_pages_stops_pagination(page1_html):
    spider = BooksSpider(max_pages="1")
    _, requests = split_results(spider.parse(make_response(page1_html)))
    assert requests == []


def test_last_page_has_no_next_link(spider, page1_html):
    stripped = re.sub(r'<li class="next">.*?</li>', "", page1_html, flags=re.S)
    assert 'class="next"' not in stripped
    _, requests = split_results(spider.parse(make_response(stripped)))
    assert requests == []


def test_max_pages_accepts_int_or_string():
    assert BooksSpider(max_pages=3).max_pages == 3
    assert BooksSpider(max_pages="3").max_pages == 3
    assert BooksSpider().max_pages is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("£51.77", 51.77),
        ("£1,234.50", 1234.5),
        ("$19.99", 19.99),
        ("12.00", 12.0),
    ],
)
def test_parse_price_variants(raw, expected):
    assert parse_price(raw) == expected


def test_parse_price_garbage_returns_none():
    assert parse_price("out of stock") is None
    assert parse_price(None) is None
    assert parse_price("") is None


@pytest.mark.parametrize(
    "class_attr,expected",
    [
        ("star-rating One", 1),
        ("star-rating Two", 2),
        ("star-rating Three", 3),
        ("star-rating Four", 4),
        ("star-rating Five", 5),
        ("other classes", None),
        (None, None),
    ],
)
def test_parse_rating_variants(class_attr, expected):
    assert parse_rating(class_attr) == expected
