"""Per-site configuration: track many shops from one command.

A sites file (YAML or JSON) declares each target once — spider, start URL,
page limit, selectors, login flow — then `crawl --site NAME` /
`watch --site NAME` resolves everything from it:

    sites:
      - name: books
        start_url: https://books.toscrape.com/
        spider: books
        max_pages: 2
      - name: any-shop
        start_url: https://shop.example.com/catalog
        spider: generic
        selectors:
          product: "div.product-card"
          title: "h2::text"
          link: "a::attr(href)"
          price: ".price::text"
          rating: ".rating::attr(data-score)"
          stock: ".stock::text"

The `generic` spider extracts with the given CSS selectors, so adding a new
shop is configuration, not code (see sites.yaml for a full example).
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import (
    BaseModel,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

SPIDERS = ("books", "jscatalog", "logincatalog", "generic")


class SitesError(ValueError):
    """Invalid sites file or unknown site name (message is user-facing)."""


class SelectorConfig(BaseModel):
    """CSS selectors for one generic site (scoped to each product card)."""

    product: str = "article.product_pod"
    title: str = "h3 a::attr(title)"
    link: str = "h3 a::attr(href)"
    price: str = "p.price_color::text"
    rating: str = "p.star-rating::attr(class)"
    stock: str = "p.instock.availability ::text"


class LoginConfig(BaseModel):
    """Login flow for the logincatalog spider (single-step by default)."""

    username_env: str = "SHOP_USER"
    password_env: str = "SHOP_PASSWORD"
    username_selector: str = "input[name=username]"
    password_selector: str = "input[name=password]"
    submit_selector: str = "button[type=submit]"
    csrf_selector: str | None = None
    csrf_input: str = "input[name=csrf_token]"
    csrf_attribute: str = "content"
    next_selector: str | None = None
    wait_selector: str | None = None
    error_selector: str | None = None


class SiteConfig(BaseModel):
    name: str
    spider: str = "books"
    start_url: str
    max_pages: int | None = None
    wait_selector: str | None = None
    render: bool = False
    pagination: str | None = None
    selectors: SelectorConfig | None = None
    login: LoginConfig | None = None

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("site name must not be empty")
        return value

    @field_validator("spider")
    @classmethod
    def _check_spider(cls, value: str) -> str:
        if value not in SPIDERS:
            raise ValueError(f"spider must be one of {', '.join(SPIDERS)}")
        return value

    @field_validator("start_url")
    @classmethod
    def _check_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("start_url must be http(s)")
        return value

    @field_validator("max_pages")
    @classmethod
    def _check_max_pages(cls, value: int | None) -> int | None:
        if value is not None and value < 1:
            raise ValueError("max_pages must be >= 1")
        return value

    @model_validator(mode="after")
    def _check_sections(self) -> SiteConfig:
        if self.selectors is not None and self.spider != "generic":
            raise ValueError("selectors are only supported with spider: generic")
        if self.pagination is not None and self.spider != "generic":
            raise ValueError("pagination is only supported with spider: generic")
        if self.login is not None and self.spider != "logincatalog":
            raise ValueError("login is only supported with spider: logincatalog")
        return self


class SitesFile(BaseModel):
    sites: list[SiteConfig] = Field(min_length=1)

    def get(self, name: str) -> SiteConfig:
        for site in self.sites:
            if site.name == name:
                return site
        known = ", ".join(site.name for site in self.sites)
        raise SitesError(f"unknown site {name!r} (known: {known})")


def load_sites(path: str | Path) -> SitesFile:
    """Read a YAML (.yaml/.yml, needs PyYAML) or JSON sites file."""
    target = Path(path)
    if not target.exists():
        raise SitesError(f"sites file not found: {target}")
    text = target.read_text(encoding="utf-8")
    if target.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise SitesError(
                "PyYAML is required for YAML sites files: pip install pyyaml "
                "(or use a .json sites file)"
            ) from exc
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    try:
        return SitesFile.model_validate(data)
    except ValidationError as exc:
        raise SitesError(f"invalid sites file {target}: {exc}") from exc


def resolve_site(path: str | Path, name: str) -> SiteConfig:
    return load_sites(path).get(name)
