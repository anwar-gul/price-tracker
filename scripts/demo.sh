#!/usr/bin/env bash
# One-command portfolio demo (~60 seconds, no accounts needed):
#   1. multi-shop tracking from a sites file (JS shop + custom selectors)
#   2. CSRF-protected, two-step login portal
#   3. Airtable push against a local mock API
#
# Usage:  scripts/demo.sh
# Real targets: set the PRICETRACKER_AIRTABLE_* / PRICETRACKER_GSHEETS_*
# variables and run `pricetracker push` (see README "Remote tables").
set -euo pipefail
cd "$(dirname "$0")/.."

PY=${PY:-.venv/bin/python}
WORK=$(mktemp -d)
DB="$WORK/demo.db"
SITES="$WORK/sites.yaml"
REQUESTS="$WORK/airtable-requests.jsonl"
PIDS=()

cleanup() {
  kill "${PIDS[@]}" >/dev/null 2>&1 || true
  wait "${PIDS[@]}" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

banner() { printf '\n\033[1m=== %s ===\033[0m\n' "$*"; }

banner "starting demo shops (plain :8765, CSRF+two-step login :8766)"
"$PY" -m pricetracker demo-shop --port 8765 --delay 0 >"$WORK/shop1.log" 2>&1 &
PIDS+=($!)
"$PY" -m pricetracker demo-shop --port 8766 --delay 0 --user demo --password demo --csrf --two-step >"$WORK/shop2.log" 2>&1 &
PIDS+=($!)
sleep 1

cat > "$SITES" <<EOF
sites:
  - name: demo-js
    spider: jscatalog
    start_url: http://127.0.0.1:8765/
    max_pages: 2
  - name: demo-custom
    spider: generic
    start_url: http://127.0.0.1:8765/
    render: true
    max_pages: 2
    selectors:
      product: "article.product_pod"
      title: "h3 a::attr(title)"
      link: "h3 a::attr(href)"
      price: "p.price_color::text"
      rating: "p.star-rating::attr(class)"
      stock: "p.instock.availability ::text"
  - name: portal
    spider: logincatalog
    start_url: http://127.0.0.1:8766/login
    login:
      username_env: PORTAL_USER
      password_env: PORTAL_PASS
      next_selector: "button[name=next]"
      submit_selector: "button[name=signin]"
      csrf_selector: "meta[name=csrf-token]"
      error_selector: ".error"
EOF

banner "1/3 multi-shop from one file: crawl --site NAME"
for SITE in demo-js demo-custom; do
  "$PY" -m pricetracker crawl --site "$SITE" --sites-file "$SITES" --db "$DB" -L WARNING
done
echo "-> 12 products from the JS catalog + the same 12 via custom selectors"

banner "2/3 CSRF + two-step login portal (meta token, username then password)"
PORTAL_USER=demo PORTAL_PASS=demo \
  "$PY" -m pricetracker crawl --site portal --sites-file "$SITES" --db "$DB" -L WARNING
echo "-> logged in with single-use CSRF tokens across two form steps"

banner "3/3 push to Airtable (local mock API)"
"$PY" scripts/fake_airtable.py "$REQUESTS" >"$WORK/airtable.port" 2>&1 &
PIDS+=($!)
sleep 1
PORT=$(cat "$WORK/airtable.port")
PRICETRACKER_AIRTABLE_TOKEN=pat-demo \
PRICETRACKER_AIRTABLE_BASE=appDEMO \
PRICETRACKER_AIRTABLE_API_BASE="http://127.0.0.1:$PORT" \
  "$PY" -m pricetracker push --target airtable --db "$DB"
echo "-> upsert batches: $(tr '\n' ' ' < "$REQUESTS")"

banner "runs recorded per site name"
"$PY" -m pricetracker status --db "$DB" | sed -n '/Recent runs/,$p'

banner "done - data was thrown away; see README for real targets"
