# Discord Ops Assistant — Shipped

Date: 2026-05-04
Branch: phase1-foundation

## What shipped

A LangChain `create_agent` (gpt-5-mini) that the operator DMs in Discord.
21 structured tools (8 read + 12 write + 1 revert). Postgres-backed conversation
history + audit log. Bidder pause/resume goes through a new `system_config` table.
Per-setup `tone_override` is prepended to the proposal+cover-letter system prompts.

Spec: `docs/superpowers/specs/2026-05-04-discord-ops-assistant-design.md`
Plan: `docs/superpowers/plans/2026-05-04-discord-ops-assistant.md`

## Verified

- `pytest tests/test_conversation_stores.py` 4/4 pass
- `pytest tests/test_prompts.py` 4/4 pass
- `pytest tests/test_assistant_tools.py` 12/12 pass (read + write + revert)
- `pytest tests/test_conversation_module.py` 2/2 pass
- `pytest tests/test_assistant_loop.py` 1/1 pass — live 3-turn conversation
  against gpt-5-mini, agent correctly added "TestClientCorp" to ignored
  clients then reverted.
- All other module imports clean (`bot.bot`, `scheduler.main`, `bin.run_one_scan`,
  `bidder.scan_cycle`, `assistant.*`).

## Pre-existing test failure unrelated to this change

`tests/integration/storage/test_orders.py::test_count_submitted_today` fails
on a Monday because of a Mon-week-start boundary in the test fixture data.
Not caused by this work.

## Deviations from the original plan

- **Tool count: 21, not 22.** Dropped `update_portfolio` because PortfolioStore
  is a list-of-rows API, not a JSON blob you patch. `list_portfolio_items`
  replaces the planned `get_portfolio`. v1 will not support DM-driven portfolio
  edits; that needs a different shape (e.g. `add_portfolio_item`).
- **`agent_runs.trigger='discord_question'`**, not `'discord_dm'` as the plan
  said. The column has a CHECK constraint allowing only
  `scheduled|discord_question|manual|per_job`.
- **Setup status/tier literal mapping.** Spec used operator-friendly aliases
  (paused/archived, selective/balanced/aggressive). Real schema literals are
  `disabled/retired` and `quiet/normal/critical`. Tools and system prompt use
  the real literals.
- **`OPERATOR_DISCORD_USER_ID` reused as `DISCORD_OWNER_USER_ID`** (already
  in `Settings`). No new env var required.

## Manual smoke test (operator runs)

Before first run, confirm `.env` has `DISCORD_OWNER_USER_ID` set to your
Discord user id. Optionally set `ASSISTANT_MODEL`, `ASSISTANT_HISTORY_WINDOW`.

1. Restart the bot:
   ```
   pm2 restart bidder-bot
   pm2 logs bidder-bot --lines 30
   ```
   Expect "Bot connected as ..." and "Synced N slash commands".

2. DM the bot. Walk this script, verifying each outcome:

   | Message | Expected |
   |---|---|
   | `what setups are active?` | Lists setups (id/name/tier/status). |
   | `tighten the budget filter on setup 1 to $500 minimum` | "Done. ..."; `select filter_dsl from setups where setup_id=1` shows new rule. |
   | `what did the bidder do today?` | Cycles/jobs/cost numbers. |
   | `add 'Acme Corp' to setup 1 ignored clients` | "Done. Added Acme Corp ..."; `select ignored_clients from setups where setup_id=1` includes Acme Corp. |
   | `revert that` | "Reverted ..."; ignored_clients no longer contains Acme Corp. |
   | `pause the bidder` | "Bidder PAUSED. Resume with: 'resume bidder'."; `select value from system_config where key='bidder_paused'` is `true`. Next bidder cycle log shows `[scan] bidder paused via system_config; skipping cycle`. |
   | `resume the bidder` | system_config flips to `false`; next cycle scans normally. |

3. Spot-check audit log:
   ```
   uv run python -c "from storage.connection import Database; import os; from dotenv import load_dotenv; load_dotenv(); db = Database(os.environ['DATABASE_URL']);
   from storage.conversations import AuditStore
   import psycopg
   conn = db.connection().__enter__()
   cur = conn.cursor()
   cur.execute('SELECT tool_name, arguments FROM assistant_audit_log ORDER BY audit_id DESC LIMIT 10')
   for r in cur.fetchall(): print(r)"
   ```
   Expect rows reflecting the smoke-test calls.

## Known limits / deferred to v2

- Reactive only (no proactive pings).
- Single operator (schema supports multi-user; gate is in `bot/bot.py`).
- No PostgresStore-style long-term semantic memory yet.
- `update_portfolio` not implemented; portfolio edits require shell or future
  `add_portfolio_item` tool.
- `revert_last_change` for `create_setup` is a no-op (no inverse).
