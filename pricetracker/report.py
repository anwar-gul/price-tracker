from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.chart import LineChart, Reference
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from . import db

HEADER_FILL = PatternFill("solid", fgColor="0F766E")
HEADER_FONT = Font(color="FFFFFF", bold=True)
DROP_FONT = Font(color="B91C1C", bold=True)
CURRENCY = "#,##0.00"

TREND_MAX_PRODUCTS = 10

CATALOG_HEADERS = ["Title", "Price", "Rating", "In Stock", "URL", "First Seen", "Last Seen"]
CHANGE_HEADERS = ["Changed At", "Title", "Old Price", "New Price", "Change", "Change %"]
HISTORY_HEADERS = ["URL", "Title", "Price", "Scraped At"]


def _write_csv(path: Path, headers: list[str], rows: list[list]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(headers)
        writer.writerows(rows)


def _style_header(ws, headers: list[str], widths: list[int]) -> None:
    for col, (header, width) in enumerate(zip(headers, widths, strict=True), start=1):
        cell = ws.cell(row=1, column=col)
        cell.value = header
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        ws.column_dimensions[get_column_letter(col)].width = width
    ws.freeze_panes = "A2"


def _trend_series(history) -> dict[str, list[tuple[datetime, float]]]:
    """Products with at least two price points, ordered by history volume."""
    series: dict[str, list[tuple[datetime, float]]] = {}
    for row in history:
        title = row["title"] or row["url"]
        when = datetime.fromisoformat(row["scraped_at"])
        if when.tzinfo is not None:  # Excel cells must be naive (UTC)
            when = when.astimezone(UTC).replace(tzinfo=None)
        series.setdefault(title, []).append((when, row["price"]))
    trends = {title: points for title, points in series.items() if len(points) >= 2}
    if len(trends) > TREND_MAX_PRODUCTS:
        ranked = sorted(trends.items(), key=lambda item: (-len(item[1]), item[0]))
        trends = dict(ranked[:TREND_MAX_PRODUCTS])
    return trends


def _add_trend_sheet(wb: Workbook, history) -> bool:
    """Add a 'Price Trends' sheet + line chart; no-op without >=2 points."""
    trends = _trend_series(history)
    if not trends:
        return False

    timestamps = sorted({when for points in trends.values() for when, _ in points})
    prices_at = {title: dict(points) for title, points in trends.items()}
    titles = sorted(trends)

    ws = wb.create_sheet("Price Trends")
    _style_header(ws, ["Date", *titles], [20, *([16] * len(titles))])
    for row_idx, when in enumerate(timestamps, start=2):
        cell = ws.cell(row=row_idx, column=1, value=when)
        cell.number_format = "yyyy-mm-dd hh:mm"
        for col_idx, title in enumerate(titles, start=2):
            price = prices_at[title].get(when)
            if price is not None:
                ws.cell(row=row_idx, column=col_idx, value=price).number_format = CURRENCY

    chart = LineChart()
    chart.title = "Price trends"
    chart.y_axis.title = "Price"
    chart.x_axis.title = "Date"
    chart.height = 10
    chart.width = 24
    chart.display_blanks = "span"  # connect across runs where a product had no change
    chart.add_data(
        Reference(ws, min_col=2, max_col=1 + len(titles), min_row=1,
                  max_row=len(timestamps) + 1),
        titles_from_data=True,
    )
    chart.set_categories(
        Reference(ws, min_col=1, min_row=2, max_row=len(timestamps) + 1)
    )
    ws.add_chart(chart, f"A{len(timestamps) + 3}")
    return True


def export_all(conn, out_dir: str | Path) -> dict[str, str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    catalog = db.fetch_catalog(conn)
    changes = db.fetch_changes(conn)
    history = db.fetch_history(conn)

    catalog_rows = [
        [
            row["title"], row["price"], row["rating"], bool(row["in_stock"]),
            row["url"], row["first_seen"], row["last_seen"],
        ]
        for row in catalog
    ]
    change_rows = [
        [
            row["changed_at"], row["title"], row["old_price"], row["new_price"],
            round(row["new_price"] - row["old_price"], 2),
            round((row["new_price"] - row["old_price"]) / row["old_price"] * 100, 1)
            if row["old_price"] else 0.0,
        ]
        for row in changes
    ]
    history_rows = [
        [row["url"], row["title"] or "", row["price"], row["scraped_at"]]
        for row in history
    ]

    catalog_csv = out / "catalog.csv"
    history_csv = out / "price_history.csv"
    _write_csv(catalog_csv, CATALOG_HEADERS, catalog_rows)
    _write_csv(history_csv, HISTORY_HEADERS, history_rows)

    wb = Workbook()
    ws = wb.active
    ws.title = "Catalog"
    _style_header(ws, CATALOG_HEADERS, [44, 10, 8, 10, 44, 22, 22])
    for row in catalog_rows:
        ws.append(row)
    for row_idx in range(2, len(catalog_rows) + 2):
        ws.cell(row=row_idx, column=2).number_format = CURRENCY

    ws = wb.create_sheet("Price Changes")
    _style_header(ws, CHANGE_HEADERS, [22, 44, 12, 12, 10, 10])
    for row in change_rows:
        ws.append(row)
    for row_idx, row in enumerate(change_rows, start=2):
        for col_idx in (3, 4, 5):
            ws.cell(row=row_idx, column=col_idx).number_format = CURRENCY
        ws.cell(row=row_idx, column=6).number_format = "0.0"
        if row[4] < 0:
            ws.cell(row=row_idx, column=5).font = DROP_FONT

    _add_trend_sheet(wb, history)

    workbook_path = out / "catalog.xlsx"
    wb.save(workbook_path)

    return {
        "catalog_csv": str(catalog_csv),
        "history_csv": str(history_csv),
        "workbook": str(workbook_path),
    }


def print_report(conn, limit: int = 20) -> None:
    counts = db.stats(conn)
    catalog = db.fetch_catalog(conn)
    prices = [row["price"] for row in catalog]

    print("Price tracker report")
    print("=" * 62)
    print(f"Books tracked:      {counts['books']}")
    print(f"Price points:       {counts['history']}")
    print(f"Price changes seen: {counts['changes']}")
    if prices:
        print(f"Catalog value:      {sum(prices):,.2f}")
        print(f"Average price:      {sum(prices) / len(prices):,.2f}")
    print()

    changes = db.fetch_changes(conn, limit=limit)
    if not changes:
        print("No price changes recorded yet.")
        return

    print(f"Latest {len(changes)} change(s):")
    print(f"{'When':<21} {'Title':<36} {'Old':>8} {'New':>8} {'Chg%':>7}")
    print("-" * 84)
    for row in changes:
        title = row["title"][:36]
        pct = (row["new_price"] - row["old_price"]) / row["old_price"] * 100
        print(
            f"{row['changed_at']:<21} {title:<36} "
            f"{row['old_price']:>8.2f} {row['new_price']:>8.2f} {pct:>6.1f}%"
        )
