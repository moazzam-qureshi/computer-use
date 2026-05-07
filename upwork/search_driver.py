"""Card-level + deep-level search driver for the BA / Researcher modules.

Two entry points:

- search(): card-level only. Drives Chrome to a constructed Upwork search
  URL, walks the UIA tree once at 33% zoom, parses all visible result
  cards. Fast (~5s per query), no panel-open. Used by the assistant's
  on-demand search_market tool.

- deep_search(): same URL navigation, but for each visible result card
  it clicks into the panel, captures the full description + real Upwork
  URL via clipboard, then closes. Slower (~15s per job × N jobs) but
  produces full Job objects suitable for forensic analysis. Used by the
  Researcher's autonomous loop.

Both reuse upwork.search.build_search_url for URL construction (see
ALLOWED_FILTER_KEYS for the 6 valid filter keys).
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from substrate import act
from upwork import feed_zoom
from upwork.feed import CHROME_WINDOW_CANDIDATES, _parse_visible_cards
from upwork.feed_cards import extract_cards_from_window, FeedCard
from upwork.panel import PanelData, capture_panel, parse_panel
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


# ---------------------------------------------------------------------------
# Deep search: panel-level capture for the Researcher.
# ---------------------------------------------------------------------------

# Upwork URLs look like https://www.upwork.com/jobs/<title-slug>_~01abc123
# The canonical job_id is the trailing ~01... token.
_JOB_ID_FROM_URL_RE = re.compile(r"~([0-9A-Za-z]+)\b")

# Per-card click overhead: panel slide-in (~3.5s) + capture_panel walk
# (~5-8s typical) + Esc close (~1.5s). Budget per card: ~12-15s.
_PANEL_OPEN_SETTLE_S = 3.5
_PANEL_CLOSE_SETTLE_S = 1.5
_DEEP_SEARCH_RENDER_WAIT_S = 15.0


def _job_id_from_url(url: str) -> Optional[str]:
    """Extract the canonical Upwork job_id token from a job URL.
    Returns None if the URL doesn't match the expected pattern.
    """
    if not url:
        return None
    m = _JOB_ID_FROM_URL_RE.search(url)
    return m.group(0) if m else None  # group(0) keeps the leading ~


def _panel_data_to_job(panel: PanelData, url: str):
    """Convert a parsed PanelData + captured URL into a domain.types.Job.

    Local import to avoid a cycle (domain doesn't depend on upwork; this
    helper exists at the boundary).
    """
    from domain.types import Job

    job_id = _job_id_from_url(url) or url  # url itself as last-resort id
    return Job(
        job_id=job_id,
        url=url or f"deep://{job_id}",
        title=panel.title,
        description=panel.description or None,
        budget_kind=panel.budget_kind,
        budget_min_usd=panel.budget_min_usd,
        budget_max_usd=panel.budget_max_usd,
        skills=list(panel.skills or []),
        client_country=panel.client_country,
        client_payment_verified=panel.client_payment_verified,
        client_rating=panel.client_rating,
        client_hires=panel.client_hires,
        client_total_spent_usd=panel.client_total_spent_usd,
        posted_at=_parse_relative_time(panel.posted_text),
        posted_text=panel.posted_text,
        proposals_count_at_first_scrape=panel.proposals_count,
    )


def deep_search(
    query: str,
    filters: Optional[dict] = None,
    max_jobs: int = 30,
    window_title: str = "Upwork",
) -> list:
    """Drive Chrome to the search URL, then panel-open each result and
    capture the full Job (description, real URL, full client metadata).

    Returns a list[Job] (domain.types.Job). Empty list if the page yielded
    no clickable results within the budget.

    Caller MUST hold the substrate ui_lock for the entire call. Caller
    MUST have already configured act.set_target_window if the substrate
    accepts multiple window-title candidates.

    Pacing: every navigate / click / Esc / observe routes through the
    shared pacing budget. A deep pass over 30 jobs eats ~150 actions in
    ~7 minutes wall-clock. The Researcher operates within the raised
    120/hr default.
    """
    from domain.types import Job  # noqa: F401  — used via _panel_data_to_job

    url = build_search_url(query, filters or {})
    _focus_chrome()
    act.navigate(url)
    time.sleep(_DEEP_SEARCH_RENDER_WAIT_S)
    act.focus_window(window_title)
    act.key("ctrl+home")
    time.sleep(0.6)

    # Reuse the feed's _parse_visible_cards: search-results pages share
    # the 'Posted' anchor + 'Save job <title>' button layout. Returns
    # list[(title, hyperlink_element)].
    title_pairs = _parse_visible_cards(window_title)
    if not title_pairs:
        return []

    out: list = []
    seen_titles: set[str] = set()

    for title, link_el in title_pairs:
        if title in seen_titles:
            continue
        seen_titles.add(title)
        if len(out) >= max_jobs:
            break

        # Click the title hyperlink. capture_panel will detect when the
        # panel is open (it polls for the "Apply now" anchor internally).
        try:
            act.focus_window(window_title)
            act.click(link_el)
        except Exception:
            continue  # stale ref or off-screen; skip this card
        time.sleep(_PANEL_OPEN_SETTLE_S)

        try:
            elements, captured_url = capture_panel(window_title)
        except Exception:
            elements, captured_url = [], ""

        if not elements:
            # Panel never opened or capture_panel returned nothing useful.
            # Esc and continue — losing this card is preferable to wedging.
            try:
                act.focus_window(window_title)
                act.key("escape")
                time.sleep(_PANEL_CLOSE_SETTLE_S)
            except Exception:
                pass
            continue

        try:
            panel_data = parse_panel(elements)
        except Exception:
            try:
                act.focus_window(window_title)
                act.key("escape")
                time.sleep(_PANEL_CLOSE_SETTLE_S)
            except Exception:
                pass
            continue

        if panel_data.title:
            job = _panel_data_to_job(panel_data, captured_url)
            out.append(job)

        # Always close the panel before the next click, even if parse
        # succeeded — leaving a panel open wedges the next card click.
        try:
            act.focus_window(window_title)
            act.key("escape")
            time.sleep(_PANEL_CLOSE_SETTLE_S)
        except Exception:
            pass

    return out


def deep_search_and_ingest(
    db,
    query: str,
    filters: Optional[dict] = None,
    max_jobs: int = 30,
    window_title: str = "Upwork",
) -> dict:
    """Drive deep_search, then ingest the resulting Jobs into the corpus.

    Returns:
        {
          "query": query,
          "filters": filters or {},
          "source": source_tag(...),
          "scanned": len(jobs),
          "inserted": N,
          "updated_existing": M,
          "sample": [first 3 jobs as compact dicts]
        }
    """
    from storage.market_corpus import MarketCorpusStore

    jobs = deep_search(query, filters, max_jobs=max_jobs,
                       window_title=window_title)
    source = source_tag(query, filters)
    corpus = MarketCorpusStore(db)
    counts = corpus.ingest_jobs(jobs, source=source)
    return {
        "query": query,
        "filters": filters or {},
        "source": source,
        "scanned": len(jobs),
        "inserted": counts["inserted"],
        "updated_existing": counts["updated_existing"],
        "sample": [
            {
                "job_id": j.job_id,
                "url": j.url,
                "title": j.title,
                "budget_kind": j.budget_kind,
                "budget_min_usd": j.budget_min_usd,
                "budget_max_usd": j.budget_max_usd,
                "posted_text": j.posted_text,
                "skills": (j.skills or [])[:5],
                "client_country": j.client_country,
                "client_payment_verified": j.client_payment_verified,
                "description_chars": len(j.description or ""),
            }
            for j in jobs[:3]
        ],
    }
