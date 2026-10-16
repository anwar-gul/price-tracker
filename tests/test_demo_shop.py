from __future__ import annotations

import http.cookiejar
import json
import re
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


# --- CSRF-protected login ---

CSRF_RE = re.compile(r'<meta name="csrf-token" content="([0-9a-f]+)">')


def post_form(base: str, opener, fields: dict):
    data = urllib.parse.urlencode(fields).encode()
    return opener.open(base + "/login", data=data, timeout=10)


def csrf_token(html: str) -> str:
    match = CSRF_RE.search(html)
    assert match, "login page has no csrf meta tag"
    return match.group(1)


def test_csrf_login_page_exposes_meta_and_empty_field():
    with serving(user="demo", password="demo", csrf=True) as base:
        html = get(base + "/login")
    assert csrf_token(html)
    assert '<input type="hidden" name="csrf_token" value="">' in html


def test_csrf_missing_token_rejected():
    with serving(user="demo", password="demo", csrf=True) as base:
        opener = authed_opener()
        try:
            post_form(base, opener, {"username": "demo", "password": "demo"})
            raised = False
        except urllib.error.HTTPError as exc:
            raised = exc.code == 403
    assert raised


def test_csrf_wrong_token_rejected():
    with serving(user="demo", password="demo", csrf=True) as base:
        opener = authed_opener()
        try:
            post_form(
                base, opener,
                {"username": "demo", "password": "demo", "csrf_token": "0" * 32},
            )
            raised = False
        except urllib.error.HTTPError as exc:
            raised = exc.code == 403
    assert raised


def test_csrf_token_is_single_use():
    with serving(user="demo", password="demo", csrf=True) as base:
        opener = authed_opener()
        token = csrf_token(get(base + "/login"))
        with post_form(
            base, opener,
            {"username": "demo", "password": "demo", "csrf_token": token},
        ) as response:
            assert response.status == 200
        try:
            post_form(
                base, opener,
                {"username": "demo", "password": "demo", "csrf_token": token},
            )
            raised = False
        except urllib.error.HTTPError as exc:
            raised = exc.code == 403
    assert raised


def test_csrf_valid_flow_grants_session():
    with serving(user="demo", password="demo", csrf=True) as base:
        opener = authed_opener()
        token = csrf_token(get(base + "/login"))
        post_form(base, opener, {"username": "demo", "password": "demo", "csrf_token": token})
        data = json.loads(opener.open(base + "/api/products?page=1", timeout=10).read())
    assert data["count"] == 6


# --- two-step login ---


def test_two_step_first_page_has_no_password_field():
    with serving(user="demo", password="demo", two_step=True) as base:
        opener = authed_opener()
        html = opener.open(base + "/login", timeout=10).read().decode()
    assert 'name="username"' in html
    assert 'name="password"' not in html
    assert 'name="next"' in html


def test_two_step_password_page_has_stage_token():
    with serving(user="demo", password="demo", two_step=True) as base:
        opener = authed_opener()
        with post_form(base, opener, {"username": "demo", "next": "1"}) as response:
            html = response.read().decode()
    assert 'name="password"' in html
    match = re.search(r'name="stage" value="([0-9a-f]+)"', html)
    assert match


def test_two_step_full_flow_grants_session():
    with serving(user="demo", password="demo", two_step=True) as base:
        opener = authed_opener()
        html = post_form(base, opener, {"username": "demo", "next": "1"}).read().decode()
        stage = re.search(r'name="stage" value="([0-9a-f]+)"', html).group(1)
        with post_form(base, opener, {"stage": stage, "password": "demo", "signin": "1"}) as resp:
            assert resp.status == 200
        data = json.loads(opener.open(base + "/api/products?page=1", timeout=10).read())
    assert data["count"] == 6


def test_two_step_unknown_stage_rejected():
    with serving(user="demo", password="demo", two_step=True) as base:
        opener = authed_opener()
        try:
            post_form(base, opener, {"stage": "nope", "password": "demo", "signin": "1"})
            raised = False
        except urllib.error.HTTPError as exc:
            raised = exc.code == 400
    assert raised


def test_two_step_empty_username_rejected():
    with serving(user="demo", password="demo", two_step=True) as base:
        opener = authed_opener()
        try:
            post_form(base, opener, {"username": "", "next": "1"})
            raised = False
        except urllib.error.HTTPError as exc:
            raised = exc.code == 401
    assert raised


def test_two_step_with_csrf_full_flow():
    with serving(user="demo", password="demo", two_step=True, csrf=True) as base:
        opener = authed_opener()
        token1 = csrf_token(get(base + "/login"))
        html = post_form(
            base, opener, {"username": "demo", "next": "1", "csrf_token": token1}
        ).read().decode()
        stage = re.search(r'name="stage" value="([0-9a-f]+)"', html).group(1)
        token2 = csrf_token(html)  # fresh token on the password page
        post_form(
            base, opener,
            {"stage": stage, "password": "demo", "signin": "1", "csrf_token": token2},
        )
        data = json.loads(opener.open(base + "/api/products?page=1", timeout=10).read())
    assert data["count"] == 6


def test_two_step_with_csrf_stale_token_rejected():
    with serving(user="demo", password="demo", two_step=True, csrf=True) as base:
        opener = authed_opener()
        token1 = csrf_token(get(base + "/login"))
        html = post_form(
            base, opener, {"username": "demo", "next": "1", "csrf_token": token1}
        ).read().decode()
        stage = re.search(r'name="stage" value="([0-9a-f]+)"', html).group(1)
        try:
            # reusing the step-1 token (already consumed) must fail
            post_form(
                base, opener,
                {"stage": stage, "password": "demo", "signin": "1", "csrf_token": token1},
            )
            raised = False
        except urllib.error.HTTPError as exc:
            raised = exc.code == 403
    assert raised
