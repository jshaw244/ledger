"""Prices & inflation, from FRED.

Source : FRED, Federal Reserve Bank of St. Louis (see fred.py)
Key    : none
Usage  : python -m ledger.fetch prices
Writes : series_points rows under section 'prices'

Every entry is a real published series. Nothing here is computed, blended, or
derived — each line is one series exactly as released.

Price levels only. Rates (mortgage, Treasury, fed funds, unemployment) moved to
`rates`, where they are charted at their actual percentages instead of being
indexed — see rates.py for why indexing a percentage misleads.
"""
from __future__ import annotations

from .fred import run_section

# FRED series id -> (display label, unit). Dict order sets chart colour order.
SERIES = {
    "CPIAUCSL":      ("Consumer Price Index (all items)",       "index 1982-84=100"),
    "CPILFESL":      ("Core CPI (less food & energy)",          "index 1982-84=100"),
    "PPIACO":        ("Producer Price Index (all commodities)", "index 1982=100"),
    "GASREGW":       ("Gasoline, regular grade",                "$/gal"),
    "APU0000708111": ("Eggs, grade A large",                    "$/doz"),
}


def run() -> str:
    return run_section("prices", SERIES)
