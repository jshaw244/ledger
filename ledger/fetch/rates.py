"""Rates, from FRED.

Source : FRED, Federal Reserve Bank of St. Louis (see fred.py)
Key    : none
Usage  : python -m ledger.fetch rates
Writes : series_points rows under section 'rates'

Split out of `prices` because these are percentages, and percentages should not be
indexed to 100. Unemployment went 3.6% -> 14.8% in April 2020, which indexes to
~410 and flattened every price series beside it; the 10-year Treasury from a 0.93%
base indexes to 535. Both numbers describe the baseline rather than the thing
being measured.

Every series here is already in the same unit, so they share an axis natively and
are charted at their **actual values** — no indexing at all. "Mortgage is at 6.2%"
is directly readable off the chart, which is the whole point of a rate.
"""
from __future__ import annotations

from .fred import run_section

# FRED series id -> (display label, unit). Dict order sets chart colour order.
SERIES = {
    "MORTGAGE30US": ("30-year fixed mortgage", "%"),
    "DGS10":        ("10-year Treasury yield", "%"),
    "FEDFUNDS":     ("Federal funds rate",     "%"),
    "UNRATE":       ("Unemployment rate",      "%"),
}

START_DATE = "2019-01-01"


def run() -> str:
    return run_section("rates", SERIES, start=START_DATE)
