"""One scan-cycle of the feed.

Outer-loop structure mirrors the legacy upwork_driver.main scan loop verbatim:
parse-visible-cards -> process-each-card-in-view -> wheel-scroll for more.
Title elements are clicked immediately within the same outer iteration that
observed them, because elements lose validity once scrolled out of view.
"""
from __future__ import annotations

import time

from substrate import act
from upwork import feed, panel, clipboard_url
from storage.jobs import JobStore
from storage.setups import SetupStore, SignalStore
from storage.orders import OrderStore
from storage.enrichments import EnrichmentStore
from storage.portfolio import PortfolioStore
from storage.agent_runs import AgentRunStore
from storage.scrape_runs import ScrapeRunStore
from domain.humanization import Humanizer, CycleType
from bidder.signal_pipeline import process_job_through_setups
from bidder.draft_pipeline import draft_order


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
    on_signal,
) -> None:
    cycle_type = humanizer.sample_cycle_type()
    run_id = scrape_runs.start(source=f"feed:{cycle_type.value}")

    if cycle_type == CycleType.NO_OP:
        scrape_runs.finish(run_id, notes="no-op cycle")
        return

    feed.refresh_feed(WINDOW)
    feed.click_most_recent_tab(WINDOW)

    # Scroll to top so the scan is deterministic.
    act.focus_window(WINDOW)
    act.key("ctrl+home")
    time.sleep(1.0)

    if cycle_type == CycleType.SKIM_ONLY:
        # Skim cycles: glance at the feed, count visible cards, do nothing else.
        cards = feed._parse_visible_cards(WINDOW)
        scrape_runs.update_counts(run_id, jobs_seen=len(cards), jobs_new=0, jobs_signaled=0)
        scrape_runs.finish(run_id, notes="skim only")
        return

    max_jobs = 2 if cycle_type == CycleType.PANEL_SKIM else 10
    setups = setups_store.list_active()

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
            act.scroll(3, method="wheel")
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

                # 2. Read the panel (multi-observe + page-down + merge).
                print("[scan]   step 2/4: capture_panel (multi-observe)", flush=True)
                try:
                    elements = panel.capture_panel(WINDOW)
                except Exception as ex:
                    print(f"[scan]   capture_panel failed: {ex!r}", flush=True)
                    elements = []
                    act.focus_window(WINDOW)
                    act.key("escape")
                    time.sleep(0.5)
                    continue

                # 3. Capture URL from the open panel.
                print("[scan]   step 3/4: capture URL via clipboard", flush=True)
                url = clipboard_url.capture_url_from_open_panel(WINDOW)
                print(f"[scan]   url={url!r}", flush=True)

                # 4. Close the panel before any LLM/IO work.
                print("[scan]   step 4/4: close panel (Esc)", flush=True)
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

                parsed = panel.parse_panel(elements)
                if title:
                    parsed.title = title
                job = _to_job(job_id, url, parsed)
                job_store.upsert(job, source="feed", raw_panel={})
                print(f"[scan]   persisted job: title={job.title[:60]!r} budget={job.budget_kind}/{job.budget_min_usd} skills={len(job.skills)}", flush=True)

                if cycle_type == CycleType.PANEL_SKIM:
                    continue

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
                    agent_run_store=agent_runs,
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
            print("[scan] wheel-scroll for more cards", flush=True)
            act.focus_window(WINDOW)
            act.scroll(3, method="wheel")
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


def _to_job(job_id: str, url: str, parsed) -> "Job":
    from domain.types import Job
    return Job(
        job_id=job_id,
        url=url,
        title=parsed.title,
        description=parsed.description,
        budget_kind=parsed.budget_kind,
        budget_min_usd=parsed.budget_min_usd,
        budget_max_usd=parsed.budget_max_usd,
        skills=parsed.skills,
        client_country=parsed.client_country,
        client_payment_verified=parsed.client_payment_verified,
        client_rating=parsed.client_rating,
        client_hires=parsed.client_hires,
        client_total_spent_usd=parsed.client_total_spent_usd,
        proposals_count_at_first_scrape=parsed.proposals_count,
    )
