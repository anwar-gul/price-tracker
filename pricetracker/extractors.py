from __future__ import annotations

from .items import BookItem
from .parsing import parse_price, parse_rating


def _first_text(scope, selector: str | None) -> str | None:
    if not selector:
        return None
    values = [value.strip() for value in scope.css(selector).getall() if value.strip()]
    return " ".join(values) if values else None


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


def extract_with_selectors(pod, response, selectors: dict) -> BookItem:
    """Extract a BookItem from a product card using per-site selectors.

    Selectors come from a sites-file `SelectorConfig`; the stock selector
    should point at the availability text ("In stock" / "Out of stock" —
    anything containing "out" is treated as out of stock).
    """
    stock_text = (_first_text(pod, selectors.get("stock")) or "").lower()
    return BookItem(
        url=response.urljoin(_first_text(pod, selectors.get("link"))),
        title=_first_text(pod, selectors.get("title")),
        price=parse_price(_first_text(pod, selectors.get("price"))),
        rating=parse_rating(_first_text(pod, selectors.get("rating"))),
        in_stock=bool(stock_text) and "out" not in stock_text,
    )
