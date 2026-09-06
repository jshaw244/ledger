"""Political money, from OpenFEC.

Source : https://api.open.fec.gov/v1
Key    : CONGRESS_API_KEY — OpenFEC is an api.data.gov sister API, so the same
         key works here (verified). Falls back to DEMO_KEY.
Usage  : python -m ledger.fetch donations
Writes : records rows under sections 'donations_top' and 'donations_contributions'

THREE corrections against the original spec, all found by probing the live API
rather than trusting the docs:

1. `/candidates/` CANNOT sort by total receipts — it returns HTTP 422 listing its
   allowed sort fields. Candidate money lives on `/candidates/totals/` instead,
   which sorts by `receipts`.

2. The field is `receipts`, not `total_receipts`; `disbursements`, not
   `total_disbursements`; and `cash_on_hand_end_period`, not
   `last_cash_on_hand_end_period`.

3. `/schedules/schedule_a/` sorted by `-contribution_receipt_amount` returns rows
   with NULL amounts and NULL dates FIRST unless `sort_hide_null=true` is passed.
   Without it the "largest contributions" table is topped by blank rows. This is
   the single most important parameter in this file.

Money fields come back inconsistently typed — `receipts` as a float,
`cash_on_hand_end_period` as the string "42587451.00" — so everything goes
through to_float().
"""
from __future__ import annotations

import logging
from datetime import date

from ..db import upsert_records
from .common import api_key, get_json, to_float

log = logging.getLogger("ledger.fetch.donations")

BASE = "https://api.open.fec.gov/v1"

TOP_CANDIDATES = 20
TOP_CONTRIBUTIONS = 25


def current_cycle(today: date | None = None) -> int:
    """FEC two-year cycles are labelled by their even end-year."""
    y = (today or date.today()).year
    return y if y % 2 == 0 else y + 1


def _top_fundraisers(key: str, cycle: int) -> int:
    payload = get_json(
        f"{BASE}/candidates/totals/",
        params={
            "api_key": key,
            "sort": "-receipts",          # NOT total_receipts — see module docstring
            "election_year": cycle,
            "per_page": TOP_CANDIDATES,
            "page": 1,
        },
    )
    results = payload.get("results") or []
    if not results:
        raise RuntimeError(f"no candidate totals for cycle {cycle}")

    items = []
    for c in results:
        cid = c.get("candidate_id")
        items.append(
            {
                "record_key": f"{cycle}:{cid}",
                "sort_value": to_float(c.get("receipts")) or 0.0,
                # Every row points at the FEC's own page for that candidate.
                "source_url": f"https://www.fec.gov/data/candidate/{cid}/" if cid else None,
                "candidate_id": cid,
                "name": c.get("name"),
                "party": c.get("party_full") or c.get("party"),
                "office": c.get("office_full") or c.get("office"),
                "state": c.get("state"),
                "incumbent": c.get("incumbent_challenge_full"),
                "receipts": to_float(c.get("receipts")),
                "disbursements": to_float(c.get("disbursements")),
                "cash_on_hand": to_float(c.get("cash_on_hand_end_period")),
                "coverage_end": c.get("coverage_end_date"),
                "cycle": cycle,
            }
        )
    return upsert_records("donations_top", items)


def _largest_contributions(key: str, cycle: int) -> int:
    payload = get_json(
        f"{BASE}/schedules/schedule_a/",
        params={
            "api_key": key,
            "sort": "-contribution_receipt_amount",
            "sort_hide_null": "true",   # REQUIRED — see module docstring
            "two_year_transaction_period": cycle,
            "per_page": TOP_CONTRIBUTIONS,
        },
        timeout=90,   # this endpoint indexes ~170M rows and is genuinely slow
    )
    results = payload.get("results") or []
    if not results:
        raise RuntimeError(f"no schedule_a rows for cycle {cycle}")

    items = []
    for c in results:
        committee = c.get("committee") or {}
        items.append(
            {
                # sub_id is OpenFEC's stable unique id for a filed transaction.
                "record_key": str(c.get("sub_id") or c.get("transaction_id")),
                "sort_value": to_float(c.get("contribution_receipt_amount")) or 0.0,
                # The scanned FEC filing itself — the primary document.
                "source_url": c.get("pdf_url"),
                "date": c.get("contribution_receipt_date"),
                "contributor": c.get("contributor_name"),
                "employer": c.get("contributor_employer"),
                "occupation": c.get("contributor_occupation"),
                "city": c.get("contributor_city"),
                "state": c.get("contributor_state"),
                "amount": to_float(c.get("contribution_receipt_amount")),
                "committee": committee.get("name") or c.get("committee_name") or c.get("committee_id"),
                "committee_type": committee.get("committee_type_full"),
                "cycle": cycle,
            }
        )
    return upsert_records("donations_contributions", items)


def run() -> str:
    key, is_demo = api_key("CONGRESS_API_KEY")
    cycle = current_cycle()

    counts, failed = {}, []
    for name, fn in (("top fundraisers", _top_fundraisers), ("contributions", _largest_contributions)):
        try:
            counts[name] = fn(key, cycle)
        except Exception as e:  # noqa: BLE001 — one view failing shouldn't lose the other
            log.warning("donations: %s failed: %s", name, e)
            failed.append(f"{name}: {e}")

    if not counts:
        raise RuntimeError("both views failed: " + "; ".join(failed))

    detail = f"cycle {cycle}: " + ", ".join(f"{v} {k}" for k, v in counts.items())
    if failed:
        detail += f" — failed: {'; '.join(failed)}"
    if is_demo:
        detail += " — using DEMO_KEY"
    return detail
