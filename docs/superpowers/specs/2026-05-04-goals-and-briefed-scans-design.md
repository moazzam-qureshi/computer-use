# Phase 2.A — Goal Setting and Briefed Scans

**Status:** Approved 2026-05-04
**Branch baseline:** `phase1-foundation`
**Implementation target:** extends `assistant/`, adds `bidder/briefed_cycle.py` + `assistant/brief_watcher.py`

## Goal

Give the assistant a north star and a hands-on execution channel. Specifically:

1. **Goal-setting.** A single active operator goal — free-text north star plus optional structured targets — persisted in the DB, loaded into every assistant turn's system prompt. The agent uses it to frame answers, judge configuration changes, and shape briefed scans.

2. **Two execution modes for the bidder.** The existing scheduled cycle (every 15-20 min, all active setups) stays untouched. A new mode — *briefed scans* — lets the agent compose a one-shot brief (prose + filter_dsl) and fire it on demand. The bidder picks it up async, runs an ephemeral cycle, marks done. A new background task — the *brief-watcher* — DMs the operator a natural-language summary when the brief completes.

The combined effect: the operator sets a goal in DM, says "go scan for X jobs," the bidder works async, and the agent reports back with context. No new Discord channels, no new buttons, no slash commands.

## Non-goals (Phase 2.A)

- DM-native approvals. Drafts still go to `#job-notifications` via the existing webhook + buttons. Approval routing comes in Phase 2.B.
- Anomaly watcher / proactive pings beyond brief-completion summaries. Phase 2.C.
- Daily / shift digests. Phase 2.C.
- Outcome tracking and follow-up DMs. Phase 2.D.
- Goal-progress reporting (e.g. "1/5 interviews this week"). Requires outcome tracking; deferred.
- Multi-goal hierarchies. Single active goal only.
- Auto-suggesting a goal when none is set. Agent can prompt the operator to set one, but does not invent.
- Modifying the scheduled cycle's behavior. Briefed scans run on a separate code path; the scheduled loop is unchanged.

## Operator-confirmed decisions

| Decision | Choice |
|---|---|
| Goal cardinality | One active goal at a time. New `set_goal` deactivates the old one. |
| Goal precision | Free-text prose plus optional structured targets (target_metric, target_value, horizon, min_hourly, min_budget, preferred_country, notes). |
| Scan-trigger model | Two distinct tools: `trigger_bidder_scan` (kicks the existing scheduled cycle, no brief) and `trigger_briefed_scan(prose, filter_patch)` (one-shot ephemeral cycle). |
| Briefed-scan execution mode | Async. Bidder consumes the brief on its next loop iteration; agent does not block. |
| Brief result delivery | The brief-watcher background task DMs the operator a natural-language summary when the brief finishes. |
| Empty-result behavior | Agent decides; no rigid policy. The brief-watcher's prompt instructs the agent to reply with whatever framing is most useful given the result. |
| Webhook for briefed-scan results | None. Briefed scans do not ping `#job-notifications`. They are the agent's concern. |
| Goal in system prompt | Loaded every turn. If no goal, the prompt suggests the agent encourage the operator to set one. |

## Architecture

### Module additions

```
storage/
  goals.py             # GoalStore: get_active, deactivate_active, create, list_history
  scan_briefs.py       # BriefStore: request, consume_pending, mark_running, mark_done, mark_notified, list_recent

bidder/
  briefed_cycle.py     # run_briefed_cycle(brief, ...) — synthesizes ephemeral Setup, runs same pipeline

assistant/
  brief_watcher.py     # background asyncio task: poll scan_briefs, summarize completed ones, DM operator
```

### Modified modules

- `assistant/tools.py` — adds 5 tools (`set_goal`, `get_goal`, `clear_goal`, `trigger_bidder_scan`, `trigger_briefed_scan`) and revert handlers for the goal tools.
- `assistant/agent.py` — `run_turn` queries `GoalStore.get_active()` and injects it into the system prompt before invoking the agent.
- `assistant/prompts.py` — adds the briefed-scan workflow section to `SYSTEM_PROMPT` and a separate `BRIEF_WATCHER_SYSTEM` constant for the brief-watcher's invocations.
- `scheduler/main.py` — at startup, launches `brief_watcher` alongside `bidder_loop` and `apply_executor_loop`. Bidder loop's iteration body checks `BriefStore.consume_pending()` first; if a brief is pending, runs `run_briefed_cycle(brief)` instead of the scheduled cycle path.
- New migration `005_goals_and_briefs.sql`.

### Data model

**`goals`** — single active goal enforced at the DB level

| Column | Type | Notes |
|---|---|---|
| `goal_id` | bigserial pk | |
| `is_active` | boolean not null default true | |
| `prose` | text not null | the north star sentence(s) |
| `target_metric` | text null | e.g. `interviews_per_week`, `applies_per_day`, `replies_per_week` |
| `target_value` | numeric null | e.g. `5` |
| `horizon` | text null | e.g. `weekly`, `monthly`, `daily` |
| `min_hourly` | numeric null | structured filter the agent can reference |
| `min_budget` | numeric null | |
| `preferred_country` | text null | |
| `notes` | text null | extra free-text context |
| `created_at` | timestamptz default now() | |
| `deactivated_at` | timestamptz null | set when superseded by `set_goal` or cleared |

Partial unique index `goals_one_active ON goals (is_active) WHERE is_active = true` enforces "at most one active row" at the DB level.

**`scan_briefs`** — pending / running / done briefed scans

| Column | Type | Notes |
|---|---|---|
| `brief_id` | bigserial pk | |
| `prose` | text not null | "Find Python AI agent jobs $80+/hr from US clients" |
| `filter_dsl` | jsonb not null | the same shape as `setups.filter_dsl` |
| `requested_by_conversation_id` | bigint null fk → `assistant_conversations` | links the brief back to the DM that triggered it |
| `requested_at` | timestamptz default now() | |
| `consumed_at` | timestamptz null | set when bidder picks it up |
| `finished_at` | timestamptz null | set on done or failed |
| `status` | text not null default 'pending' | check: `pending` \| `running` \| `done` \| `failed` |
| `cycle_notes` | text null | jobs scanned, signals fired, drafts created, errors |
| `notified_at` | timestamptz null | set after brief-watcher DMs the operator |
| `result_summary` | jsonb null | structured counters (jobs_scanned, signals_fired, drafts_created, error) |

Indexes: `(status, requested_at)` for the consume-pending lookup; partial `WHERE status = 'done' AND notified_at IS NULL` for the brief-watcher's poll.

### Tool catalog additions

| Tool | Args | Effect |
|---|---|---|
| `set_goal` | `prose: str`, optional `target_metric: str`, `target_value: float`, `horizon: str`, `min_hourly: float`, `min_budget: float`, `preferred_country: str`, `notes: str` | Deactivates current active goal (if any) by setting `deactivated_at = now(), is_active = false`, then inserts new row. Audit `before_state` captures the old active goal; `after_state` captures the new one. |
| `get_goal` | — | Returns active goal as dict, or `{"error": "no active goal"}`. |
| `clear_goal` | — | Sets active goal's `is_active = false, deactivated_at = now()`. After: no active row. |
| `trigger_bidder_scan` | — | Calls `BidderStateStore.request_force_run()`. Bidder picks up within ~30s and runs a normal scheduled cycle. Reply contains "Scan requested. The bidder will start within 30s." |
| `trigger_briefed_scan` | `prose: str`, `filter_patch: dict` | Validates `filter_patch` against `FiltersPatch`; converts to `filter_dsl`; inserts a `scan_briefs` row; returns `{brief_id, status: "pending"}`. The agent's reply tells the operator the brief is queued and they will be DM'd when it completes. |

All 5 tools route through the existing `_audited_write` template. `revert_last_change` works for `set_goal` and `clear_goal` (inverse: restore the previous active goal). `trigger_bidder_scan` and `trigger_briefed_scan` have no meaningful inverse (you cannot un-scan); their `_apply_revert` branch is a defensive no-op.

### Tool count after Phase 2.A

22 (Phase 1) + 5 (this phase) = **27 tools**.

### System prompt additions

Two new sections in `assistant/prompts.py:SYSTEM_PROMPT`:

**Goal section** (rendered conditionally per turn by `run_turn`):

```
=== Operator's Active Goal ===
"{prose}"
Targets: {target_value} {target_metric} per {horizon}     (only if set)
Filters: min hourly ${min_hourly}, min budget ${min_budget}, prefers {preferred_country}     (only those set)
Notes: {notes}     (only if set)
==============================
```

If no active goal:

```
=== No Goal Set ===
The operator has not set an active goal. If they describe what they
want from the system, suggest setting one with set_goal so future
decisions can orient around it.
===================
```

**Briefed-scan workflow** (static, in the base system prompt):

> When the operator asks you to scan, look for jobs, or check what's available NOW (phrases like "go scan", "look for X", "find me Y jobs", "see what's out there"), do this:
> 1. Check the active goal. The brief should reflect both the goal and the immediate request — they may differ.
> 2. Translate the request into a `filter_patch` (same shape `update_setup_filters` accepts: `min_budget`, `required_skills`, `min_hourly`, etc.).
> 3. Call `trigger_briefed_scan(prose, filter_patch)`. It returns a `brief_id` immediately and the bidder runs async.
> 4. Reply to the operator briefly: "Scanning now (brief #N). I'll DM you when it's done." Do not block waiting for results in the same turn.
> 5. When the bidder finishes, the brief-watcher will DM the operator separately with a summary. You don't need to track this — just trust the watcher.
>
> Use `trigger_bidder_scan` (no args) instead when the operator wants to re-run the regular scheduled cycle (e.g. "rerun with the new filters", "scan again with the change you just made"). It does not take a brief; it just kicks the existing scheduled cycle.

### Brief-watcher

`assistant/brief_watcher.py` exposes an async `run_brief_watcher(db, bot, settings)` coroutine that loops every 5 seconds:

1. `BriefStore.next_unnotified_done()` returns one row where `status='done' AND notified_at IS NULL`, or `None`.
2. If `None`, sleep 5s and continue.
3. Otherwise, build a turn payload: brief prose + filter_dsl + cycle_notes + `result_summary` + the recent jobs/orders that came from this cycle (joined via the `cycle_notes` or via timestamp window — see implementation).
4. Invoke the agent in a one-shot mode (no conversation history loaded) using `BRIEF_WATCHER_SYSTEM`:
   > "A briefed scan you triggered just finished. Brief: ...; results: .... Summarize for the operator in natural language. They want context — what was scanned, what matched the brief and the active goal, what's worth their attention. Be terse, senior-engineer tone, no fluff."
5. DM the operator using `bot.get_user(settings.discord_owner_user_id).send(...)`. Split with `truncate_for_discord` if needed.
6. `BriefStore.mark_notified(brief_id)`.

Failure modes:
- DM channel unavailable (operator not reachable, bot lost user) → log and mark `notified_at = now()` anyway with a note in cycle_notes; the brief is otherwise complete and we don't want to retry forever.
- Agent invocation fails → log; do NOT mark notified; next iteration will retry. After 5 failed retries the watcher gives up and marks notified to prevent loops.

The brief-watcher launches alongside `bidder_loop` and `apply_executor_loop` in `scheduler.main.main`'s `setup_hook`:

```python
bot.loop.create_task(run_brief_watcher(db, bot, settings))
```

### Bidder loop integration (briefed cycle path)

The bidder loop's main iteration changes minimally. At the top of each iteration, before the existing scheduled-cycle decision:

```python
brief = brief_store.consume_pending()
if brief is not None:
    try:
        run_briefed_cycle(brief, ...same stores as scheduled cycle... )
        brief_store.mark_done(brief.brief_id, result_summary={...})
    except Exception as e:
        brief_store.mark_failed(brief.brief_id, error=str(e))
    continue  # do not run a scheduled cycle this iteration; loop again
# ... existing scheduled-cycle path ...
```

`run_briefed_cycle` synthesizes an ephemeral `Setup` from the brief — name `"brief-{brief_id}"`, status `"active"` in memory only (never written to setups table), tier `"normal"`, the brief's `filter_dsl`, prose from the brief — and runs the same `process_job_through_setups` + `draft_order` pipeline against just that one synthetic setup.

Drafted orders from briefed cycles still write to `orders` table normally, so the agent can query them via `list_orders` later. They are tagged via `signals.market_state` with `{"source": "briefed_scan", "brief_id": N}` so the brief-watcher can find them by brief_id.

The existing webhook / channel embed path for new orders is unchanged. To avoid pinging `#job-notifications` for briefed-scan orders, the `on_signal` callback (registered by `scheduler.main`) checks `signal.market_state.get("source")`; if `"briefed_scan"`, it skips the webhook send. Operator only sees these via the brief-watcher's DM.

### Failure handling

| Failure | Behavior |
|---|---|
| Brief insert fails | Tool returns `{"error": "..."}` to the agent; agent surfaces in DM. |
| Bidder crashes mid-briefed-cycle | `mark_failed` records the error in `cycle_notes`; brief-watcher includes it in the summary so the operator knows. |
| Brief-watcher cannot DM (e.g. user not in cache) | Log warning, mark notified, move on. |
| Agent invocation in brief-watcher fails | Retry up to 5 times; then mark notified to break the loop, log the error. |
| Operator DMs another briefed scan while one is running | Both rows exist; bidder consumes them in order. The agent's reply makes clear the second is queued. |
| Agent calls `trigger_briefed_scan` with malformed `filter_patch` | `FiltersPatch` validation rejects, tool returns `{"error": "validation: ..."}`, agent retries or asks for clarification. |

### Cost tracking

- Every `run_turn` invocation already writes one row to `agent_runs` with `agent_name='assistant'`, `trigger='discord_question'`. Goal-setting and `trigger_briefed_scan` calls roll up under that.
- The brief-watcher's invocations write a separate row with `agent_name='brief_watcher'`, `trigger='scheduled'`, and `trigger_context={"brief_id": N}` so daily cost queries can split watcher cost from interactive cost.

### Logging

- Bidder logs `[scan] briefed brief_id=N prose='...'` at the top of a briefed cycle.
- Brief-watcher logs each poll-result-found and each DM dispatch (one line each, brief_id + length of summary).
- `trigger_briefed_scan` tool logs the brief_id it created.

### Concurrency

- The existing per-user `asyncio.Lock` in `assistant/dm_handler.py` guards the assistant's main turns. The brief-watcher does NOT acquire this lock when DMing the operator — its DMs are independent of any in-flight conversation. This means the operator might receive a brief-watcher DM mid-conversation, but Discord shows them in order; we lose nothing.
- Bidder loop is single-threaded by design (UI Automation requires it). Briefed and scheduled cycles never overlap.

## Testing

- **Unit tests** (`tests/test_goals_store.py`, `tests/test_briefs_store.py`, `tests/test_assistant_tools.py` extensions): exercise each tool's happy path + validation + audit-row contents. Goal store tests prove the partial unique index enforces single-active.
- **Integration test** (`tests/test_briefed_scan_loop.py`): inserts a brief, calls `run_briefed_cycle` directly with mocked Upwork primitives or a `--dry` substrate flag, confirms the brief transitions pending → running → done, confirms `result_summary` is populated, confirms `signals.market_state.source == 'briefed_scan'`.
- **Integration test** (`tests/test_brief_watcher.py`, gated on `OPENAI_API_KEY`): inserts a fake done brief, runs one iteration of the watcher, confirms an agent invocation happened, confirms `notified_at` was set. The DM dispatch is mocked.
- **Manual smoke**: operator DMs the bot a goal, confirms the agent acknowledges with the goal recap. Operator DMs "go scan for Python jobs $80+/hr." Agent calls `trigger_briefed_scan`, replies with "Scanning now (brief #N)." Within 1-3 minutes, operator receives a follow-up DM from the brief-watcher with the result summary. Operator confirms `select * from scan_briefs order by brief_id desc limit 1` shows status='done', notified_at set, result_summary populated.

## Open questions deferred to implementation

- **Brief-watcher poll interval.** Default 5 seconds. Configurable via env `BRIEF_WATCHER_POLL_SECONDS` if needed.
- **`run_briefed_cycle` timeout.** A briefed cycle should complete in ~30-90s based on observed scheduled-cycle timing. If a brief is in `running` for >5 min, the watcher treats it as `failed` and includes a "this brief stalled" note in the summary. Implement as a query-side check (`status='running' AND consumed_at < now() - interval '5 minutes'`) rather than a heartbeat.
- **Agent prompt for briefed-scan summarization.** The exact wording of `BRIEF_WATCHER_SYSTEM` lives in code; this spec specifies its job, not its phrasing.

## Implementation order (sketch — to be elaborated in the plan)

1. Migration `005_goals_and_briefs.sql`.
2. `storage/goals.py` (GoalStore) with tests.
3. `storage/scan_briefs.py` (BriefStore) with tests.
4. New tools (`set_goal`, `get_goal`, `clear_goal`, `trigger_bidder_scan`, `trigger_briefed_scan`) + revert handlers + tests.
5. Goal-injection in `assistant/agent.py:run_turn`.
6. System-prompt additions in `assistant/prompts.py`.
7. `bidder/briefed_cycle.py` with the ephemeral-Setup synthesis logic.
8. Bidder loop integration: consume pending briefs first, skip webhook for briefed-cycle orders.
9. `assistant/brief_watcher.py` background task.
10. Wire brief-watcher into `scheduler/main.py`.
11. Integration test for the full briefed-cycle path.
12. Manual smoke test, documented.
