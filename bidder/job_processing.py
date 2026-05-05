"""Phase 2.B Pass-2 worker: process one claimed feed_card_queue row.

The flow mirrors bidder/scan_cycle.py:run_one_cycle's per-card body, but
takes a queued title (not a setup list) and uses the active goal as the
relevance gate. URL is captured by clicking the live element ref from a
fresh UIA walk, then walking the panel for the Copy-to-clipboard button.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Optional

import pyautogui

from substrate import act
from upwork import feed, panel, clipboard_url, feed_zoom
from upwork.apply_form import detect_login_required
from storage.card_queue import FeedCardQueueStore, QueueItem
from storage.jobs import JobStore
from storage.orders import OrderStore
from storage.enrichments import EnrichmentStore
from storage.portfolio import PortfolioStore
from storage.agent_runs import AgentRunStore
from storage.setups import SetupStore, SignalStore
from storage.goals import Goal
from domain.types import Job, Signal, Order
from bidder.draft_pipeline import draft_order
from ai.panel_extract import extract_panel
from ai.relevance_goal import check_goal_relevance
from ai.enrichment import enrich_job
from scheduler.failure_pings import LoginExpired


WINDOW = "Upwork"


class CardNotVisible(Exception):
    """Pass 2 walked the feed at 33% zoom but the queued title wasn't in
    the visible cards. Card may have scrolled off; caller should requeue
    (or eventually skip after N attempts)."""


class PanelCaptureFailed(Exception):
    """capture_panel returned 0 elements after one retry."""


def _job_id_from_url(url: str) -> str:
    import re
    m = re.search(r"~([0-9a-zA-Z]{10,})", url)
    return m.group(1) if m else url


def _to_job(job_id: str, url: str, extracted, fallback_title: str) -> Job:
    title = extracted.title or fallback_title or ""
    return Job(
        job_id=job_id,
        url=url,
        title=title,
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


def _do_substrate_recipe() -> None:
    """Refresh feed + Ctrl+Home + Ctrl+- ×6 (33% zoom). Caller manages focus
    via the surrounding ui_lock."""
    feed.refresh_feed(WINDOW)
    if detect_login_required():
        raise LoginExpired("Upwork session expired; need to log in via Chrome")
    act.focus_window(WINDOW)
    act.key("ctrl+home")
    time.sleep(0.6)
    feed_zoom.zoom_to_33pct()


def _capture_panel_with_retry() -> tuple[list, str]:
    """capture_panel + one 1.5s retry on 0-element response. Raises
    PanelCaptureFailed if both attempts return empty."""
    elements, url = panel.capture_panel(WINDOW)
    if not elements:
        print("[processing] capture_panel returned 0 elements; retrying after 1.5s sleep", flush=True)
        time.sleep(1.5)
        elements, url = panel.capture_panel(WINDOW)
        if not elements:
            raise PanelCaptureFailed("capture_panel returned 0 elements after retry")
    if not url:
        # Fallback: legacy capture if Copy didn't surface during the panel walk.
        url = clipboard_url.capture_url_from_open_panel(WINDOW) or ""
    return elements, url


def process_queued_card(
    queue_row: QueueItem,
    goal_at_detect: Goal,
    *,
    setups_store: SetupStore,
    signal_store: SignalStore,
    job_store: JobStore,
    order_store: OrderStore,
    enrichment_store: EnrichmentStore,
    portfolio: PortfolioStore,
    agent_runs: AgentRunStore,
) -> tuple[Optional[Signal], Optional[Order], Optional[Job], str]:
    """Pass-2 worker for a single claimed queue row.

    Returns (signal, order, job, status) where status is one of:
      'drafted'     — relevance passed and an order was drafted; (signal, order, job) all set
      'skipped'     — relevance rejected; job persisted
      'not_visible' — title not in current viewport; caller should requeue
    Raises:
      LoginExpired       — caller pings the operator
      PanelCaptureFailed — caller marks the queue row failed
      Other exceptions   — caller marks the queue row failed
    """
    print(f"[processing] start queue_id={queue_row.queue_id} title={queue_row.job_title[:80]!r}", flush=True)

    # 1. Substrate recipe -> fresh walk with live element refs at 33% zoom.
    _do_substrate_recipe()
    try:
        visible = feed._parse_visible_cards(WINDOW)
        print(f"[processing] visible cards in feed: {len(visible)}", flush=True)

        # 2. Find the queued title. Use a forgiving prefix match because the
        #    Save-job button name may have stripped/reshaped the title relative
        #    to the title-hyperlink's name. Prefer exact; fall back to prefix.
        target_title = queue_row.job_title
        target_title_lower = target_title.strip().lower()
        link_el = None
        matched_title = None
        for ftitle, fel in visible:
            if ftitle.strip().lower() == target_title_lower:
                link_el = fel
                matched_title = ftitle
                break
        if link_el is None:
            for ftitle, fel in visible:
                fl = ftitle.strip().lower()
                # Either side may be the canonicalized prefix of the other.
                if (fl and target_title_lower.startswith(fl[:30])) or \
                   (target_title_lower and fl.startswith(target_title_lower[:30])):
                    link_el = fel
                    matched_title = ftitle
                    break
        if link_el is None:
            print(f"[processing] title not visible in current viewport; will requeue", flush=True)
            return None, None, None, "not_visible"

        # 3. Click the live ref. Bounds gating mirrors run_one_cycle: skip
        #    cards too low on the screen (taskbar zone). At 33% zoom the
        #    bounds shrink dramatically, so this rarely fires, but defensive.
        bounds = link_el.bounds
        mid_y = (bounds[1] + bounds[3]) // 2
        if mid_y > 1100:
            print(f"[processing] click target y={mid_y} below safe zone; treating as not_visible", flush=True)
            return None, None, None, "not_visible"

        print(f"[processing] click title -> open panel ({matched_title!r})", flush=True)
        act.focus_window(WINDOW)
        act.click(link_el)
        time.sleep(3.0)
    finally:
        # Reset zoom now so the panel walk runs at 100%, matching the legacy
        # capture_panel behavior. The panel scroll heuristics are tuned for
        # standard zoom; running them at 33% is untested terrain.
        try:
            act.focus_window(WINDOW)
            feed_zoom.reset_zoom()
        except Exception:
            pass

    # 4. Walk panel + capture URL via Copy-to-clipboard button.
    try:
        elements, url = _capture_panel_with_retry()
    finally:
        # Always close the panel, regardless of capture outcome.
        try:
            act.focus_window(WINDOW)
            act.key("escape")
            time.sleep(0.5)
        except Exception:
            pass

    if not url:
        raise PanelCaptureFailed("URL not captured (Copy button didn't surface)")
    print(f"[processing] url captured: {url}", flush=True)

    # 5. Extract structured fields via existing LLM panel-extract.
    job_id = _job_id_from_url(url)
    extracted = extract_panel(elements, job_id=job_id, agent_run_store=agent_runs)
    job = _to_job(job_id, url, extracted, fallback_title=matched_title or queue_row.job_title)
    job_store.upsert(job, source="feed", raw_panel={})
    print(
        f"[processing] persisted job: title={job.title[:60]!r} "
        f"budget={job.budget_kind}/{job.budget_min_usd}-{job.budget_max_usd} "
        f"skills={len(job.skills)} country={job.client_country}",
        flush=True,
    )

    # 6. Always enrich (corpus value), only if not yet enriched.
    if enrichment_store.get(job.job_id) is None:
        enrichment = enrich_job(job, agent_run_store=agent_runs)
        enrichment_store.upsert(job.job_id, enrichment)

    # 7. Goal-driven relevance gate.
    relevance = check_goal_relevance(job, goal_at_detect, agent_run_store=agent_runs)
    print(
        f"[processing] relevance: relevant={relevance.relevant} "
        f"score={relevance.score:.2f} reason={relevance.reasoning[:120]!r}",
        flush=True,
    )
    if not relevance.relevant:
        return None, None, job, f"skipped: {relevance.reasoning[:200]}"

    # 8. Synthesize a goal-driven setup row so signals/orders FK constraints
    #    are satisfied. Status='retired' so the legacy list_active() path
    #    never picks this up. One row per goal (idempotent on name).
    setup_id = _ensure_goal_setup(setups_store, goal_at_detect)

    # 9. Write Signal.
    signal = Signal(
        signal_id=None,
        job_id=job.job_id,
        primary_setup_id=setup_id,
        matched_setups=[{
            "setup_id": setup_id,
            "match_reason": "goal+llm",
            "matched_rules": ["goal:relevant"],
            "application_flags": relevance.application_flags or [],
        }],
        fired_at=datetime.now(timezone.utc),
        market_state={
            "source": "goal_detection",
            "goal_id": goal_at_detect.goal_id,
            "queue_id": queue_row.queue_id,
            "triage_reasoning": queue_row.triage_reasoning,
            "proposals_count": job.proposals_count_at_first_scrape,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    signal_id = signal_store.create(signal)
    signal.signal_id = signal_id

    # 10. Create draft order, then draft it (Doc + cover letter).
    order = Order(
        order_id=None,
        signal_id=signal_id,
        job_id=job.job_id,
        setup_id=setup_id,
        status="drafting",
        bid_amount_usd=None,
        connects_spent=None,
        cover_letter_body=None,
        doc_url=None,
        screening_answers_json=None,
        drafted_at=None,
        approved_at=None,
        submitted_at=None,
        failed_reason=None,
        idempotency_key=str(uuid.uuid4()),
    )
    order_id = order_store.create_draft(order)
    order.order_id = order_id

    print("[processing] DRAFTING Doc + cover letter", flush=True)
    order = draft_order(
        job, order, portfolio=portfolio, order_store=order_store,
        agent_run_store=agent_runs, setups_store=setups_store,
    )
    return signal, order, job, "drafted"


def _ensure_goal_setup(setups_store: SetupStore, goal: Goal) -> int:
    """Return the setup_id for a synthetic 'goal:<goal_id>' setup, creating it
    if necessary. Phase 2.B routes detection-driven orders through this row
    so signals/orders FK constraints stay satisfied while detection no longer
    iterates over real setups. Status='retired' keeps it out of list_active.
    """
    from domain.types import Setup, FilterDsl
    name = f"goal:{goal.goal_id}"
    # Cheap lookup: scan the table by name. The set of synthetic setups stays
    # tiny (one per active goal ever set), so a full scan is fine.
    with setups_store._db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT setup_id FROM setups WHERE name = %s", (name,))
            row = cur.fetchone()
    if row is not None:
        return row[0]
    ephemeral = Setup(
        setup_id=0, name=name, status="retired", tier="normal",
        filter_dsl=FilterDsl({"all_of": []}),
        prose_definition=goal.prose,
        pitch_template_id=None, cover_letter_template_id=None,
        auto_apply_enabled=False, escalation_config={},
    )
    return setups_store.create(ephemeral)
