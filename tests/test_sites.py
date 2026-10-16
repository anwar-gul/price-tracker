from __future__ import annotations

from types import SimpleNamespace

import pytest

from pricetracker import cli
from pricetracker.sites import SitesError, load_sites, resolve_site

YAML_SITES = """
sites:
  - name: books
    spider: books
    start_url: https://books.toscrape.com/catalogue/page-1.html
    max_pages: 2
  - name: custom
    spider: generic
    start_url: https://shop.example.com/catalog
    render: true
    pagination: "a.next::attr(href)"
    selectors:
      product: "div.card"
      title: "h2::text"
      price: ".price::text"
  - name: portal
    spider: logincatalog
    start_url: https://portal.example.com/login
    login:
      username_env: PORTAL_USER
      password_env: PORTAL_PASS
      csrf_selector: "meta[name=csrf-token]"
      next_selector: "button[name=next]"
"""


def write_sites(tmp_path, text: str = YAML_SITES, suffix: str = ".yaml"):
    path = tmp_path / f"sites{suffix}"
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_yaml_sites(tmp_path):
    sites = load_sites(write_sites(tmp_path))
    assert [site.name for site in sites.sites] == ["books", "custom", "portal"]


def test_loads_json_sites(tmp_path):
    import json

    data = {
        "sites": [{"name": "books", "spider": "books", "start_url": "https://example.com/"}]
    }
    path = write_sites(tmp_path, json.dumps(data), suffix=".json")
    sites = load_sites(path)
    assert sites.get("books").spider == "books"


def test_selector_defaults_are_books_compatible(tmp_path):
    site = load_sites(write_sites(tmp_path)).get("custom")
    assert site.selectors.product == "div.card"
    assert site.selectors.link == "h3 a::attr(href)"  # default kept
    assert site.render is True


def test_unknown_site_lists_known_names(tmp_path):
    with pytest.raises(SitesError, match="unknown site 'nope'.*known: books"):
        resolve_site(write_sites(tmp_path), "nope")


def test_missing_file_is_a_user_error(tmp_path):
    with pytest.raises(SitesError, match="sites file not found"):
        load_sites(tmp_path / "missing.yaml")


def test_invalid_spider_rejected(tmp_path):
    text = "sites:\n  - name: x\n    spider: nope\n    start_url: https://x.test/\n"
    with pytest.raises(SitesError, match="spider must be one of"):
        load_sites(write_sites(tmp_path, text))


def test_selectors_only_for_generic(tmp_path):
    text = (
        "sites:\n  - name: x\n    spider: books\n    start_url: https://x.test/\n"
        "    selectors:\n      product: div\n"
    )
    with pytest.raises(SitesError, match="only supported with spider: generic"):
        load_sites(write_sites(tmp_path, text))


def test_login_only_for_logincatalog(tmp_path):
    text = (
        "sites:\n  - name: x\n    spider: books\n    start_url: https://x.test/\n"
        "    login:\n      username_env: U\n"
    )
    with pytest.raises(SitesError, match="only supported with spider: logincatalog"):
        load_sites(write_sites(tmp_path, text))


def test_start_url_must_be_http(tmp_path):
    text = "sites:\n  - name: x\n    start_url: ftp://x.test/\n"
    with pytest.raises(SitesError, match="start_url must be http"):
        load_sites(write_sites(tmp_path, text))


def site_args(tmp_path, name, **overrides):
    base = dict(
        site=name,
        sites_file=str(write_sites(tmp_path)),
        username=None,
        password=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_cli_resolves_generic_site(tmp_path):
    spider, kwargs = cli._site_crawl_args(site_args(tmp_path, "custom"))
    assert spider == "generic"
    assert kwargs["run_name"] == "custom"
    assert kwargs["start_url"] == "https://shop.example.com/catalog"
    assert kwargs["render"] == "1"
    assert kwargs["pagination"] == "a.next::attr(href)"
    assert kwargs["selectors"]["product"] == "div.card"
    assert kwargs["selectors"]["link"] == "h3 a::attr(href)"


def test_cli_resolves_books_site_without_extra_kwargs(tmp_path):
    spider, kwargs = cli._site_crawl_args(site_args(tmp_path, "books"))
    assert spider == "books"
    assert kwargs["max_pages"] == 2
    assert "selectors" not in kwargs
    assert "csrf_selector" not in kwargs


def test_cli_login_site_reads_credentials_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTAL_USER", "alice")
    monkeypatch.setenv("PORTAL_PASS", "s3cret")
    spider, kwargs = cli._site_crawl_args(site_args(tmp_path, "portal"))
    assert spider == "logincatalog"
    assert kwargs["username"] == "alice"
    assert kwargs["password"] == "s3cret"
    assert kwargs["csrf_selector"] == "meta[name=csrf-token]"
    assert kwargs["next_selector"] == "button[name=next]"


def test_cli_login_site_flags_override_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTAL_USER", "alice")
    monkeypatch.setenv("PORTAL_PASS", "s3cret")
    args = site_args(tmp_path, "portal", username="bob")
    _, kwargs = cli._site_crawl_args(args)
    assert kwargs["username"] == "bob"


def test_cli_login_site_requires_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("PORTAL_USER", raising=False)
    monkeypatch.delenv("PORTAL_PASS", raising=False)
    with pytest.raises(SitesError, match="set PORTAL_USER and PORTAL_PASS"):
        cli._site_crawl_args(site_args(tmp_path, "portal"))


def test_crawl_with_missing_sites_file_exits_2(tmp_path, capsys):
    args = cli.build_parser().parse_args(
        ["crawl", "--site", "x", "--sites-file", str(tmp_path / "missing.yaml")]
    )
    assert cli.cmd_crawl(args) == 2
    assert "sites file not found" in capsys.readouterr().err


def test_crawl_parser_accepts_login_flags():
    args = cli.build_parser().parse_args(
        [
            "crawl", "--spider", "logincatalog",
            "--csrf-selector", "meta[name=csrf-token]",
            "--next-selector", "button[name=next]",
            "--submit-selector", "button[name=signin]",
        ]
    )
    assert args.csrf_selector == "meta[name=csrf-token]"
    assert args.submit_selector == "button[name=signin]"
