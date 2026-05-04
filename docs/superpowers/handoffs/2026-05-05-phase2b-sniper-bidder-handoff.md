# Phase 2.B Handoff — Sniper Bidder (UIA + text-LLM triage)

**Date:** 2026-05-05
**Status:** Ready to implement. All architectural questions answered empirically.
**Branch baseline:** `phase1-foundation` (no merge to main).
**Predecessors:** Phase 1 (Discord ops assistant) + Phase 2.A (goals + briefed scans), both shipped.

---

## What this document is

A complete handoff so a fresh session can write the spec, plan, and implementation for Phase 2.B without re-running any diagnostics. Every architectural decision below is grounded in either reading existing code or running a `bin/debug_*.py` script and seeing real output. **Do not re-validate by running new scripts; just implement.**

The original draft spec was at `docs/superpowers/specs/2026-05-04-sniper-bidder-vision-triage-design.md`. **Treat it as superseded.** It described a vision-LLM-on-screenshots design; we abandoned that in favor of UIA-extracted text + text-LLM triage after measuring vision cost and discovering UIA exposes everything we need from the cards. The new spec needs to be written from scratch, but it can lift the non-architectural sections (data model for goals, briefed-scan integration, etc.) wholesale.

---

## What you (the next session) should do

1. **Read this handoff fully** before touching code.
2. **Skim the diagnostic scripts** that produced these findings: `bin/debug_feed_uia_extent.py`, `bin/debug_uia_url_extraction.py`, `bin/debug_phase2b_e2e.py`, `bin/debug_zoom_extreme.py`. They are not throwaway code — `debug_phase2b_e2e.py` is essentially a working prototype of the production architecture, two-pass and all.
3. **Write the revised spec** at `docs/superpowers/specs/2026-05-05-sniper-bidder-uia-text-triage-design.md`. Use the locked decisions in this doc. Mark the old vision-triage spec as superseded.
4. **Get user approval on the new spec** before writing the plan.
5. **Write the plan**, then implement.

---

## The vision in one paragraph

The bidder becomes goal-driven and sniper-style. No active goal → bidder idle. Active goal → every ~30 seconds the substrate refreshes the Upwork feed at 33% zoom, walks the UIA tree once, extracts ~9-10 structured `FeedCard` records (title, posted, budget, description preview, skills, country, payment-verified, total-spent, etc.) directly from the accessibility tree, dedups against `feed_card_queue`, and if there are genuinely new cards sends them in one batch text-LLM call to gpt-5-mini against the active goal. Survivors get queued. A separate processing coroutine consumes the queue: it does a fresh feed walk (also at 33%), finds matched cards by title in feed order using LIVE element refs from the same walk, clicks, runs the existing `panel.capture_panel` to grab the URL via the Copy-to-clipboard button (the panel-internal scroll loop the legacy code already has), runs the existing draft pipeline. The brief consumer (Phase 2.A) keeps priority over detection-driven cards in the same processing queue.

---

## Architecture decisions (all empirically validated)

### Substrate recipe — LOCKED at 33% zoom

Final substrate recipe for **detection** (extract data only, no clicks):

```
1. Refresh feed (navigate to /nx/find-work/most-recent in a new tab, wait for hydrate)
2. act.focus_window("Upwork")
3. act.key("ctrl+home")
4. pyautogui.hotkey("ctrl", "-")  ×6  with ~0.18s sleep between presses → 33% zoom
5. observe.observe(window_title=WINDOW, include_unnamed=False, include_text=True)
6. Parse Posted-anchor slices into FeedCard records
7. pyautogui.hotkey("ctrl", "0")  to reset zoom (so future tabs don't inherit)
```

**Why 33% and not 67% (the original idea):**

| Zoom | Cards in walk A | Walks needed for 10 cards | Panel walk | Click target size |
|---|---|---|---|---|
| 67% (Ctrl+- ×4) | 2 | 3 (with Down×21 ×2 between) | works | 688px × 21px |
| 50% (Ctrl+- ×5) | 3 | 3 | works | 516px × 16px |
| **33% (Ctrl+- ×6)** | **9** | **1** | **works** | **344px × 11px** |
| 25% (Ctrl+- ×7) | 8 | 1 | **silent fail** | 258px × 9px |

At 33%, the **`Down×21 ×2` dance disappears entirely**. One UIA walk gets all 9-10 cards. The panel-walk (`upwork.panel.capture_panel`) still works because the Copy-to-clipboard button stays at the top of the panel viewport — same dividend the original 67% recipe had.

At 25%, `panel.capture_panel` silently returned 0 elements. We don't know if that's a panel-load-timing issue at extreme zoom or if the Apply-button heuristic stops matching, but 25% is empirically out.

**Detection-pass total time at 33%:** ~15-20s (most is the refresh+hydrate; UIA walk is ~5s; zoom is ~1.5s).

### URL extraction — LOCKED on click+capture, NOT direct

We tested whether Chrome exposes hyperlink hrefs via UIA. It does not. Probed everything: `getattr(el, 'value')`, `GetValuePattern().Value`, `LegacyIAccessibleValue` (COM property 30049), `LegacyIAccessibleDescription` (30051), `HelpText` (30005), `AriaProperties` (30070), parent + child elements. None of them surface the URL. See `bin/debug_uia_url_extraction.py`.

**Conclusion:** the URL must be captured by clicking the card and reading the panel's "Copy to clipboard" button via the existing `upwork.panel.capture_panel` mechanism. Same path the legacy bidder uses. No way around it.

### Two-pass architecture — LOCKED

The script `bin/debug_phase2b_e2e.py` proves this works end-to-end. Both passes use the 33% substrate recipe.

**Pass 1 (detection — runs every ~30s):**
1. Substrate recipe → walk → extract `FeedCard` records (no clicks).
2. Dedup against `feed_card_queue.job_url` (we don't know URLs yet, so dedup is by `title` for v1; URL dedup is added once a card has been processed).
3. If there are genuinely new titles, send the batch to gpt-5-mini with the active goal (one call, ~30s, ~$0.001).
4. Insert each matched title into `feed_card_queue` with `status='queued'`.

**Pass 2 (processing — consumes the queue):**
1. Pull next queued row.
2. Substrate recipe → walk (fresh, gets live element refs).
3. Iterate `_parse_visible_cards(WINDOW)` returns; for each `(title, link_el)`, if title matches a queued row, click `link_el` immediately while the ref is fresh.
4. `panel.capture_panel(WINDOW)` → URL + panel elements.
5. `panel.parse_panel(elements)` → structured PanelData.
6. `ai.relevance_goal.check_goal_relevance(...)` → relevance check (defense in depth).
7. If relevant, run existing `bidder.draft_pipeline.draft_order` to produce the Doc + cover letter.
8. Esc to close panel; mark queue row processed.

**Critical rule from debugging:** never try to "find a card by title in a fresh walk after the queue says it should be there." That's what failed in `debug_phase2b_e2e.py` v1. Element refs only live within the observation that produced them. Pass 2 must walk-and-click in the same iteration; queue + matched-title-set are the cross-coroutine state, not element refs.

### FeedCard parser — LOCKED structure (anchored, not heuristic)

Card slice in the UIA tree has a deterministic shape we can rely on. From `bin/debug_feed_uia_extent.py` v3 dump:

```
[0]  text       'Posted'
[1]  text       '<N minutes/hours/days ago>' or 'yesterday'
[2]  text       <title>
[3]  hyperlink  <title>          ← THIS is what we click
[4]  text       <title>
[5]  button     'Job feedback <title>'
[6]  button     'Save job <title>'                  ← canonical title source
[7]  text       'Hourly: $X-$Y' / 'Hourly' / 'Fixed-price'
[8]  text       'Intermediate' / 'Expert' / 'Entry level'
[9]  text       'Est. Time:' or 'Est. Budget:'
[10] text       <duration string> or <budget amount>
[11] text       <description preview, the longest text element ≥80 chars>
[12] button     'more about "..."'
[13] text       'more'
[14] text       'about "..."'
[15..N] interleaved hyperlink/text pairs for skill chips
[N+1] text      'Verified'  (sometimes preceded by 'Skip skills' button)
[N+2] text      'Payment verified'
[N+3] text      'Rating is X.X out of 5.'
[N+4] text      '$<spent>'  (e.g. '$0', '$40K+', '$100K+')
[N+5] text      'spent'
[N+6] text      <country>
[N+7] text      'Proposals:'
[N+8] text      <range>  (e.g. 'Less than 5', '20 to 50', '50+')
```

The working extractor is in `bin/debug_phase2b_e2e.py:_parse_card_slice`. Lift it directly. It correctly handled all 9 cards in the live test, with these field accuracies:

| Field | Coverage | Notes |
|---|---|---|
| title | 9/9 | from `Save job <title>` button — most reliable source |
| posted_text | 9/9 | element[1] |
| budget_text | 8/9 | one card with no budget shown was the AI Voice Agent fixed-price one |
| experience_level | 8/9 | same |
| description_preview | 9/9 | longest text ≥80 chars |
| skills | 8/9 | hyperlink elements not equal to title |
| payment_verified | 7/9 | depends on element being present |
| rating | 7/9 | regex `'Rating is\s+([\d.]+)\s+out of 5'` |
| spent | 7/9 | `<$amount>, "spent">` pair |
| country | 7/9 | element after "spent" |
| proposals | 7/9 | element after "Proposals:" |

The 2/9 cards missing client-trust fields are jobs without enough client history to display them — that's signal worth keeping (treat absence as "thin client"). Not a parser bug.

### LLM triage — LOCKED text in / structured out

Mirror the existing `ai/relevance.py` shape. Use `langchain.agents.create_agent` with `response_format=Pydantic` and `model="gpt-5-mini"`.

System prompt (paraphrased):

> Strict job-feed triage filter. Given the operator's active goal and a list of jobs from the Upwork feed, return ONLY the matches. Use the EXACT title text from the input. Be lenient on prose match (description preview suggests AI work) but strict on hard numeric targets if the goal specifies them.

User payload:
```json
{
  "goal": "<goal.prose + structured fields>",
  "jobs": [<list of FeedCard.to_dict_for_llm() outputs>]
}
```

**Critical: serialize with `json.dumps(..., ensure_ascii=False)`.** Without this, em-dashes in titles become `—`, the LLM echoes them back as `\x14` (DC4) or other corruptions, and downstream title-matching breaks. We hit this in v1 of the e2e script. Fix is one kwarg.

Triage call: ~30s, ~$0.001. Real measurement, not estimated.

### Goal gate — LOCKED

No active goal → both detection and processing coroutines sleep ~30s, log `[detection] no active goal, idle` once per minute, do nothing. Phase 2.A's `GoalStore` already exists. Read `goals.get_active()` at the top of every loop iteration.

### Setups — DECOMMISSIONED as scan-time filter

The bidder no longer iterates over `setups_store.list_active()` for matching. The goal is the filter. Setups stay as DB rows for legacy data and the agent's existing setup-mutation tools (`update_setup_filters`, `add_ignored_client`, etc. all keep working) but they no longer influence what the bidder scans. Phase 2.D may delete setups entirely; for now they're just inert.

`bidder/scan_cycle.py:run_one_cycle` and `bidder/signal_pipeline.py:process_job_through_setups` are still used by **briefed scans** (Phase 2.A), which synthesize a single ephemeral retired setup from the brief. Don't remove that path. Just bypass it for detection-driven cards, which take a different path through `bidder/processing_loop.py` (new) → `bidder/job_processing.py:process_job_url` (new) → goal-driven relevance check → draft.

### Briefed scans — KEEP priority

Processing coroutine on each iteration:
1. First check `BriefStore.consume_pending()`. If a brief is pending, run the existing Phase 2.A briefed-cycle path. Continue.
2. Else, claim next from `feed_card_queue` and process it.

Briefs win because the operator explicitly asked for them. This matches Phase 2.A's existing semantics.

### Webhook for detection-driven matches — KEEP

Detection-driven matches that pass relevance and get drafted post to `#job-notifications` via the existing webhook + `OrderApprovalView` button flow. **No DM-native approvals in Phase 2.B** — that's Phase 2.C. Keep the existing channel embed.

Briefed-scan signals stay tagged with `market_state.source='briefed_scan'` (Phase 2.A) and continue to be suppressed from the channel; the brief-watcher DMs the operator a summary. No change.

---

## Bugs / known issues to track

These came up during diagnostics and aren't blockers, but the implementer should know:

1. **Per-tab zoom inheritance flake.** Chrome remembers per-tab zoom. If a substrate sub-step opens a stray new tab, the recipe's `Ctrl+- ×6` could land on the wrong tab and stack on top of the existing zoom (e.g. a tab already at 33% becomes 25% or worse). The legacy `refresh_feed` opens a new tab via Ctrl+T then navigates; the recipe should ALWAYS pair refresh+zoom and never trust persistent state. Always reset (`Ctrl+0`) at the END of a detection pass so the next refresh+zoom starts from a clean baseline.

2. **`panel.capture_panel` occasional silent failure (`0 elements, url=''`).** Saw this twice across multiple test runs at boundary zoom levels (67% once, 25% always). Likely a panel-load-timing issue when the Apply button heuristic doesn't see what it expects. Add a retry loop: if `len(elements) == 0`, sleep 1.5s and re-attempt the capture once. Never tight-loop forever.

3. **`panel.parse_panel.title` is unreliable.** It sometimes returns the description's first sentence as the "title". Don't depend on it; use the FeedCard.title we already extracted from the Save-job button. The `parse_panel` other fields (budget, country, skills, description, payment_verified, total_spent) are reliable.

4. **`act.key("down")` runs through the pacing layer with humanish delay.** 21 down-arrows take ~7-15s of pacing pause. At 33% we don't need them; the recipe is now `Ctrl+- ×6` + one walk + done. So this isn't a Phase 2.B problem. But for any code that DOES need batched key presses, prefer `pyautogui.press("down", presses=21, interval=0.05)` to get all 21 in ~1s.

5. **Hydrate sleep.** `upwork.feed.refresh_feed` has a flat `time.sleep(20.0)` after navigate. That's the dominant cost in the recipe. v1 of Phase 2.B can keep it; **a future optimization** is "poll UIA for first 'Posted' anchor; proceed when it appears or 10s elapsed." Drops the 20s flat to ~3-5s typical. Worth doing eventually but not blocking.

6. **Chrome window-title can be stale.** During one diagnostic, `uia.WindowControl(SubName="Upwork")` returned a window titled "Upwork Login - Log in to your Upwork account" even though the actual visible page was the feed. The substrate's `act.focus_window` already handles this with retries; the implementer just needs to be aware that `WindowControl.Name` lags the actual page by seconds.

---

## Data model — new + reused

### New table — `feed_card_queue`

```sql
CREATE TABLE feed_card_queue (
    queue_id           bigserial PRIMARY KEY,
    job_url            text UNIQUE,                    -- nullable; populated AFTER pass 2 captures it
    job_title          text NOT NULL,                  -- the bridge between detection and processing
    posted_text        text,
    detected_at        timestamptz NOT NULL DEFAULT now(),
    triage_reasoning   text,                            -- LLM's reason for matching
    status             text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'processing', 'processed', 'failed', 'skipped')),
    claimed_at         timestamptz,
    finished_at        timestamptz,
    error_text         text,
    order_id           bigint REFERENCES orders(order_id),
    goal_id_at_detect  bigint REFERENCES goals(goal_id) -- audit which goal matched
);

CREATE INDEX feed_card_queue_pending ON feed_card_queue (detected_at)
    WHERE status = 'queued';
```

**Idempotency:**
- Detection inserts with `ON CONFLICT (job_url) DO NOTHING` — but `job_url` is null at detection time. Use a partial unique index on `(job_title, status)` WHERE `status = 'queued'` to prevent duplicate-title queueing while a row is in flight.
- Once Pass 2 captures the URL, `UPDATE ... SET job_url = ?` on the row.
- After job_url is known, future detection passes can dedup against `job_url` cleanly.

### Reused — Phase 2.A schemas

`goals` (single active row, partial unique index), `scan_briefs` (Phase 2.A queue), `system_config['bidder_paused']`, `assistant_audit_log`, `assistant_messages`, `assistant_conversations`. No changes to any of these.

### Decommissioned (deferred to Phase 2.D, do nothing now)

- `setups` table — keep, but no longer read by detection. The agent's setup-mutation tools still write to it.

---

## Module structure

```
storage/
  card_queue.py            # NEW: FeedCardQueueStore (request, claim_next, mark_processed, mark_failed, mark_skipped, set_url)

upwork/
  feed_cards.py            # NEW: FeedCard dataclass + parse_card_slice (lifted from bin/debug_phase2b_e2e.py)
  feed_zoom.py             # NEW: zoom_to_33pct() + reset_zoom() helpers around pyautogui.hotkey

ai/
  feed_triage.py           # NEW: text-LLM triage call. mirrors ai/relevance.py shape. Pydantic TriageResponse. ensure_ascii=False!
  relevance_goal.py        # NEW: goal-driven relevance check (Phase 2.A spec called for this; still needed)

bidder/
  detection_loop.py        # NEW: async run_detection_loop(...) -- the every-30s pass-1 worker
  processing_loop.py       # NEW: async run_processing_loop(...) -- consumes queue + briefs, calls process_job_url
  job_processing.py        # NEW: process_job_url(job_url, goal, ...) -- panel walk + relevance + draft, refactored from run_one_cycle's per-card body
  scan_cycle.py            # MODIFIED: keep run_one_cycle but it's only used by briefed scans now
  signal_pipeline.py       # KEEP: used by briefed scans

scheduler/
  main.py                  # MODIFIED: replace bidder_loop task with two new tasks (detection + processing)
```

The script `bin/debug_phase2b_e2e.py` is structured exactly like this, just inlined. Lift the four functions into the four new modules:
- `_parse_card_slice` → `upwork/feed_cards.py`
- `_extract_cards_from_current_view` + the substrate recipe → `bidder/detection_loop.py`'s body
- `llm_triage` → `ai/feed_triage.py`
- `_click_and_capture_one` + `_process_visible_matches` → `bidder/processing_loop.py`'s body, with `panel.capture_panel` + `panel.parse_panel` for the data extraction step

---

## Failure handling

| Failure | Behavior |
|---|---|
| No active goal | Both loops sleep 30s, log once per minute, idle. |
| `bidder_paused = true` | Both loops sleep 30s, re-check. |
| Chrome unavailable / window not found | Existing behavior: log + Discord ping; sleep 60s; retry. |
| Login required | Existing `LoginExpired` exception; ping channel; sleep 60s; retry. |
| Triage LLM fails / malformed JSON | Skip cycle. No survivors enqueued. Try again next cycle. |
| Triage returns title not in card list | Filter at queue insert (validate against extracted titles); log warning; drop. (Em-dash bug — `ensure_ascii=False` should prevent the common case but keep the validation defense-in-depth.) |
| `panel.capture_panel` returns 0 elements | Sleep 1.5s, retry once. Then `mark_failed`. |
| Card disappeared from feed by time pass 2 walks | The walk simply won't return that title; queue row stays `queued`; eventually mark stale and skip after N attempts. |
| Drafting fails | `mark_failed` with error_text; processing continues to next queue row. |
| Relevance check rejects after triage said yes | `mark_skipped` with reason. Common case. |

---

## Cost tracking

- Triage calls: `agent_runs` row with `agent_name='feed_triage'`, `trigger='scheduled'`, `trigger_context={"n_cards": N, "goal_id": M}`.
- Goal-relevance calls: `agent_runs` row with `agent_name='relevance_goal'` (Phase 2.A spec already named this; just hadn't been built).
- Existing per-job extract / proposal / cover-letter rows continue.

The `agent_runs.trigger` CHECK constraint allows `scheduled|discord_question|manual|per_job` (per Phase 1 handoff). All Phase 2.B code paths use `scheduled` for autonomous loops.

**Daily cost ceiling at 30s detection cadence:**
- Vision NOT used; pure text triage at ~$0.001/call when there are new cards. Most cycles will dedup-first and not call LLM at all. Estimate ~50-100 LLM triage calls/day = ~$0.05-0.10/day in triage.
- ~10-20 jobs/day pass triage and reach relevance check + drafting = ~$1-2/day in heavyweight pipeline.
- **Total: ~$1-2/day at hunting volume.** No vision cost overhead.

---

## Test plan

**Unit:**
- `tests/test_feed_card_queue.py` — enqueue idempotency, claim_next atomicity, mark_*, set_url after click, pending dedup against in-flight rows.
- `tests/test_feed_cards.py` — lift the raw element dumps from the diagnostic output as fixtures; assert each FeedCard field is parsed correctly. (The dumps in `results_e2e.md` for cards 1-5 are perfect fixture material.)

**Integration:**
- `tests/test_feed_triage.py` — gated on `OPENAI_API_KEY`. Hardcode a few FeedCards + a goal, assert the LLM returns matched titles in the structured response. Verify `ensure_ascii=False` keeps em-dash titles intact.

**Manual smoke:**
- Reproduce the live `bin/debug_phase2b_e2e.py` flow against the production scheduler. Set a goal in DM. Watch `pm2 logs scheduler` for `[detection] cycle start`, `[detection] survivors=N`, `[processing] processing job_title=...`. Confirm matching jobs appear in `#job-notifications` with URLs.

---

## Operational notes

- **VPS deployment:** PM2 process is named `scheduler`, not `bidder-bot`. The Phase 1 handoff has the exact command for migration application.
- **Migration:** new migration file `006_feed_card_queue.sql`. Apply via `uv run python` snippet from Phase 2.A handoff.
- **Manual smoke after deploy:** DM the bot `set my goal to "..."`. Within 30-60s detection should fire. Within another 1-2 min processing should produce a channel post for the first match. The whole pipeline self-validates if you watch `pm2 logs scheduler --lines 0`.

---

## What's deliberately NOT in Phase 2.B

- DM-native approvals → Phase 2.C
- Anomaly watcher / proactive pings → Phase 2.C
- Daily / shift digests → Phase 2.C
- Outcome tracking + follow-up DMs → Phase 2.D
- Goal-progress reporting (computing "1/5 interviews this week") → Phase 2.D
- Replacing `setups` entirely → Phase 2.D (or never)
- Smart hydrate replacement (poll-for-Posted-anchor instead of flat 20s sleep) → optimization, post-ship
- Two-Chrome architecture → not needed; 33% recipe + single window is sufficient
- Vision-based triage → abandoned; superseded spec at `docs/superpowers/specs/2026-05-04-sniper-bidder-vision-triage-design.md`

---

## Files to read before implementing

In priority order:
1. `bin/debug_phase2b_e2e.py` — working two-pass prototype. The implementation is a refactor of this file.
2. `bidder/scan_cycle.py:run_one_cycle` — for the panel-walk + click pattern that processing must mirror.
3. `bidder/draft_pipeline.py:draft_order` — what processing calls after relevance passes.
4. `assistant/brief_watcher.py` — example of a clean asyncio loop with cost tracking that runs alongside the bidder.
5. `storage/scan_briefs.py` — pattern for `claim_next`-style atomic queue stores; `feed_card_queue` mirrors it.
6. `storage/goals.py` — for `GoalStore.get_active()` usage.
7. `ai/relevance.py` — pattern for `create_agent` + `response_format=Pydantic`.
8. `assistant/dm_handler.py` + `bot/bot.py` — for how Phase 1 DM routing already works (don't break it).

---

## Implementation order suggestion

1. Migration `006_feed_card_queue.sql`.
2. `storage/card_queue.py` + tests.
3. `upwork/feed_cards.py` (lift `_parse_card_slice` + `FeedCard` from diagnostic).
4. `upwork/feed_zoom.py` (3 lines of pyautogui calls).
5. `ai/feed_triage.py` + tests (gated on API key).
6. `ai/relevance_goal.py` (mirrors `ai/relevance.py`, takes a Goal not a Setup).
7. `bidder/job_processing.py` — refactor reusable panel-walk + draft path.
8. `bidder/detection_loop.py`.
9. `bidder/processing_loop.py` (consumes briefs FIRST, then queue).
10. Wire two new tasks into `scheduler/main.py:setup_hook`. Replace `bidder_loop` task.
11. Manual smoke test, handoff doc.

Roughly 11 tasks, similar scope to Phase 1 / Phase 2.A.

---

## Final note for the next session

Everything here is empirically validated. Don't second-guess the 33% zoom decision; we tested 25% (broke), 33% (perfect), 50% (worked but slower), 67% (worked but slowest). Don't second-guess the click+capture pattern; we proved URL extraction from UIA is impossible on Chrome. Don't second-guess the two-pass model; we proved find-by-title-across-walks doesn't work because element refs die.

Build with confidence.
