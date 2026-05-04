# Discord Ops Assistant — Design

**Status:** Approved 2026-05-04
**Branch baseline:** `phase1-foundation`
**Implementation target:** new `assistant/` module, peer of `bidder/`

## Goal

A conversational LangChain agent the operator talks to via Discord DM. It oversees the existing bidder + bidder-adjacent activity. It can:

1. Answer questions grounded in the system's data (jobs, orders, costs, skip reasons, connects).
2. Mutate configuration through structured DB-backed tools (filters, ignored clients, tier, auto-apply, connects caps, pause/resume, portfolio, pitch tone, setup CRUD).
3. Persist mutations directly; the running bidder picks up new config on its next cycle.

The agent does not drive the bidder. The bidder remains autonomous, reading active setups from Postgres each cycle. The assistant and bidder share Postgres as their only contract.

## Non-goals (v1)

- Proactive pings, digests, or anomaly alerts. Reactive only.
- Multi-user. Single configured operator user.
- Voice or image input.
- Web UI.
- Replacing existing slash commands. Slash commands continue to work unchanged.
- MCP / external client surface. The tool functions are reusable later if we want to expose MCP, but that is not part of v1.

## Operator-confirmed decisions

| Decision | Choice |
|---|---|
| Configuration backing | DB-backed; agent uses structured CRUD tools, never edits free-text blobs |
| Mutation policy | Tool calls execute immediately; agent posts a diff summary in the reply; operator can revert via chat |
| Mutation surface | Full: filters, ignored clients, tier, auto-apply, connects caps, pause/resume, portfolio, pitch tone, setup CRUD |
| Read approach | Curated read tools only; no SQL escape hatch |
| Chat surface | Discord DM to the bot |
| Conversation memory | Single rolling thread per operator, persisted in Postgres |
| Proactivity | Reactive only |
| Tool shape | Verb-per-tool (`add_ignored_client` and `remove_ignored_client` stay split, etc.); ~22 tools total |
| Model | `gpt-5-mini` with `reasoning_effort="low"` default |

## Architecture

### Module layout

New top-level package `assistant/`, peer of existing `bidder/`:

```
assistant/
  __init__.py
  agent.py          # create_agent setup, model config, system-prompt assembly
  tools.py          # 22 @tool functions, thin wrappers over storage/ + domain/
  conversation.py   # rolling-thread state, summarization at message-count cap
  dm_handler.py     # async handler invoked from bot/bot.py on_message
  prompts.py        # system prompt template + diff-message helpers
```

New storage module:

```
storage/
  conversations.py  # ConversationStore + MessageStore + AuditStore
```

New migration adding tables: `assistant_conversations`, `assistant_messages`, `assistant_audit_log`, `system_config`. Plus a column add on `setups`: `ignored_clients text[] not null default '{}'` and a column add: `tone_override text`.

The `bidder/` package and existing slash commands are not modified except for the two integration points listed in §Wiring.

### Data model

**`assistant_conversations`** — one row per operator (single-row table for v1; schema future-proofs to multi-user)

| Column | Type | Notes |
|---|---|---|
| `conversation_id` | bigserial pk | |
| `discord_user_id` | text unique not null | matches `OPERATOR_DISCORD_USER_ID` env |
| `summary` | text null | running summary of older history |
| `created_at` | timestamptz default now() | |
| `updated_at` | timestamptz default now() | bumped each turn |

**`assistant_messages`** — append-only message log

| Column | Type | Notes |
|---|---|---|
| `message_id` | bigserial pk | |
| `conversation_id` | bigint fk | |
| `role` | text not null | `user` \| `assistant` \| `tool` |
| `content` | text not null | JSON-encoded for `tool` rows, plain for others |
| `tool_call_id` | text null | populated for `tool` rows |
| `tool_name` | text null | populated for `tool` rows |
| `created_at` | timestamptz default now() | |

Index: `(conversation_id, message_id desc)` for fast recent-N retrieval.

**`assistant_audit_log`** — write-tool history, append-only

| Column | Type | Notes |
|---|---|---|
| `audit_id` | bigserial pk | |
| `conversation_id` | bigint null fk | nullable for future non-conversational mutations |
| `tool_name` | text not null | |
| `arguments` | jsonb not null | |
| `result` | jsonb not null | tool's return value or error dict |
| `before_state` | jsonb null | snapshot of mutated row(s) before |
| `after_state` | jsonb null | snapshot after |
| `created_at` | timestamptz default now() | |

Index: `(conversation_id, audit_id desc)` for fast revert lookup.

**`system_config`** — global runtime flags

| Column | Type | Notes |
|---|---|---|
| `key` | text primary key | e.g. `bidder_paused`, `connects_daily_cap`, `connects_weekly_cap` |
| `value` | jsonb not null | typed per key |
| `updated_at` | timestamptz default now() | |

**Schema additions on `setups`:**

- `ignored_clients text[] not null default '{}'` — per-setup blocklist
- `tone_override text null` — free-text note prepended to proposal prompt

### Conversation memory mechanics

- Each turn loads the conversation's `summary` plus the most recent N messages (default N = 40, configurable via `ASSISTANT_HISTORY_WINDOW`).
- When `assistant_messages` count for the conversation exceeds N, an async summarization task is enqueued at end-of-turn: it summarizes the oldest half into `summary`, then deletes those summarized messages (or marks them archived — see "Open question" below).
- Summarization runs out-of-band; it does not block the user-visible reply.

**Open question to resolve during implementation:** delete summarized messages vs. soft-archive with a flag. Default to soft-archive (set `archived_at`) so audit replay stays possible if we need it later.

## Agent

### Model and framework

- Framework: `langchain.agents.create_agent` (consistent with existing `ai/` layer).
- Model: `gpt-5-mini` (configurable via `ASSISTANT_MODEL`).
- Reasoning effort: `low` default (configurable via `ASSISTANT_REASONING_EFFORT`).
- Recursion limit: LangChain default (25). Caught and surfaced as a friendly message.

### Per-DM control flow

1. `bot/bot.py:on_message` filters DMs from the configured operator user ID. Non-DM, wrong-user, and self-messages return silently.
2. `assistant/dm_handler.handle(discord_message)` acquires a per-user `asyncio.Lock`, sends a Discord typing indicator, then:
   - Append user message to `assistant_messages`.
   - Load `summary` + last N messages → assemble LangChain message list.
   - Invoke `agent.invoke({"messages": [...]})`. Agent may call any number of tools in a turn.
   - For each tool call: execute synchronously; for write-tools, capture before/after and write an `assistant_audit_log` row inside the same transaction as the mutation.
   - Final assistant message → persisted to `assistant_messages`, then posted back to Discord. If >2000 chars, split at paragraph boundaries.
3. If the message-count threshold was crossed during this turn, schedule async summarization. Lock is released before scheduling; summarization does not block reply.
4. Cost tracking: every agent invocation writes one row to existing `agent_runs` with `kind='assistant_turn'`.

### Concurrency

One DM in flight at a time per operator (per-user `asyncio.Lock`). Discord does not pipeline DMs at the API level, but the lock guards against the operator firing several messages within seconds while a long agent turn runs.

### System prompt

`assistant/prompts.py` holds the system prompt template. Final wording lives in code; this spec captures the shape.

Sections:

- **Role.** "You are the operator's Upwork bidding ops assistant. The bidder runs autonomously every 15 to 20 minutes. Your job is to answer questions about its activity and adjust its configuration on request."
- **Domain knowledge.** Brief description of what a Setup is, the trading-as-metaphor vocabulary (Setup, Signal, Order, Connects ledger, bidder), the data model, the tool catalog at a high level.
- **Behavior rules:**
  - Always confirm via diff after writing. After any write tool succeeds, the final reply MUST include "Done. <one-line summary of the change>".
  - Be terse. No marketing fluff. Match the operator's voice: senior engineer, direct, blunt when needed.
  - Read before guessing. If the request is ambiguous (e.g. "loosen the budget filter" without naming a setup), call `list_setups` first; if still ambiguous, ask one question.
  - Never invent setup IDs, job IDs, or client names. Ground every reference in tool output.
  - Destructive-feeling actions (`pause_bidder`, `archive_setup`) execute, but the diff message must be visually clear: "Bidder PAUSED. Resume with: 'resume bidder'".
  - If a tool returns `{"error": "..."}`, surface it naturally in chat and offer the next sensible step.

### Failure handling

| Failure | Behavior |
|---|---|
| Tool raises | Caught by tool wrapper, returned as `{"error": "..."}`. Agent surfaces it conversationally. |
| Pydantic validation fails | Returned as `{"error": "validation: <detail>"}`. Same path as above. |
| Recursion limit hit | Caught; reply: "I got stuck mid-thought. Try rephrasing." |
| Postgres unavailable | Reply: "Database is unreachable; the bidder may also be down. Check `pm2 status`." |
| OpenAI API unavailable | Reply: "OpenAI is unreachable, try again in a minute." |

## Tool catalog

All tools live in `assistant/tools.py`, decorated with `@tool` from LangChain. Each is a thin wrapper over existing `storage/` and `domain/` modules. Total: 22 tools (8 read, 13 write, 1 revert).

### Read tools (8)

| Tool | Args | Returns |
|---|---|---|
| `list_setups` | — | list of `{setup_id, name, status, tier, auto_apply, summary_of_filters}` for all setups |
| `get_setup` | `setup_id: int` | full Setup including `filter_dsl`, `prose_definition`, `ignored_clients`, `tone_override` |
| `list_orders` | `status?: str = "queued"`, `limit?: int = 20` | list of `{order_id, job_id, setup_id, status, drafted_at, job_title, client}` |
| `get_job` | `job_id: int` | full Job + match/skip reason from latest associated signal |
| `recent_activity` | `hours?: int = 24` | `{cycles_run, jobs_scanned, jobs_matched, jobs_skipped, drafts_created, applies, total_llm_cost}` |
| `connects_status` | — | `{daily_cap, weekly_cap, daily_spent, weekly_spent, next_daily_reset, next_weekly_reset}` |
| `get_portfolio` | — | current portfolio JSON |
| `search_jobs` | `query: str`, `status?: str` | list of `{job_id, title, client, posted_at, status}` matching title/client/skill substring |

### Write tools (13)

| Tool | Args | Effect |
|---|---|---|
| `update_setup_filters` | `setup_id: int`, `patch: dict` | Pydantic-validated patch merged into `filter_dsl.spec`. Allowed keys: `min_budget`, `max_budget`, `exclude_fixed_under`, `min_hourly`, `max_hourly`, `required_skills`, `excluded_skills`, `min_client_spend`, `payment_verified_required`, `excluded_durations` |
| `add_ignored_client` | `setup_id: int`, `client_name: str` | Append to `setups.ignored_clients` if not present |
| `remove_ignored_client` | `setup_id: int`, `client_name: str` | Remove from `setups.ignored_clients` |
| `set_setup_tier` | `setup_id: int`, `tier: "selective" \| "balanced" \| "aggressive"` | Update `setups.tier` |
| `set_auto_apply` | `setup_id: int`, `enabled: bool` | Toggle `setups.auto_apply_enabled` |
| `pause_setup` | `setup_id: int` | Set `setups.status = 'paused'` |
| `resume_setup` | `setup_id: int` | Set `setups.status = 'active'` |
| `archive_setup` | `setup_id: int` | Set `setups.status = 'archived'` (soft delete) |
| `create_setup` | `name: str`, `tier: str`, `filters: dict`, `prose: str` | Insert new Setup row, return `setup_id` |
| `set_connects_cap` | `daily?: int`, `weekly?: int` | Upsert `system_config` keys `connects_daily_cap` and/or `connects_weekly_cap` |
| `pause_bidder` | — | Upsert `system_config['bidder_paused'] = true`. Bidder reads this at cycle start. |
| `resume_bidder` | — | Set `system_config['bidder_paused'] = false` |
| `update_portfolio` | `patch: dict` | Pydantic-validated deep-merge into portfolio JSON |
| `update_pitch_tone` | `setup_id: int`, `tone_notes: str` | Set `setups.tone_override`. Empty string clears it. |

### Revert tool (1)

| Tool | Args | Effect |
|---|---|---|
| `revert_last_change` | — | Look up most recent `assistant_audit_log` row for current conversation. Apply inverse using `before_state`. If most recent row is itself a revert, walk back one more. Returns `{reverted_tool, restored_state}` or `{error: "nothing to revert"}`. |

### Tool implementation invariants

Every write-tool follows this template:

```
1. Validate args (Pydantic; on failure return {"error": "validation: ..."}).
2. Open transaction.
3. Read current state of affected row(s).
4. Apply mutation.
5. Read new state.
6. Insert audit row (before_state, after_state, tool_name, arguments, conversation_id).
7. Commit.
8. Return short success dict for the agent to quote in its diff message.
```

Errors are *returned*, not raised. The agent sees `{"error": "..."}` and surfaces it naturally.

A single `_audited_write` helper in `assistant/tools.py` enforces the template; per-tool code is the pure mutation. This keeps each tool function small.

## Wiring

### `bot/bot.py`

Add an `on_message` handler beside the existing slash-command setup:

```
@client.event
async def on_message(message):
    if message.author == client.user: return
    if not isinstance(message.channel, discord.DMChannel): return
    if str(message.author.id) != OPERATOR_DISCORD_USER_ID: return
    await assistant.dm_handler.handle(message)
```

Slash commands and existing alert webhooks are untouched.

### `bidder/scan_cycle.py`

Two additions at cycle start:

1. Read `system_config['bidder_paused']`. If `true`, exit cycle early (no scan, no draft, no apply). Log a single "bidder paused" line per cycle.
2. Read `system_config['connects_daily_cap']` and `connects_weekly_cap`. If present, override env-derived defaults for this cycle.

### `ai/proposal_gen.py` (or wherever the proposal prompt is assembled)

Read `setups.tone_override` for the current setup. If non-null and non-empty, prepend it to the proposal-generation prompt as an "Operator note on tone" section.

All other config (filters, ignored clients, tier, auto_apply, status) is already read from `setups` per-cycle, so changes propagate automatically with no new wiring.

### Environment variables

| Var | Default | Purpose |
|---|---|---|
| `OPERATOR_DISCORD_USER_ID` | required | The only user the assistant responds to |
| `ASSISTANT_MODEL` | `gpt-5-mini` | Override agent model |
| `ASSISTANT_REASONING_EFFORT` | `low` | Override reasoning effort |
| `ASSISTANT_HISTORY_WINDOW` | `40` | Messages before summarization triggers |

### Logging and cost

- Assistant module uses the existing logger; one-line summary per turn (user message hash, tool calls made, tokens, latency).
- Each agent invocation writes one row to existing `agent_runs` with `kind='assistant_turn'`. Daily cost queries already work; this just adds another `kind`.

### Deployment

Runs in the same PM2-managed bot process. No new daemon. The bidder remains its own PM2 process and reads from Postgres only.

## Testing

- **Unit tests** (`tests/test_assistant_tools.py`): for each tool, cover the happy path, validation failure, and (for writes) the audit row contents.
- **Integration test** (`tests/test_assistant_loop.py`): spin up an in-memory conversation, send a sequence of synthetic user messages, assert the expected tool calls fired and DB state changed.
- **Manual smoke test:** real Discord DM, walk through a representative scenario end-to-end:
  1. "what setups are active?"
  2. "tighten the budget filter on setup 2 to $500 minimum"
  3. "what did the bidder do today?"
  4. "add Acme Corp to setup 2's ignored list"
  5. "revert that"
  6. "pause the bidder"
  7. "resume the bidder"

## Open questions and future deferrals

- **Summarized message handling.** Default to soft-archive (`archived_at` flag) over hard delete. Decide during implementation.
- **Multi-user.** Schema already keys conversations by `discord_user_id`; expanding later is a config change, not a migration.
- **MCP export.** Tool functions in `assistant/tools.py` are pure functions over `storage/` and `domain/`. They can be re-exposed as an MCP server later without changes to the assistant module.
- **Proactive pings.** Out of v1. Not blocked by this design — a future module could subscribe to bidder events and DM the operator using the same Discord client.
- **Voice/image input.** Out of v1.

## Implementation order (sketch — to be elaborated in the plan)

1. Migration: new tables + column additions.
2. `storage/conversations.py` (ConversationStore, MessageStore, AuditStore) + `system_config` accessor.
3. `assistant/tools.py` read tools + `_audited_write` helper + write tools + `revert_last_change`.
4. `assistant/conversation.py` (load/save/summarize).
5. `assistant/agent.py` (prompt + create_agent + invoke wrapper, cost-tracking integration).
6. `assistant/dm_handler.py` (Discord glue, lock, typing indicator, message split).
7. `bot/bot.py` `on_message` integration.
8. `bidder/scan_cycle.py` reads `system_config` flags.
9. `ai/proposal_gen.py` reads `tone_override`.
10. Tests (unit + integration).
11. Manual smoke test, documented.
