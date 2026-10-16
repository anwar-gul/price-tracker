# price-tracker

**Scrape a product catalog → price history (SQLite or Postgres) → drop alerts → CSV/Excel reports.**
**Production-ready: Docker (Playwright image), scheduling with locking, webhook/Slack/email alerts, Prometheus metrics, healthcheck mode, Postgres support.**

Scheduled price monitoring for shops and competitors. Scrapy spiders crawl static
catalogs, **JavaScript-rendered catalogs (headless Chromium via Playwright)** and
**login-gated catalogs (browser session handling)** across pagination, store
every price point, detect changes between runs, alert on drops, and
export a clean Excel/CSV report. Built against
[books.toscrape.com](https://books.toscrape.com) (static) and a local JS demo shop;
pointing it at another catalog = adapting one `parse()` method.

```
crawl (50 pages, polite) ──> SQLite / Postgres ──> change detection ──> Slack/webhook/email
                              │                        │
                              │                        └─> Prometheus metrics (watch --metrics-port)
                              └────────> exports/catalog.xlsx + CSVs
```

## Demo

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium   # only needed for the JS-catalog spider

.venv/bin/python -m pricetracker crawl --db data/prices.db                # full crawl
.venv/bin/python -m pricetracker report --db data/prices.db               # summary
.venv/bin/python -m pricetracker export --db data/prices.db --out exports # CSV + Excel
.venv/bin/python -m pricetracker watch --interval-minutes 60              # scheduled loop
.venv/bin/python -m pricetracker status --check-max-age-minutes 120      # healthcheck (exit 1 if stale)
```

Real output (full crawl of the live site):

```
INFO: Tracked 1000 books | 0 price drops | 0 rises -> data/prices.db
```

```
Price tracker report
==============================================================
Books tracked:      1000
Price points:       1000
Price changes seen: 1
Catalog value:      35,070.35
Average price:      35.07

Latest 1 change(s):
When                  Title                                     Old      New    Chg%
------------------------------------------------------------------------------------
2026-10-15T21:41:35+00:00  Most Blessed of the Patriarchs...    44.48    37.81  -15.0%
```

When a price drops, the pipeline logs `PRICE DROP: '<title>' 44.48 -> 37.81 (-15.0%)`
and appends to `data/alerts.log`. Email alerts are available via SMTP env vars
(`SMTP_HOST`, `SMTP_USER`, `SMTP_PASS`, `ALERT_FROM`, `ALERT_TO`).

The Excel export includes a **Price Trends** sheet: one column per product (top 10
by history volume) on a shared date axis, with a line chart so drops are visible
at a glance. Lines connect across runs where a product had no change.

## JavaScript catalogs (Playwright)

Many shops render products client-side: the HTML a plain scraper receives contains
**no products at all**. The `jscatalog` spider renders each page in headless Chromium,
waits for the content to appear, then feeds the same SQLite/alert/export pipeline.

A local demo shop ships with the repo so this is provable offline:

```bash
.venv/bin/python -m pricetracker demo-shop --port 8765          # terminal 1
.venv/bin/python -m pricetracker crawl --spider books     --start-url http://127.0.0.1:8765/
.venv/bin/python -m pricetracker crawl --spider jscatalog --start-url http://127.0.0.1:8765/
```

Real output — same URL, same pipeline, one browser:

```
# plain HTTP spider: the JS catalog is invisible
INFO: Tracked 0 books | 0 price drops | 0 rises

# Playwright spider: rendered, extracted, stored
INFO: Tracked 12 books | 0 price drops | 0 rises
```

Restart the shop with `--sale 15` and re-crawl to see the full loop:

```
WARNING: PRICE DROP: 'Aeron Chair' 1299.00 -> 1104.15 (-15.0%)
WARNING: PRICE DROP: 'Standing Desk' 649.00 -> 551.65 (-15.0%)
...
INFO: Tracked 12 books | 12 price drops | 0 rises
```

The shop's JS waits 0.8s before rendering (configurable with `--delay`) and renders
markup compatible with the static target, so both spiders share the same extractor.
Selectors for a real client site are swapped in `extractors.py` + the spider.

## Login-gated catalogs (sessions)

Many B2B/wholesale catalogs only show prices after signing in. The `logincatalog`
spider renders the login page, fills the form, submits it, waits for the catalog,
then reuses that browser session (cookies) for every paginated page — the same
flow a logged-in person produces. Credentials come from `--username`/`--password`
flags or `SHOP_USER`/`SHOP_PASSWORD` env vars (keep passwords out of shell history
in production).

The demo shop can gate itself behind a login form, so this works offline too:

```bash
.venv/bin/python -m pricetracker demo-shop --port 8765 --user demo --password demo  # terminal 1
.venv/bin/python -m pricetracker crawl --spider books --start-url http://127.0.0.1:8765/
.venv/bin/python -m pricetracker crawl --spider logincatalog --start-url http://127.0.0.1:8765/login \
    --username demo --password demo
```

Real output — same gated URL:

```
# plain HTTP spider: redirected to the login form, sees no products
INFO: Tracked 0 books | 0 price drops | 0 rises

# login spider: signs in, session carried across both rendered pages
INFO: Tracked 12 books | 0 price drops | 0 rises
```

The gate protects the JSON API too (`/api/products` requires the session cookie),
so only a real logged-in browser — not a naive HTTP client — gets the products.

## Features

- **Scrapy spider** — handles pagination, retries, throttling, robots.txt (`ROBOTSTXT_OBEY = True`, 0.5s delay)
- **JavaScript catalogs** — `jscatalog` spider renders pages with headless Chromium (`scrapy-playwright`), waits for content, skips images/fonts for speed, then uses the same pipeline
- **Login-gated catalogs** — `logincatalog` spider fills/submits a login form in the browser and carries the session cookie through pagination
- **Price history** — SQLite out of the box, **Postgres for multi-writer/production** (`PRICETRACKER_DATABASE_URL`); every price point kept, only real changes create new history rows
- **Change detection** — `(old → new, %, DROP/RISE)` recorded between runs; drops trigger alerts
- **Alerts** — Slack incoming webhook, generic JSON webhook (retries + backoff), optional SMTP email, always-on `alerts.log` audit trail, per-product cooldown, zero-items (selector-drift) warning
- **Exports** — `catalog.csv`, `price_history.csv`, styled `catalog.xlsx` (Catalog + Price Changes sheets, plus a Price Trends sheet with a line chart of every product's price over time once changes exist)
- **Scheduling** — `watch` command with **single-instance file lock**, interruptible sleep, optional Prometheus metrics endpoint; or plain cron
- **Run tracking** — every crawl recorded in `runs` (items/changes/drops/status); `status` doubles as a Docker healthcheck
- **Ops-ready** — Docker image (Playwright base), compose with Postgres, `.env` config (pydantic-settings), structured logs (structlog), Prometheus metrics, `prune` for retention, CI (ruff + pytest 3.12/3.13 + Postgres job + image smoke test)
- **Crash-safe batches** — unparseable items are dropped with a warning, never abort the crawl

## Commands

| Command | What it does |
|---|---|
| `crawl [--spider books\|jscatalog\|logincatalog] [--start-url U] [--max-pages N] [--username U --password P] [--db PATH\|URL]` | One crawl (exit 0 only if the run succeeded) |
| `report [--limit N]` | Totals + recent price changes |
| `export [--out DIR]` | CSV + Excel exports |
| `watch [--interval-minutes M] [--runs N] [--metrics-port P] [--lock-file PATH] [--db PATH\|URL]` | Repeating crawl + export + report; second instance exits 1 |
| `status [--check-max-age-minutes N]` | DB stats + recent runs; with `--check-max-age-minutes` exits 1 when the last successful run is too old |
| `prune [--keep-days N]` | Delete history/changes/alerts older than N days, then VACUUM (SQLite) |
| `init-db` | Create tables (idempotent) — run once before `watch` on a fresh DB |
| `demo-shop [--port P] [--delay S] [--sale %] [--user U --password P]` | Local JS-rendered catalog, optionally login-gated |

Scheduling with cron:

```cron
0 * * * * cd /path/to/price-tracker && .venv/bin/python -m pricetracker crawl && .venv/bin/python -m pricetracker export
```

## Configuration

Every setting is optional. Precedence: CLI flag → env var → `.env` file → default.
Copy `.env.example` to `.env` to see all of them.

| Env var (prefix `PRICETRACKER_`) | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///data/prices.db` | Storage: path, `sqlite://`, or `postgresql+psycopg://` URL |
| `WEBHOOK_URL` | *(off)* | Slack incoming webhook (auto-format) or any JSON webhook |
| `ALERT_COOLDOWN_HOURS` | `24` | Per-product silence window after a delivered drop alert |
| `ALERT_ON_ZERO_ITEMS` | `true` | Warn when a crawl suddenly finds 0 items (selector drift) |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` / `ALERT_FROM` / `ALERT_TO` | *(off)* | Email alerts (legacy unprefixed names still supported) |
| `DOWNLOAD_DELAY`, `RETRY_TIMES`, `USER_AGENT` | `0.5`, `3`, browser UA | Crawl politeness / identification |
| `PROXY_URL` | *(off)* | HTTP proxy for crawls (including Playwright browser traffic) |
| `PLAYWRIGHT_CONNECT_URL` | *(off)* | Connect to a remote browser (e.g. browserless) instead of launching locally |
| `LOG_LEVEL`, `LOG_FORMAT` | `INFO`, `console` | structlog; `json` for fleets |
| `METRICS_PORT` | `0` (off) | Prometheus endpoint for `watch` |

## Production (Docker)

```bash
cp .env.example .env            # adjust, e.g. PRICETRACKER_WEBHOOK_URL
docker compose up -d --build    # Postgres + scheduler (hourly watch, metrics on :9109)

docker compose run --rm scheduler crawl --spider books --max-pages 5   # one-off crawl
docker compose run --rm scheduler status --check-max-age-minutes 120  # healthcheck
docker compose run --rm scheduler prune --keep-days 90
```

- The scheduler container has a **Docker healthcheck** running `status --check-max-age-minutes 120`.
- **Metrics** (`:9109/metrics`): `pricetracker_runs_total{spider,status}`, `pricetracker_items_total`,
  `pricetracker_price_drops_total`, `pricetracker_alerts_total{channel,status}`,
  `pricetracker_last_success_timestamp_seconds`.
- **Alerts**: set `PRICETRACKER_WEBHOOK_URL` to a Slack webhook (payload auto-detected) or any
  endpoint accepting JSON `{service, message, event, title, url, old_price, new_price, pct_change, at}`.
  Webhook failures retry with backoff; every alert is also appended to `alerts.log` regardless.
- **Retention**: schedule `prune --keep-days 90` (cron or compose one-off) to bound database growth.
- CI runs ruff, the full pytest suite on Python 3.12/3.13, the Postgres test against a service
  container, and builds + smoke-tests the image (`.github/workflows/ci.yml`).

## Architecture

| Module | Responsibility |
|---|---|
| `spiders/base.py` | Shared start URL / page limit / domain wiring |
| `spiders/books.py` | Static catalog crawl (books.toscrape.com) |
| `spiders/jscatalog.py` | Playwright-rendered crawl for JS catalogs |
| `spiders/login.py` | Login-form session handling on top of `jscatalog` |
| `extractors.py` | Product-card → item extraction (shared by all spiders) |
| `parsing.py` | Price/rating normalization (`£1,234.50` → `1234.5`) |
| `pipelines.py` | DB writes, change detection, alert dispatch |
| `extensions.py` | Run tracking (start/finish, stats) + zero-items warning |
| `db.py` | SQLAlchemy Core: schema, upsert/history/change logic, runs, prune |
| `notify.py` | Webhook/Slack/email delivery, cooldown, alert audit log |
| `config.py` | pydantic-settings: env/.env config with validation |
| `settings.py` | Scrapy settings derived from config (throttle, proxy, Playwright) |
| `log.py` / `metrics.py` | structlog setup / Prometheus counters |
| `report.py` | CSV/Excel exports, console report |
| `demo_shop.py` | Local JS-rendered catalog (`demo-shop` command) |
| `cli.py` | `crawl` / `report` / `export` / `watch` / `status` / `prune` / `init-db` / `demo-shop` |

```
books(url PK, title, price, rating, in_stock, first_seen, last_seen)
price_history(id, url, price, scraped_at)           -- every price change point
price_changes(id, url, title, old, new, changed_at) -- event log, feeds alerts
alerts(id, url, kind, title, detail, status, sent_at) -- sent/failed/suppressed/logged
runs(id, spider, status, started_at, finished_at, items, changes, drops, message)
```

## Tests

```bash
.venv/bin/python -m pytest -q                    # 86 passed (1 skipped: pg)
PRICETRACKER_TEST_PG_URL=postgresql+psycopg://pt:pw@127.0.0.1:55432/pricetracker \
  .venv/bin/python -m pytest -q -m pg            # Postgres roundtrip (docker run postgres:17-alpine)
.venv/bin/ruff check pricetracker tests
```

The suite runs all spiders against saved HTML fixtures (one static, one captured
from a real browser render), covers pagination limits, price/rating parsing edge
cases, DB upsert + history semantics (SQLite + Postgres), run tracking, alert
delivery (local webhook receiver: retries, Slack shape, cooldown), config
validation incl. legacy env aliases, the watch lock, prune retention, the demo
shop API (incl. the login gate: redirects without a cookie, 401 on bad
credentials, session grants catalog access), credential resolution for the login
spider, and export/report contents. Two integration tests launch Chromium
end-to-end against the demo shop (plain JS and login-gated); they skip
automatically when Chromium is not installed.

## Roadmap

- Google Sheets / Airtable export targets
- Per-site selector config file (multi-shop tracking)
- CSRF-token / multi-step login flows for stricter sites
- Alembic migrations (currently `init-db` creates the schema idempotently)
