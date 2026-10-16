from __future__ import annotations

import re

RATING_WORDS = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}

_PRICE_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)")


def parse_price(text: str | None) -> float | None:
    """'£51.77' -> 51.77  |  '£1,234.50' -> 1234.5  |  garbage -> None"""
    if not text:
        return None
    match = _PRICE_RE.search(text.replace("\xa0", " "))
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def parse_rating(class_attr: str | None) -> int | None:
    """'star-rating Three' -> 3"""
    if not class_attr:
        return None
    for word in class_attr.split():
        if word in RATING_WORDS:
            return RATING_WORDS[word]
    return None
