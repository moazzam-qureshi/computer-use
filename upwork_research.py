"""
Upwork search crawler — market intelligence corpus builder.

Drives Upwork's search via direct URL navigation (no UI button-clicking),
scrapes the top N results from each search (panel-opened for ground-truth
data), and writes everything into upwork.db. Does NOT judge relevance,
generate proposals, or alert per job — it builds the corpus that strategies
are derived from later.

Query format in research_queries.txt:
    <bare query>
    <query> | <key=value>, <key=value>, ...

Supported filter keys (mapped to Upwork URL params):
    payment_verified=1
    t=0  (Hourly)  |  t=1  (Fixed-Price)
    hourly_rate=25-35  (or open-ended like 50-)
    amount=500-999     (Fixed-price tier)
    proposals=0-4 | 5-9 | 10-14 | 15-19 | 20-49
    duration_v3=week | month | semester | ongoing

Usage:
    uv run upwork_research.py --query "LLM engineer"
    uv run upwork_research.py --query 'ai agent developer | payment_verified=1, t=0, hourly_rate=25-'
    uv run upwork_research.py                                       # reads research_queries.txt
    uv run upwork_research.py --max-jobs 30
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv

import act
import db
import notify
import observe
import pacing
import upwork_driver as ud  # reuse parse_feed_cards / collect_panel_info / capture_url
# Note: upwork_driver's module-level UTF-8 stdout wrapper applies once we
# import it, so we don't need our own.

WINDOW = "Upwork"
TARGET_WINDOWS = (
    "Upwork",
    "Best Matches",
    "Most Recent",
    "Saved Jobs",
    "Find Work",
    "Search results",
    "Jobs",
    "Just a moment",
    "Google Chrome",
)

SEARCH_URL_BASE = "https://www.upwork.com/nx/search/jobs/"
QUERIES_FILE = Path("research_queries.txt")

# Filter keys we accept. Anything else in the query line is rejected at parse
# time so typos don't silently produce a malformed URL.
ALLOWED_FILTER_KEYS = frozenset({
    "payment_verified",  # =1
    "t",                  # =0 (Hourly), =1 (Fixed-Price)
    "hourly_rate",        # =min-max, e.g. 25-35 or 50-
    "amount",             # =min-max for fixed-price, e.g. 500-999
    "proposals",          # =0-4, 5-9, 10-14, 15-19, 20-49
    "duration_v3",        # =week|month|semester|ongoing
})


def log(msg: str) -> None:
    print(f"[research] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Query specs: parse + URL build
# ---------------------------------------------------------------------------

@dataclass
class QuerySpec:
    """A search query plus optional filter map. Filters get appended to the
    URL as query params; the source string in the DB encodes them too so
    time-series analytics can distinguish narrowing buckets.
    """
    query: str
    filters: dict[str, str] = field(default_factory=dict)

    @property
    def source(self) -> str:
        """Stable, sorted-by-key source string for DB attribution."""
        if not self.filters:
            return f"search:{self.query}"
        kv = ",".join(f"{k}={self.filters[k]}" for k in sorted(self.filters))
        return f"search:{self.query}|{kv}"

    @property
    def url(self) -> str:
        """Build the Upwork search URL with all filters applied. Spaces in
        the query are encoded as %20 to match Upwork's own URLs."""
        params = ["from_recent_search=true", f"q={quote(self.query, safe='')}", "sort=relevance%2Bdesc"]
        for k in sorted(self.filters):
            params.append(f"{k}={quote(self.filters[k], safe='-')}")
        return f"{SEARCH_URL_BASE}?{'&'.join(params)}"


def parse_query_line(line: str) -> QuerySpec:
    """Parse one line of research_queries.txt.

    Forms:
        "LLM engineer"
        "ai agent developer | payment_verified=1, t=0, hourly_rate=25-35"

    Raises ValueError on unknown filter keys or malformed key=value pairs."""
    line = line.strip()
    if "|" not in line:
        return QuerySpec(query=line)
    query_part, filters_part = line.split("|", 1)
    query = query_part.strip()
    filters: dict[str, str] = {}
    for raw in filters_part.split(","):
        raw = raw.strip()
        if not raw:
            continue
        if "=" not in raw:
            raise ValueError(f"Malformed filter (no '='): {raw!r}")
        k, v = raw.split("=", 1)
        k = k.strip()
        v = v.strip()
        if k not in ALLOWED_FILTER_KEYS:
            raise ValueError(
                f"Unknown filter key {k!r}. Allowed: {sorted(ALLOWED_FILTER_KEYS)}"
            )
        filters[k] = v
    return QuerySpec(query=query, filters=filters)


# ---------------------------------------------------------------------------
# Query loading
# ---------------------------------------------------------------------------

def load_queries(path: Path = QUERIES_FILE) -> list[QuerySpec]:
    """Read the queries file. One spec per line. Blank lines and `#` comments
    ignored. Lines that fail to parse are logged and skipped (don't crash the
    crawler over a single bad line)."""
    if not path.exists():
        return []
    out: list[QuerySpec] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        try:
            out.append(parse_query_line(s))
        except ValueError as ex:
            log(f"  WARN: line {lineno} in {path.name}: {ex}; skipping")
    return out


# ---------------------------------------------------------------------------
# Search-results loaded check
# ---------------------------------------------------------------------------

def _wait_for_results(timeout: float = 30.0) -> bool:
    """Poll until the results list renders. Anchor: the feed's 'Posted' text
    label appears (which indicates job cards have rendered). Generous timeout
    to absorb Cloudflare challenges on direct URL navigation."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            obs = observe.observe(window_title=TARGET_WINDOWS, include_unnamed=False, include_text=True)
        except Exception:
            time.sleep(1.0)
            continue
        for e in obs.elements:
            if e.role == "text" and (e.name or "").strip() == "Posted":
                return True
        time.sleep(1.0)
    return False


# ---------------------------------------------------------------------------
# Per-card scrape (reuses scanner panel logic)
# ---------------------------------------------------------------------------

def _scrape_one_card(conn, source: str, info_from_feed: ud.JobInfo, title_el) -> str | None:
    """Open the panel for one card, collect panel data, capture URL, upsert + record scrape event.
    Returns the job_id on success, or None on failure (and logs why)."""
    act.click(title_el)
    time.sleep(3.0)

    try:
        info = ud.collect_panel_info(WINDOW, expected_title=info_from_feed.title)
    except Exception as ex:
        log(f"    ERROR reading panel: {ex}")
        try:
            act.key("escape")
        except Exception:
            pass
        time.sleep(0.5)
        return None

    if len(info.description) < 80 and not info.budget:
        log("    panel didn't render usable data — skipping")
        try:
            act.key("escape")
        except Exception:
            pass
        time.sleep(0.5)
        return None

    url = ud.capture_url_via_clipboard(WINDOW)
    try:
        act.key("escape")
    except Exception:
        pass
    time.sleep(0.5)

    if not url:
        log("    no URL captured for this card")
        return None

    try:
        job_id = db.extract_job_id(url)
    except ValueError:
        log(f"    malformed URL: {url!r}")
        return None

    job_data = {
        "url": url,
        "title": info.title,
        "description": info.description,
        "budget": info.budget,
        "client_summary": info.client_summary,
        "skills": info.tags or [],
        "posted_at_text": info.posted,
    }
    is_new = db.upsert_job(conn, job_data, source=source)
    db.record_scrape_event(
        conn,
        job_id=job_id,
        source=source,
        posted_at_text=info.posted,
        raw_panel_json=json.dumps({
            "title": info.title,
            "url": info.url,
            "posted": info.posted,
            "budget": info.budget,
            "tags": info.tags,
            "description": info.description,
            "client_summary": info.client_summary,
        }, ensure_ascii=False),
    )
    log(f"    {'NEW' if is_new else 'UPDATE'}: {info.title[:80]}")
    return job_id


def crawl_query(conn, spec: QuerySpec, max_jobs: int) -> dict:
    """Run one query spec end-to-end. Returns a stats dict."""
    log(f"=== crawling: {spec.source} ===")
    stats = {"query": spec.query, "source": spec.source, "scraped": 0, "new": 0, "updated": 0, "failed": 0}

    # Navigate directly to the constructed search URL — no UI button-clicking
    url = spec.url
    log(f"  navigating: {url}")
    act.navigate(url)
    time.sleep(3.0)

    if not _wait_for_results(timeout=30.0):
        log("  results did not render within 30s; skipping query")
        return stats

    # Scroll to top to make the scan deterministic
    act.key("ctrl+home")
    time.sleep(1.0)

    seen_titles: set[str] = set()
    no_progress_iters = 0

    while stats["scraped"] < max_jobs:
        try:
            cards = ud.parse_feed_cards(WINDOW)
        except Exception as ex:
            log(f"  parse_feed_cards error: {ex}")
            break
        new_cards = [(i, e) for i, e in cards if i.title not in seen_titles]
        log(f"  -- view: {len(cards)} cards in tree, {len(new_cards)} new --")
        if not new_cards:
            no_progress_iters += 1
            if no_progress_iters >= 2:
                log("  no new cards after 2 iterations — end of results")
                break
            act.scroll(3, method="wheel")
            time.sleep(1.0)
            continue
        no_progress_iters = 0

        for info_from_feed, title_el in new_cards:
            if stats["scraped"] >= max_jobs:
                break
            seen_titles.add(info_from_feed.title)

            log(f"  [{stats['scraped'] + 1}/{max_jobs}] {info_from_feed.title[:80]}")
            try:
                # Was this job already in the DB before we scraped?
                # We'll know after upsert returns is_new — but we want to count
                # before vs after, so do a pre-check.
                tentative_url = ""
                # _scrape_one_card does the upsert; track count via DB lookup
                # but simpler: have it return job_id and re-check is_new through the
                # last_scraped_at == discovered_at trick.
                job_id = _scrape_one_card(conn, spec.source, info_from_feed, title_el)
            except (act.FocusLost, observe.WaitTimeout) as ex:
                log(f"    recoverable error: {type(ex).__name__}: {ex}")
                try:
                    act.key("escape")
                except Exception:
                    pass
                stats["failed"] += 1
                continue

            if job_id is None:
                stats["failed"] += 1
                continue
            stats["scraped"] += 1

            # Determine new vs update by comparing discovered_at vs last_scraped_at
            row = conn.execute(
                "SELECT discovered_at, last_scraped_at FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row and row["discovered_at"] == row["last_scraped_at"]:
                stats["new"] += 1
            else:
                stats["updated"] += 1

            # Pacing budget check
            s = pacing.get_pacer().stats()
            if s["actions_last_hour"] >= 0.85 * s["budget"]:
                log(f"  pacing budget at 85% — stopping query")
                return stats

        # Scroll for the next iteration
        if stats["scraped"] < max_jobs:
            act.scroll(3, method="wheel")
            time.sleep(0.9)

    return stats


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", help="Single query to crawl. If omitted, reads research_queries.txt.")
    ap.add_argument("--max-jobs", type=int, default=20, help="Max jobs to scrape per query.")
    ap.add_argument("--actions-per-hour", type=int, default=80,
                    help="Pacing budget. Crawler is read-only so a higher cap is fine.")
    args = ap.parse_args()

    load_dotenv()
    pacing.configure(pacing.PacingConfig(max_actions_per_hour=args.actions_per_hour))
    act.set_target_window(TARGET_WINDOWS)
    conn = db.connect()
    db.init_schema(conn)

    if args.query:
        try:
            specs = [parse_query_line(args.query)]
        except ValueError as ex:
            log(f"--query is malformed: {ex}")
            sys.exit(2)
    else:
        specs = load_queries()
        if not specs:
            log(f"No --query supplied and {QUERIES_FILE} is empty/missing/has no valid lines. Exiting.")
            sys.exit(0)

    log(f"Will crawl {len(specs)} query/queries:")
    for s in specs:
        log(f"  - {s.source}")

    # Make sure Chrome is focused
    focused = False
    for title in TARGET_WINDOWS:
        if act.focus_window(title):
            focused = True
            break
    if not focused:
        log("Could not focus a Chrome window. Aborting.")
        sys.exit(1)

    overall = {"queries": 0, "scraped": 0, "new": 0, "updated": 0, "failed": 0}
    per_query: list[dict] = []
    for spec in specs:
        stats = crawl_query(conn, spec, args.max_jobs)
        per_query.append(stats)
        overall["queries"] += 1
        overall["scraped"] += stats["scraped"]
        overall["new"] += stats["new"]
        overall["updated"] += stats["updated"]
        overall["failed"] += stats["failed"]

    summary = (
        f"**Research crawl complete** — {overall['queries']} queries\n"
        f"Scraped {overall['scraped']} | New: {overall['new']} | Updated: {overall['updated']} | Failed: {overall['failed']}\n"
        + "\n".join(
            f"  • {s['source']}: scraped={s['scraped']}, new={s['new']}, updated={s['updated']}, failed={s['failed']}"
            for s in per_query
        )
    )
    log(summary.replace("\n", " | "))
    try:
        notify._post(summary)  # reuse existing internal post helper
    except Exception as ex:
        log(f"  WARN: Discord notify failed: {ex}")

    conn.close()


if __name__ == "__main__":
    main()
