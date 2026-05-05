# Phase 2.B — Sniper Bidder (UIA Extraction + Text-LLM Triage)

**Status:** Draft, awaiting approval 2026-05-05
**Branch baseline:** `phase1-foundation`
**Supersedes:** [2026-05-04-sniper-bidder-vision-triage-design.md](2026-05-04-sniper-bidder-vision-triage-design.md) (vision-on-screenshots design)
**Handoff source:** `docs/superpowers/handoffs/2026-05-05-phase2b-sniper-bidder-handoff.md` — every architectural decision below was empirically validated; do not re-run diagnostics
**Implementation target:** new `bidder/detection_loop.py` + `bidder/processing_loop.py`, decommissions `bidder_loop` and `run_one_cycle`'s setup-iteration logic

## Goal

Make the bidder a sniper, not a spammer. The operator sets a single active goal. The bidder runs a deterministic UIA substrate recipe at 33% zoom every ~60s to extract structured `FeedCard` records directly from the accessibility tree, sends only genuinely new cards to a text-LLM triage call against the active goal, queues survivors, and runs the heavyweight pipeline (panel walk + LLM relevance + drafting) only on those survivors. No active goal → bidder idle.

Headline numbers (empirically measured, not estimated):

- Detection latency: ~15-20s per cycle (most is refresh+hydrate; UIA walk ~5s; zoom ~1.5s).
- Triage cost: ~$0.001 per call, only when there are new titles. Most cycles dedup-first and call zero LLMs.
- Daily ceiling: ~$1-2/day at hunting volume; ~$0.05-0.10/day in triage.

## Non-goals (Phase 2.B)

- DM-native approvals (Phase 2.C). Drafts go to `#job-notifications` via the existing `OrderApprovalView` flow.
- Two-Chrome architecture. Single Chrome, single `ui_lock`. The 33% recipe is fast enough that a second Chrome is overkill.
- Anomaly watcher / proactive pings beyond brief-completion (Phase 2.C).
- Outcome tracking + follow-up DMs (Phase 2.D).
- Goal-progress reporting ("1/5 interviews this week") (Phase 2.D).
- Sniper-priority queue ordering (FIFO is fine for v1).
- Triaging older / scrolled-down jobs. Top ~9-10 cards visible at 33% zoom only.
- Bypassing the LLM relevance check after triage. Defense in depth — triage is fast/cheap and fallible; the panel-data relevance check is the gate that protects drafting cost.
- Replacing the existing scheduled briefed-scan path. Briefed scans (Phase 2.A) keep working unchanged.
- Replacing `setups` (Phase 2.D or never).
- Smart-hydrate replacement for the flat 20s sleep in `refresh_feed` (post-ship optimization).
- Vision-based triage (abandoned; see superseded spec).

## Operator-confirmed decisions

| Decision | Choice |
|---|---|
| Bidder execution gate | Goal-only. No active goal → no work. |
| Triage method | Text LLM (`gpt-5-mini`) over UIA-extracted FeedCard records. |
| Substrate recipe | Refresh + Ctrl+Home + Ctrl+- ×6 (zoom 33%). One UIA walk gets all 9-10 cards. |
| URL extraction | Click card title → `panel.capture_panel` reads URL via "Copy to clipboard" button. UIA does NOT expose hyperlink hrefs on Chrome. |
| Inter-cycle reset | Refresh feed every cycle. Ctrl+0 at end of each detection pass to reset zoom for next cycle. |
| Detection / processing model | Two coroutines, single Chrome, shared `ui_lock`. |
| Defense in depth | LLM relevance check still runs on triage survivors before drafting. |
| Setups as scan-time filter | Decommissioned. Goal is the filter. Briefed scans still synthesize an ephemeral retired setup. |
| Brief priority | Briefs win over detection-driven cards in the processing queue. |

## Architecture

### Substrate recipe — LOCKED at 33% zoom

The detection coroutine acquires the Chrome `ui_lock` and runs:

```
1. Refresh feed (navigate to https://www.upwork.com/nx/find-work/most-recent
   in a new tab; existing upwork.feed.refresh_feed already does this with
   a flat 20s hydrate sleep — keep it for v1)
2. act.focus_window("Upwork")
3. act.key("ctrl+home")
4. pyautogui.hotkey("ctrl", "-")  ×6  with ~0.18s sleep between presses → 33% zoom
5. observe.observe(window_title=WINDOW, include_unnamed=False, include_text=True)
6. Parse Posted-anchor slices into FeedCard records (one walk → 9-10 cards)
7. pyautogui.hotkey("ctrl", "0")  to reset zoom
8. Release ui_lock
```

**Why 33% (validated):**

| Zoom | Cards in walk A | Walks needed for 10 cards | Panel walk | Click target size |
|---|---|---|---|---|
| 67% (Ctrl+- ×4) | 2 | 3 (with Down×21 ×2 between) | works | 688px × 21px |
| 50% (Ctrl+- ×5) | 3 | 3 | works | 516px × 16px |
| **33% (Ctrl+- ×6)** | **9** | **1** | **works** | **344px × 11px** |
| 25% (Ctrl+- ×7) | 8 | 1 | **silent fail** | 258px × 9px |

At 33%, the `Down×21 ×2` dance from the original 67% recipe disappears entirely. Single UIA walk. 25% breaks the panel walk; 33% is the bottom of the safe range.

The triage LLM call runs **after lock release** so the processing coroutine can pick up briefs / queued cards while triage is in flight (~30s).

### URL extraction — LOCKED on click + Copy-to-clipboard

Chrome does NOT expose hyperlink hrefs via UIA. We probed every reasonable property (`Value`, `LegacyIAccessibleValue`, `LegacyIAccessibleDescription`, `HelpText`, `AriaProperties`, parent + child elements). None surface the URL. See `bin/debug_uia_url_extraction.py`.

URL capture must use the existing legacy bidder mechanism: open the panel, find the "Copy to clipboard" button, click it, read clipboard. This is what `upwork.panel.capture_panel` already does. No way around it.

**Implication for architecture:** Pass 1 (detection) cannot know URLs. It dedups by `job_title`. Pass 2 (processing) clicks the matched card, captures the URL via panel, and stores it on the queue row.

### Two-pass architecture — LOCKED

`bin/debug_phase2b_e2e.py` proves the design end-to-end. Both passes use the 33% substrate recipe.

**Pass 1 — detection (every ~60s):**

```
1. Goal gate: GoalStore.get_active() → if None, log once/min, sleep, continue
2. Pause gate: system_config.bidder_paused → if true, sleep 30s, continue
3. Acquire ui_lock
4. Substrate recipe (refresh + Ctrl+Home + Ctrl+- ×6 + observe + Ctrl+0)
5. Release ui_lock
6. Parse observation into list[FeedCard] via _parse_card_slice
7. Dedup against feed_card_queue: drop titles already queued (status='queued')
   AND titles whose URL has been captured and is in the queue regardless of status
8. If genuinely new titles exist: ai.feed_triage.triage_feed_cards(goal, cards)
   → returns matched titles + reasoning. ~30s, ~$0.001
9. For each survivor: validate title exists in extracted FeedCard list (defense
   against em-dash corruption / hallucination); insert into feed_card_queue
   with status='queued', goal_id_at_detect=goal.id, triage_reasoning=...
10. Sleep DETECTION_INTERVAL_SECONDS (default 60s)
```

**Pass 2 — processing (consumes briefs first, then queue):**

```
1. Pause gate
2. Goal gate: if no goal, sleep 5s, continue
3. Brief priority: BriefStore.consume_pending() → if pending, run existing
   Phase 2.A briefed_cycle path; continue
4. Else: FeedCardQueueStore.claim_next() → claims oldest queued row with
   FOR UPDATE SKIP LOCKED, sets status='processing', claimed_at=now()
5. If no row: sleep 5s, continue
6. Acquire ui_lock
7. Substrate recipe (refresh + zoom) → fresh UIA walk with LIVE element refs
8. Iterate _parse_visible_cards(WINDOW); for each (title, link_el):
     if title == claimed_row.job_title: click(link_el) immediately while ref is fresh
9. panel.capture_panel(WINDOW) → URL + panel elements
   - if 0 elements: sleep 1.5s, retry once; on second failure, mark_failed
10. panel.parse_panel(elements) → structured PanelData
11. Update queue row with set_url(queue_id, url)
12. ai.relevance_goal.check_goal_relevance(panel_data, goal_at_detect)
    - relevance check uses goal_id_at_detect (the goal active when this card
      was matched), NOT the current goal — fairness to the operator's intent
    - if rejects: mark_skipped(queue_id, reason); release lock; continue
13. bidder.draft_pipeline.draft_order(...) → Order (Doc + cover letter)
14. Post to #job-notifications via existing OrderApprovalView flow
15. mark_processed(queue_id, order_id)
16. Press Esc to close panel
17. Release ui_lock
```

**Critical rule:** Never try to "find a card by title in a fresh walk after the queue says it should be there." Element refs only live within the observation that produced them. Pass 2 must walk-and-click in the same iteration. The cross-coroutine state is the queue + matched-title set, never element refs.

If the card disappears from the feed by the time Pass 2 walks (e.g. it scrolled past in the 30+ seconds since detection), the walk simply won't return that title. The queue row stays `queued`. After N retry attempts the row should be marked `skipped` with reason `'card_no_longer_visible'` (track attempt count on the row; v1 can use a simple staleness check by `detected_at`).

### FeedCard parser — LOCKED structure

UIA card slices have a deterministic shape (validated against 9 cards in `bin/debug_phase2b_e2e.py`):

```
[0]  text       'Posted'
[1]  text       '<N minutes/hours/days ago>' or 'yesterday'
[2]  text       <title>
[3]  hyperlink  <title>          ← CLICK TARGET
[4]  text       <title>
[5]  button     'Job feedback <title>'
[6]  button     'Save job <title>'                  ← canonical title source
[7]  text       'Hourly: $X-$Y' / 'Hourly' / 'Fixed-price'
[8]  text       'Intermediate' / 'Expert' / 'Entry level'
[9]  text       'Est. Time:' or 'Est. Budget:'
[10] text       <duration string> or <budget amount>
[11] text       <description preview, longest text element ≥80 chars>
[12] button     'more about "..."'
[13] text       'more'
[14] text       'about "..."'
[15..N] interleaved hyperlink/text pairs for skill chips
[N+1] text      'Verified' (sometimes preceded by 'Skip skills' button)
[N+2] text      'Payment verified'
[N+3] text      'Rating is X.X out of 5.'
[N+4] text      '$<spent>'
[N+5] text      'spent'
[N+6] text      <country>
[N+7] text      'Proposals:'
[N+8] text      <range>  (e.g. 'Less than 5', '20 to 50', '50+')
```

Field accuracy on 9 live cards:

| Field | Coverage | Source |
|---|---|---|
| title | 9/9 | `Save job <title>` button — most reliable |
| posted_text | 9/9 | element[1] |
| budget_text | 8/9 | element[7] (one fixed-price card had no budget shown) |
| experience_level | 8/9 | element[8] |
| description_preview | 9/9 | longest text ≥80 chars |
| skills | 8/9 | hyperlink elements not equal to title |
| payment_verified | 7/9 | element presence |
| rating | 7/9 | regex `Rating is\s+([\d.]+)\s+out of 5` |
| spent | 7/9 | `<$amount>` paired with `'spent'` |
| country | 7/9 | element after `'spent'` |
| proposals | 7/9 | element after `'Proposals:'` |

The 2/9 cards missing client-trust fields are jobs without enough client history to display them — that's signal worth keeping (treat absence as "thin client"). Not a parser bug.

The working extractor lives at `bin/debug_phase2b_e2e.py:_parse_card_slice`. Lift directly into `upwork/feed_cards.py`.

### LLM triage — LOCKED text-in / structured-out

Mirror `ai/relevance.py`. Use `langchain.agents.create_agent` with `response_format=Pydantic` and `model="gpt-5-mini"`.

**System prompt (paraphrased):**

> Strict job-feed triage filter. Given the operator's active goal and a list of jobs from the Upwork feed, return ONLY the matches. Use the EXACT title text from the input. Be lenient on prose match (description preview suggests the work could fit the goal's domain) but strict on hard numeric targets if the goal specifies them (min hourly, min budget, preferred country, etc.).

**User payload:**

```json
{
  "goal": "<goal.prose + structured fields>",
  "jobs": [<list of FeedCard.to_dict_for_llm() outputs>]
}
```

**Response model:**

```python
class TriageMatch(BaseModel):
    title: str  # EXACT title text, copied verbatim
    reasoning: str  # one-sentence reason

class TriageResponse(BaseModel):
    matches: list[TriageMatch]
```

**Critical: serialize with `json.dumps(payload, ensure_ascii=False)`.** Without this kwarg, em-dashes in titles become `—`, the LLM echoes them back as `\x14` (DC4) or other corruptions, and downstream title-matching breaks. We hit this in v1 of the e2e script. Always preserve raw Unicode.

**Hallucination defense in depth:** at queue insert, validate that each survivor's title appears verbatim in the FeedCard list. Drop and warn-log otherwise.

**Timing:** ~30s per call, ~$0.001 per call. Real measurement.

### Goal gate

Both loops check `GoalStore(db).get_active()` at the top of every iteration. Detection logs `[detection] no active goal, idle` once per minute (rate-limited via a recent-log timestamp). Processing sleeps 5s and re-checks. The gate is mandatory and applies even when there are unprocessed queue rows — if the operator clears the goal, in-flight processing finishes its current row but new pulls block.

### Pause flag

The existing `system_config.bidder_paused` flag (Phase 1) gates both loops. Both check it at the top of each iteration; if true, sleep 30s and re-check. `pause_bidder` / `resume_bidder` tools work unchanged.

### Force-run flag

The existing `bidder_state.force_run_requested` column is no longer consumed (detection runs every 60s; "force a run" is meaningless). The `trigger_bidder_scan` tool becomes a friendly no-op that returns a status message: "the bidder runs every 60s; the most recent cycle was at <ts>." The column stays in `bidder_state` for schema stability.

### Briefed scans (Phase 2.A) keep priority

Processing loop, every iteration, **first** consults `BriefStore.consume_pending()`. If a pending brief exists, it runs the Phase 2.A briefed_cycle path against the brief's filter_dsl (which still uses `signal_pipeline.process_job_through_setups` against an ephemeral retired setup synthesized from the brief). If no brief, claims next from `feed_card_queue`.

So queue priority is: **briefs > detection-driven cards**. Briefs win because the operator explicitly asked for them.

Briefed-scan signals stay tagged with `market_state.source='briefed_scan'` (Phase 2.A) and continue to be suppressed from `#job-notifications`; the brief-watcher DMs the operator a summary. No change.

### Decommissioned

- `scheduler.main.bidder_loop` task → replaced by `run_detection_loop` + `run_processing_loop` tasks.
- `bidder.scan_cycle.run_one_cycle`'s "iterate over setups" logic — bypassed for detection-driven cards. The function stays in place because briefed scans still call it (with a single ephemeral retired setup synthesized from the brief).
- `bidder.signal_pipeline.process_job_through_setups` — kept; used only by briefed scans.
- `setups` table as a scan-time filter — decommissioned. Schema unchanged. Agent's setup-mutation tools still write to it; bidder no longer reads `status='active'` for filtering.

## Data model

### New table — `feed_card_queue` (migration `006_feed_card_queue.sql`)

```sql
CREATE TABLE feed_card_queue (
    queue_id           bigserial PRIMARY KEY,
    job_url            text UNIQUE,                    -- nullable; populated by Pass 2
    job_title          text NOT NULL,                  -- bridge between detection and processing
    posted_text        text,
    detected_at        timestamptz NOT NULL DEFAULT now(),
    triage_reasoning   text,
    status             text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'processing', 'processed', 'failed', 'skipped')),
    claimed_at         timestamptz,
    finished_at        timestamptz,
    error_text         text,
    order_id           bigint REFERENCES orders(order_id),
    goal_id_at_detect  bigint REFERENCES goals(goal_id)
);

CREATE INDEX feed_card_queue_pending ON feed_card_queue (detected_at)
    WHERE status = 'queued';

-- Prevent duplicate-title queueing while a row is in flight
CREATE UNIQUE INDEX feed_card_queue_inflight_title
    ON feed_card_queue (job_title)
    WHERE status IN ('queued', 'processing');
```

**Idempotency strategy:**

- At detection time `job_url` is null. The partial unique index on `(job_title)` WHERE `status IN ('queued','processing')` blocks duplicate-title insertion while a row is in flight. `INSERT ... ON CONFLICT DO NOTHING` against that index works.
- Pass 2 captures the URL and `UPDATE ... SET job_url = ?` on the row. The `UNIQUE (job_url)` constraint then dedups against any future detection cycle that happens to extract the same URL after the row is fully processed (`status='processed'/'skipped'/'failed'`).
- Pass 1 dedup query: `SELECT job_title FROM feed_card_queue WHERE status IN ('queued','processing') OR job_url = ?`. Drop matching titles before triage call.

### Reused — Phase 2.A schemas

`goals` (single active row, partial unique index), `scan_briefs` (Phase 2.A queue), `system_config['bidder_paused']`, `bidder_state`, `assistant_audit_log`, `assistant_messages`, `assistant_conversations`, `agent_runs`. No changes.

### `setups` table — schema unchanged, no longer read by bidder for scan-time filtering.

## Stores

### `storage/card_queue.py` — `FeedCardQueueStore`

- `enqueue(job_title: str, posted_text: Optional[str], triage_reasoning: str, goal_id: int) -> Optional[int]`
  Insert with `ON CONFLICT (job_title) WHERE status IN ('queued','processing') DO NOTHING`. Returns new `queue_id` or None.
- `list_inflight_titles_and_known_urls() -> tuple[set[str], set[str]]`
  For Pass 1 dedup. Returns (in-flight titles, all known URLs).
- `claim_next() -> Optional[QueueItem]`
  Atomic `UPDATE ... WHERE status='queued' ORDER BY detected_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED → status='processing', claimed_at=now()`. Returns claimed row or None.
- `set_url(queue_id: int, job_url: str) -> None`
  Sets `job_url` after Pass 2 captures it.
- `mark_processed(queue_id: int, order_id: int) -> None`
- `mark_skipped(queue_id: int, reason: str) -> None`
- `mark_failed(queue_id: int, error: str) -> None`

## Modules

### `upwork/feed_cards.py` (new)

```python
@dataclass
class FeedCard:
    title: str                          # from 'Save job <title>' button
    posted_text: str
    budget_text: Optional[str]
    experience_level: Optional[str]
    description_preview: Optional[str]
    skills: list[str]
    payment_verified: bool
    rating: Optional[float]
    spent: Optional[str]
    country: Optional[str]
    proposals: Optional[str]
    link_el: Element                     # live UIA element for click — DO NOT serialize / persist

    def to_dict_for_llm(self) -> dict:
        """Serializable view for triage payload. Excludes link_el."""

def parse_card_slice(elements: list[Element]) -> Optional[FeedCard]:
    """Lifted from bin/debug_phase2b_e2e.py:_parse_card_slice. Anchored on
    'Posted' marker. Returns None if slice doesn't have the expected shape."""

def parse_visible_cards(window_title: str) -> list[FeedCard]:
    """Walk UIA tree once, slice on 'Posted' anchors, parse each slice.
    Returns ordered list (feed order)."""
```

### `upwork/feed_zoom.py` (new)

```python
def zoom_to_33pct() -> None:
    """pyautogui.hotkey('ctrl', '-') × 6 with ~0.18s sleep between presses."""

def reset_zoom() -> None:
    """pyautogui.hotkey('ctrl', '0')."""
```

(Three-line wrappers around `pyautogui`. Live separately so the recipe is one import.)

### `ai/feed_triage.py` (new)

```python
class TriageMatch(BaseModel):
    title: str
    reasoning: str

class TriageResponse(BaseModel):
    matches: list[TriageMatch]

def triage_feed_cards(
    cards: list[FeedCard],
    goal: Goal,
    *,
    agent_run_store: AgentRunStore,
) -> TriageResponse:
    """Build user payload via json.dumps(..., ensure_ascii=False).
    Call create_agent with model=VISION_TRIAGE_MODEL (renamed conceptually
    but still controlled by env; default 'gpt-5-mini'),
    response_format=TriageResponse. Cost-track as agent_name='feed_triage'."""
```

### `ai/relevance_goal.py` (new)

Mirrors `ai/relevance.py` shape but takes a `Goal` instead of a `Setup`.

```python
def check_goal_relevance(
    job: Job,
    panel: PanelData,
    goal: Goal,
    *,
    agent_run_store: AgentRunStore,
) -> RelevanceCheck:
    """LLM relevance gate against the active goal. Same prompt scaffolding as
    ai.relevance.check_relevance with goal-specific context. Cost-track as
    agent_name='relevance_goal'."""
```

The legacy `check_relevance` keeps working for briefed scans (which synthesize an ephemeral retired Setup with `prose=brief.prose`).

### `bidder/job_processing.py` (new)

Refactored chunk of the old `run_one_cycle` per-card body:

```python
def process_queued_card(
    queue_row: QueueItem,
    goal_at_detect: Goal,
    *,
    job_store: JobStore,
    enrichment_store: EnrichmentStore,
    signal_store: SignalStore,
    order_store: OrderStore,
    portfolio: PortfolioStore,
    agent_run_store: AgentRunStore,
    card_queue: FeedCardQueueStore,
) -> Optional[Order]:
    """Pass-2 worker for a single claimed queue row.

    1. Substrate recipe → fresh walk with live link refs
    2. Find FeedCard whose title matches queue_row.job_title; click link_el
    3. panel.capture_panel(WINDOW) with one 1.5s retry on 0-element response
    4. panel.parse_panel(elements) → PanelData
    5. card_queue.set_url(queue_row.queue_id, captured_url)
    6. check_goal_relevance(panel_data, goal_at_detect)
    7. If relevant: draft_pipeline.draft_order(...) → Order
    8. Esc to close panel
    9. Return Order or None"""
```

The key decision: navigation is by **clicking the matched live element ref in the same walk**, not by `navigate(job_url)`. The URL isn't known until after the click + panel walk. (This is a difference from the superseded vision spec, which assumed UIA exposed hrefs.)

### `bidder/detection_loop.py` (new)

```python
async def run_detection_loop(
    db: Database,
    settings: Settings,
    ui_lock: asyncio.Lock,
):
    """Forever-loop. Every DETECTION_INTERVAL_SECONDS (default 60):
       1. Pause gate (system_config.bidder_paused)
       2. Goal gate (GoalStore.get_active)
       3. Acquire ui_lock; substrate recipe; release lock
       4. Parse FeedCards from observation
       5. Dedup against feed_card_queue inflight + URLs
       6. If new cards: triage_feed_cards(...) (no lock held)
       7. Validate survivors against extracted titles (hallucination defense)
       8. card_queue.enqueue(...) for each survivor
       9. Sleep DETECTION_INTERVAL_SECONDS"""
```

### `bidder/processing_loop.py` (new)

```python
async def run_processing_loop(
    bot,
    db: Database,
    settings: Settings,
    humanizer: Humanizer,
    ui_lock: asyncio.Lock,
):
    """Forever-loop. Each iteration:
       1. Pause gate
       2. Goal gate; if no goal sleep 5s and continue
       3. Brief priority: BriefStore.consume_pending → if pending, run
          existing Phase 2.A briefed_cycle(brief) path; continue
       4. Else: card_queue.claim_next() → row or None
       5. If no row: sleep 5s, continue
       6. Acquire ui_lock
       7. process_queued_card(row, goal_at_detect) → Order or None
          - mark_processed on Order
          - mark_skipped on relevance reject
          - mark_failed on raised exception
       8. If Order: post to #job-notifications via existing approval flow
       9. Release ui_lock"""
```

`goal_at_detect` is loaded from `goals.get_by_id(row.goal_id_at_detect)`, NOT the current active goal — fairness to the operator's intent at detection time. If that goal has since been deactivated/deleted, fall back to the current active goal; if there is no current active goal, mark_skipped with reason `'goal_deactivated_before_processing'`.

### `bidder/scan_cycle.py` (modified, not removed)

`run_one_cycle` keeps existing behavior. Now invoked only by `briefed_cycle` (the Phase 2.A path).

### `bidder/signal_pipeline.py` (kept)

`process_job_through_setups` keeps working for briefed scans.

## Wiring

### `scheduler/main.py` changes

Replace the `bidder_loop` task with two new tasks. The full `setup_hook` becomes:

```python
async def setup_hook():
    print("setup_hook fired; starting detection + processing + apply_executor + brief_watcher loops", flush=True)
    bot.loop.create_task(run_detection_loop(db, settings, ui_lock))
    bot.loop.create_task(run_processing_loop(bot, db, settings, humanizer, ui_lock))
    bot.loop.create_task(apply_executor_loop(bot, settings, db, humanizer))
    from assistant.brief_watcher import run_brief_watcher
    bot.loop.create_task(run_brief_watcher(db, bot, settings))
```

DM `on_message` handler, slash commands, and `apply_executor_loop` stay unchanged.

### Environment variables

| Var | Default | Purpose |
|---|---|---|
| `DETECTION_INTERVAL_SECONDS` | `60` | Pass-1 cadence. |
| `FEED_TRIAGE_MODEL` | `gpt-5-mini` | Override triage model (was `VISION_TRIAGE_MODEL` in superseded spec). |
| (existing) `ASSISTANT_MODEL` | `gpt-5-mini` | Unchanged. |

## Failure handling

| Failure | Behavior |
|---|---|
| No active goal | Both loops sleep, log once/min, idle. |
| `bidder_paused = true` | Both loops sleep 30s, re-check. |
| Chrome unavailable / window not found | Existing behavior: log + Discord ping; sleep 60s; retry. |
| Login required | Existing `LoginExpired` exception; ping channel; sleep 60s; retry. |
| Hydrate flat-sleep dominant cost | Keep flat 20s for v1; smart-poll is a post-ship optimization. |
| Triage LLM fails / malformed JSON | Skip cycle. No survivors enqueued. Try again next cycle. |
| Triage returns title not in extracted FeedCards | Drop with warn log; defense-in-depth against em-dash bug + hallucination. |
| Card title from queue not in fresh Pass-2 walk | Increment attempt counter; if N attempts (e.g. 3), mark_skipped with reason `'card_no_longer_visible'`. |
| `panel.capture_panel` returns 0 elements | Sleep 1.5s, retry once. Then `mark_failed`. |
| `panel.parse_panel.title` returns garbage (description first sentence) | Don't depend on it. Use `queue_row.job_title` for the canonical title. Other panel fields (budget, country, skills, description, payment_verified, total_spent) are reliable. |
| Per-tab zoom inheritance flake | Always pair refresh+zoom; always `Ctrl+0` at end of detection pass. |
| Stale `WindowControl.Name` | `act.focus_window` already retries; just be aware. |
| Drafting fails | `mark_failed` with truncated error_text; processing continues. |
| Relevance check rejects after triage said yes | `mark_skipped` with reason. Common; not a failure. |

## Cost tracking

- Triage calls: `agent_runs` row per call with `agent_name='feed_triage'`, `trigger='scheduled'`, `trigger_context={"n_cards": N, "goal_id": M}`.
- Goal-relevance calls: `agent_runs` row with `agent_name='relevance_goal'`, `trigger='scheduled'`.
- Existing per-job extract / proposal / cover-letter rows continue.

The `agent_runs.trigger` CHECK constraint allows `scheduled|discord_question|manual|per_job` (Phase 1). All Phase 2.B autonomous loops use `scheduled`.

**Daily ceiling at 60s detection cadence:**
- ~50-100 LLM triage calls/day = ~$0.05-0.10/day in triage (most cycles dedup-first; no LLM call).
- ~10-20 jobs/day pass triage and reach relevance check + drafting = ~$1-2/day in heavyweight pipeline.
- **Total: ~$1-2/day at hunting volume.** No vision overhead.

## Testing

**Unit:**
- `tests/test_feed_card_queue.py` — enqueue idempotency (in-flight title block + URL block), `claim_next` atomicity (FOR UPDATE SKIP LOCKED), `mark_processed`/`mark_skipped`/`mark_failed`, `set_url` after click, dedup of in-flight rows.
- `tests/test_feed_cards.py` — lift the raw element dumps from `results_e2e.md` for cards 1-5 as fixtures; assert each FeedCard field parses correctly. Cover the no-budget-shown / no-client-history edge cases.
- `tests/test_feed_zoom.py` — mock `pyautogui.hotkey`, verify call sequence (6 presses + sleeps for zoom_to_33pct, 1 press for reset_zoom).

**Integration:**
- `tests/test_feed_triage.py` — gated on `OPENAI_API_KEY`. Hardcode a few FeedCards + a goal, assert the LLM returns matched titles in the structured response. Include an em-dash title in the input; verify it round-trips intact (proves `ensure_ascii=False`).
- `tests/test_relevance_goal.py` — gated on `OPENAI_API_KEY`. Smoke test of the new check.

**Manual smoke:**
- Reproduce the live `bin/debug_phase2b_e2e.py` flow against the production scheduler. Set a goal in DM. Watch `pm2 logs scheduler --lines 0` for `[detection] cycle start`, `[detection] survivors=N`, `[processing] processing job_title=...`. Confirm matching jobs appear in `#job-notifications` with URLs and Doc links. Confirm non-matching cycles don't post.

## Module structure summary

```
storage/
  card_queue.py            # NEW: FeedCardQueueStore

upwork/
  feed_cards.py            # NEW: FeedCard dataclass + parse_card_slice + parse_visible_cards
  feed_zoom.py             # NEW: zoom_to_33pct() + reset_zoom()

ai/
  feed_triage.py           # NEW: text-LLM triage call (ensure_ascii=False!)
  relevance_goal.py        # NEW: goal-driven relevance check (Phase 2.A spec called for this)

bidder/
  detection_loop.py        # NEW: every-60s pass-1 worker
  processing_loop.py       # NEW: consumes briefs first, then queue
  job_processing.py        # NEW: process_queued_card (panel walk + relevance + draft, refactored)
  scan_cycle.py            # MODIFIED: run_one_cycle still used by briefed scans only
  signal_pipeline.py       # KEEP: used by briefed scans

scheduler/
  main.py                  # MODIFIED: replace bidder_loop task with detection + processing tasks

migrations/
  006_feed_card_queue.sql  # NEW
```

## Implementation order

1. Migration `006_feed_card_queue.sql`.
2. `storage/card_queue.py` + tests.
3. `upwork/feed_cards.py` (lift `_parse_card_slice` + `FeedCard` from `bin/debug_phase2b_e2e.py`).
4. `upwork/feed_zoom.py`.
5. `ai/feed_triage.py` + tests (gated on API key).
6. `ai/relevance_goal.py` (mirrors `ai/relevance.py`).
7. `bidder/job_processing.py` — refactor reusable panel-walk + draft path.
8. `bidder/detection_loop.py`.
9. `bidder/processing_loop.py` (consumes briefs FIRST, then queue).
10. Wire into `scheduler/main.py:setup_hook`. Replace `bidder_loop` task. Update `trigger_bidder_scan` tool to friendly no-op.
11. Manual smoke + handoff doc.

Roughly 11 tasks, similar scope to Phase 1 / Phase 2.A.

## Operational notes

- **VPS deployment:** PM2 process is named `scheduler`.
- **Migration application:** `uv run python -c "from storage.db import apply_migrations; apply_migrations()"` (per Phase 2.A handoff pattern).
- **Manual smoke after deploy:** DM the bot `set my goal to "..."`. Within 60-90s detection should fire. Within another 1-2 min processing should produce a channel post for the first match. The whole pipeline self-validates if you watch `pm2 logs scheduler --lines 0`.

## Files to read before implementing (priority order)

1. `bin/debug_phase2b_e2e.py` — working two-pass prototype. Implementation is a refactor of this.
2. `bidder/scan_cycle.py:run_one_cycle` — for the panel-walk + click pattern processing must mirror.
3. `bidder/draft_pipeline.py:draft_order` — what processing calls after relevance passes.
4. `assistant/brief_watcher.py` — example of a clean asyncio loop with cost tracking that runs alongside the bidder.
5. `storage/scan_briefs.py` — pattern for `claim_next`-style atomic queue stores; `feed_card_queue` mirrors it.
6. `storage/goals.py` — for `GoalStore.get_active()` usage.
7. `ai/relevance.py` — pattern for `create_agent` + `response_format=Pydantic`.
8. `assistant/dm_handler.py` + `bot/bot.py` — for how Phase 1 DM routing already works (don't break it).

## Open questions (deferred to implementation, not blocking)

- **Stale-row policy:** how many Pass-2 attempts before `mark_skipped(reason='card_no_longer_visible')`? v1 sketch: 3 attempts or `now() - detected_at > 10 min`.
- **Smart-hydrate replacement:** poll for first `Posted` anchor in UIA tree; proceed when found or 10s elapsed. Drops the flat 20s sleep to ~3-5s typical. Worth doing post-ship; not blocking.
- **Goal-deactivation during processing:** if `goal_id_at_detect` row is gone or no longer active, current spec says fall back to current goal, else mark_skipped. Confirm during implementation that this matches operator expectation; alternative is to always use current goal.
