"""One scan-cycle of the feed.

Outer-loop structure mirrors the legacy upwork_driver.main scan loop verbatim:
parse-visible-cards -> process-each-card-in-view -> wheel-scroll for more.
Title elements are clicked immediately within the same outer iteration that
observed them, because elements lose validity once scrolled out of view.
"""
from __future__ import annotations

import time

from substrate import act, observe
from upwork import feed, panel, clipboard_url
from upwork.apply_form import detect_login_required
from storage.jobs import JobStore
from storage.setups import SetupStore, SignalStore
from storage.orders import OrderStore
from storage.enrichments import EnrichmentStore
from storage.portfolio import PortfolioStore
from storage.agent_runs import AgentRunStore
from storage.scrape_runs import ScrapeRunStore
from storage.conversations import SystemConfigStore
from domain.humanization import Humanizer, CycleType
from bidder.signal_pipeline import process_job_through_setups
from bidder.draft_pipeline import draft_order
from scheduler.failure_pings import LoginExpired
from ai.panel_extract import extract_panel


WINDOW = "Upwork"


def run_one_cycle(
    *,
    humanizer: Humanizer,
    setups_store: SetupStore,
    signal_store: SignalStore,
    job_store: JobStore,
    order_store: OrderStore,
    enrichment_store: EnrichmentStore,
    portfolio: PortfolioStore,
    agent_runs: AgentRunStore,
    scrape_runs: ScrapeRunStore,
    sysconfig: SystemConfigStore,
    on_signal,
) -> None:
    if bool(sysconfig.get("bidder_paused") or False):
        print("[scan] bidder paused via system_config; skipping cycle", flush=True)
        return
    # Always full-scan. The humanizer's cycle-type mix (skim/no-op/panel-skim)
    # was an anti-detection nicety; in practice it costs real opportunities
    # and the operator prefers consistent coverage over behavioral camouflage.
    cycle_type = CycleType.FULL_SCAN
    run_id = scrape_runs.start(source=f"feed:{cycle_type.value}")
    print(f"[scan] cycle_type={cycle_type.value} run_id={run_id}", flush=True)

    feed.refresh_feed(WINDOW)
    feed.click_most_recent_tab(WINDOW)

    # If Upwork is asking us to log in, bail this cycle cleanly so the
    # bidder_loop's outer try/except can ping Discord. The user re-authenticates
    # in the open Chrome tab; the next cycle picks up where we left off.
    if detect_login_required():
        print("[scan] LOGIN REQUIRED detected; aborting cycle", flush=True)
        scrape_runs.finish(run_id, notes="login_required")
        raise LoginExpired("Upwork session expired; need to log in via Chrome")

    # Scroll to top so the scan is deterministic.
    act.focus_window(WINDOW)
    act.key("ctrl+home")
    time.sleep(1.0)

    max_jobs = 10
    setups = setups_store.list_active()

    # Pre-click dedup: pull all titles seen in the last 14 days. If a card's
    # title matches one of these, it's a job we already extracted in a prior
    # cycle, so skip it WITHOUT opening the panel. Saves ~30s of panel-walk
    # overhead per skipped card and keeps the cycle focused on truly new
    # listings instead of re-scanning the same top-of-feed jobs every cycle.
    known_titles_lower = job_store.known_titles_recent(days=14)
    print(f"[scan] dedup index loaded: {len(known_titles_lower)} known titles from last 14 days", flush=True)

    seen_titles: set[str] = set()
    seen, new, signaled = 0, 0, 0
    no_progress_iters = 0
    evaluated = 0

    while evaluated < max_jobs:
        cards = feed._parse_visible_cards(WINDOW)
        new_cards = [(t, el) for t, el in cards if t not in seen_titles]
        print(f"[scan] observation: {len(cards)} cards in view, {len(new_cards)} new", flush=True)

        if not new_cards:
            no_progress_iters += 1
            print(f"[scan] no new cards (iter {no_progress_iters}/2)", flush=True)
            if no_progress_iters >= 2:
                print("[scan] end of feed reached, stopping", flush=True)
                break
            act.focus_window(WINDOW)
            # Down-arrow keys land on the focused window regardless of
            # cursor position. Wheel scroll silently no-ops if the cursor
            # isn't over Chrome's content area, which happens on VMs and
            # multi-monitor setups after click_xy events leave the cursor
            # in a sidebar. ~8 arrow presses ≈ one card height.
            act.scroll(8, method="arrow")
            time.sleep(1.0)
            continue

        no_progress_iters = 0

        for title, title_el in new_cards:
            if evaluated >= max_jobs:
                break

            # Skip cards whose click target is in the taskbar zone — clicks at
            # the bottom of the screen escape Chrome and hit Search/system tray.
            # The next outer-loop iteration will wheel-scroll, lift this card up,
            # and re-observe it.
            mid_y = (title_el.bounds[1] + title_el.bounds[3]) // 2
            if mid_y > 1100:
                print(f"[scan]   skip-for-now: '{title[:60]}' y={mid_y} below safe click zone; will retry after scroll", flush=True)
                continue

            # PRE-CLICK DEDUP + EARLY EXIT.
            #
            # 'Most Recent' is sorted newest-first. The moment we see a card
            # we already have in the DB, every card after it is also already
            # known (by definition of the sort). So we end the cycle right
            # there instead of paying ~30s of panel walk per known card.
            #
            # Two ways to detect "known": URL (perfect, when Chrome exposes
            # the hyperlink's href via UIA ValuePattern) and title (95%+
            # accurate fallback against the last-14d titles index). Either
            # match triggers early-exit.
            link_value = (getattr(title_el, "value", None) or "").strip()
            preclick_job_id = _job_id_from_url(link_value) if "~" in link_value else ""
            if preclick_job_id and preclick_job_id != link_value and job_store.is_known(preclick_job_id):
                print(f"[scan] hit known card (URL match): '{title[:60]}' job_id={preclick_job_id}; ending cycle", flush=True)
                evaluated = max_jobs  # break the outer while loop too
                break
            if title.strip().lower() in known_titles_lower:
                print(f"[scan] hit known card (title match): '{title[:60]}'; ending cycle", flush=True)
                evaluated = max_jobs
                break

            seen_titles.add(title)
            evaluated += 1
            seen += 1
            print(f"[scan] [{evaluated}/{max_jobs}] {title[:80]}", flush=True)

            try:
                # 1. Open panel by clicking the title element.
                print("[scan]   step 1/4: click title -> open panel", flush=True)
                act.focus_window(WINDOW)
                act.click(title_el)
                time.sleep(3.0)

                # 2. Read the panel: arrow-down scroll all the way to panel-end,
                #    clicking Copy the moment it surfaces. URL capture happens
                #    inside capture_panel while bounds are live. We continue
                #    scrolling after URL capture to surface description / budget
                #    chips / skills / client trust signals below the Copy button.
                print("[scan]   step 2/3: capture_panel (full panel walk)", flush=True)
                try:
                    elements, url = panel.capture_panel(WINDOW)
                except Exception as ex:
                    print(f"[scan]   capture_panel failed: {ex!r}", flush=True)
                    elements, url = [], ""
                    act.focus_window(WINDOW)
                    act.key("escape")
                    time.sleep(0.5)
                    continue

                # If capture_panel didn't surface Copy at all (very short or
                # unusually-laid-out panels), fall back to the legacy entry
                # point that does its own scroll-and-find.
                if not url:
                    print("[scan]   capture_panel returned no URL; falling back to legacy capture_url_from_open_panel", flush=True)
                    url = clipboard_url.capture_url_from_open_panel(WINDOW) or ""
                print(f"[scan]   url={url!r}", flush=True)

                print("[scan]   step 3/3: close panel (Esc)", flush=True)
                act.focus_window(WINDOW)
                act.key("escape")
                time.sleep(0.5)

                if not url:
                    print("[scan]   skip: no URL captured", flush=True)
                    continue

                job_id = _job_id_from_url(url)
                if job_store.is_known(job_id):
                    print(f"[scan]   skip: already known job_id={job_id}", flush=True)
                    continue
                new += 1

                # LLM-based extraction: feeds the raw element-name dump to a
                # gpt-4o-mini call that returns a structured PanelExtraction.
                # No regex maintenance per Upwork layout change.
                extracted = extract_panel(elements, job_id=job_id, agent_run_store=agent_runs)
                if title:
                    extracted.title = title
                job = _to_job(job_id, url, extracted)
                job_store.upsert(job, source="feed", raw_panel={})
                # Add to in-memory dedup set so subsequent cards in this
                # cycle with the same title (rare but possible if Upwork
                # reorders the feed mid-scan) skip without re-extracting.
                if job.title:
                    known_titles_lower.add(job.title.strip().lower())
                print(f"[scan]   persisted job: title={job.title[:60]!r} budget={job.budget_kind}/{job.budget_min_usd}-{job.budget_max_usd} skills={len(job.skills)} posted={job.posted_text!r}", flush=True)

                print("[scan]   running setup match + enrichment + relevance", flush=True)
                result = process_job_through_setups(
                    job, setups=setups, enrichment_store=enrichment_store,
                    signal_store=signal_store, order_store=order_store,
                    agent_run_store=agent_runs,
                )
                if result is None:
                    print("[scan]   no setup matched", flush=True)
                    continue
                signal, order = result
                print("[scan]   SETUP MATCHED -> drafting Doc + cover letter", flush=True)
                order = draft_order(
                    job, order, portfolio=portfolio, order_store=order_store,
                    agent_run_store=agent_runs, setups_store=setups_store,
                )
                signaled += 1
                on_signal(signal, order, job)
            except Exception as ex:
                print(f"[scan]   ERROR: {type(ex).__name__}: {ex}", flush=True)
                try:
                    act.focus_window(WINDOW)
                    act.key("escape")
                    time.sleep(0.5)
                except Exception:
                    pass
                continue

        # After processing this batch, scroll for more cards.
        if evaluated < max_jobs:
            print("[scan] arrow-scroll for more cards", flush=True)
            act.focus_window(WINDOW)
            # See note above: arrow keys are reliable across host/VM/multi-mon
            # setups in a way that mouse-wheel-at-cursor isn't.
            act.scroll(8, method="arrow")
            time.sleep(0.9)

    scrape_runs.update_counts(run_id, jobs_seen=seen, jobs_new=new, jobs_signaled=signaled)
    scrape_runs.finish(run_id, notes=cycle_type.value)


def _job_id_from_url(url: str) -> str:
    """Extract Upwork job's permanent ID from any of the URL forms it ships in:

    - https://www.upwork.com/jobs/Some-Title_~012345abcdef/   (slug + id)
    - https://www.upwork.com/jobs/~022050585202817893756/    (id only, modern feed)
    - https://www.upwork.com/freelance-jobs/apply/~012345abcdef
    """
    import re
    m = re.search(r"~([0-9a-zA-Z]{10,})", url)
    return m.group(1) if m else url


def _to_job(job_id: str, url: str, extracted) -> "Job":
    """Map a PanelExtraction (or PanelData, for backwards-compat) onto a Job."""
    from domain.types import Job
    return Job(
        job_id=job_id,
        url=url,
        title=extracted.title or "",
        description=extracted.description,
        budget_kind=extracted.budget_kind,
        budget_min_usd=extracted.budget_min_usd,
        budget_max_usd=extracted.budget_max_usd,
        skills=extracted.skills or [],
        client_country=extracted.client_country,
        client_payment_verified=extracted.client_payment_verified,
        client_rating=extracted.client_rating,
        client_hires=extracted.client_hires,
        client_total_spent_usd=extracted.client_total_spent_usd,
        posted_text=extracted.posted_text,
        proposals_count_at_first_scrape=extracted.proposals_count,
    )
