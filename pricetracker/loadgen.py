"""Synthetic catalog server for load tests.

Serves books.toscrape.com-compatible HTML (same markup the `books` spider
parses), so it can measure end-to-end crawl -> pipeline -> SQLite throughput
without touching a real website.

    python -m pricetracker.loadgen --port 8900 --items 5000
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RATINGS = ("One", "Two", "Three", "Four", "Five")

ROBOTS_TXT = b"User-agent: *\nAllow: /\n"


def product_card(index: int) -> str:
    price = 10.0 + (index * 37 % 900) / 10.0
    rating = RATINGS[index % len(RATINGS)]
    return (
        '<article class="product_pod">'
        f'<h3><a href="product-{index}.html" title="Load Book {index}">'
        f"Load Book {index}</a></h3>"
        f'<p class="star-rating {rating}"></p>'
        f'<p class="price_color">£{price:.2f}</p>'
        '<p class="instock availability">In stock</p>'
        "</article>"
    )


def render_page(page: int, page_size: int, total: int) -> str:
    first = (page - 1) * page_size
    cards = "".join(product_card(i) for i in range(first, min(first + page_size, total)))
    next_link = ""
    if first + page_size < total:
        next_link = f'<li class="next"><a href="page-{page + 1}.html">next</a></li>'
    return (
        "<!DOCTYPE html><html><head><title>Load catalog</title></head><body>"
        f'<div class="row">{cards}</div>'
        f'<ul class="pager">{next_link}</ul>'
        "</body></html>"
    )


class CatalogHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - http.server API
        path = self.path.split("?")[0]
        if path == "/robots.txt":
            body, status, ctype = ROBOTS_TXT, 200, "text/plain"
        elif path.startswith("/page-") and path.endswith(".html"):
            try:
                page = int(path[len("/page-"):-len(".html")])
            except ValueError:
                page = 0
            if page < 1 or (page - 1) * self.server.page_size >= self.server.total:
                body, status, ctype = b"not found", 404, "text/plain"
            else:
                body = render_page(page, self.server.page_size, self.server.total).encode()
                status, ctype = 200, "text/html; charset=utf-8"
        else:
            body, status, ctype = b"not found", 404, "text/plain"

        if self.server.delay:
            import time

            time.sleep(self.server.delay)
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def create_server(
    port: int = 0, total: int = 1000, page_size: int = 20, delay: float = 0.0
) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), CatalogHandler)
    server.total = total
    server.page_size = page_size
    server.delay = delay
    return server


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8900)
    parser.add_argument("--items", type=int, default=1000)
    parser.add_argument("--page-size", type=int, default=20)
    parser.add_argument("--delay", type=float, default=0.0)
    args = parser.parse_args(argv)

    server = create_server(args.port, args.items, args.page_size, args.delay)
    pages = -(-args.items // args.page_size)
    print(
        f"Load catalog: {args.items} items over {pages} pages at "
        f"http://127.0.0.1:{args.port}/page-1.html"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
