"""Card-level search driver for the BA module.

Drives Chrome to a constructed Upwork search URL, waits for results to render,
and walks the UIA tree once to parse all visible result cards. Card-level only
— no panel-open, no clipboard URL capture per card. The bidder still owns
deep panel scans; this driver is for cheap, fast market-corpus growth.

Mirrors the substrate recipe used by bidder/detection_loop.py:
  focus -> navigate -> wait-for-render -> Ctrl+Home -> zoom 33% -> observe
  -> reset zoom -> parse.

Uses upwork.search.build_search_url for URL construction (see ALLOWED_FILTER_KEYS
in that module for the 6 valid filter keys).
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from substrate import act
from upwork import feed_zoom
from upwork.feed import CHROME_WINDOW_CANDIDATES
from upwork.feed_cards import extract_cards_from_window, FeedCard
from upwork.search import build_search_url


# Wait budget for the results page to hydrate after navigation. Search renders
# roughly as fast as the feed; 15s is generous and matches the feed's 20s
# baseline minus the Ctrl+T tab-open cost we're skipping.
_RESULTS_RENDER_WAIT_S = 15.0


@dataclass
class CardResult:
    """Card-level search result. Field shapes match what's actually parseable
    from the search-results page UIA tree.
    """
    title: str
    snippet: Optional[str] = None        # description preview from card
    budget_kind: str = "unknown"         # 'hourly' | 'fixed' | 'unknown'
    budget_min_usd: Optional[float] = None
    budget_max_usd: Optional[float] = None
    budget_text: Optional[str] = None    # raw chip text, kept for debugging
    posted_text: Optional[str] = None    # e.g. "12 minutes ago"
    posted_at: Optional[datetime] = None  # parsed from posted_text
    skills: list[str] = field(default_factory=list)
    client_country: Optional[str] = None
    payment_verified: Optional[bool] = None
    job_url: Optional[str] = None        # best-effort; often None on cards


_HOURLY_RANGE_RE = re.compile(
    r"Hourly:\s*\$?([\d.]+)\s*-\s*\$?([\d.]+)", re.IGNORECASE
)
_HOURLY_SINGLE_RE = re.compile(r"Hourly:\s*\$?([\d.]+)", re.IGNORECASE)
_FIXED_AMOUNT_RE = re.compile(r"\$([\d,]+(?:\.\d+)?)")

_RELATIVE_TIME_RE = re.compile(
    r"^(\d+)\s+(minute|hour|day|week|month)s?\s+ago$", re.IGNORECASE
)


def _parse_budget(budget_text: Optional[str], est_value: Optional[str]) -> tuple[str, Optional[float], Optional[float]]:
    """Return (kind, min_usd, max_usd). Kind is 'hourly' | 'fixed' | 'unknown'.

    Cards expose budget like:
      'Hourly: $25.00 - $50.00'
      'Hourly: $50.00'
      'Hourly'                  (rate not shown on card; budget None)
      'Fixed-price'             with separate Est. value '$4,000.00'
    """
    if not budget_text:
        return "unknown", None, None
    txt = budget_text.strip()
    if txt.lower().startswith("hourly"):
        m = _HOURLY_RANGE_RE.search(txt)
        if m:
            return "hourly", float(m.group(1)), float(m.group(2))
        m = _HOURLY_SINGLE_RE.search(txt)
        if m:
            v = float(m.group(1))
            return "hourly", v, v
        return "hourly", None, None
    if txt.lower().startswith("fixed"):
        if est_value:
            m = _FIXED_AMOUNT_RE.search(est_value)
            if m:
                v = float(m.group(1).replace(",", ""))
                return "fixed", v, v
        return "fixed", None, None
    return "unknown", None, None


def _parse_relative_time(s: Optional[str]) -> Optional[datetime]:
    """'12 minutes ago' -> datetime in UTC. None on parse failure."""
    if not s:
        return None
    m = _RELATIVE_TIME_RE.match(s.strip())
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2).lower()
    delta_map = {
        "minute": timedelta(minutes=n),
        "hour": timedelta(hours=n),
        "day": timedelta(days=n),
        "week": timedelta(weeks=n),
        "month": timedelta(days=n * 30),
    }
    delta = delta_map.get(unit)
    if delta is None:
        return None
    return datetime.now(timezone.utc) - delta


def _feed_card_to_card_result(fc: FeedCard) -> Optional[CardResult]:
    """Lift a FeedCard (from upwork.feed_cards) into a CardResult.

    Returns None if the FeedCard is unusable (missing title — happens for
    spurious 'Posted' anchors that aren't real cards).
    """
    if not fc.title:
        return None
    kind, b_min, b_max = _parse_budget(fc.budget_text, fc.est_value)
    return CardResult(
        title=fc.title,
        snippet=fc.description_preview,
        budget_kind=kind,
        budget_min_usd=b_min,
        budget_max_usd=b_max,
        budget_text=fc.budget_text,
        posted_text=fc.posted_text,
        posted_at=_parse_relative_time(fc.posted_text),
        skills=list(fc.skills),
        client_country=fc.country,
        payment_verified=fc.payment_verified or None,
        job_url=None,  # search cards don't expose href via UIA reliably
    )


def _focus_chrome() -> None:
    for candidate in CHROME_WINDOW_CANDIDATES:
        if act.focus_window(candidate):
            return
    raise RuntimeError(
        f"Chrome window not found (tried titles {list(CHROME_WINDOW_CANDIDATES)!r}). "
        "Run launch_chrome.py first."
    )


def search(
    query: str,
    filters: Optional[dict] = None,
    max_cards: int = 30,
    window_title: str = "Upwork",
) -> list[CardResult]:
    """Drive Chrome to the constructed search URL and parse all visible cards.

    Args:
        query: free-text search term (will be URL-encoded).
        filters: dict using the keys in upwork.search.ALLOWED_FILTER_KEYS.
                 Unknown keys raise ValueError via build_search_url's validation
                 (caller validates before passing to us, normally).
        max_cards: cap on returned cards. Card capture is single-walk at 33%
                   zoom — typical yield is 8-12 cards per walk. To collect
                   more, raise this; the driver will scroll and re-walk.
        window_title: focus target for substrate. Default 'Upwork' matches
                      the bidder's setup.

    Returns:
        list[CardResult] in the order they appear on the page.

    Raises:
        RuntimeError if Chrome cannot be focused (run launch_chrome.py first).
    """
    url = build_search_url(query, filters or {})
    _focus_chrome()
    act.navigate(url)
    time.sleep(_RESULTS_RENDER_WAIT_S)
    act.focus_window(window_title)
    act.key("ctrl+home")
    time.sleep(0.6)
    feed_zoom.zoom_to_33pct()
    try:
        feed_cards_list = extract_cards_from_window(window_title)
    finally:
        try:
            act.focus_window(window_title)
            from upwork.feed_zoom import zoom_to_33pct  # noqa: F401
            # Reset to 100% so subsequent navigations / panel walks aren't
            # stacked on a 33% baseline. Use Ctrl+0 directly via act.key to
            # avoid importing a private reset helper.
            act.key("ctrl+0")
            time.sleep(0.4)
        except Exception:
            pass

    out: list[CardResult] = []
    for fc in feed_cards_list:
        cr = _feed_card_to_card_result(fc)
        if cr is None:
            continue
        out.append(cr)
        if len(out) >= max_cards:
            break
    return out


def source_tag(query: str, filters: Optional[dict] = None) -> str:
    """Stable source-tag string for corpus rows.

    Format: 'ba:<query>|<k1>=<v1>,<k2>=<v2>' with filter keys sorted so
    re-issuing the same query in a different filter-iteration order
    produces the same source tag (matters for dedup).
    """
    base = f"ba:{query.strip()}"
    if not filters:
        return base
    parts = [f"{k}={filters[k]}" for k in sorted(filters)]
    return f"{base}|{','.join(parts)}"


def search_and_ingest(
    db,
    query: str,
    filters: Optional[dict] = None,
    max_cards: int = 30,
    window_title: str = "Upwork",
) -> dict:
    """Convenience wrapper: drive the search, parse cards, ingest into corpus.

    Returns:
        {
          "query": query,
          "filters": filters or {},
          "source": source_tag(...),
          "scanned": len(parsed_cards),
          "inserted": N,
          "updated_existing": M,
          "sample": [first 3 cards as dicts]
        }
    """
    from storage.market_corpus import MarketCorpusStore  # local import to avoid
                                                          # cycles at module load

    cards = search(query, filters, max_cards=max_cards, window_title=window_title)
    source = source_tag(query, filters)
    corpus = MarketCorpusStore(db)
    counts = corpus.ingest_cards(cards, source=source)
    return {
        "query": query,
        "filters": filters or {},
        "source": source,
        "scanned": len(cards),
        "inserted": counts["inserted"],
        "updated_existing": counts["updated_existing"],
        "sample": [
            {
                "title": c.title,
                "budget_kind": c.budget_kind,
                "budget_min_usd": c.budget_min_usd,
                "budget_max_usd": c.budget_max_usd,
                "posted_text": c.posted_text,
                "skills": c.skills[:5],
                "client_country": c.client_country,
                "payment_verified": c.payment_verified,
            }
            for c in cards[:3]
        ],
    }
