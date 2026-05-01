"""
Upwork search crawler — market intelligence corpus builder.

Drives Upwork's search box for one or more queries, scrapes the top N results
from each search (panel-opened for ground-truth data), and writes everything
into upwork.db. Does NOT judge relevance, generate proposals, or alert per
job — it just builds the corpus that strategies will be derived from later.

Usage:
    uv run upwork_research.py --query "LLM engineer"
    uv run upwork_research.py --query "AI engineer" --max-jobs 30
    uv run upwork_research.py                          # reads research_queries.txt
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
from pathlib import Path

# UTF-8 console output (only apply when running as a script, not when imported)
if __name__ == "__main__":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

import pyperclip
from dotenv import load_dotenv

import act
import db
import notify
import observe
import pacing
import upwork_driver as ud  # reuse parse_feed_cards / collect_panel_info / capture_url

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

FIND_WORK_URL = "https://www.upwork.com/nx/find-work/best-matches"
QUERIES_FILE = Path("research_queries.txt")


def log(msg: str) -> None:
    print(f"[research] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Query loading
# ---------------------------------------------------------------------------

def load_queries(path: Path = QUERIES_FILE) -> list[str]:
    """Read the queries file. One per line. Blank lines and `#` comments ignored."""
    if not path.exists():
        return []
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


# ---------------------------------------------------------------------------
# Search input
# ---------------------------------------------------------------------------

def _find_search_input():
    """Find the search-for-jobs input on the find-work page."""
    obs = observe.observe(window_title=TARGET_WINDOWS, include_unnamed=True, include_text=True)
    # The search input is an `edit` whose name contains "Search for jobs".
    for e in obs.elements:
        if e.role != "edit":
            continue
        n = (e.name or "").strip().lower()
        if "search for jobs" in n:
            return e
    return None


def _wait_for_results(timeout: float = 20.0) -> bool:
    """Poll until the results list renders. Anchor: the feed's 'Posted' text
    label appears (which indicates job cards have rendered)."""
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


def perform_search(query: str) -> bool:
    """Type `query` into the search input and submit. Returns True if results
    rendered, False on timeout."""
    log(f"  performing search: {query!r}")
    el = _find_search_input()
    if el is None:
        log("  WARN: could not find 'Search for jobs' input on the page")
        return False
    log(f"  search input bounds={el.bounds}")
    act.click(el)
    time.sleep(0.4)
    # Clear any existing text (Ctrl+A, then paste replaces selection)
    act.key("ctrl+a")
    time.sleep(0.15)
    pyperclip.copy(query)
    time.sleep(0.15)
    act.key("ctrl+v")
    time.sleep(0.3)
    act.key("enter")
    log("  query submitted, waiting for results...")
    return _wait_for_results(timeout=20.0)


# ---------------------------------------------------------------------------
# Per-card scrape (reuses scanner panel logic)
# ---------------------------------------------------------------------------

def _scrape_one_card(conn, query: str, info_from_feed: ud.JobInfo, title_el) -> str | None:
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

    source = f"search:{query}"
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


def crawl_query(conn, query: str, max_jobs: int) -> dict:
    """Run one search query end-to-end. Returns a stats dict."""
    log(f"=== crawling query: {query!r} ===")
    stats = {"query": query, "scraped": 0, "new": 0, "updated": 0, "failed": 0}

    # Navigate to find-work
    log(f"  navigating to {FIND_WORK_URL}")
    act.navigate(FIND_WORK_URL)
    time.sleep(5.0)  # generous initial render

    if not perform_search(query):
        log("  results did not render; skipping query")
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
                job_id = _scrape_one_card(conn, query, info_from_feed, title_el)
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
        queries = [args.query]
    else:
        queries = load_queries()
        if not queries:
            log(f"No --query supplied and {QUERIES_FILE} is empty/missing. Exiting.")
            sys.exit(0)

    log(f"Will crawl {len(queries)} query/queries: {queries}")

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
    for q in queries:
        stats = crawl_query(conn, q, args.max_jobs)
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
            f"  • {s['query']}: scraped={s['scraped']}, new={s['new']}, updated={s['updated']}, failed={s['failed']}"
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
