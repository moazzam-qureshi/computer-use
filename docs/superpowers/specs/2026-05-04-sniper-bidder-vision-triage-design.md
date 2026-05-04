# Phase 2.B — Sniper Bidder (Vision Triage + Goal-Driven Detection)

**Status:** Approved 2026-05-04
**Branch baseline:** `phase1-foundation`
**Implementation target:** new `bidder/detection_loop.py` + `bidder/processing_loop.py`, decommissions `bidder_loop` and `run_one_cycle`'s setup-iteration logic

## Goal

Make the bidder a sniper, not a spammer. The operator sets a single active
goal. The bidder uses **vision triage on three deterministic screenshots of
the feed** to identify goal-matching jobs in seconds, then runs the
heavyweight pipeline (panel walk + LLM relevance + drafting) only on
survivors. No active goal → bidder does nothing.

The headline numbers we're targeting:

- Detection latency: ~30s worst case, ~15s typical (vs. today's 10-20 min average).
- Heavyweight cost: paid only for jobs that match the goal (vs. today's "every visible new job").
- Vision-triage cost: ~$0.001 per detection pass × 30s cadence = ~$2.50/day.

## Non-goals (Phase 2.B)

- DM-native approvals. Drafts still go to `#job-notifications`. Phase 2.C.
- Two-Chrome architecture. Single Chrome with lock-mediated detection /
  processing. Two-Chrome was solving the wrong problem; the right fix is
  reducing how often we open the panel.
- Anomaly watcher beyond brief-completion. Phase 2.C.
- Outcome tracking + follow-up DMs. Phase 2.D.
- Goal-progress reporting. Phase 2.D.
- Sniper-priority queue ordering (FIFO is fine for v1).
- Triaging older / scrolled-down jobs. We cover the top ~10 only.
- Bypassing the LLM relevance check after vision triage. We keep it as
  defense in depth.
- Replacing the existing scheduled briefed-scan path. Briefed scans
  (Phase 2.A) keep working unchanged.

## Operator-confirmed decisions

| Decision | Choice |
|---|---|
| Bidder execution gate | Goal-only. No active goal → no work. |
| Triage method | Vision (`gpt-5-mini` multi-image) over rule-based. |
| Substrate recipe | Ctrl+- ×4 (zoom 67%), Down×21, Down×21. Three screenshots, ten cards. |
| Inter-cycle reset | Refresh feed every cycle. Clean slate. |
| Down-arrow behavior | Down-arrow scrolls the page (no need to focus the feed list element first). |
| Vision triage relationship to setups | Goal-only. Setups stop being a scan-time filter. |
| Detection / processing model | Two coroutines, single Chrome, shared `ui_lock`. |
| Defense in depth | LLM relevance check still runs on vision survivors before drafting. |

## Architecture

### Substrate dance — the deterministic recipe

The detection coroutine acquires the Chrome `ui_lock` and runs:

```
1. Focus Chrome window
2. Refresh feed (navigate to https://www.upwork.com/nx/find-work/most-recent)
3. Wait ~3s for hydrate
4. Ctrl+0 (reset zoom in case prior cycle left it zoomed)
5. Ctrl+- × 4 (zoom level 67%; cards 1-2 visible)
6. Take screenshot of Chrome window → image_1
7. Down × 21 → cards 3-6 visible
8. Take screenshot of Chrome window → image_2
9. Down × 21 → cards 7-10 visible
10. Take screenshot of Chrome window → image_3
11. Ctrl+Home (back to top)
12. Ctrl+0 (reset zoom for the parse step)
13. Run upwork.feed._parse_visible_cards (UIA tree walk)
    → list of (canonical_title, hyperlink_element) for cards in viewport
14. Scroll down through the feed (Ctrl+Home → Down/PageDown ×N) collecting
    additional (title, hyperlink) pairs until we have ~10 mapped, OR
    end-of-feed marker reached
```

After step 14 we release `ui_lock`. The vision call runs without holding
the lock so processing can begin while triage is in flight.

```
15. Vision call: send image_1 + image_2 + image_3 + active goal to
    gpt-5-mini → returns list of matched job titles
16. For each matched title:
      - Look up the corresponding hyperlink_element via the UIA-built map
      - If found: read the URL from the element (Upwork exposes the href
        on the title hyperlink); if not, log + drop as hallucination
      - Insert into feed_card_queue (ON CONFLICT (job_url) DO NOTHING)
17. Sleep N seconds (default 30s) → loop
```

### Vision triage prompt

`ai/vision_triage.py:VISION_TRIAGE_SYSTEM` is roughly:

> You are a strict job-feed triage filter. The operator's active goal is
> the only thing that matters. You receive 3 screenshots of the Upwork
> "Most Recent" feed (cards 1-2, 3-6, 7-10). Return a JSON object:
> `{"matched_titles": [...]}` containing the EXACT title text of each
> card that matches the goal. Be strict on numeric targets (min hourly,
> min budget, preferred country) when the goal specifies them. Be lenient
> on prose match — if the description preview shows the work could fit
> the goal's domain, include it. Do not invent titles; copy them
> verbatim from the screenshots. If no card matches, return
> `{"matched_titles": []}`.

The user message is the goal payload + the three images. Output is JSON,
parsed by the caller. Hallucinated titles (titles that don't appear in
the UIA-extracted card list) are logged and dropped at the queue layer.

### Detection / processing split

```
Detection coroutine                    Processing coroutine
─────────────────                      ────────────────────
every DETECTION_INTERVAL_SECONDS:      loop:
  acquire ui_lock                        pull next from feed_card_queue
    refresh feed                          (status='queued', oldest first)
    zoom + screenshot ×3 + scroll        if no row: sleep 5s, retry
    UIA card parse                       acquire ui_lock
  release ui_lock                          navigate to job_url directly
                                           panel walk + LLM extract
  vision triage (no Chrome lock)           if relevance check passes:
                                             draft_order (Doc + cover letter)
  for each survivor:                         post to #job-notifications
    enqueue feed_card_queue                mark queue row as processed
                                         release ui_lock
```

Detection's lock-hold time per cycle: ~10-15s (refresh + hydrate + zoom +
3 screenshots + 2×21 keypresses + UIA observe). Vision call: ~3-5s, no
lock held. Processing's lock-hold time per survivor: ~30-90s. New
survivors detected during a long processing operation queue up; they
get picked up the moment processing is done.

### Goal gate

At the top of each detection iteration:

```python
goal = GoalStore(db).get_active()
if goal is None:
    if not _logged_no_goal_recently:
        log "[detection] no active goal, idle"
        _logged_no_goal_recently = True
    sleep DETECTION_INTERVAL_SECONDS
    continue
```

The processing coroutine is also goal-gated:

```python
goal = GoalStore(db).get_active()
if goal is None:
    sleep 5  # in case operator sets a goal during a quiet window
    continue
```

### Bidder pause flag

The existing `system_config.bidder_paused` flag (Phase 1) gates both
loops. Detection and processing both check it at the top of each
iteration; if `true`, sleep 30s and re-check. `pause_bidder` /
`resume_bidder` tools work unchanged.

### Bidder force-run flag

The existing `bidder_state.force_run_requested` flag (Phase 1) is
**no longer relevant** because detection runs every 30s by default; you
can't meaningfully "force a run" when the bidder is already firing twice
a minute. The `trigger_bidder_scan` tool stays as a no-op-with-a-friendly-
reply for backward compatibility ("the bidder runs every 30s; nothing
to force"). The `force_run_requested` column stays in `bidder_state` for
schema stability but is no longer consumed by the detection loop.

### Briefed scans (Phase 2.A) keep working

Briefed scans bypass detection entirely:

- `trigger_briefed_scan` enqueues a row in `scan_briefs`.
- The processing coroutine, on each iteration, **first** checks
  `BriefStore.consume_pending()`; if a brief is pending, runs the
  ephemeral cycle path (Phase 2.A) against the brief's filter_dsl.
- Otherwise consumes the next `feed_card_queue` row (Phase 2.B detection).

So the queue priority is: briefs > vision-detected cards. Briefs win
because the operator explicitly asked for them.

### Decommissioned / replaced

- `scheduler.main.bidder_loop` — replaced by two coroutines launched
  in `setup_hook`: `run_detection_loop` and `run_processing_loop`.
- `bidder.scan_cycle.run_one_cycle`'s "scroll feed and process every
  card" logic — gone. Reusable pieces (open panel by clicking link,
  walk panel, extract, run relevance, draft) get refactored into
  `bidder/job_processing.py:process_job_url(url, ...)` which the new
  `processing_loop` calls.
- `bidder.signal_pipeline.process_job_through_setups` — keeps working
  for briefed scans (Phase 2.A path uses it). Detection-driven cards
  go through a new path that uses **only the active goal** for the
  LLM relevance check.

## Data model

### New table — `feed_card_queue`

```sql
CREATE TABLE feed_card_queue (
    queue_id           bigserial PRIMARY KEY,
    job_url            text NOT NULL UNIQUE,
    job_title          text NOT NULL,
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
```

`UNIQUE(job_url)` makes detection's `INSERT ... ON CONFLICT DO NOTHING`
naturally idempotent. `goal_id_at_detect` records which goal was active
when the card was detected, so we have an audit trail when the operator
changes their goal mid-cycle.

### `goals` table — unchanged from Phase 2.A.

### `scan_briefs` table — unchanged from Phase 2.A.

### `setups` table — schema unchanged. Bidder no longer reads
`status='active'` for scan-time filtering, but the agent's setup-mutation
tools (`update_setup_filters`, `add_ignored_client`, etc.) keep working
on the rows. Setups become a manual-config concept retained for legacy
data and possible future re-use; v2 may delete them entirely.

## Stores

### `storage/card_queue.py` — `FeedCardQueueStore`

- `enqueue(job_url, job_title, posted_text, triage_reasoning, goal_id) -> Optional[int]`:
  INSERT ... ON CONFLICT (job_url) DO NOTHING. Returns the new
  queue_id, or None if the URL was already in the queue.
- `claim_next() -> Optional[QueueItem]`: atomic UPDATE ... WHERE status='queued'
  ... ORDER BY detected_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED → status='processing',
  claimed_at=now(). Returns the claimed row or None.
- `mark_processed(queue_id, order_id)`: status='processed', finished_at=now(),
  order_id=...
- `mark_failed(queue_id, error)`: status='failed', error_text=truncated,
  finished_at=now()
- `mark_skipped(queue_id, reason)`: status='skipped' (used when the
  relevance check rejects after vision said yes — the heavyweight LLM
  disagreed with the vision filter)

## Modules

### `upwork/screenshot.py`

```python
def capture_chrome_window_image(window_title_hint: str = "Upwork") -> Image:
    """Find the Chrome window matching the hint, get its bounds, capture
    via PIL.ImageGrab.grab(bbox=window_bounds). Returns PIL Image.

    Uses Win32 GetWindowRect after focus_window finds the HWND. Resizes
    to max 1568px on the long edge (Anthropic vision input sweet spot)
    before returning."""
```

### `upwork/feed_zoom.py`

```python
def zoom_out(times: int = 4) -> None:
    """Press Ctrl+- the specified number of times. Caller has focused window."""

def reset_zoom() -> None:
    """Press Ctrl+0."""
```

### `ai/vision_triage.py`

```python
def vision_triage(
    images: list[Image],
    goal: Goal,
    *,
    agent_run_store: AgentRunStore,
) -> tuple[list[str], str]:
    """Send screenshots + goal to gpt-5-mini, return (matched_titles, raw_reasoning).

    Multi-image content array. Strict JSON output via response_format.
    Hallucinated titles (those not in UIA card list) are filtered at the
    caller, not here. Cost-tracked via CostTracker(agent_name='vision_triage')."""
```

### `bidder/detection_loop.py`

```python
async def run_detection_loop(db: Database, settings: Settings, ui_lock: asyncio.Lock):
    """Forever-loop. Every DETECTION_INTERVAL_SECONDS (default 30):
       1. Goal gate (skip if no active goal)
       2. Pause gate (skip if system_config.bidder_paused)
       3. Acquire ui_lock; do the substrate dance; release lock
       4. Vision triage (no lock)
       5. Enqueue survivors
       6. Sleep until next interval"""
```

### `bidder/processing_loop.py`

```python
async def run_processing_loop(bot, db: Database, settings: Settings,
                                humanizer: Humanizer, ui_lock: asyncio.Lock):
    """Forever-loop. Each iteration:
       1. Pause gate
       2. Goal gate (no goal → sleep 5s, retry; cards drafted under one
          goal but processed after operator switches goal use the
          goal_id_at_detect snapshot for the relevance check, NOT the
          new goal — fairness to the operator's original intent)
       3. First check briefs (Phase 2.A): if pending brief exists,
          consume + run briefed_cycle; continue
       4. Else: claim_next from feed_card_queue
       5. If a row was claimed:
          - acquire ui_lock
          - process_job_url(job_url, goal_at_detect, ...)
          - if relevance passes and draft succeeds: post to channel,
            mark_processed
          - if relevance rejects: mark_skipped
          - if anything raises: mark_failed
          - release ui_lock
       6. If no row: sleep 5s"""
```

### `bidder/job_processing.py` (new)

Refactored chunk of the old `run_one_cycle`:

```python
def process_job_url(
    job_url: str,
    goal: Goal,
    *,
    setups_store: SetupStore,
    job_store: JobStore,
    enrichment_store: EnrichmentStore,
    signal_store: SignalStore,
    order_store: OrderStore,
    portfolio: PortfolioStore,
    agent_run_store: AgentRunStore,
) -> Optional[Order]:
    """Navigate to job URL → panel walk → LLM extract → LLM relevance check
    against the GOAL (not setups) → if matches: draft order + return.
    If relevance rejects: return None.

    The relevance check uses a new ai.relevance_goal.check_goal_relevance
    function instead of ai.relevance.check_relevance — the new one takes
    a Goal, not a Setup. Same shape, different prompt context."""
```

The "navigate to job URL directly" path replaces "click the title in
the feed". Cleaner: deterministic page-load instead of opportunistic
in-feed click that breaks if the feed scrolled.

### `ai/relevance_goal.py` (new)

```python
def check_goal_relevance(
    job: Job,
    goal: Goal,
    *,
    agent_run_store: AgentRunStore,
) -> RelevanceCheck:
    """LLM relevance gate against the active goal. Same pattern as
    ai.relevance.check_relevance but with a different prompt. The
    legacy check_relevance keeps working for briefed scans (which
    synthesize an ephemeral Setup with prose=brief.prose)."""
```

## Wiring

### `scheduler/main.py` changes

Replace the `bidder_loop` task with two new tasks:

```python
async def setup_hook():
    print("setup_hook fired; starting detection + processing + apply_executor + brief_watcher loops", flush=True)
    bot.loop.create_task(run_detection_loop(db, settings, ui_lock))
    bot.loop.create_task(run_processing_loop(bot, db, settings, humanizer, ui_lock))
    bot.loop.create_task(apply_executor_loop(bot, settings, db, humanizer))
    from assistant.brief_watcher import run_brief_watcher
    bot.loop.create_task(run_brief_watcher(db, bot, settings))
```

The DM `on_message` handler stays unchanged. Slash commands stay
unchanged. The `apply_executor_loop` stays unchanged.

### Environment variables

| Var | Default | Purpose |
|---|---|---|
| `DETECTION_INTERVAL_SECONDS` | `30` | Hot loop cadence. |
| `VISION_TRIAGE_MODEL` | `gpt-5-mini` | Override vision model. |
| (existing) `ASSISTANT_MODEL` | `gpt-5-mini` | Unchanged. |

## Failure handling

| Failure | Behavior |
|---|---|
| No active goal | Detection sleeps. Logs once per minute, not per cycle. |
| `bidder_paused=true` | Both loops sleep 30s and re-check. |
| Chrome window not found | Detection logs + Discord ping; sleeps 60s; retries (existing behavior). |
| Login required | Detection raises `LoginExpired`; ping Discord; sleep 60s; retry (existing behavior). |
| Refresh hangs / page never hydrates | After 30s waiting for hydrate signal, log + skip cycle. |
| Vision API fails | Skip cycle. No survivors enqueued. Try again next cycle. |
| Vision returns hallucinated title | Filter at queue layer (validate against UIA card list); log warning; drop. |
| Vision returns malformed JSON | Try once more with stricter prompt; if still bad, skip cycle. |
| Job-URL navigation fails | `mark_failed` in queue with error; processing moves to next row. |
| Panel walk fails | Same as above. |
| Relevance check rejects | `mark_skipped` with reason; not a failure. |
| Drafting (Doc / cover letter) fails | `mark_failed`; processing continues. |

## Cost tracking

- Vision triage: `agent_runs` row per call with `agent_name='vision_triage'`,
  `trigger='scheduled'`, `trigger_context={"images": 3, "goal_id": N}`.
- Goal-relevance LLM: `agent_runs` row per call with `agent_name='relevance_goal'`.
- Existing per-job extract / proposal / cover-letter rows continue.

Daily cost ceiling at 30s detection cadence with no goal-matches: ~$2.50
in vision calls. With ~10 goal-matches/day requiring full pipeline:
+~$1-2 in extract + relevance + drafting. Total: ~$3-5/day at idle, ~$5-10/day
at hunting volume.

## Testing

- **Unit**: FeedCardQueueStore (enqueue idempotency, claim_next atomicity, mark_*).
- **Unit**: vision_triage with a recorded set of feed screenshots (skipped
  if `OPENAI_API_KEY` unset; otherwise verifies the model's JSON shape
  and that hallucination filtering works).
- **Unit**: feed_zoom helpers (mock `act.key`, verify call sequence).
- **Integration**: detection-loop end-to-end with mocked screenshot capture
  (uses fixtures of real screenshots; verifies enqueue happens, dedup
  works, hallucinated titles drop).
- **Integration**: processing-loop with a queued URL; mocks the substrate
  navigation and confirms the relevance / draft / mark_processed path.
- **Manual smoke**: Set a goal in DM. Watch `pm2 logs scheduler` for
  `[detection] cycle start`, `[detection] survivors=N`,
  `[processing] processing job_url=...`. Confirm matching jobs appear
  in `#job-notifications`. Confirm non-matching jobs are skipped silently.

## Open questions deferred to implementation

- **Screenshot transport**: Anthropic vision API accepts base64 or URL; we'll
  use base64 to avoid the upload step. Image preprocessing (resize, format)
  determined empirically — start with PNG, downscale if cost matters.
- **Hydrate-wait heuristic**: today `refresh_feed` does a flat 20s sleep
  after navigate. Detection cadence at 30s makes this prohibitive — we
  need a smarter wait. Try: sleep 3s, then poll for "Posted" anchor in
  UIA tree; if found within 10s, proceed; else log + skip.
- **What if the operator changes goal mid-cycle**: the detection-loop's
  next cycle picks up the new goal automatically. In-flight queue rows
  recorded `goal_id_at_detect`; processing uses that goal for the
  relevance check (operator's original intent). New goal applies to the
  next detection pass.
- **Force-run behavior**: `trigger_bidder_scan` becomes a friendly no-op
  ("the bidder runs every 30s; here's the most recent cycle's stats…").
  Implementation: tool returns a status dict instead of writing the
  obsolete force-run flag.

## Implementation order (sketch — to be elaborated in the plan)

1. Migration `006_feed_card_queue.sql`
2. `storage/card_queue.py` (FeedCardQueueStore) + tests
3. `upwork/screenshot.py` — capture Chrome window image
4. `upwork/feed_zoom.py` — zoom helpers
5. `ai/vision_triage.py` — multi-image vision call + parsing
6. `ai/relevance_goal.py` — goal-driven relevance check
7. `bidder/job_processing.py` — refactor reusable panel-walk + draft path
8. `bidder/detection_loop.py` — full detection coroutine
9. `bidder/processing_loop.py` — queue consumer (also handles brief-priority)
10. Wire into `scheduler/main.py:setup_hook` (replace `bidder_loop`)
11. Update `trigger_bidder_scan` tool to be a friendly no-op
12. Tests + manual smoke + handoff doc
