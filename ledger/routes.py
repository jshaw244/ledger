"""Ledger blueprint — the page, and the on-demand refresh endpoints.

The page is rendered server-side (tables in Jinja, chart data embedded as JSON)
rather than fetched over XHR. Two reasons: there's no build step and no client
state to keep in sync, and it means the page cannot show a chart while silently
failing to show why a section is stale — freshness comes from the same render.
"""
from __future__ import annotations

import logging
import threading

from flask import Blueprint, jsonify, render_template

from .db import get_last_runs, get_records, get_series, init_db

log = logging.getLogger("ledger.routes")

ledger_bp = Blueprint(
    "ledger",
    __name__,
    template_folder="templates",
    static_folder="static",
)

# The page, declaratively. `section` is the key data is stored under; `fetch` is
# the fetcher responsible for it (donations feeds two panels from one run).
PANELS = [
    {
        "id": "markets",
        "kind": "series",
        "section": "markets",
        "fetch": "markets",
        "label": "Markets",
        "title": "Markets & commodities",
        "subtitle": "Weekly closes over five years, indexed to 100 at the start of the window so series on different scales stay comparable. Hover for the actual level.",
        "source": "Yahoo Finance (via yfinance)",
    },
    {
        "id": "prices",
        "kind": "series",
        "section": "prices",
        "fetch": "prices",
        "label": "Prices",
        "title": "Prices & inflation",
        "subtitle": "Published FRED series, indexed to 100 at the start of the window. No blending, no derived index — each line is one series as released.",
        "source": "FRED, Federal Reserve Bank of St. Louis",
    },
    {
        "id": "government",
        "kind": "table",
        "section": "government",
        "fetch": "government",
        "label": "Government",
        "title": "Recent House roll-call votes",
        "subtitle": "The most recent House floor votes, newest first. “Roll” is the roll-call number; “source” opens the measure on congress.gov.",
        "source": "api.congress.gov",
        "columns": [
            {"key": "date", "label": "Date"},
            {"key": "legislation", "label": "Measure"},
            {"key": "question", "label": "Question"},
            {"key": "result", "label": "Result", "fmt": "badge"},
            {"key": "yea", "label": "Yea", "fmt": "num"},
            {"key": "nay", "label": "Nay", "fmt": "num"},
            {"key": "roll_call", "label": "Roll", "fmt": "num"},
        ],
    },
    {
        "id": "donations_top",
        "kind": "bar",
        "section": "donations_top",
        "fetch": "donations",
        "label": "Donations",
        "title": "Top fundraisers this cycle",
        "subtitle": "Whoever has raised the most — no curated candidate list. Totals are as of each committee's most recent coverage date, so they are not all measured to the same day.",
        "source": "OpenFEC /candidates/totals/",
        "bar": {"label_key": "name", "value_key": "receipts", "meta_keys": ["party", "office", "state"]},
        "columns": [
            {"key": "name", "label": "Candidate"},
            {"key": "party", "label": "Party"},
            {"key": "office", "label": "Office"},
            {"key": "state", "label": "State"},
            {"key": "receipts", "label": "Raised", "fmt": "money"},
            {"key": "disbursements", "label": "Spent", "fmt": "money"},
            {"key": "cash_on_hand", "label": "Cash on hand", "fmt": "money"},
            {"key": "coverage_end", "label": "As of"},
        ],
    },
    {
        "id": "donations_contributions",
        "kind": "table",
        "section": "donations_contributions",
        "fetch": "donations",
        "label": "Donations",
        "title": "Largest itemized contributions",
        "subtitle": "Single itemized receipts, largest first. Many of the biggest are organizations giving to super PACs rather than individuals.",
        "source": "OpenFEC /schedules/schedule_a/",
        "columns": [
            {"key": "date", "label": "Date"},
            {"key": "contributor", "label": "Contributor"},
            {"key": "employer", "label": "Employer"},
            {"key": "committee", "label": "Recipient committee"},
            {"key": "amount", "label": "Amount", "fmt": "money"},
        ],
    },
]

# ---------------------------------------------------------------------------
#  On-demand refresh (the scheduled task is the normal path; this is the button)
# ---------------------------------------------------------------------------

_fetch_lock = threading.Lock()
_fetch_state: dict = {"running": False, "results": [], "error": None}


def _run_fetch() -> None:
    from .fetch import run_all

    try:
        results = run_all()
        _fetch_state["results"] = results
        _fetch_state["error"] = None
    except Exception as e:  # noqa: BLE001 — surfaced through the status endpoint
        log.exception("ledger fetch thread failed")
        _fetch_state["error"] = f"{type(e).__name__}: {e}"
    finally:
        _fetch_state["running"] = False


@ledger_bp.route("/api/fetch", methods=["POST"])
def api_fetch():
    with _fetch_lock:
        if _fetch_state["running"]:
            return jsonify({"started": False, "reason": "already running"}), 409
        _fetch_state.update(running=True, results=[], error=None)

    threading.Thread(target=_run_fetch, daemon=True).start()
    return jsonify({"started": True})


@ledger_bp.route("/api/fetch/status")
def api_fetch_status():
    return jsonify(dict(_fetch_state))


# ---------------------------------------------------------------------------
#  Page
# ---------------------------------------------------------------------------


def _build_panels() -> list[dict]:
    runs = get_last_runs()
    panels = []

    for spec in PANELS:
        p = dict(spec)
        # Freshness is reported per *fetcher*, since that's what actually ran.
        p["run"] = runs.get(spec["fetch"])
        try:
            if spec["kind"] == "series":
                p["series"] = get_series(spec["section"])
                p["empty"] = not p["series"]
            else:
                p["rows"] = get_records(spec["section"], limit=25)
                p["empty"] = not p["rows"]
                # Vote tallies need one API call each, so a tight quota can leave
                # the oldest rows without them. Say so only when it actually
                # happened, rather than carrying a permanent caveat for a case
                # that no longer occurs with a real API key.
                p["missing_tallies"] = sum(
                    1 for r in p["rows"] if "yea" in (c["key"] for c in spec.get("columns", []))
                    and r.get("yea") is None
                )
        except Exception as e:  # noqa: BLE001 — one bad panel must not 500 the page
            log.warning("panel %s failed to load: %s", spec["id"], e)
            p["series"], p["rows"], p["empty"], p["load_error"] = [], [], True, str(e)
        panels.append(p)

    return panels


@ledger_bp.route("/")
def index():
    init_db()
    return render_template("ledger.html", panels=_build_panels())
