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
