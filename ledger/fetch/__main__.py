"""CLI entry point for the fetchers.

    python -m ledger.fetch                 # every section
    python -m ledger.fetch prices markets  # just these
    python -m ledger.fetch --list          # what's available
    python -m ledger.fetch --raw donations # dump one raw API result, parse nothing

`--raw` exists because the project's field names must be observed, not assumed.
Before trusting any parsed output from a new or changed endpoint, print what the
API actually returns and read it.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from . import SECTIONS, run_all


def _raw(section: str) -> int:
    """Print one unparsed result from `section`'s upstream endpoint."""
    from .common import api_key, get_json, get_text

    if section == "prices":
        from .prices import CSV_URL, SERIES
        sid = next(iter(SERIES))
        print(get_text(CSV_URL, params={"id": sid})[:1500])
    elif section == "government":
        from .government import BASE
        key, _ = api_key("CONGRESS_API_KEY")
        payload = get_json(BASE, params={"api_key": key, "format": "json", "limit": 1})
        print(json.dumps(payload, indent=2)[:3000])
    elif section == "donations":
        from .donations import BASE, current_cycle
        key, _ = api_key("CONGRESS_API_KEY")
        for path, extra in (
            ("candidates/totals/", {"sort": "-receipts", "election_year": current_cycle()}),
            ("schedules/schedule_a/", {"sort": "-contribution_receipt_amount",
                                       "sort_hide_null": "true",
                                       "two_year_transaction_period": current_cycle()}),
        ):
            print(f"\n===== {path} =====")
            payload = get_json(f"{BASE}/{path}", params={"api_key": key, "per_page": 1, **extra}, timeout=90)
            print(json.dumps((payload.get("results") or [None])[0], indent=2)[:3000])
    elif section == "markets":
        import yfinance as yf
        from .markets import TICKERS
        sym = next(iter(TICKERS))
        print(yf.Ticker(sym).history(period="1mo", interval="1wk").tail())
    else:
        print(f"unknown section {section!r}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="python -m ledger.fetch", description=__doc__)
    p.add_argument("sections", nargs="*", help=f"one or more of: {', '.join(SECTIONS)}")
    p.add_argument("--list", action="store_true", help="list known sections and exit")
    p.add_argument("--raw", action="store_true", help="dump one raw upstream result, parse nothing")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    if args.list:
        for s in SECTIONS:
            print(s)
        return 0

    unknown = [s for s in args.sections if s not in SECTIONS]
    if unknown:
        print(f"unknown section(s): {', '.join(unknown)}", file=sys.stderr)
        return 2

    if args.raw:
        if not args.sections:
            print("--raw needs a section", file=sys.stderr)
            return 2
        return max(_raw(s) for s in args.sections)

    results = run_all(args.sections or None)

    print("\n=== FETCH SUMMARY ===")
    for r in results:
        print(f"  {r['status']:<8} {r['section']:<12} {r['detail']}")

    # Non-zero only on a real error. A skipped section (no API key) is an expected
    # state, not a failure — a scheduled task shouldn't alarm over it.
    return 1 if any(r["status"] == "error" for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
