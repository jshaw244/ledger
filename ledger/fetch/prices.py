"""Prices & inflation, from FRED's CSV export.

Source : https://fred.stlouisfed.org/graph/fredgraph.csv?id=<series_id>
Key    : none — the CSV export is public and unauthenticated
Usage  : python -m ledger.fetch prices
Writes : series_points rows under section 'prices'

Verified live: the header is `observation_date,<SERIES_ID>` — note NOT `DATE`,
which is what older FRED documentation and most blog posts still show. Missing
observations come through as a literal '.' and are dropped.
"""
from __future__ import annotations

import csv
import io
import logging

from ..db import upsert_series_points
from .common import get_text, to_float

log = logging.getLogger("ledger.fetch.prices")

CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"

# FRED series id -> (display label, unit). Everything here is a real published
# series; nothing is computed or blended.
SERIES = {
    "CPIAUCSL":      ("Consumer Price Index (all items)", "index 1982-84=100"),
    "CPILFESL":      ("Core CPI (less food & energy)",    "index 1982-84=100"),
    "PPIACO":        ("Producer Price Index (all commodities)", "index 1982=100"),
    "GASREGW":       ("Gasoline, regular grade",          "$/gal"),
    "APU0000708111": ("Eggs, grade A large",              "$/doz"),
    "MORTGAGE30US":  ("30-year fixed mortgage",           "%"),
    "UNRATE":        ("Unemployment rate",                "%"),
}

# Enough history for a multi-year trend without turning the chart into a smear.
START_DATE = "2019-01-01"


def _fetch_series(series_id: str, label: str, unit: str, order: int) -> list[dict]:
    text = get_text(CSV_URL, params={"id": series_id, "cosd": START_DATE})
    reader = csv.reader(io.StringIO(text))

    header = next(reader, None)
    if not header or len(header) < 2:
        raise ValueError(f"unexpected CSV header for {series_id}: {header!r}")

    points = []
    for row in reader:
        if len(row) < 2:
            continue
        value = to_float(row[1])
        if value is None:  # FRED writes '.' for a missing observation
            continue
        points.append(
            {
                "series_key": series_id,
                "series_label": label,
                "point_date": row[0],
                "value": value,
                "unit": unit,
                # Position in SERIES — pins this series to one chart color.
                "sort_order": order,
            }
        )
    return points


def run() -> str:
    """Fetch every series. One bad series must not kill the others."""
    total, ok, failed = 0, [], []

    for order, (series_id, (label, unit)) in enumerate(SERIES.items()):
        try:
            points = _fetch_series(series_id, label, unit, order)
            total += upsert_series_points("prices", points)
            ok.append(f"{series_id}({len(points)})")
        except Exception as e:  # noqa: BLE001 — isolate per series, per the brief
            log.warning("prices: %s failed: %s", series_id, e)
            failed.append(f"{series_id}: {e}")

    if not ok:
        raise RuntimeError("every series failed: " + "; ".join(failed))

    detail = f"{total} points from {len(ok)}/{len(SERIES)} series"
    if failed:
        detail += f" — failed: {'; '.join(failed)}"
    return detail
