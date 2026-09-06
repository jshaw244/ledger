"""Ledger — public-record tracking, a module of the personal app hub.

Every number this module shows traces to a named public filing or feed. Nothing
here is an inferred score, a blended index, or a third-party judgment presented as
data; where a source can't support that standard, the feature isn't built.

Public surface for the hub to consume:
  - `ledger_bp`    : Flask blueprint (register at /ledger)
  - `get_summary`  : hub-card contract, get_summary() -> dict
"""
from .routes import ledger_bp
from .summary import get_summary

__all__ = ["ledger_bp", "get_summary"]
__version__ = "0.1.0"
