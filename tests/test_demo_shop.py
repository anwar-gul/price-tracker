from __future__ import annotations

import http.cookiejar
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager

from pricetracker.demo_shop import create_server


@contextmanager
def serving(**kwargs):
    """Start the demo shop on a free port; yield its base URL."""
    server = create_server(port=0, delay=0.0, **kwargs)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.read().decode("utf-8")


def test_static_shell_contains_no_product_markup():
    with serving() as base:
        html = get(base + "/")
    assert "product_pod" not in html
    assert "Loading" in html


def test_api_first_page():
    with serving() as base:
        data = json.loads(get(base + "/api/products?page=1"))
    assert data["count"] == 6
    assert data["next"] is True
    assert "product_pod" in data["html"]
    assert "\u00a31,299.00" in data["html"]


def test_api_last_page_has_no_next():
    with serving() as base:
        data = json.loads(get(base + "/api/products?page=2"))
    assert data["count"] == 6
    assert data["next"] is False


def test_api_beyond_last_page_is_empty():
    with serving() as base:
        data = json.loads(get(base + "/api/products?page=99"))
    assert data["count"] == 0
    assert data["html"] == ""


def test_sale_scales_prices():
    with serving(sale=20.0) as base:
        data = json.loads(get(base + "/api/products?page=1"))
    assert "\u00a31,039.20" in data["html"]  # 1299.00 * 0.80


def authed_opener() -> urllib.request.OpenerDirector:
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def post_login(base: str, opener, username: str, password: str):
    data = urllib.parse.urlencode({"username": username, "password": password}).encode()
    return opener.open(base + "/login", data=data, timeout=10)


def test_login_required_without_cookie():
    with serving(user="demo", password="demo") as base:
        html = get(base + "/")  # 303 -> /login is followed automatically
    assert 'name="username"' in html


def test_wrong_credentials_rejected():
    with serving(user="demo", password="demo") as base:
        opener = authed_opener()
        try:
            post_login(base, opener, "demo", "wrong")
            raised = False
        except urllib.error.HTTPError as exc:
            raised = exc.code == 401
    assert raised


def test_login_grants_catalog_access():
    with serving(user="demo", password="demo") as base:
        opener = authed_opener()
        with post_login(base, opener, "demo", "demo") as response:
            assert response.status == 200
        data = json.loads(opener.open(base + "/api/products?page=1", timeout=10).read())
    assert data["count"] == 6
    assert "product_pod" in data["html"]
