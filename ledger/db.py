"""Ledger storage — one SQLite file shared by every section.

Why generic tables instead of one per section: sections get added often, but they
only ever need two presentations — a timeseries (chart) or a ranked list of rows
(table). Keeping the schema stable means adding a section is a fetcher plus a
template block, never a migration.

`fetch_runs` exists because of the project's core rule: every number here must be
traceable to a real filing or feed. That means the page has to be honest about
which sections are fresh, which are stale, and which failed — silently showing
last week's numbers as if they were today's would break the premise.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# Resolved relative to the package, not the cwd, so a scheduled task launched
# from anywhere still finds the same database.
REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
DB_FILE = DATA_DIR / "ledger.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS series_points (
    section      TEXT NOT NULL,
    series_key   TEXT NOT NULL,
    series_label TEXT NOT NULL,
    point_date   TEXT NOT NULL,
    value        REAL NOT NULL,
    unit         TEXT,
    -- Position in the fetcher's declared series list. Exists so a series always
    -- gets the SAME chart color: without it, series order (and therefore color)
    -- would follow whatever order rows happen to come back in, and a chart's
    -- colors would silently reshuffle between fetches.
    sort_order   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (section, series_key, point_date)
);
CREATE INDEX IF NOT EXISTS idx_series_section_date
    ON series_points (section, point_date);

CREATE TABLE IF NOT EXISTS records (
    section       TEXT NOT NULL,
    record_key    TEXT NOT NULL,
    sort_value    REAL,
    payload_json  TEXT NOT NULL,
    source_url    TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL,
    PRIMARY KEY (section, record_key)
);
CREATE INDEX IF NOT EXISTS idx_records_section_sort
    ON records (section, sort_value DESC);

CREATE TABLE IF NOT EXISTS fetch_runs (
    section     TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL,
    detail      TEXT,
    PRIMARY KEY (section, started_at)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def connect_readonly() -> sqlite3.Connection:
    """Read-only handle for render paths (routes, get_summary) so a page view can
    never block or corrupt a concurrent fetch."""
    conn = sqlite3.connect(f"file:{DB_FILE}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


# Columns added after the first release, as {table: {column: DDL type+default}}.
# CREATE TABLE IF NOT EXISTS silently does nothing on an existing table, so new
# columns have to be added explicitly or an older database keeps the old shape.
_ADDED_COLUMNS = {
    "series_points": {"sort_order": "INTEGER NOT NULL DEFAULT 0"},
}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, columns in _ADDED_COLUMNS.items():
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not existing:  # table not created yet; the schema script handles it
            continue
        for name, ddl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


# ---------------------------------------------------------------------------
#  Writes — used by fetchers only
# ---------------------------------------------------------------------------


def upsert_series_points(section: str, points: list[dict]) -> int:
    """points: [{series_key, series_label, point_date, value, unit?}, ...]

    Re-fetching the same date overwrites the value rather than duplicating it, so
    a fetcher is safe to re-run and a revised figure (FRED revises regularly)
    lands correctly.
    """
    if not points:
        return 0
    rows = [
        (
            section,
            p["series_key"],
            p["series_label"],
            p["point_date"],
            float(p["value"]),
            p.get("unit"),
            int(p.get("sort_order", 0)),
        )
        for p in points
    ]
    with connect() as conn:
        conn.executemany(
            "INSERT INTO series_points "
            "  (section, series_key, series_label, point_date, value, unit, sort_order) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(section, series_key, point_date) DO UPDATE SET "
            "  value = excluded.value, "
            "  series_label = excluded.series_label, "
            "  unit = excluded.unit, "
            "  sort_order = excluded.sort_order",
            rows,
        )
    return len(rows)


def upsert_records(section: str, items: list[dict]) -> int:
    """items: [{record_key, sort_value?, source_url?, **display fields}, ...]

    Everything outside the reserved keys is stored as the row's display payload.
    `first_seen_at` is preserved across re-fetches so the page can show when a
    filing first appeared, not merely when it was last scraped.
    """
    if not items:
        return 0
    now = _now()
    rows = []
    for it in items:
        payload = {k: v for k, v in it.items() if k not in {"record_key", "sort_value", "source_url"}}
        rows.append(
            (
                section,
                str(it["record_key"]),
                it.get("sort_value"),
                json.dumps(payload, default=str),
                it.get("source_url"),
                now,
                now,
            )
        )
    with connect() as conn:
        conn.executemany(
            "INSERT INTO records "
            "  (section, record_key, sort_value, payload_json, source_url, first_seen_at, last_seen_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(section, record_key) DO UPDATE SET "
            "  sort_value = excluded.sort_value, "
            "  payload_json = excluded.payload_json, "
            "  source_url = excluded.source_url, "
            "  last_seen_at = excluded.last_seen_at",
            rows,
        )
    return len(rows)


def start_run(section: str) -> str:
    started = _now()
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO fetch_runs (section, started_at, status) VALUES (?, ?, 'running')",
            (section, started),
        )
    return started


def finish_run(section: str, started_at: str, status: str, detail: str = "") -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE fetch_runs SET finished_at = ?, status = ?, detail = ? "
            "WHERE section = ? AND started_at = ?",
            (_now(), status, detail[:500], section, started_at),
        )


# ---------------------------------------------------------------------------
#  Reads — used by routes and get_summary
# ---------------------------------------------------------------------------


def get_series(section: str, limit_days: int = 400) -> list[dict]:
    """Chart data for one section, as [{key, label, unit, points: [[date, value]]}].

    Series come back in the fetcher's declared order (`sort_order`), which is what
    keeps each series on the same chart color run after run.
    """
    with connect_readonly() as conn:
        rows = conn.execute(
            "SELECT series_key, series_label, unit, sort_order, point_date, value FROM ("
            "  SELECT * FROM series_points WHERE section = ? "
            "  ORDER BY point_date DESC LIMIT ?"
            ") ORDER BY sort_order ASC, series_key ASC, point_date ASC",
            (section, limit_days * 40),
        ).fetchall()

    grouped: dict[str, dict] = {}
    for r in rows:
        s = grouped.setdefault(
            r["series_key"],
            {"key": r["series_key"], "label": r["series_label"], "unit": r["unit"], "points": []},
        )
        s["points"].append([r["point_date"], r["value"]])
    return list(grouped.values())


def get_records(section: str, limit: int = 25) -> list[dict]:
    with connect_readonly() as conn:
        rows = conn.execute(
            "SELECT record_key, sort_value, payload_json, source_url, first_seen_at "
            "FROM records WHERE section = ? "
            "ORDER BY sort_value DESC NULLS LAST, record_key LIMIT ?",
            (section, limit),
        ).fetchall()
    out = []
    for r in rows:
        item = json.loads(r["payload_json"])
        item["record_key"] = r["record_key"]
        item["source_url"] = r["source_url"]
        item["first_seen_at"] = r["first_seen_at"]
        out.append(item)
    return out


def get_last_runs() -> dict[str, dict]:
    """Most recent run per section — drives the freshness/failure banner."""
    with connect_readonly() as conn:
        rows = conn.execute(
            "SELECT section, started_at, finished_at, status, detail FROM fetch_runs r "
            "WHERE started_at = (SELECT MAX(started_at) FROM fetch_runs WHERE section = r.section)"
        ).fetchall()
    return {r["section"]: dict(r) for r in rows}
