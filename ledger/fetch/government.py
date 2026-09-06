"""House roll-call votes, from api.congress.gov.

Source : https://api.congress.gov/v3/house-vote
Key    : CONGRESS_API_KEY (an api.data.gov key; falls back to DEMO_KEY)
Usage  : python -m ledger.fetch government
Writes : records rows under section 'government'

Two things verified live that the docs don't make obvious:

1. The list endpoint is ordered by `updateDate` descending, NOT by when the vote
   was held. A 2023 vote that got bulk-revised last month sorts above a vote from
   last week. So we pull a wide page and re-sort by `startDate` locally.

2. The list carries no vote tallies at all — only the detail endpoint
   (/house-vote/{congress}/{session}/{rollCall}) returns `votePartyTotal`. That
   costs one extra request per vote, which is why tally enrichment is capped, and
   capped much harder on DEMO_KEY (~30 requests/hour).
"""
from __future__ import annotations

import logging
from datetime import datetime

from ..db import upsert_records
from .common import api_key, get_json

log = logging.getLogger("ledger.fetch.government")

BASE = "https://api.congress.gov/v3/house-vote"

LIST_LIMIT = 250      # wide page, because list order != chronological order
KEEP_VOTES = 20       # how many recent votes to display

# Tally lookups cost one request each. A real api.data.gov key allows 1000/hour,
# so enriching every displayed vote costs ~21 requests on a once-daily fetch —
# nothing. DEMO_KEY's ceiling is 40/hour shared with the donations section, hence
# the much lower cap there.
ENRICH_REAL = KEEP_VOTES
ENRICH_DEMO = 3


def _start_epoch(vote: dict) -> float:
    raw = (vote.get("startDate") or "").strip()
    if not raw:
        return 0.0
    try:
        return datetime.fromisoformat(raw).timestamp()
    except ValueError:
        return 0.0


def _tallies(vote: dict, key: str) -> dict:
    """Fetch per-party totals for one vote and flatten them to a summary."""
    url = f"{BASE}/{vote['congress']}/{vote['sessionNumber']}/{vote['rollCallNumber']}"
    detail = get_json(url, params={"api_key": key, "format": "json"})
    body = detail.get("houseRollCallVote") or {}

    yea = nay = present = not_voting = 0
    by_party = {}
    for pt in body.get("votePartyTotal") or []:
        p = (pt.get("party") or {}).get("type") or pt.get("voteParty") or "?"
        y, n = int(pt.get("yeaTotal") or 0), int(pt.get("nayTotal") or 0)
        yea += y
        nay += n
        present += int(pt.get("presentTotal") or 0)
        not_voting += int(pt.get("notVotingTotal") or 0)
        by_party[p] = f"{y}-{n}"

    return {
        "yea": yea,
        "nay": nay,
        "present": present,
        "not_voting": not_voting,
        "by_party": by_party,
        "question": body.get("voteQuestion"),
    }


def run() -> str:
    key, is_demo = api_key("CONGRESS_API_KEY")

    payload = get_json(BASE, params={"api_key": key, "format": "json", "limit": LIST_LIMIT})
    votes = payload.get("houseRollCallVotes") or []
    if not votes:
        raise RuntimeError("no houseRollCallVotes in response")

    votes.sort(key=_start_epoch, reverse=True)
    votes = votes[:KEEP_VOTES]

    budget = ENRICH_DEMO if is_demo else ENRICH_REAL
    enriched = 0
    items = []

    for v in votes:
        legislation = f"{v.get('legislationType') or ''} {v.get('legislationNumber') or ''}".strip()
        item = {
            "record_key": str(v.get("identifier") or f"{v.get('congress')}-{v.get('rollCallNumber')}"),
            "sort_value": _start_epoch(v),
            # Prefer the bill page; fall back to the Clerk's raw XML, which is the
            # actual primary record.
            "source_url": v.get("legislationUrl") or v.get("sourceDataURL"),
            "congress": v.get("congress"),
            "roll_call": v.get("rollCallNumber"),
            "legislation": legislation or "—",
            "vote_type": v.get("voteType"),
            "result": v.get("result"),
            "date": (v.get("startDate") or "")[:10],
            "clerk_url": v.get("sourceDataURL"),
        }

        if enriched < budget:
            try:
                item.update(_tallies(v, key))
                enriched += 1
            except Exception as e:  # noqa: BLE001 — a missing tally is not a failed vote
                log.warning("government: tallies failed for roll %s: %s", item["roll_call"], e)

        items.append(item)

    n = upsert_records("government", items)
    detail = f"{n} votes ({enriched} with tallies)"
    if is_demo:
        detail += " — DEMO_KEY: tallies capped, set CONGRESS_API_KEY for the rest"
    return detail
