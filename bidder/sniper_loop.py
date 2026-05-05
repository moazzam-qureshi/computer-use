"""Phase 2.B sniper loop — single coroutine that mirrors bin/debug_phase2b_e2e.py.

ARCHITECTURE (do not deviate from this — the diagnostic ran successfully with
this exact shape, deviations introduced cross-loop coordination bugs):

  Each cycle:
    Pass 1 — ONE substrate recipe (refresh + Ctrl+Home + Ctrl+- ×4)
             -> extract cards across 3 viewports with Down×21 between
             -> dedup against feed_card_queue history
             -> if any new cards: triage them in ONE LLM call
             -> matched_titles = survivors

    Pass 2 — ONE fresh substrate recipe (refresh + Ctrl+Home + Ctrl+- ×4)
             -> walk 3 viewports with Down×21 between
             -> in each viewport, find any visible card whose title is in
                matched_titles AND not yet clicked, click immediately using
                LIVE element ref from current walk
             -> capture_panel (sets zoom back to 100%) + parse_panel + Esc
             -> for each captured panel: extract -> goal-relevance -> draft
                -> Discord post

  Then sleep IDLE_BETWEEN_CYCLES_SECONDS (default 300s = 5 minutes).

  Briefs (Phase 2.A) keep priority: at the top of every iteration, before
  Pass 1 runs, if BriefStore has a pending brief, the legacy briefed_cycle
  path runs instead (run_one_cycle with the ephemeral retired setup), and
  the sniper cycle is skipped this iteration.

  ui_lock is held for the entire sniper cycle (Pass 1 + Pass 2 + drafting).
  No other loop can touch Chrome during. apply_executor_loop and the
  brief-watcher coordinate via the same lock.
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

import pyautogui

from substrate import act
from upwork import feed, panel as upwork_panel, feed_zoom, feed_cards
from upwork.apply_form import detect_login_required
from storage.connection import Database
from storage.goals import GoalStore, Goal
from storage.card_queue import FeedCardQueueStore
from storage.scan_briefs import BriefStore
from storage.jobs import JobStore
from storage.setups import SetupStore, SignalStore
from storage.orders import OrderStore
from storage.enrichments import EnrichmentStore
from storage.portfolio import PortfolioStore
from storage.agent_runs import AgentRunStore
from storage.scrape_runs import ScrapeRunStore
from storage.bidder_state import BidderStateStore
from storage.conversations import SystemConfigStore
from domain.types import Job, Signal, Order, Setup, FilterDsl
from bidder.scan_cycle import run_one_cycle
from bidder.draft_pipeline import draft_order
from ai.feed_triage import triage_feed_cards
from ai.relevance_goal import check_goal_relevance
from ai.enrichment import enrich_job
from ai.panel_extract import extract_panel
from bot.alerts import build_signal_embed, OrderApprovalView
from scheduler.failure_pings import LoginExpired


WINDOW = "Upwork"
DEFAULT_IDLE_SECONDS = 300        # 5-minute idle between drained cycles
PAUSE_SLEEP_SECONDS = 30          # how long to sleep when paused / no goal
DOWN_PRESSES_PER_VIEWPORT = 21    # matches diagnostic Pass A → Pass B → Pass C


def _with_com(fn, *args, **kwargs):
    import uiautomation as ua
    with ua.UIAutomationInitializerInThread():
        return fn(*args, **kwargs)


# ============================================================================
# Pass 1 — extract cards across 3 viewports + triage
# ============================================================================

def _do_substrate_recipe_setup() -> None:
    """Refresh + reset zoom + Ctrl+- ×6 (33%) + Ctrl+Home. Verbatim sequence
    from bin/debug_zoom_extreme.py:_try_zoom_level for the 33% test, which
    was the empirically validated configuration.

    At 33% zoom the entire feed (9-10 cards) is visible in a single walk —
    no Down×21 viewport-B/C dance required.

    Caller MUST hold the ui_lock for the entire pass. Caller MUST have already
    called act.set_target_window(("Upwork", "Google Chrome")).
    """
    # debug_zoom_extreme.py:_try_zoom_level sequence verbatim:
    #   1. focus_window
    #   2. Ctrl+0 reset to 100%  -> handled inside zoom_to_33pct
    #   3. Ctrl+Home              -> back to top of feed BEFORE zooming
    #   4. Ctrl+- ×N              -> handled inside zoom_to_33pct
    feed.refresh_feed(WINDOW)
    act.focus_window(WINDOW)
    # Reset zoom + Ctrl+Home BEFORE the Ctrl+- presses so the zoom-out
    # animation centres on the top of the feed.
    import pyautogui as _pa
    _pa.hotkey("ctrl", "0")
    time.sleep(0.4)
    act.focus_window(WINDOW)
    act.key("ctrl+home")
    time.sleep(0.5)
    # Now zoom out 6× to 33%. zoom_to_33pct also re-presses Ctrl+0 first,
    # which is redundant but harmless.
    feed_zoom.zoom_to_33pct()


def _pass1_extract_all_cards() -> list[feed_cards.FeedCard]:
    """Substrate setup + ONE walk at 33% zoom. Returns the parsed FeedCards.

    debug_zoom_extreme.py validated that at 33% zoom, a single UIA walk
    surfaces all visible feed cards — no viewport B/C needed.
    """
    _do_substrate_recipe_setup()
    cards = feed_cards.extract_cards_from_window(WINDOW)
    print(f"[sniper] Pass 1 walk extracted {len(cards)} cards at 33% zoom", flush=True)
    return cards


# ============================================================================
# Pass 2 — click matched titles in feed order using LIVE refs
# ============================================================================


class _CaptureResult:
    """One card's outcome from Pass 2 click + capture_panel."""
    __slots__ = ("title", "url", "elements", "panel_data", "error")

    def __init__(self, title: str, url: str = "", elements=None, panel_data=None, error: str = ""):
        self.title = title
        self.url = url
        self.elements = elements or []
        self.panel_data = panel_data
        self.error = error


def _click_and_capture_one(title: str, link_el) -> _CaptureResult:
    """Click a LIVE link element, capture panel, Esc. Mirrors
    bin/debug_phase2b_e2e.py:_click_and_capture_one verbatim.

    Element ref MUST come from the same UIA observation that this call
    is invoked within (no scrolls between observation and click).
    """
    bounds = link_el.bounds
    mid_y = (bounds[1] + bounds[3]) // 2
    # Match the diagnostic's hardcoded 1100 ceiling. Cards below this height
    # at 67% zoom are too close to the taskbar to click cleanly.
    if mid_y > 1100:
        return _CaptureResult(title, error=f"card y={mid_y} too low; would hit taskbar")

    print(f"[sniper] >>> clicking: {title[:80]!r}", flush=True)
    try:
        act.focus_window(WINDOW)
        act.click(link_el)
    except Exception as e:
        return _CaptureResult(title, error=f"click failed: {e!r}")

    # Initial post-click settle. The panel slides in from the right; on a
    # cold first click 2.5s is usually enough but a freshly-Esc'd-prior-
    # panel can take longer because the close animation is still settling.
    time.sleep(3.5)

    # Poll capture_panel until we get a non-empty result, or until we've
    # waited 8 extra seconds (3.5 + up to 8 = max ~11.5s before giving up).
    # capture_panel internally checks for the "Apply now" button as proof
    # that the panel is open. If the panel hasn't loaded yet, it returns
    # ([], "") immediately — that's a transient miss, not a permanent
    # failure. We retry with extra settle time.
    elements: list = []
    url: str = ""
    poll_deadline = time.time() + 8.0
    poll_attempt = 0
    while True:
        poll_attempt += 1
        try:
            elements, url = upwork_panel.capture_panel(WINDOW)
        except Exception as e:
            try:
                act.focus_window(WINDOW)
                act.key("escape")
                time.sleep(0.5)
            except Exception:
                pass
            return _CaptureResult(title, error=f"capture_panel failed: {e!r}")
        if elements:
            # Panel was open; capture_panel did its job. Use whatever it
            # returned (URL may or may not be present; that's a separate
            # concern handled below).
            if poll_attempt > 1:
                print(f"[sniper] panel opened on poll attempt {poll_attempt}", flush=True)
            break
        if time.time() >= poll_deadline:
            # Panel never opened within the budget. Treat as a clean miss
            # so the outer loop continues with the next match. No tab spam,
            # no cascading failure.
            print(
                f"[sniper] panel never opened for {title[:60]!r} "
                f"after {poll_attempt} polls / ~{8.0 + 3.5:.1f}s; skipping",
                flush=True,
            )
            return _CaptureResult(title, error="panel never opened post-click")
        # Wait a bit and retry. The panel-open animation typically completes
        # within 1-2s of click, but a busy page can be slower.
        time.sleep(1.5)

    panel_data = upwork_panel.parse_panel(elements)

    # Always close the panel before returning, win or lose. Use a generous
    # post-Esc settle so the next consecutive click (within the same Pass 2
    # batch) doesn't race the close animation.
    try:
        act.focus_window(WINDOW)
        act.key("escape")
        time.sleep(1.5)
    except Exception:
        pass

    if not url:
        return _CaptureResult(title, error="no URL captured (Copy button didn't surface)")

    return _CaptureResult(title, url=url, elements=elements, panel_data=panel_data)


def _process_visible_matches(
    matched_titles: set[str],
    already_clicked: set[str],
) -> list[_CaptureResult]:
    """Walk the current viewport; click any visible card whose title is in
    matched_titles and not yet clicked. Mirrors diagnostic verbatim."""
    visible = feed._parse_visible_cards(WINDOW)
    print(f"[sniper] visible in this viewport: {len(visible)}", flush=True)
    results: list[_CaptureResult] = []
    for title, link_el in visible:
        if title not in matched_titles or title in already_clicked:
            continue
        already_clicked.add(title)
        r = _click_and_capture_one(title, link_el)
        results.append(r)
    return results


def _pass2_click_all_matches(matched_titles: set[str]) -> list[_CaptureResult]:
    """Walk the SAME feed Pass 1 just left us on, clicking matched titles
    with LIVE element refs from this walk.

    Do NOT re-refresh the feed. Pass 1 already navigated to Most Recent at
    33% zoom and finished its walk; we just need a fresh observation to get
    element refs that haven't been invalidated. Re-running the substrate
    recipe (which opens a new tab via Ctrl+T) was the actual bug — it
    abandoned the panel-ready feed page and started over.
    """
    # Re-focus + Ctrl+Home so we're back at the top of the feed in case
    # post-Pass-1 cursor / focus drift moved us. Do NOT open a new tab.
    try:
        act.focus_window(WINDOW)
        act.key("ctrl+home")
        time.sleep(0.6)
    except Exception:
        pass

    already_clicked: set[str] = set()
    results = _process_visible_matches(matched_titles, already_clicked)

    # Reset zoom for next cycle's clean baseline.
    try:
        act.focus_window(WINDOW)
        feed_zoom.reset_zoom()
    except Exception:
        pass

    return results


# ============================================================================
# Per-result drafting pipeline
# ============================================================================


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


def _ensure_goal_setup(setups_store: SetupStore, goal: Goal) -> int:
    """Return setup_id for synthetic 'goal:<id>' setup, creating if needed."""
    name = f"goal:{goal.goal_id}"
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


def _draft_from_capture(
    capture: _CaptureResult,
    goal: Goal,
    triage_reasoning: str,
    queue_id: int,
    *,
    job_store: JobStore,
    setups_store: SetupStore,
    signal_store: SignalStore,
    order_store: OrderStore,
    enrichment_store: EnrichmentStore,
    portfolio: PortfolioStore,
    agent_runs: AgentRunStore,
) -> tuple[Optional[Signal], Optional[Order], Optional[Job], str]:
    """Take a successful _CaptureResult and run extract → relevance → draft.
    Returns (signal, order, job, status). Status is 'drafted' or 'skipped: <why>'."""
    job_id = _job_id_from_url(capture.url)
    extracted = extract_panel(capture.elements, job_id=job_id, agent_run_store=agent_runs)
    job = _to_job(job_id, capture.url, extracted, fallback_title=capture.title)
    job_store.upsert(job, source="feed", raw_panel={})
    print(
        f"[sniper] persisted job {job_id}: title={job.title[:60]!r} "
        f"budget={job.budget_kind}/{job.budget_min_usd}-{job.budget_max_usd} "
        f"country={job.client_country}",
        flush=True,
    )

    # Always enrich (corpus value), only if not yet enriched.
    if enrichment_store.get(job.job_id) is None:
        enrichment = enrich_job(job, agent_run_store=agent_runs)
        enrichment_store.upsert(job.job_id, enrichment)

    # Goal-driven relevance gate.
    relevance = check_goal_relevance(job, goal, agent_run_store=agent_runs)
    print(
        f"[sniper] relevance: relevant={relevance.relevant} score={relevance.score:.2f} "
        f"reason={relevance.reasoning[:120]!r}",
        flush=True,
    )
    if not relevance.relevant:
        return None, None, job, f"skipped: {relevance.reasoning[:200]}"

    setup_id = _ensure_goal_setup(setups_store, goal)

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
            "goal_id": goal.goal_id,
            "queue_id": queue_id,
            "triage_reasoning": triage_reasoning,
            "proposals_count": job.proposals_count_at_first_scrape,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    signal_id = signal_store.create(signal)
    signal.signal_id = signal_id

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
    order.order_id = order_store.create_draft(order)

    print("[sniper] DRAFTING Doc + cover letter", flush=True)
    order = draft_order(
        job, order, portfolio=portfolio, order_store=order_store,
        agent_run_store=agent_runs, setups_store=setups_store,
    )
    return signal, order, job, "drafted"


# ============================================================================
# One full cycle (Pass 1 + Pass 2 + drafting), runs under ui_lock
# ============================================================================


def _run_one_sniper_cycle_sync(
    goal: Goal,
    *,
    card_queue: FeedCardQueueStore,
    job_store: JobStore,
    setups_store: SetupStore,
    signal_store: SignalStore,
    order_store: OrderStore,
    enrichment_store: EnrichmentStore,
    portfolio: PortfolioStore,
    agent_runs: AgentRunStore,
) -> list[tuple[Signal, Order, Job, str]]:
    """Synchronous full cycle: Pass 1 + Pass 2 + drafting. Returns list of
    (signal, order, job, triage_reasoning) tuples for the channel-post step.

    Caller has already acquired ui_lock and verified Chrome focus is set up.

    May raise LoginExpired (caller pings operator + sleeps).
    All other exceptions are caught per-card so one failure doesn't kill the cycle.
    """
    # ----- Login check up front -----
    if detect_login_required():
        raise LoginExpired("Upwork session expired; need to log in via Chrome")

    # ----- Pass 1: extract cards across 3 viewports -----
    print(f"[sniper] === PASS 1 (extract) goal_id={goal.goal_id} ===", flush=True)
    t0 = time.time()
    all_cards = _pass1_extract_all_cards()
    print(f"[sniper] Pass 1 extracted {len(all_cards)} cards in {time.time() - t0:.1f}s", flush=True)

    if not all_cards:
        print("[sniper] no cards extracted; ending cycle", flush=True)
        return []

    # ----- Dedup against queue history (already-processed in last 14 days) -----
    inflight = card_queue.list_inflight_titles()
    known = card_queue.list_known_titles_recent(days=14)
    new_cards = [c for c in all_cards if c.title and c.title not in inflight and c.title not in known]
    print(
        f"[sniper] {len(new_cards)} new cards after dedup "
        f"(inflight={len(inflight)}, 14d-known={len(known)})",
        flush=True,
    )
    if not new_cards:
        print("[sniper] no new cards; ending cycle", flush=True)
        return []

    # ----- Triage (one LLM call) -----
    t0 = time.time()
    try:
        triage = triage_feed_cards(new_cards, goal, agent_run_store=agent_runs)
    except Exception as e:
        print(f"[sniper] triage failed: {e!r}; skipping cycle", flush=True)
        return []
    print(
        f"[sniper] triage took {time.time() - t0:.1f}s; matched {len(triage.matches)}/{len(new_cards)}",
        flush=True,
    )
    for m in triage.matches:
        print(f"[sniper]   [MATCH] {m.title[:80]!r}: {m.reason[:120]}", flush=True)

    # Validate against extracted titles (defense against em-dash hallucination).
    extracted_titles = {c.title for c in new_cards if c.title}
    matched_titles = {m.title for m in triage.matches if m.title in extracted_titles}
    dropped = {m.title for m in triage.matches} - matched_titles
    if dropped:
        print(f"[sniper] dropped {len(dropped)} hallucinated titles: {list(dropped)[:3]}", flush=True)

    # Record EVERY new extracted card as known so the next cycle's dedup catches
    # all of them — including the ones triage rejected. Without this, triage
    # rejects keep reappearing as "new" every cycle and burn LLM tokens.
    posted_by_title = {c.title: c.posted_text for c in new_cards if c.title}
    triage_reason_by_title = {m.title: m.reason for m in triage.matches}
    triage_rejected = extracted_titles - matched_titles
    for title in triage_rejected:
        rej_qid = card_queue.enqueue(
            job_title=title,
            posted_text=posted_by_title.get(title),
            triage_reasoning="triage_rejected: did not match goal",
            goal_id=goal.goal_id,
        )
        if rej_qid is not None:
            card_queue.mark_skipped(rej_qid, "triage_rejected")
    if triage_rejected:
        print(f"[sniper] recorded {len(triage_rejected)} triage-rejected titles as skipped", flush=True)

    if not matched_titles:
        print("[sniper] no triage matches; ending cycle", flush=True)
        return []

    # Persist queue rows for audit. Each match becomes a row that we'll mark
    # processed/failed/skipped in this same cycle. We never wait for the next
    # cycle to drain — drain happens entirely within Pass 2 + drafting below.
    queue_id_by_title: dict[str, int] = {}
    for title in matched_titles:
        qid = card_queue.enqueue(
            job_title=title,
            posted_text=posted_by_title.get(title),
            triage_reasoning=triage_reason_by_title.get(title),
            goal_id=goal.goal_id,
        )
        if qid is not None:
            queue_id_by_title[title] = qid
    print(f"[sniper] enqueued {len(queue_id_by_title)} queue rows for audit", flush=True)

    # ----- Pass 2: fresh substrate + click each matched title in feed order -----
    print(f"[sniper] === PASS 2 (click + capture {len(matched_titles)} matches) ===", flush=True)
    t0 = time.time()
    captures = _pass2_click_all_matches(matched_titles)
    print(f"[sniper] Pass 2 produced {len(captures)} captures in {time.time() - t0:.1f}s", flush=True)

    # ----- Per-capture drafting -----
    drafts: list[tuple[Signal, Order, Job, str]] = []
    seen_titles = {c.title for c in captures}

    for capture in captures:
        qid = queue_id_by_title.get(capture.title)
        # Pass 2 sets status='processing' via claim_next normally, but we're
        # not using claim_next here — we already enqueued and now we transition
        # row status directly based on outcome.
        if capture.error:
            print(f"[sniper]   FAILED {capture.title[:60]!r}: {capture.error}", flush=True)
            if qid is not None:
                card_queue.mark_failed(qid, capture.error)
            continue
        if qid is not None:
            card_queue.set_url(qid, capture.url)
        # Run drafting pipeline.
        try:
            signal, order, job, status = _draft_from_capture(
                capture,
                goal=goal,
                triage_reasoning=triage_reason_by_title.get(capture.title, ""),
                queue_id=qid or 0,
                job_store=job_store,
                setups_store=setups_store,
                signal_store=signal_store,
                order_store=order_store,
                enrichment_store=enrichment_store,
                portfolio=portfolio,
                agent_runs=agent_runs,
            )
        except Exception as e:
            print(f"[sniper]   DRAFT FAILED {capture.title[:60]!r}: {e!r}", flush=True)
            if qid is not None:
                card_queue.mark_failed(qid, repr(e)[:500])
            continue

        if status == "drafted":
            assert signal is not None and order is not None and job is not None
            if qid is not None:
                card_queue.mark_processed(qid, order.order_id)
            drafts.append((signal, order, job, triage_reason_by_title.get(capture.title, "goal:relevant")))
        else:
            # skipped — relevance check rejected
            if qid is not None:
                card_queue.mark_skipped(qid, status)

    # Mark titles that were enqueued but never surfaced in any viewport.
    for title, qid in queue_id_by_title.items():
        if title not in seen_titles:
            card_queue.mark_skipped(qid, "card_no_longer_visible")

    return drafts


# ============================================================================
# Top-level loop coroutine
# ============================================================================


async def run_sniper_loop(
    bot,
    settings,
    db: Database,
    humanizer,
    ui_lock: asyncio.Lock,
):
    """Forever-loop. Each iteration:
       1. Pause + goal gates.
       2. Briefs first: if BriefStore.consume_pending returns a brief, run the
          existing Phase 2.A briefed_cycle path; continue.
       3. Acquire ui_lock; run one full sniper cycle (Pass 1 + Pass 2 + drafting).
       4. Release lock; post Discord embeds for drafted orders.
       5. Sleep IDLE_BETWEEN_CYCLES_SECONDS (default 300s).
    """
    await bot.wait_until_ready()
    channel = bot.get_channel(settings.discord_channel_id)

    goals_store = GoalStore(db)
    card_queue = FeedCardQueueStore(db)
    brief_store = BriefStore(db)
    setups_store = SetupStore(db)
    signal_store = SignalStore(db)
    job_store = JobStore(db)
    order_store = OrderStore(db)
    enrichment_store = EnrichmentStore(db)
    portfolio_store = PortfolioStore(db)
    agent_runs = AgentRunStore(db)
    scrape_runs = ScrapeRunStore(db)
    sysconfig = SystemConfigStore(db)
    bidder_state = BidderStateStore(db)

    idle_seconds = int(os.environ.get("DETECTION_INTERVAL_SECONDS", DEFAULT_IDLE_SECONDS))
    print(f"[sniper] starting loop; idle between cycles = {idle_seconds}s", flush=True)

    # Brief signal handler (for the legacy run_one_cycle path the briefed scan reuses).
    async def on_signal(signal, order, job):
        if (signal.market_state or {}).get("source") == "briefed_scan":
            print(f"[sniper] suppressing channel post for briefed-scan signal_id={signal.signal_id}", flush=True)
            return
        setup = setups_store.get(signal.primary_setup_id)
        primary_match = next(
            (m for m in signal.matched_setups if m.get("setup_id") == signal.primary_setup_id),
            signal.matched_setups[0] if signal.matched_setups else {},
        )
        application_flags = primary_match.get("application_flags") or []
        embed = build_signal_embed(
            setup_name=setup.name, tier=setup.tier, title=job.title,
            budget_text=f"{job.budget_kind} ${job.budget_min_usd or 0:.0f}",
            posted_text=job.posted_text or "recent",
            client_summary=job.client_country or "?",
            why_matched=", ".join(
                m["matched_rules"][0] if m.get("matched_rules") else ""
                for m in signal.matched_setups
            ),
            cover_letter_preview=order.cover_letter_body or "",
            application_flags=application_flags,
        )
        view = OrderApprovalView(order_id=order.order_id, doc_url=order.doc_url or "")
        await channel.send(embed=embed, view=view)

    def sync_on_signal(signal, order, job):
        asyncio.run_coroutine_threadsafe(on_signal(signal, order, job), bot.loop)

    last_no_goal_log_at: Optional[float] = None

    while True:
        try:
            # ----- 1. Pause gate -----
            if bool(sysconfig.get("bidder_paused") or False):
                await asyncio.sleep(PAUSE_SLEEP_SECONDS)
                continue
            if bidder_state.get().paused:
                await asyncio.sleep(PAUSE_SLEEP_SECONDS)
                continue

            # ----- 2. Briefs first -----
            pending_brief = brief_store.consume_pending()
            if pending_brief is not None:
                print(f"[sniper] consuming brief brief_id={pending_brief.brief_id}", flush=True)
                try:
                    async with ui_lock:
                        await asyncio.to_thread(
                            _with_com,
                            run_one_cycle,
                            humanizer=humanizer,
                            setups_store=setups_store,
                            signal_store=signal_store,
                            job_store=job_store,
                            order_store=order_store,
                            enrichment_store=enrichment_store,
                            portfolio=portfolio_store,
                            agent_runs=agent_runs,
                            scrape_runs=scrape_runs,
                            sysconfig=sysconfig,
                            on_signal=sync_on_signal,
                            brief=pending_brief,
                        )
                    with db.connection() as conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                "SELECT count(*) FROM orders WHERE setup_id = "
                                "(SELECT setup_id FROM scan_briefs WHERE brief_id = %s)",
                                (pending_brief.brief_id,),
                            )
                            drafts_count = cur.fetchone()[0]
                    brief_store.mark_done(
                        pending_brief.brief_id,
                        result_summary={"drafts_created": drafts_count},
                        cycle_notes="ok",
                    )
                except LoginExpired:
                    owner_mention = f"<@{settings.discord_owner_user_id}> " if settings.discord_owner_user_id else ""
                    if channel is not None:
                        await channel.send(
                            f"{owner_mention}Upwork login required. Open the Chrome tab and sign in. "
                            f"The bidder will resume on the next cycle."
                        )
                    brief_store.mark_failed(pending_brief.brief_id, error="login_required")
                except Exception as e:
                    print(f"[sniper] brief brief_id={pending_brief.brief_id} failed: {e!r}", flush=True)
                    brief_store.mark_failed(pending_brief.brief_id, error=repr(e)[:500])
                continue

            # ----- 3. Goal gate -----
            goal = goals_store.get_active()
            if goal is None:
                now = time.monotonic()
                if last_no_goal_log_at is None or (now - last_no_goal_log_at) >= 60.0:
                    print("[sniper] no active goal, idle", flush=True)
                    last_no_goal_log_at = now
                await asyncio.sleep(PAUSE_SLEEP_SECONDS)
                continue
            last_no_goal_log_at = None

            # ----- 4. Chrome health check -----
            from substrate.launch_chrome import ensure_chrome_running
            ok = await asyncio.to_thread(_with_com, ensure_chrome_running)
            if not ok:
                print("[sniper] Chrome unavailable; sleeping 60s", flush=True)
                if channel is not None:
                    owner_mention = f"<@{settings.discord_owner_user_id}> " if settings.discord_owner_user_id else ""
                    await channel.send(
                        f"{owner_mention}Chrome could not be launched. Sleeping 60s before retry."
                    )
                await asyncio.sleep(60)
                continue

            # ----- 5. Run one full sniper cycle under ui_lock -----
            print(f"[sniper] === cycle start goal_id={goal.goal_id} ===", flush=True)
            cycle_t0 = time.time()
            drafts: list[tuple[Signal, Order, Job, str]] = []
            try:
                async with ui_lock:
                    drafts = await asyncio.to_thread(
                        _with_com,
                        _run_one_sniper_cycle_sync,
                        goal,
                        card_queue=card_queue,
                        job_store=job_store,
                        setups_store=setups_store,
                        signal_store=signal_store,
                        order_store=order_store,
                        enrichment_store=enrichment_store,
                        portfolio=portfolio_store,
                        agent_runs=agent_runs,
                    )
            except LoginExpired:
                owner_mention = f"<@{settings.discord_owner_user_id}> " if settings.discord_owner_user_id else ""
                print("[sniper] LOGIN_REQUIRED", flush=True)
                if channel is not None:
                    await channel.send(
                        f"{owner_mention}Upwork login required. Open the Chrome tab and sign in. "
                        f"The bidder will resume on the next cycle."
                    )
                await asyncio.sleep(60)
                continue
            except Exception as e:
                print(f"[sniper] cycle failed: {e!r}", flush=True)
                # Don't crash the loop — sleep idle and retry.
                await asyncio.sleep(idle_seconds)
                continue

            cycle_elapsed = time.time() - cycle_t0
            print(
                f"[sniper] === cycle finished in {cycle_elapsed:.1f}s; "
                f"drafted {len(drafts)} orders ===",
                flush=True,
            )

            # ----- 6. Post Discord embeds for each drafted order -----
            for signal, order, job, triage_reasoning in drafts:
                try:
                    setup = setups_store.get(order.setup_id)
                    primary_match = next(
                        (m for m in signal.matched_setups if m.get("setup_id") == signal.primary_setup_id),
                        signal.matched_setups[0] if signal.matched_setups else {},
                    )
                    application_flags = primary_match.get("application_flags") or []
                    embed = build_signal_embed(
                        setup_name=setup.name, tier=setup.tier, title=job.title,
                        budget_text=_budget_text(job),
                        posted_text=job.posted_text or "recent",
                        client_summary=job.client_country or "?",
                        why_matched=triage_reasoning or "goal:relevant",
                        cover_letter_preview=order.cover_letter_body or "",
                        application_flags=application_flags,
                    )
                    view = OrderApprovalView(order_id=order.order_id, doc_url=order.doc_url or "")
                    if channel is not None:
                        await channel.send(embed=embed, view=view)
                except Exception as e:
                    print(f"[sniper] channel post failed for order_id={order.order_id}: {e!r}", flush=True)

            # ----- 7. Sleep until next cycle -----
            await asyncio.sleep(idle_seconds)

        except Exception as e:
            print(f"[sniper] unhandled exception in loop: {e!r}", flush=True)
            await asyncio.sleep(PAUSE_SLEEP_SECONDS)


def _budget_text(job) -> str:
    if job.budget_min_usd is None and job.budget_max_usd is None:
        return f"{job.budget_kind or 'unknown'}"
    if job.budget_max_usd is None or job.budget_max_usd == job.budget_min_usd:
        return f"{job.budget_kind} ${job.budget_min_usd or 0:.0f}"
    return f"{job.budget_kind} ${job.budget_min_usd:.0f}-${job.budget_max_usd:.0f}"
