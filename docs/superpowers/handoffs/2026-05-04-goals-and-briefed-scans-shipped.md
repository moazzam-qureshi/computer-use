# Phase 2.A: Goals + Briefed Scans — Shipped

Date: 2026-05-04
Branch: phase1-foundation

## What shipped

The assistant now has a north star and a hands-on execution channel. Operator
sets a single active goal in DM (free-text + optional structured targets),
and the goal is loaded into every assistant turn's system prompt. When the
operator says "go scan for X", the agent composes a brief, queues a
`scan_briefs` row, and the bidder picks it up async on its next iteration,
runs an ephemeral cycle against a real `setups` row marked `status='retired'`,
and a new `brief_watcher` background task DMs the operator a natural-language
summary when each brief finishes.

5 new tools added to the assistant (now 27 total): `set_goal`, `get_goal`,
`clear_goal`, `trigger_bidder_scan` (kicks the regular cycle), and
`trigger_briefed_scan(prose, filter_patch)`.

Spec: docs/superpowers/specs/2026-05-04-goals-and-briefed-scans-design.md
Plan: docs/superpowers/plans/2026-05-04-goals-and-briefed-scans.md

## Verified

- 5/5 GoalStore tests pass.
- 5/5 BriefStore tests pass (atomic consume_pending, mark_done, mark_failed, next_unnotified ordering).
- 18/18 assistant_tools tests pass (12 existing + 4 goal + 2 trigger).
- Full test suite: 77 passed, 1 pre-existing Monday-boundary failure unrelated to this change.
- All module imports clean (`scheduler.main`, `bidder.scan_cycle`, `assistant.brief_watcher`, etc.).
- PM2 restart shows `setup_hook fired; starting bidder + apply_executor + brief_watcher loops` and `[brief-watcher] started, polling every 5.0s`.

## Spec → plan deviation

The spec described `run_briefed_cycle` as a separate function. After verifying
that `signals.primary_setup_id` and `orders.setup_id` both have FK constraints
to `setups`, the implementation persists a real `setups` row (status='retired')
per brief instead of using a synthetic in-memory Setup. `run_one_cycle` gains
an optional `brief: Brief | None` parameter rather than a sibling function.
The scheduled cycle's `list_active()` ignores retired setups, so the brief
row is a clean audit trail rather than pollution. Bonus: agent can later
query "orders from brief 7" via `setup_id`.

## Manual smoke test (operator runs)

1. **Set a goal in DM:**
   ```
   set my goal to "Land 3 strong AI agent / RAG jobs per week from US clients paying $80+/hr"
   ```
   Expected: bot replies confirming the goal. Verify with:
   ```
   uv run python -c "
   from storage.connection import Database
   from storage.goals import GoalStore
   import os
   from dotenv import load_dotenv
   load_dotenv()
   print(GoalStore(Database(os.environ['DATABASE_URL'])).get_active())
   "
   ```

2. **Trigger a briefed scan in DM:**
   ```
   go scan for python AI agent jobs $80 per hour or higher right now
   ```
   Expected: agent replies "Scanning now (brief #N). I'll DM you when it's done."
   Verify in DB:
   ```
   uv run python -c "
   from storage.connection import Database
   import os
   from dotenv import load_dotenv
   load_dotenv()
   db = Database(os.environ['DATABASE_URL'])
   with db.connection() as conn:
       with conn.cursor() as cur:
           cur.execute('SELECT brief_id, status, prose FROM scan_briefs ORDER BY brief_id DESC LIMIT 1')
           print(cur.fetchone())
   "
   ```

   Watch `pm2 logs scheduler` for:
   - `[bidder] consuming brief brief_id=N`
   - `[scan] briefed cycle brief_id=N setup_id=...`
   - `[bidder] brief brief_id=N done; drafts=X`
   - `[brief-watcher] DM'd brief_id=N (... chars)`

3. **Within 1-3 minutes, expect a SECOND DM** from the bot summarizing the brief in natural language.

4. **Confirm the brief did NOT post to `#job-notifications`.** If it did, the on_signal guard in `scheduler.main` is wrong.

5. **Test `trigger_bidder_scan`** (the no-brief variant): DM `run a normal scan now`. Expect the agent to call `trigger_bidder_scan` and the next scheduled cycle to fire within 30s. Its results DO post to `#job-notifications` (existing behavior, untouched).

## Known limits / deferred to Phase 2.B+

- Drafts from briefed scans still create `orders` rows but DM-native approval embeds are NOT implemented; approval flow lives in Phase 2.B.
- Anomaly watcher / proactive pings beyond brief-completion: Phase 2.C.
- Daily / shift digests: Phase 2.C.
- Outcome tracking + follow-up DMs: Phase 2.D.
- Goal-progress reporting (numeric "1/5 interviews this week"): Phase 2.D, depends on outcome tracking.
- Pre-existing test failure in `tests/integration/storage/test_orders.py::test_count_submitted_today` (Monday-boundary, unrelated).
