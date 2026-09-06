"""Markets & commodities, via yfinance.

Source : Yahoo Finance, through the yfinance package
Key    : none
Usage  : python -m ledger.fetch markets
Writes : series_points rows under section 'markets'

Caveat worth knowing: unlike every other source in this module, this one is a
scrape of a commercial site rather than a government feed, so it is the most
likely to break when Yahoo changes something. It is isolated per-ticker for that
reason, and a total failure here degrades one chart rather than the fetch run.

Weekly bars, not daily: this is a five-year trend panel, and daily resolution
would be ~5x the rows for a line that looks identical at this zoom level.
"""
from __future__ import annotations

import logging

from ..db import upsert_series_points

log = logging.getLogger("ledger.fetch.markets")

# Symbol -> (display label, unit). Indices, commodities, and rates — the
# headline numbers, not a personal portfolio.
TICKERS = {
    "^GSPC":  ("S&P 500",              "index"),
    "^IXIC":  ("Nasdaq Composite",     "index"),
    "^DJI":   ("Dow Jones Industrial", "index"),
    "CL=F":   ("Crude oil (WTI)",      "$/bbl"),
    "GC=F":   ("Gold",                 "$/oz"),
    "NG=F":   ("Natural gas",          "$/MMBtu"),
    "^TNX":   ("10-year Treasury yield", "%"),
}

PERIOD = "5y"
INTERVAL = "1wk"


def _fetch_ticker(symbol: str, label: str, unit: str, order: int) -> list[dict]:
    import yfinance as yf  # imported lazily so a broken yfinance can't stop other sections

    hist = yf.Ticker(symbol).history(period=PERIOD, interval=INTERVAL, auto_adjust=True)
    if hist is None or hist.empty:
        raise ValueError("empty history returned")
    if "Close" not in hist.columns:
        raise ValueError(f"no Close column; got {list(hist.columns)}")

    points = []
    for ts, close in hist["Close"].items():
        if close is None or close != close:  # NaN
            continue
        points.append(
            {
                "series_key": symbol,
                "series_label": label,
                "point_date": ts.date().isoformat(),
                "value": float(close),
                "unit": unit,
                # Position in TICKERS — pins this series to one chart color.
                "sort_order": order,
            }
        )
    return points


def run() -> str:
    total, ok, failed = 0, [], []

    for order, (symbol, (label, unit)) in enumerate(TICKERS.items()):
        try:
            points = _fetch_ticker(symbol, label, unit, order)
            total += upsert_series_points("markets", points)
            ok.append(f"{symbol}({len(points)})")
        except Exception as e:  # noqa: BLE001 — isolate per ticker
            log.warning("markets: %s failed: %s", symbol, e)
            failed.append(f"{symbol}: {e}")

    if not ok:
        raise RuntimeError("every ticker failed: " + "; ".join(failed))

    detail = f"{total} points from {len(ok)}/{len(TICKERS)} tickers"
    if failed:
        detail += f" — failed: {'; '.join(failed)}"
    return detail
