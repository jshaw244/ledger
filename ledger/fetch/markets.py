"""Markets & commodities, from FRED.

Source : FRED, Federal Reserve Bank of St. Louis (see fred.py)
Key    : none
Usage  : python -m ledger.fetch markets
Writes : series_points rows under section 'markets'

Previously sourced from Yahoo Finance via yfinance. That was the one scraped,
commercial source in a module whose whole premise is that every number traces to a
public filing or feed — and it earned its removal on practical grounds too:
yfinance requires curl_cffi, whose native `_wrapper.pyd` is unsigned and is
blocked outright by Windows Smart App Control, so every fetch raised a system
warning. It also dragged in pandas, lxml, peewee, protobuf, websockets, and
beautifulsoup4 for what is ultimately a column of closing prices.

FRED publishes all of it, unauthenticated, as the same CSV the prices section
already reads. The one casualty is gold: FRED's gold benchmark series was
discontinued and now 404s, and LBMA has no documented free API, so that slot stays
empty until a clean public source turns up.
"""
from __future__ import annotations

from .fred import run_section

# FRED series id -> (display label, unit). Dict order sets chart colour order.
#
# Market levels and prices only. The 10-year Treasury yield used to sit here and
# now lives in `prices` with the other rates: indexing a percentage to 100 turns
# 0.93% -> 5% into an index of 535 and swamps everything beside it, which is a
# statement about the baseline rather than about the market.
SERIES = {
    "SP500":      ("S&P 500",                 "index"),
    "NASDAQCOM":  ("Nasdaq Composite",        "index"),
    "DJIA":       ("Dow Jones Industrial",    "index"),
    "DCOILWTICO": ("Crude oil (WTI)",         "$/bbl"),
    # Monthly average, not the daily spot series (DHHNGSP). Henry Hub daily spot
    # spikes to an index of ~1180 during the February 2021 Texas freeze, which
    # compresses every other series into the floor of the chart. The monthly
    # average peaks near 325 and still shows the 2022 run-up plainly. Both are
    # published FRED series — this is a choice of series, not a smoothing we apply.
    "MHHNGSP":    ("Natural gas (Henry Hub, monthly avg)", "$/MMBtu"),
}

# Deliberately not 2019. WTI settled at -$37/bbl on 2020-04-20, and indexing to
# 100 at a positive base makes a negative price a negative index — the chart dips
# below zero and the ratio stops meaning anything across the sign change.
START_DATE = "2021-01-01"


def run() -> str:
    return run_section("markets", SERIES, start=START_DATE)
