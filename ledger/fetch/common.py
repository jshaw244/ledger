"""Shared plumbing for the fetchers: HTTP with retries, and API-key handling.

Key handling deserves a note. A missing key is NOT an error — it's a section that
can't run yet. The two are surfaced differently (`skipped` vs `error`) so the page
can say "needs a CONGRESS_API_KEY" instead of "the API is broken", which are very
different things for the reader to act on.

DEMO_KEY is api.data.gov's shared throttled key (roughly 30 requests/hour). It is
enough to prove a fetcher works end-to-end without registering, but far too tight
for the government section's per-vote detail calls, so fetchers are expected to
scale their own ambition down when `is_demo` is True.
"""
from __future__ import annotations

import logging
import os
import time

import requests

log = logging.getLogger("ledger.fetch")

USER_AGENT = "personal-hub-ledger/0.1"
DEMO_KEY = "DEMO_KEY"


class MissingKey(RuntimeError):
    """Raised when a section needs an API key that isn't configured."""


def api_key(env_name: str, allow_demo: bool = True) -> tuple[str, bool]:
    """Return (key, is_demo). Raises MissingKey when there's nothing usable."""
    key = os.getenv(env_name)
    if key:
        return key, False
    if allow_demo:
        log.warning("%s not set — falling back to DEMO_KEY (heavily rate-limited)", env_name)
        return DEMO_KEY, True
    raise MissingKey(f"{env_name} is not set")


def _request(method: str, url: str, *, attempts: int = 3, **kw) -> requests.Response:
    kw.setdefault("timeout", 45)
    headers = {"User-Agent": USER_AGENT, **kw.pop("headers", {})}
    last: Exception | None = None

    for i in range(attempts):
        try:
            r = requests.request(method, url, headers=headers, **kw)
        except requests.RequestException as e:
            last = e
        else:
            # 429/5xx are worth retrying; 4xx otherwise means we asked wrongly and
            # retrying will just ask wrongly again.
            if r.status_code < 400 or (r.status_code not in (429,) and r.status_code < 500):
                r.raise_for_status()
                return r
            last = requests.HTTPError(f"{r.status_code} from {url}: {r.text[:200]}")

        if i < attempts - 1:
            wait = 2 ** i
            log.warning("%s %s failed (%s); retrying in %ss", method, url, last, wait)
            time.sleep(wait)

    raise last  # type: ignore[misc]


def get_json(url: str, params: dict | None = None, **kw) -> dict:
    return _request("GET", url, params=params, **kw).json()


def get_text(url: str, params: dict | None = None, **kw) -> str:
    return _request("GET", url, params=params, **kw).text


def to_float(value) -> float | None:
    """OpenFEC returns some money fields as strings ('42587451.00') and others as
    floats, and FRED writes '.' for a missing observation. Normalize all of it."""
    if value is None or value == "" or value == ".":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
