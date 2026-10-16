from __future__ import annotations

from pricetracker.spiders.login import LoginCatalogSpider


def test_credentials_from_args():
    spider = LoginCatalogSpider(username="alice", password="s3cret")
    assert spider.username == "alice"
    assert spider.password == "s3cret"


def test_credentials_from_env(monkeypatch):
    monkeypatch.setenv("SHOP_USER", "envuser")
    monkeypatch.setenv("SHOP_PASSWORD", "envpass")
    spider = LoginCatalogSpider()
    assert spider.username == "envuser"
    assert spider.password == "envpass"


def test_args_override_env(monkeypatch):
    monkeypatch.setenv("SHOP_USER", "envuser")
    spider = LoginCatalogSpider(username="alice")
    assert spider.username == "alice"


def test_default_start_url_is_login():
    spider = LoginCatalogSpider()
    assert spider.start_urls == ["http://localhost:8000/login"]


def test_inherits_pagination_and_pipeline_target():
    spider = LoginCatalogSpider(max_pages="2")
    assert spider.max_pages == 2
    assert spider.wait_selector == "article.product_pod"


def test_default_flow_is_single_step():
    spider = LoginCatalogSpider()
    assert spider.next_selector is None
    assert spider.csrf_selector is None
    assert spider.submit_selector == "button[type=submit]"
    assert spider.username_selector == "input[name=username]"
    assert spider.password_selector == "input[name=password]"
    assert spider.render is True  # login always renders


def test_csrf_and_two_step_args_are_stored():
    spider = LoginCatalogSpider(
        csrf_selector="meta[name=csrf-token]",
        csrf_input="input[name=csrf_token]",
        csrf_attribute="value",
        next_selector="button[name=next]",
        submit_selector="button[name=signin]",
        error_selector=".error",
        login_timeout=5,
        run_name="portal",
    )
    assert spider.csrf_selector == "meta[name=csrf-token]"
    assert spider.csrf_input == "input[name=csrf_token]"
    assert spider.csrf_attribute == "value"
    assert spider.next_selector == "button[name=next]"
    assert spider.submit_selector == "button[name=signin]"
    assert spider.error_selector == ".error"
    assert spider.login_timeout_ms == 5000
    assert spider.run_name == "portal"


def test_custom_selectors_override_extraction():
    spider = LoginCatalogSpider(
        selectors={"product": "div.card", "title": "h2::text"}
    )
    assert spider.selectors["product"] == "div.card"
    assert spider.selectors["price"] == "p.price_color::text"  # default kept
