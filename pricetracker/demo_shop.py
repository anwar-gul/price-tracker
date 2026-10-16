"""Local JS-rendered demo shop used to demonstrate the Playwright spiders.

The page shell deliberately contains no product markup: cards are rendered
client-side (fetch + setTimeout), exactly like a typical SPA catalog. Plain
HTTP crawlers see an empty page; the Playwright spider sees everything.

Optional login gate (`--user`/`--password`): all pages require a session
cookie obtained via the POST /login form — used to demo login-based
session handling (spiders/logincatalog.py).

Rendered DOM mirrors books.toscrape.com's structure (article.product_pod,
p.price_color, star-rating <Word>, p.instock.availability, li.next) so the
same extractor works for both targets.
"""

from __future__ import annotations

import hmac
import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PER_PAGE = 6

CATALOG = [
    ("Aeron Chair", 1299.00, "Five", True),
    ("Standing Desk", 649.00, "Four", True),
    ("Monitor Arm", 129.00, "Four", True),
    ("Mechanical Keyboard", 159.00, "Five", True),
    ("Noise-Cancelling Headphones", 299.00, "Three", True),
    ("USB-C Dock", 189.00, "Four", True),
    ("Webcam 4K", 129.00, "Three", False),
    ("Desk Lamp", 79.00, "Four", True),
    ("Ergonomic Mouse", 89.00, "Five", True),
    ("Laptop Stand", 49.00, "Four", True),
    ("Cable Organizer", 19.00, "Three", True),
    ("Footrest", 59.00, "Four", False),
]

SHELL = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>JS Demo Shop</title></head>
<body>
<h1>JS Demo Shop</h1>
<p>Prices are rendered client-side after __DELAY__s — invisible to plain HTTP crawlers.</p>
<div id="catalog">Loading&hellip;</div>
<script>
(() => {
  const page = parseInt(new URLSearchParams(location.search).get("page") || "1", 10);
  setTimeout(async () => {
    const res = await fetch(`/api/products?page=${page}`);
    const data = await res.json();
    const pager = data.next
      ? `<ul class="pager"><li class="next"><a href="?page=${page + 1}">next</a></li></ul>`
      : "";
    document.getElementById("catalog").innerHTML = data.html + pager;
  }, __DELAY__ * 1000);
})();
</script>
</body>
</html>
"""

LOGIN_PAGE = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Sign in</title></head>
<body>
<h1>Sign in</h1>
__ERROR__
<form method="post" action="/login">
  <input name="username" placeholder="username" autocomplete="username">
  <input name="password" type="password" placeholder="password" autocomplete="current-password">
  <button type="submit">Sign in</button>
</form>
</body>
</html>
"""


def _slug(name: str) -> str:
    return name.lower().replace(" ", "-")


def _product_html(name: str, price: float, rating: str, in_stock: bool) -> str:
    cls = "instock availability" if in_stock else "outofstock availability"
    label = "In stock" if in_stock else "Out of stock"
    return (
        '<article class="product_pod">'
        f'<h3><a href="/product/{_slug(name)}" title="{name}">{name}</a></h3>'
        f'<p class="star-rating {rating}"></p>'
        f'<p class="price_color">\u00a3{price:,.2f}</p>'
        f'<p class="{cls}">{label}</p>'
        "</article>"
    )


class _Handler(BaseHTTPRequestHandler):
    server_version = "JSDemoShop/0.2"

    def do_GET(self):  # noqa: N802 - http.server API
        parsed = urlparse(self.path)
        config = self.server.config
        if parsed.path == "/login":
            self._login_page()
            return
        if config["auth"] and not self._authenticated():
            self._redirect("/login")
            return
        if parsed.path == "/api/products":
            self._api(parsed)
        elif parsed.path in ("/", "/index.html"):
            self._shell()
        else:
            self._send(404, "text/plain; charset=utf-8", b"not found")

    def do_POST(self):  # noqa: N802 - http.server API
        parsed = urlparse(self.path)
        if parsed.path == "/login":
            self._login()
            return
        self._send(404, "text/plain; charset=utf-8", b"not found")

    # --- auth ---

    def _authenticated(self) -> bool:
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            name, _, value = part.strip().partition("=")
            if name == "session" and value in self.server.sessions:
                return True
        return False

    def _login_page(self, error: bool = False):
        body = LOGIN_PAGE.replace(
            "__ERROR__",
            '<p class="error">Invalid credentials</p>' if error else "",
        ).encode()
        self._send(401 if error else 200, "text/html; charset=utf-8", body)

    def _login(self):
        length = int(self.headers.get("Content-Length", "0"))
        form = parse_qs(self.rfile.read(length).decode("utf-8"))
        username = form.get("username", [""])[0]
        password = form.get("password", [""])[0]
        config = self.server.config

        if hmac.compare_digest(username, config["user"]) and hmac.compare_digest(
            password, config["password"]
        ):
            token = secrets.token_hex(16)
            self.server.sessions.add(token)
            self.send_response(303)
            self.send_header("Set-Cookie", f"session={token}; Path=/; HttpOnly")
            self.send_header("Location", "/")
            self.end_headers()
        else:
            self._login_page(error=True)

    def _redirect(self, location: str):
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()

    # --- content ---

    def _api(self, parsed):
        config = self.server.config
        try:
            page = max(1, int(parse_qs(parsed.query).get("page", ["1"])[0]))
        except ValueError:
            page = 1
        start = (page - 1) * PER_PAGE
        chunk = config["catalog"][start : start + PER_PAGE]
        html = "".join(_product_html(*product) for product in chunk)
        next_page = start + PER_PAGE < len(config["catalog"])
        body = json.dumps({"html": html, "next": next_page, "count": len(chunk)}).encode()
        self._send(200, "application/json; charset=utf-8", body)

    def _shell(self):
        body = SHELL.replace("__DELAY__", str(self.server.config["delay"])).encode()
        self._send(200, "text/html; charset=utf-8", body)

    def _send(self, code: int, content_type: str, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep the demo quiet
        pass


def create_server(
    port: int = 0,
    delay: float = 0.8,
    sale: float = 0.0,
    user: str | None = None,
    password: str | None = None,
) -> ThreadingHTTPServer:
    """Build (but don't start) the demo shop. port=0 picks a free port.

    Set user + password to require login via POST /login (session cookie).
    """
    catalog = [
        (name, round(price * (1 - sale / 100.0), 2), rating, stock)
        for name, price, rating, stock in CATALOG
    ]
    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    server.config = {
        "catalog": catalog,
        "delay": delay,
        "auth": bool(user and password),
        "user": user or "",
        "password": password or "",
    }
    server.sessions = set()
    return server


def serve(
    port: int = 8000,
    delay: float = 0.8,
    sale: float = 0.0,
    user: str | None = None,
    password: str | None = None,
) -> None:
    server = create_server(port=port, delay=delay, sale=sale, user=user, password=password)
    actual_port = server.server_address[1]
    auth_note = "auth: ON" if server.config["auth"] else "auth: off"
    print(
        f"JS demo shop: http://localhost:{actual_port}/  "
        f"(JS delay {delay}s, sale {sale:g}%, {auth_note})"
    )
    print("Crawl it with:  python -m pricetracker crawl --spider jscatalog")
    if server.config["auth"]:
        print(
            "Login-gated:    python -m pricetracker crawl --spider logincatalog "
            f"--username {user} --password <password>"
        )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
