"""The hub-card contract: get_summary() -> dict.

Read-only, cheap, and FAIL-GRACEFUL — this runs on hub renders, so a broken module
must return a degraded card, never raise. Shape: {"headline", "detail", "url"}.
"""
from __future__ import annotations

from .db import connect_readonly


def get_summary() -> dict:
    try:
        conn = connect_readonly()
        try:
            sections = conn.execute(
                "SELECT status, COUNT(*) n FROM fetch_runs r "
                "WHERE started_at = (SELECT MAX(started_at) FROM fetch_runs WHERE section = r.section) "
                "GROUP BY status"
            ).fetchall()
            points = conn.execute("SELECT COUNT(*) FROM series_points").fetchone()[0]
            records = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
            last = conn.execute(
                "SELECT MAX(finished_at) FROM fetch_runs WHERE status = 'ok'"
            ).fetchone()[0]
        finally:
            conn.close()

        by_status = {r["status"]: r["n"] for r in sections}
        if not by_status:
            detail = "No fetch has run yet."
        else:
            bits = [f"{points + records:,} data points"]
            if by_status.get("error"):
                bits.append(f"{by_status['error']} section(s) failing")
            if by_status.get("skipped"):
                bits.append(f"{by_status['skipped']} awaiting an API key")
            if last:
                bits.append(f"updated {last[:10]}")
            detail = " · ".join(bits)

        return {"headline": "Ledger", "detail": detail, "url": "/ledger/"}
    except Exception as e:  # noqa: BLE001 — deliberately swallow; card must not raise
        return {"headline": "Ledger", "detail": "unavailable", "url": "/ledger/", "error": str(e)}
