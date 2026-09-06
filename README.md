# Ledger

Public-record tracking — a module of the
[personal app hub](https://github.com/jshaw244/hub).

## The rule this module is built around

**Every number traces to a named public filing or feed.** No inferred scores, no
blended indices, no third-party judgments presented as data. Where a source can't
support that standard, the feature doesn't get built the way it was imagined.

Two consequences that look like missing features but are deliberate:

- **No automated court-record search by name.** Common names produce false
  matches, and misattributing a case to the wrong person is a serious harm, not a
  bug. Legal history, when built, is a hand-curated watchlist where every entry
  carries a `source_url`; the fetcher only refreshes the status of entries a human
  already verified.
- **No media bias / "promote-suppress" scoring, ever.** There is no neutral public
  record of what an outlet chose not to cover, so any such score encodes somebody's
  editorial judgment — and the orgs that publish bias ratings are themselves
  contested along the exact lines the score would be used to argue about. Piping
  one in as "data" would contradict the premise of the whole module. This is a
  boundary to hold, not a feature to reinterpret.

The `fetch_runs` table and the per-panel freshness pill exist for the same reason:
a page claiming traceable numbers is obliged to say when it *couldn't* refresh one,
rather than showing last month's figure as though it were current.

## Sections

| Section | Source | Key | Shape |
|---|---|---|---|
| Markets | Yahoo Finance via `yfinance` | none | 7 series, weekly closes, 5y |
| Prices | FRED CSV export | none | 7 published series |
| Government | `api.congress.gov` `/v3/house-vote` | `CONGRESS_API_KEY` | recent roll-call votes |
| Donations | OpenFEC `/v1` | `CONGRESS_API_KEY` | top fundraisers + largest itemized receipts |

`CONGRESS_API_KEY` is an [api.data.gov](https://api.data.gov/signup/) key. One key
covers **both** congress.gov and OpenFEC — they're sister APIs on the same
gateway (verified). It does **not** cover CourtListener or the FCC public-file API;
those are separate registrations.

Without a key both government and donations fall back to `DEMO_KEY`, api.data.gov's
shared throttled key (~30 requests/hour). Enough to prove the fetchers work; too
tight for the government section's per-vote tally lookups, which is why that
section caps itself at 3 tally calls on `DEMO_KEY` versus 12 with a real one.

## Corrections to the original spec, found by probing the live APIs

The field names in the original brief came from documentation and third-party
wrappers rather than a live call. Four were wrong, and each would have shipped a
broken or silently-empty panel:

1. **`/candidates/` cannot sort by total receipts** — it returns HTTP 422 and
   lists its allowed sort fields. Candidate money lives on
   **`/candidates/totals/`**, sorted by `receipts`.
2. **The money fields are `receipts` / `disbursements` / `cash_on_hand_end_period`**,
   not `total_receipts` / `total_disbursements` / `last_cash_on_hand_end_period`.
3. **`schedule_a` needs `sort_hide_null=true`.** Sorted by
   `-contribution_receipt_amount` without it, the top rows are records with NULL
   amounts and NULL dates — the "largest contributions" table comes back blank at
   the top and looks like a parsing bug.
4. **`/house-vote` is ordered by `updateDate`, not by vote date**, so a 2023 vote
   bulk-revised last month outranks last week's vote. The fetcher pulls a wide page
   and re-sorts on `startDate` locally. The list also carries **no tallies at all** —
   those need one extra detail request per vote.

Money fields also come back inconsistently typed (`receipts` a float,
`cash_on_hand_end_period` the string `"42587451.00"`), so everything goes through
`to_float()`.

Re-verify the same way whenever an endpoint changes:

```powershell
python -m ledger.fetch --raw donations   # dumps one raw result, parses nothing
```

## Usage

```powershell
python -m ledger.fetch                 # every section
python -m ledger.fetch prices markets  # just these
python -m ledger.fetch --list
python -m ledger.fetch --raw government
```

Exit code is non-zero only on a real **error**. A section *skipped* for a missing
API key is an expected state, not a failure — a scheduled task shouldn't alarm on it.

`scripts/refresh_ledger.ps1` is the scheduled-task entry point; it logs to
`data/fetch.log`.

## Layout

```
ledger\                 <- package
  db.py                 <- ledger.db: series_points, records, fetch_runs
  routes.py             <- blueprint + PANELS (the page, declaratively)
  summary.py            <- get_summary() hub card
  templates\ledger.html
  fetch\
    common.py           <- HTTP w/ retries, API-key handling, to_float
    markets.py prices.py government.py donations.py
    __main__.py         <- the CLI, incl. --raw
data\                   <- ledger.db (gitignored — derived, rebuildable)
```

`data/` is gitignored on purpose: the database is derived from public sources and
is rebuilt by re-running the fetchers, so it doesn't belong in version control.

## Chart conventions

Colors come from the validated reference categorical palette **in its documented
slot order** — the ordering is the colorblind-safety mechanism, not decoration, so
don't reorder or hand-pick. Two rules the code enforces:

- **One axis, never two.** Series on different scales (an index level, $/bbl, a
  percent) are indexed to 100 at the window start so they share a single axis. The
  tooltip shows the real level, since the index itself would be a misleading number
  to read off.
- **Color follows the entity, never its rank.** `series_points.sort_order` records
  each series' position in its fetcher's declared list, so a series keeps the same
  color across fetches instead of being repainted when the row order shifts.

## Adding a section

1. Write `ledger/fetch/<name>.py` exposing `run() -> str`, with each series or
   sub-view in its own `try/except` so one failure doesn't kill the rest.
2. Register it in `SECTIONS` in `ledger/fetch/__init__.py`.
3. Append a panel dict to `PANELS` in `ledger/routes.py`.

Storage needs no change — `series_points` covers charts and `records` covers tables.
