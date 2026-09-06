"""Ledger blueprint: an overview page, one page per panel, and CSV export.

Each panel gets its own URL (/ledger/markets, /ledger/rates, ...) rather than all
of them stacking on one page. The stacked layout stopped scaling once there were
six panels, and it will get worse as sections are added — a reader after one
number should not scroll past five charts to reach it.

Pages are rendered server-side (tables in Jinja, chart data embedded as JSON)
rather than fetched over XHR: no build step, no client state to keep in sync, and
a chart cannot render while silently failing to show why its data is stale, since
freshness comes from the same render.
"""
from __future__ import annotations

import csv
import io
import logging
import threading
from datetime import date

from flask import Blueprint, Response, abort, jsonify, render_template, request, url_for

from .db import get_last_runs, get_records, get_series, init_db

log = logging.getLogger("ledger.routes")

ledger_bp = Blueprint(
    "ledger",
    __name__,
    template_folder="templates",
    static_folder="static",
)

# The pages, declaratively. `id` is the URL slug; `section` is the key data is
# stored under; `fetch` is the fetcher responsible for it (donations feeds two
# panels from one run, which is why the three are not one field).
PANELS = [
    {
        "id": "markets",
        "kind": "series",
        "section": "markets",
        "fetch": "markets",
        "label": "Markets",
        "title": "Markets & commodities",
        "subtitle": "Index levels and commodity prices since January 2021, each indexed to 100 at the start so they share one axis. Hover for the actual level.",
        "source": "FRED, Federal Reserve Bank of St. Louis",
        "indexed": True,
    },
    {
        "id": "prices",
        "kind": "series",
        "section": "prices",
        "fetch": "prices",
        "label": "Prices",
        "title": "Prices & inflation",
        "subtitle": "Price levels only, indexed to 100 at the start of the window. No blending, no derived index — each line is one published series as released.",
        "source": "FRED, Federal Reserve Bank of St. Louis",
        "indexed": True,
    },
    {
        "id": "rates",
        "kind": "series",
        "section": "rates",
        "fetch": "rates",
        "label": "Rates",
        "title": "Rates",
        "subtitle": "Actual percentages, not indexed — these already share a unit, so the chart shows the real rate you can read off directly.",
        "source": "FRED, Federal Reserve Bank of St. Louis",
        "indexed": False,
        "axis_label": "percent",
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
        "label": "Fundraisers",
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
        "label": "Contributions",
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

PANELS_BY_ID = {p["id"]: p for p in PANELS}


# ---------------------------------------------------------------------------
#  Loading
# ---------------------------------------------------------------------------


def _load_panel(spec: dict) -> dict:
    """Attach data + freshness to a panel spec. Never raises."""
    p = dict(spec)
    # Freshness is reported per *fetcher*, since that is what actually ran.
    p["run"] = get_last_runs().get(spec["fetch"])
    # Defaults set unconditionally: templates serialise these into JSON, and a
    # missing key becomes Jinja's Undefined, which `| tojson` refuses.
    p["series"], p["rows"] = [], []
    p["empty"], p["load_error"], p["missing_tallies"] = True, None, 0
    try:
        if spec["kind"] == "series":
            p["series"] = get_series(spec["section"])
            p["empty"] = not p["series"]
        else:
            p["rows"] = get_records(spec["section"], limit=25)
            p["empty"] = not p["rows"]
            # Vote tallies need one API call each, so a tight quota can leave the
            # oldest rows without them. Say so only when it actually happened.
            keys = {c["key"] for c in spec.get("columns", [])}
            p["missing_tallies"] = (
                sum(1 for r in p["rows"] if r.get("yea") is None) if "yea" in keys else 0
            )
    except Exception as e:  # noqa: BLE001 — one bad panel must not 500 the page
        log.warning("panel %s failed to load: %s", spec["id"], e)
        p["empty"], p["load_error"] = True, str(e)
    return p


def _has_content(p: dict) -> bool:
    return not p["empty"] and not p["load_error"]


def _date_bounds(p: dict) -> tuple[str, str]:
    """Earliest and latest observation across a series panel, as ISO dates."""
    dates = [pt[0] for s in p.get("series") or [] for pt in (s["points"][0], s["points"][-1])]
    return (min(dates), max(dates)) if dates else ("", "")


def _nav(active_id: str | None) -> list[dict]:
    """Second-tier nav: the hub bar switches modules, this switches panels."""
    items = [{
        "label": "Overview",
        "url": url_for("ledger.index"),
        "active": active_id is None,
    }]
    for spec in PANELS:
        items.append({
            "label": spec["label"],
            "url": url_for("ledger.panel", panel_id=spec["id"]),
            "active": spec["id"] == active_id,
        })
    return items


# ---------------------------------------------------------------------------
#  Refresh (the scheduled task is the normal path; this is the button)
# ---------------------------------------------------------------------------

_fetch_lock = threading.Lock()
_fetch_state: dict = {"running": False, "results": [], "error": None}


def _run_fetch(sections: list[str] | None) -> None:
    from .fetch import run_all

    try:
        _fetch_state["results"] = run_all(sections)
        _fetch_state["error"] = None
    except Exception as e:  # noqa: BLE001 — surfaced through the status endpoint
        log.exception("ledger fetch thread failed")
        _fetch_state["error"] = f"{type(e).__name__}: {e}"
    finally:
        _fetch_state["running"] = False


@ledger_bp.route("/api/fetch", methods=["POST"])
def api_fetch():
    # A panel page refreshes just its own section; the overview refreshes all.
    raw = request.args.get("sections") or ""
    sections = [s for s in raw.split(",") if s] or None

    with _fetch_lock:
        if _fetch_state["running"]:
            return jsonify({"started": False, "reason": "already running"}), 409
        _fetch_state.update(running=True, results=[], error=None)

    threading.Thread(target=_run_fetch, args=(sections,), daemon=True).start()
    return jsonify({"started": True, "sections": sections or "all"})


@ledger_bp.route("/api/fetch/status")
def api_fetch_status():
    return jsonify(dict(_fetch_state))


# ---------------------------------------------------------------------------
#  Pages
# ---------------------------------------------------------------------------


@ledger_bp.route("/")
def index():
    init_db()
    return render_template(
        "ledger_index.html",
        panels=[_load_panel(s) for s in PANELS],
        nav=_nav(None),
    )


@ledger_bp.route("/<panel_id>")
def panel(panel_id: str):
    spec = PANELS_BY_ID.get(panel_id)
    if not spec:
        abort(404)
    init_db()
    p = _load_panel(spec)
    lo, hi = _date_bounds(p)
    return render_template(
        "ledger_panel.html",
        p=p,
        nav=_nav(panel_id),
        date_min=lo,
        date_max=hi,
    )


# ---------------------------------------------------------------------------
#  CSV export
# ---------------------------------------------------------------------------


def _csv_response(rows: list[list], filename: str) -> Response:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@ledger_bp.route("/<panel_id>/export.csv")
def export(panel_id: str):
    spec = PANELS_BY_ID.get(panel_id)
    if not spec:
        abort(404)

    start = request.args.get("from") or ""
    end = request.args.get("to") or ""
    p = _load_panel(spec)
    stamp = date.today().isoformat()

    if spec["kind"] == "series":
        # Wide format — one column per series — because that is what opens usefully
        # in a spreadsheet. Series sampled at different frequencies (daily vs
        # monthly) simply leave blanks on dates they do not report.
        labels = [s["label"] for s in p["series"]]
        by_date: dict[str, dict[str, float]] = {}
        for s in p["series"]:
            for d, v in s["points"]:
                if (not start or d >= start) and (not end or d <= end):
                    by_date.setdefault(d, {})[s["label"]] = v

        rows = [["date", *labels]]
        for d in sorted(by_date):
            rows.append([d, *[by_date[d].get(lbl, "") for lbl in labels]])
        name = f"ledger-{panel_id}-{start or 'start'}-to-{end or stamp}.csv"
    else:
        cols = spec.get("columns", [])
        rows = [[c["label"] for c in cols] + ["source_url"]]
        for r in p["rows"]:
            rows.append([r.get(c["key"], "") for c in cols] + [r.get("source_url", "")])
        name = f"ledger-{panel_id}-{stamp}.csv"

    return _csv_response(rows, name)
