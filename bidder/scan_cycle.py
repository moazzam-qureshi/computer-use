"""One scan-cycle of the feed."""
from __future__ import annotations

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
    cards = feed.top_of_feed_cards(WINDOW, max_cards=10)

    if cycle_type == CycleType.SKIM_ONLY:
        scrape_runs.update_counts(run_id, jobs_seen=len(cards), jobs_new=0, jobs_signaled=0)
        scrape_runs.finish(run_id, notes="skim only")
        return

    n_panels = 2 if cycle_type == CycleType.PANEL_SKIM else len(cards)
    setups = setups_store.list_active()

    seen, new, signaled = 0, 0, 0
    for card in cards[:n_panels]:
        seen += 1
        if not feed.open_card_panel(card):
            continue
        try:
            elements = panel_observe_elements(WINDOW)
            url = clipboard_url.capture_url_from_open_panel(WINDOW)
        finally:
            feed.close_panel(WINDOW)
        if url is None:
            continue
        job_id = _job_id_from_url(url)
        if job_store.is_known(job_id):
            continue
        new += 1
        parsed = panel.parse_panel(elements)
        job = _to_job(job_id, url, parsed)
        job_store.upsert(job, source="feed", raw_panel={})

        if cycle_type == CycleType.PANEL_SKIM:
            continue

        result = process_job_through_setups(
            job, setups=setups, enrichment_store=enrichment_store,
            signal_store=signal_store, order_store=order_store, agent_run_store=agent_runs,
        )
        if result is None:
            continue
        signal, order = result
        order = draft_order(
            job, order, portfolio=portfolio, order_store=order_store, agent_run_store=agent_runs,
        )
        signaled += 1
        on_signal(signal, order, job)

    scrape_runs.update_counts(run_id, jobs_seen=seen, jobs_new=new, jobs_signaled=signaled)
    scrape_runs.finish(run_id, notes=cycle_type.value)


def panel_observe_elements(window_title: str):
    from substrate import observe
    return observe.observe(window_title=window_title, include_unnamed=True).elements


def _job_id_from_url(url: str) -> str:
    import re
    m = re.search(r"_~([\w]+)", url)
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
