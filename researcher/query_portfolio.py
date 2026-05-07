"""Query portfolio for the Researcher.

v1: operator-curated list stored in system_config under key
'researcher_query_portfolio'. Each entry is a dict:
    {"query": "<text>", "filters": {<filter_dict>}, "added_at": "<iso>",
     "added_by": "<seed|operator|agent>"}

Operator manages the portfolio via assistant tools (added in R-Task 8).
v1 has no agent-driven curation; the loop just runs whatever's in the
list.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from storage.conversations import SystemConfigStore


_KEY = "researcher_query_portfolio"


def load(sysconfig: SystemConfigStore) -> list[dict]:
    """Return the portfolio as list[dict]. Empty list if unset."""
    raw = sysconfig.get(_KEY)
    if raw is None:
        return []
    if not isinstance(raw, list):
        # Defensive: someone wrote the wrong shape; treat as empty rather
        # than crash the loop.
        return []
    return raw


def save(sysconfig: SystemConfigStore, portfolio: list[dict]) -> None:
    sysconfig.set(_KEY, portfolio)


def add(
    sysconfig: SystemConfigStore,
    *,
    query: str,
    filters: Optional[dict] = None,
    added_by: str = "operator",
) -> bool:
    """Add a query to the portfolio. Returns True if added, False if a
    duplicate (same query + filters) already exists.
    """
    portfolio = load(sysconfig)
    for entry in portfolio:
        if entry.get("query") == query and entry.get("filters", {}) == (filters or {}):
            return False
    portfolio.append({
        "query": query,
        "filters": filters or {},
        "added_at": datetime.now(timezone.utc).isoformat(),
        "added_by": added_by,
    })
    save(sysconfig, portfolio)
    return True


def remove(sysconfig: SystemConfigStore, query: str) -> int:
    """Remove all entries matching `query` (any filters). Returns count removed."""
    portfolio = load(sysconfig)
    kept = [e for e in portfolio if e.get("query") != query]
    removed = len(portfolio) - len(kept)
    if removed:
        save(sysconfig, kept)
    return removed
