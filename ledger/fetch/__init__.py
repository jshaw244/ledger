"""Fetcher registry and run wrapper.

Every fetch goes through `run_section()`, which records a row in `fetch_runs`
whether it succeeds, fails, or is skipped for a missing key. That bookkeeping is
the point: the dashboard's premise is that every number traces to a real filing,
which obliges it to be equally honest about numbers it could NOT refresh.

Status values:
  ok      - fetched and stored
  skipped - a required API key isn't configured (not a failure; nothing to fix
            in the code)
  error   - the source was reachable-ish but the fetch failed
"""
from __future__ import annotations

import logging
from importlib import import_module

from ..db import finish_run, init_db, start_run
from .common import MissingKey

log = logging.getLogger("ledger.fetch")

# section name -> module path, imported lazily so one module's heavy or broken
# dependency (yfinance, notably) can't stop the others from running.
SECTIONS = {
    "markets": "ledger.fetch.markets",
    "prices": "ledger.fetch.prices",
    "rates": "ledger.fetch.rates",
    "government": "ledger.fetch.government",
    "donations": "ledger.fetch.donations",
}


def run_section(section: str) -> dict:
    if section not in SECTIONS:
        raise KeyError(f"unknown section {section!r}; known: {', '.join(SECTIONS)}")

    init_db()
    started = start_run(section)
    log.info("fetch %s: starting", section)

    try:
        module = import_module(SECTIONS[section])
        detail = module.run() or "ok"
    except MissingKey as e:
        log.warning("fetch %s: skipped — %s", section, e)
        finish_run(section, started, "skipped", str(e))
        return {"section": section, "status": "skipped", "detail": str(e)}
    except Exception as e:  # noqa: BLE001 — recorded, not raised; the run continues
        log.error("fetch %s: FAILED — %s", section, e)
        finish_run(section, started, "error", f"{type(e).__name__}: {e}")
        return {"section": section, "status": "error", "detail": f"{type(e).__name__}: {e}"}

    log.info("fetch %s: ok — %s", section, detail)
    finish_run(section, started, "ok", detail)
    return {"section": section, "status": "ok", "detail": detail}


def run_all(sections: list[str] | None = None) -> list[dict]:
    return [run_section(s) for s in (sections or list(SECTIONS))]
