# Price Tracker

Track product prices, spot price drops, and export the results to Excel or CSV.

**Start here:** follow the three steps below. Everything after that is optional.

## First run

**You need:** Python 3.11 or newer, an internet connection, and a terminal open
in this project's folder. These commands use macOS/Linux paths.

### 1. Install once

Run each line in order:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

**Checkpoint:** wait for installation to finish and the terminal prompt to return.
The basic demo needs no Docker, database setup, or browser download.

### 2. Collect a small sample

This reads one page from the practice site, [Books to Scrape](https://books.toscrape.com):

```bash
.venv/bin/python -m pricetracker crawl --max-pages 1
```

**Checkpoint:** look for a `Tracked … books` message with a nonzero count.
Prices are saved in `data/prices.db` by default.

**Zero price drops is normal.** The first crawl saves starting prices. Later
crawls detect changes only if the site's prices change.

### 3. See your results

Print a summary:

```bash
.venv/bin/python -m pricetracker report
```

Create the spreadsheet and CSV files:

```bash
.venv/bin/python -m pricetracker export
```

**Done:** open `exports/catalog.xlsx` in Excel or another spreadsheet app.
You also get `exports/catalog.csv` and `exports/price_history.csv`.

You can stop here. Your saved data will still be there next time.

## What do you want to do next?

- [Collect the full catalog or run hourly](#everyday-commands)
- [Fix a setup problem](#if-you-get-stuck)
- [Try a JavaScript shop or a shop with login](#browser-demos)
- [Set up alerts or change settings](#settings-and-alerts)
- [Run with Docker and Postgres](#docker-and-operations)
- [Look up commands or work on the code](#developer-reference)

## Everyday commands

Run these from the project folder. No virtual-environment activation is needed.

**Collect the full catalog** (this takes longer than the one-page sample):

```bash
.venv/bin/python -m pricetracker crawl
```

**Track every hour**, with a report and export after each crawl:

```bash
.venv/bin/python -m pricetracker init-db
.venv/bin/python -m pricetracker watch --interval-minutes 60
```

Leave the terminal running. Press **Ctrl+C** to stop.
`init-db` safely creates missing tables and prepares the default data folder.

**Check recent runs:**

```bash
.venv/bin/python -m pricetracker status
```

**See all options for a command:**

```bash
.venv/bin/python -m pricetracker crawl --help
```

## If you get stuck

| What you see | What to do |
|---|---|
| `python3: command not found` | Install Python 3.11 or newer, then reopen your terminal. |
| `.venv/bin/python` cannot be found | Check that you are in the project folder and have completed step 1. |
| `No module named …` | Run the installation command in step 1 again using `.venv/bin/python`. |
| No price changes | Run another crawl after prices change, or try the sale demo below. |
| A browser executable is missing | Run `.venv/bin/python -m playwright install chromium`. Only browser-based spiders need it. |
| A custom shop returns zero products | Check whether it needs JavaScript or login, then adapt the product selectors for that shop. |
| `another watch is already running` | Stop the existing watcher with Ctrl+C before starting another. |

**Using Windows?** Create the environment with `py -m venv .venv`, then use
`.venv\Scripts\python.exe` wherever this guide uses `.venv/bin/python`.
For browser setup, use `.venv\Scripts\python.exe -m playwright install chromium`.

## Browser demos

A *spider* is the part of the app that reads a catalog. Choose one for the site:

| Site type | Spider |
|---|---|
| Products are already in the page HTML | `books` (default) |
| JavaScript loads the products | `jscatalog` |
| You must sign in to see prices | `logincatalog` |

**Before either demo:** install Chromium once.

```bash
.venv/bin/python -m playwright install chromium
```

The demos run locally after installation. To track a different real shop, adapt
`pricetracker/extractors.py` and the relevant spider; changing the URL alone
usually isn't enough.

<details>
<summary><strong>Try the JavaScript shop and a 15% price drop</strong></summary>

Open two terminals in the project folder.

**1. Terminal A — start the shop and leave it running:**

```bash
.venv/bin/python -m pricetracker demo-shop --port 8765
```

**2. Terminal B — collect the starting prices:**

```bash
.venv/bin/python -m pricetracker crawl --spider jscatalog --start-url http://127.0.0.1:8765/
```

Expected: **12 products tracked**. Logs call them “books” even in the demo shop.

**3. Terminal A — press Ctrl+C, then restart with a sale:**

```bash
.venv/bin/python -m pricetracker demo-shop --port 8765 --sale 15
```

**4. Terminal B — repeat the same crawl command from step 2.**

Expected: **12 price drops**, if you started with the regular demo prices.
Run `report` or `export` to view them. Drop alerts are also written to
`data/alerts.log` by default.

Press Ctrl+C in Terminal A when you are finished.

</details>

<details>
<summary><strong>Try a shop that requires login</strong></summary>

Stop any demo shop already using port 8765 with Ctrl+C.

**Terminal A — start the login demo and leave it running:**

```bash
.venv/bin/python -m pricetracker demo-shop --port 8765 --user demo --password demo
```

**Terminal B — sign in and collect prices:**

```bash
.venv/bin/python -m pricetracker crawl --spider logincatalog --start-url http://127.0.0.1:8765/login --username demo --password demo
```

Expected: **12 products tracked**. The browser keeps the login session across pages.
Press Ctrl+C in Terminal A when finished.

The credentials above are only for this local demo. For real shops, use
`SHOP_USER` and `SHOP_PASSWORD` environment variables to keep passwords out of
shell history. More complex login flows may need changes to the spider.

</details>

## Settings and alerts

**No configuration is needed for the first run.** To customize it, copy the example:

```bash
cp .env.example .env
```

Edit `.env` to set `PRICETRACKER_WEBHOOK_URL` for Slack or a JSON webhook,
or the SMTP settings for email. Local drop alerts go to `data/alerts.log` by default.

<details>
<summary><strong>All settings and defaults</strong></summary>

Every setting is optional. Precedence: CLI flag → env var → `.env` file → default.
Copy `.env.example` to `.env` to see all of them.

| Env var (prefix `PRICETRACKER_`) | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///data/prices.db` | Storage: path, `sqlite://`, or `postgresql+psycopg://` URL |
| `WEBHOOK_URL` | *(off)* | Slack incoming webhook (auto-format) or any JSON webhook |
| `ALERT_COOLDOWN_HOURS` | `24` | Per-product silence window after a delivered drop alert |
| `ALERT_ON_ZERO_ITEMS` | `true` | Warn when a crawl suddenly finds 0 items (selector drift) |
| `ALERT_MAX_ATTEMPTS` | `5` | Delivery attempts before a failed alert is marked `dead` |
| `ALERT_RETRY_BASE_SECONDS` | `60` | Base of the exponential retry backoff (60s, 120s, 240s, ...) |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` / `ALERT_FROM` / `ALERT_TO` | *(off)* | Email alerts (legacy unprefixed names still supported) |
| `DOWNLOAD_DELAY`, `RETRY_TIMES`, `USER_AGENT` | `0.5`, `3`, browser UA | Crawl politeness / identification |
| `PROXY_URL` | *(off)* | HTTP proxy for crawls (including Playwright browser traffic) |
| `PLAYWRIGHT_CONNECT_URL` | *(off)* | Connect to a remote browser (e.g. browserless) instead of launching locally |
| `LOG_LEVEL`, `LOG_FORMAT` | `INFO`, `console` | structlog; `json` for fleets |
| `METRICS_PORT` | `0` (off) | Prometheus endpoint for `watch` |
| `PRUNE_KEEP_DAYS`, `PRUNE_EVERY_HOURS` | `90`, `24` | `watch` runs retention prune automatically when due (0 hours = off) |

</details>

## Docker and operations

Optional: use Docker Compose to run Postgres and an hourly scheduler in the background.

<details>
<summary><strong>Docker setup, health checks, metrics, and retention</strong></summary>

Before starting, set `POSTGRES_PASSWORD` in `.env` to replace the Compose default.

```bash
cp .env.example .env            # adjust, e.g. PRICETRACKER_WEBHOOK_URL
docker compose up -d --build    # Postgres + scheduler (hourly watch, metrics on :9109)

docker compose run --rm scheduler migrate                             # apply schema migrations
docker compose run --rm scheduler crawl --spider books --max-pages 5  # one-off crawl
docker compose run --rm scheduler status --check-max-age-minutes 120  # healthcheck
docker compose run --rm scheduler retry-alerts                        # flush failed alerts now
```

- **Schema migrations**: Alembic (`migrations/`, revisions under `migrations/versions/`);
  run `migrate` on every deploy, then start the scheduler. `init-db` remains for tests/demos.
- Base images are **digest-pinned** (Playwright runtime + Postgres); Dependabot keeps
  pip, Actions and Docker pins fresh, and CI runs a **Trivy scan** (HIGH/CRITICAL, fixed-only) on the built image.
- The scheduler container has a **Docker healthcheck** running `status --check-max-age-minutes 120`.
- **Metrics** (`:9109/metrics`): `pricetracker_runs_total{spider,status}`, `pricetracker_items_total`,
  `pricetracker_price_drops_total`, `pricetracker_alerts_total{channel,status}`,
  `pricetracker_last_success_timestamp_seconds`.
- **Alerts**: set `PRICETRACKER_WEBHOOK_URL` to a Slack webhook (payload auto-detected) or any
  endpoint accepting JSON `{service, message, event, title, url, old_price, new_price, pct_change, at}`.
  Failed deliveries land in the alerts outbox and are retried automatically by `watch`
  (`retry-alerts` for a manual flush); after `ALERT_MAX_ATTEMPTS` they are marked `dead`.
  Every alert is also appended to `alerts.log` regardless.
- **Retention**: `watch` prunes automatically (default: daily, keep 90 days);
  `prune --keep-days N` for one-off cleanup.
- CI runs ruff, the full pytest suite on Python 3.12/3.13, the Postgres tests against a service
  container, builds + smoke-tests the image, and scans it (`.github/workflows/ci.yml`).

Stop the services with `docker compose down`. Named volumes retain the stored data.
The exports live in the Docker `exports` volume, not your local `exports/` folder.

</details>

## Developer reference

<details>
<summary><strong>Command reference and cron scheduling</strong></summary>

| Command | What it does |
|---|---|
| `crawl [--spider books\|jscatalog\|logincatalog] [--start-url U] [--max-pages N] [--username U --password P] [--db PATH\|URL]` | One crawl (exit 0 only if the run succeeded) |
| `report [--limit N]` | Totals + recent price changes |
| `export [--out DIR]` | CSV + Excel exports |
| `watch [--interval-minutes M] [--runs N] [--metrics-port P] [--lock PATH] [--db PATH\|URL]` | Repeating crawl + export + report; second instance exits 1 |
| `status [--check-max-age-minutes N]` | DB stats + recent runs; with `--check-max-age-minutes` exits 1 when the last successful run is too old |
| `prune [--keep-days N]` | Delete history/changes/alerts older than N days, then VACUUM (SQLite) |
| `migrate` | Apply Alembic schema migrations — use this in production |
| `retry-alerts [--limit N]` | Re-deliver failed alerts whose retry backoff has elapsed |
| `init-db` | Create tables (idempotent) — for tests/demos; production uses `migrate` |
| `demo-shop [--port P] [--delay S] [--sale %] [--user U --password P]` | Local JS-rendered catalog, optionally login-gated |

Scheduling with cron:

```cron
0 * * * * cd /path/to/price-tracker && .venv/bin/python -m pricetracker crawl && .venv/bin/python -m pricetracker export
```

</details>

<details>
<summary><strong>Supported features</strong></summary>

- **Scrapy spider** — handles pagination, retries, throttling, robots.txt (`ROBOTSTXT_OBEY = True`, 0.5s delay)
- **JavaScript catalogs** — `jscatalog` spider renders pages with headless Chromium (`scrapy-playwright`), waits for content, skips images/fonts for speed, then uses the same pipeline
- **Login-gated catalogs** — `logincatalog` spider fills/submits a login form in the browser and carries the session cookie through pagination
- **Price history** — SQLite out of the box, **Postgres for multi-writer/production** (`PRICETRACKER_DATABASE_URL`); starting prices and subsequent price changes kept in history
- **Change detection** — `(old → new, %, DROP/RISE)` recorded between runs; drops trigger alerts
- **Alerts** — Slack incoming webhook, generic JSON webhook (retries + backoff), optional SMTP email, always-on `alerts.log` audit trail, per-product cooldown, zero-items (selector-drift) warning
- **Alert outbox** — failed deliveries are stored with payload + attempt count, retried automatically on later `watch` cycles (or via `retry-alerts`) with exponential backoff, and marked `dead` after the attempt cap
- **Exports** — `catalog.csv`, `price_history.csv`, styled `catalog.xlsx` (Catalog + Price Changes sheets, plus a Price Trends sheet with a line chart for up to 10 products with the most history once changes exist)
- **Scheduling** — `watch` command with **single-instance file lock**, interruptible sleep, optional Prometheus metrics endpoint; or plain cron
- **Run tracking** — every crawl recorded in `runs` (items/changes/drops/status); `status` doubles as a Docker healthcheck
- **Ops-ready** — Docker image (Playwright base), compose with Postgres, `.env` config (pydantic-settings), structured logs (structlog), Prometheus metrics, `prune` for retention, CI (ruff + pytest 3.12/3.13 + Postgres job + image smoke test)
- **Crash-safe batches** — unparseable items are dropped with a warning, never abort the crawl

</details>

<details>
<summary><strong>Code map and database tables</strong></summary>

Module paths below are relative to `pricetracker/`.

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
| `db.py` | SQLAlchemy Core: schema, upsert/history/change logic, runs, alert outbox, prune |
| `migrate.py` + `migrations/` | Alembic setup: versioned schema upgrades (`migrate` command) |
| `notify.py` | Webhook/Slack/email delivery, cooldown, retry outbox, alert audit log |
| `config.py` | pydantic-settings: env/.env config with validation |
| `settings.py` | Scrapy settings derived from config (throttle, proxy, Playwright) |
| `log.py` / `metrics.py` | structlog setup / Prometheus counters |
| `report.py` | CSV/Excel exports, console report |
| `demo_shop.py` | Local JS-rendered catalog (`demo-shop` command) |
| `cli.py` | `crawl` / `report` / `export` / `watch` / `status` / `prune` / `migrate` / `retry-alerts` / `init-db` / `demo-shop` |

```
books(url PK, title, price, rating, in_stock, first_seen, last_seen)
price_history(id, url, price, scraped_at)           -- every price change point
price_changes(id, url, title, old, new, changed_at) -- event log, feeds alerts
alerts(id, url, kind, title, detail, status, channel, payload, attempts,
       next_attempt_at, last_error, sent_at)        -- outbox: sent/failed/suppressed/logged/dead
runs(id, spider, status, started_at, finished_at, items, changes, drops, message)
```

</details>

<details>
<summary><strong>Run tests and lint checks</strong></summary>

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q                    # 95 passed (2 skipped: pg)
PRICETRACKER_TEST_PG_URL=postgresql+psycopg://pt:pw@127.0.0.1:55432/pricetracker \
  .venv/bin/python -m pytest -q -m pg            # Postgres roundtrip + migrations (docker run postgres:17-alpine)
.venv/bin/ruff check pricetracker tests
```

The suite runs all spiders against saved HTML fixtures (one static, one captured
from a real browser render), covers pagination limits, price/rating parsing edge
cases, DB upsert + history semantics (SQLite + Postgres), run tracking, Alembic
migrations, alert delivery (local webhook receiver: retries, Slack shape,
cooldown, outbox retry backoff, dead-letter), config validation incl. legacy env
aliases, the watch lock, prune retention, the demo shop API (incl. the login
gate: redirects without a cookie, 401 on bad credentials, session grants catalog
access), credential resolution for the login spider, and export/report
contents. Two integration tests launch Chromium end-to-end against the demo
shop (plain JS and login-gated); they skip automatically when Chromium is not
installed.

</details>

<details>
<summary><strong>Roadmap</strong></summary>

- Google Sheets / Airtable export targets
- Per-site selector config file (multi-shop tracking)
- CSRF-token / multi-step login flows for stricter sites

</details>
