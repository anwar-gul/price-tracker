from __future__ import annotations

from ..extractors import extract_product
from .base import CatalogSpider


class BooksSpider(CatalogSpider):
    """Crawls the static books.toscrape.com catalogue (pagination + extraction).

    Limit pages with: scrapy crawl books -a max_pages=5
    Point at another start page: -a start_url=...
    For JavaScript-rendered catalogs, use the `jscatalog` spider instead.
    """

    name = "books"
    default_start_url = "https://books.toscrape.com/catalogue/page-1.html"

    def parse(self, response, page=1):
        for pod in response.css("article.product_pod"):
            yield extract_product(pod, response)

        if self.max_pages and page >= self.max_pages:
            return

        next_href = response.css("li.next a::attr(href)").get()
        if next_href:
            yield response.follow(next_href, callback=self.parse, cb_kwargs={"page": page + 1})
