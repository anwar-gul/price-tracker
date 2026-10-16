from __future__ import annotations

from .items import BookItem
from .parsing import parse_price, parse_rating


def extract_product(pod, response) -> BookItem:
    """Extract a BookItem from a product card (parsel selector + response).

    Both the static books spider and the Playwright JS-catalog spider use
    this, since the demo shop renders books.toscrape-compatible markup.
    """
    availability = " ".join(
        text.strip() for text in pod.css("p.instock.availability ::text").getall()
    ).strip()
    if not availability:
        availability = " ".join(
            text.strip() for text in pod.css("*[class*=availability] ::text").getall()
        ).strip()
    return BookItem(
        url=response.urljoin(pod.css("h3 a::attr(href)").get()),
        title=pod.css("h3 a::attr(title)").get(),
        price=parse_price(pod.css("p.price_color::text").get()),
        rating=parse_rating(pod.css("p.star-rating::attr(class)").get()),
        in_stock="In stock" in availability,
    )
