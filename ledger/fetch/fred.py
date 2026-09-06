"""Shared FRED CSV client.

Both the markets and prices sections are FRED series, so the fetching, parsing,
and per-series error isolation live here once rather than being duplicated with a
different bug in each.

Source : https://fred.stlouisfed.org/graph/fredgraph.csv?id=<series_id>
Key    : none — the CSV export is public and unauthenticated

Verified live: the header is `observation_date,<SERIES_ID>` — NOT `DATE`, which is
what older FRED documentation and most blog posts still show. Missing observations
come through as a literal '.' and are dropped; daily series legitimately have them
for weekends and holidays.
"""
from __future__ import annotations

import csv
import io
import logging

from ..db import prune_series, upsert_series_points
from .common import get_text, to_float

log = logging.getLogger("ledger.fetch.fred")

CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"

# Default window. Sections override it: the start date is not cosmetic, because
# every chart indexes to 100 at the first observation, so the window choice sets
# the baseline every series is measured against.
START_DATE = "2019-01-01"


def fetch_series(series_id: str, label: str, unit: str, order: int, start: str = START_DATE) -> list[dict]:
    text = get_text(CSV_URL, params={"id": series_id, "cosd": start})
    reader = csv.reader(io.StringIO(text))

    header = next(reader, None)
    if not header or len(header) < 2:
        raise ValueError(f"unexpected CSV header for {series_id}: {header!r}")

    points = []
    for row in reader:
        if len(row) < 2:
            continue
        value = to_float(row[1])
        if value is None:  # '.' — no observation for that date
            continue
        points.append(
            {
                "series_key": series_id,
                "series_label": label,
                "point_date": row[0],
                "value": value,
                "unit": unit,
                # Position in the section's declared dict — pins this series to
                # one chart color across fetches.
                "sort_order": order,
            }
        )
    return points


def run_section(section: str, series: dict[str, tuple[str, str]], start: str = START_DATE) -> str:
    """Fetch every series in `series` into `section`.

    `series` maps FRED series id -> (display label, unit), and its order defines
    chart colour order. One bad series must not kill the others.
    """
    total, ok, failed = 0, [], []

    for order, (series_id, (label, unit)) in enumerate(series.items()):
        try:
            points = fetch_series(series_id, label, unit, order, start)
            total += upsert_series_points(section, points)
            ok.append(series_id)
        except Exception as e:  # noqa: BLE001 — isolate per series
            log.warning("%s: %s failed: %s", section, series_id, e)
            failed.append(f"{series_id}: {e}")

    # Drop anything this section no longer wants: series removed from the dict
    # above, and points that fall outside the current window. Fetchers upsert, so
    # without this both linger and keep being charted.
    if ok and not failed:
        removed = prune_series(section, keep_keys=list(series), before_date=start)
        if removed:
            log.info("%s: pruned %s rows from series no longer tracked", section, removed)

    if not ok:
        raise RuntimeError("every series failed: " + "; ".join(failed))

    detail = f"{total} points from {len(ok)}/{len(series)} series"
    if failed:
        detail += f" — failed: {'; '.join(failed)}"
    return detail
