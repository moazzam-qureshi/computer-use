"""Search-results query-spec helpers — extracted from upwork_research.py.

The Phase-1 plan only asks for parse_query_line and build_search_url; per-card
crawling stays in the legacy file until a later phase ports it onto the new
substrate.
"""
from __future__ import annotations

from urllib.parse import quote

SEARCH_URL_BASE = "https://www.upwork.com/nx/search/jobs/"

ALLOWED_FILTER_KEYS = frozenset({
    "payment_verified",  # =1
    "t",                  # =0 (Hourly), =1 (Fixed-Price)
    "hourly_rate",        # =min-max
    "amount",             # =min-max for fixed-price
    "proposals",          # =0-4, 5-9, 10-14, 15-19, 20-49
    "duration_v3",        # =week|month|semester|ongoing
})


def parse_query_line(raw: str) -> tuple[str, dict]:
    """Parse one query line into (query_term, filters).

    Forms:
        "LLM engineer"
        "ai agent developer | payment_verified=1, t=0, hourly_rate=25-35"

    Raises ValueError on unknown filter keys or malformed key=value pairs.
    """
    line = raw.strip()
    if "|" not in line:
        return line, {}
    query_part, filters_part = line.split("|", 1)
    query = query_part.strip()
    filters: dict[str, str] = {}
    for tok in filters_part.split(","):
        tok = tok.strip()
        if not tok:
            continue
        if "=" not in tok:
            raise ValueError(f"Malformed filter (no '='): {tok!r}")
        k, v = tok.split("=", 1)
        k = k.strip()
        v = v.strip()
        if k not in ALLOWED_FILTER_KEYS:
            raise ValueError(
                f"Unknown filter key {k!r}. Allowed: {sorted(ALLOWED_FILTER_KEYS)}"
            )
        filters[k] = v
    return query, filters


def build_search_url(query_term: str, filters: dict) -> str:
    """Construct the Upwork search URL with query + filters applied."""
    params = [
        "from_recent_search=true",
        f"q={quote(query_term, safe='')}",
        "sort=relevance%2Bdesc",
    ]
    for k in sorted(filters or {}):
        params.append(f"{k}={quote(str(filters[k]), safe='-')}")
    return f"{SEARCH_URL_BASE}?{'&'.join(params)}"
