# Handoff: Resume Phase 1 at Task 11

**Created:** 2026-05-02
**Branch:** `phase1-foundation` (do NOT merge to main)
**Working dir:** `d:\Personal\Projects\computer-use`
**Plan:** `docs/superpowers/plans/2026-05-02-phase1-trading-system-foundation.md`
**Spec:** `docs/superpowers/specs/2026-05-02-trading-system-architecture-design.md`

## Status

Tasks 1-10 shipped. Tasks 11-12 remain. Stopping here because Task 11 spans ~900 plan lines and 13 sub-steps (bidder/, scheduler/, bot/, seed scripts) and the previous session was burning context handling Tasks 7-10. Resume in a fresh session.

### Commits on `phase1-foundation` since last handoff (newest first)

```
4a1e5c1 Align proposal prompt with structured schema; guard CostTracker exit against masking exceptions  (Task 10 fixup)
e557185 ai/ layer: schemas, cost tracker, prompts, single-shot create_agent calls                       (Task 10)
e493f2a Guard order count helpers against tz-naive datetimes                                            (Task 9 fixup)
d2a9c1a Implement DAL: jobs, setups, orders, portfolio, agent_runs, connects, scrape_runs, enrichments  (Task 9)
9cd5ecf Fix humanization: replace em-dash in greeting variant with comma                                (Task 8 fixup)
452b0f0 Add humanization layer (diurnal envelope, cycle mix, voice variants)                            (Task 8)
c377574 Add domain types + scoring + risk (pure logic, fully tested)                                    (Task 7)
```

### Test state

`uv run pytest tests/ --ignore=tests/test_research_query.py --ignore=tests/integration/ai -q` → **70 passed**.

`tests/integration/ai/test_relevance.py` works (live OpenAI call); excluded from regression run to avoid double-billing.

`tests/test_research_query.py` still has the pre-existing collection error from legacy `upwork_research.py` import path; resolved in Task 12 (legacy cleanup).

### What's on disk now (since last handoff)

**Domain layer (Tasks 7-8):**
- `domain/types.py` — Job, Setup, FilterDsl, MatchResult, Signal, Order, OutcomeEvent dataclasses + JobId/SetupId/OrderId/SignalId aliases
- `domain/scoring.py` — `score_job_against_setup(job, setup) -> MatchResult`, 6-key DSL (skill_in, budget_min_at_least, budget_kind_in, client_payment_verified, client_country_in, description_matches), all_of/any_of/single-rule
- `domain/risk.py` — `RiskCaps`, `OrderTimestamps`, `RiskDecision`, `RiskBlocked`, `can_submit_order(ts, caps, now)`
- `domain/humanization.py` — `Humanizer` (seeded `random.Random`), `DiurnalEnvelope`, `default_envelope()`, `CycleType` enum, `_CYCLE_WEIGHTS`. **Em-dash fix already applied to greeting variant.**

**Storage layer (Task 9):**
- `storage/jobs.py` — JobStore (upsert + skills, get, is_known)
- `storage/setups.py` — SetupStore (create, get, list_active, update_status), SignalStore (create)
- `storage/orders.py` — OrderStore (create_draft, update_status, set_drafted, mark_submitted, get, list_by_status, count_submitted_today, count_submitted_this_week, last_submitted_at) + OutcomeEventStore. **count_submitted_today / count_submitted_this_week now raise ValueError if `now.tzinfo is None`** (correctness fix).
- `storage/portfolio.py` — PortfolioStore (add, list_all, list_matching_tags)
- `storage/agent_runs.py` — AgentRunStore (start, finish). CHECK constraint: `trigger ∈ {scheduled, discord_question, manual, per_job}`, `status ∈ {running, succeeded, failed}`.
- `storage/connects_ledger.py` — ConnectsLedgerStore (record, spent_in_window)
- `storage/scrape_runs.py` — ScrapeRunStore (start, update_counts, finish)
- `storage/enrichments.py` — EnrichmentStore (upsert, get); imports `from ai.schemas import Enrichment`

**ai/ layer (Task 10):**
- `ai/__init__.py`, `ai/schemas.py` — RelevanceCheck, Enrichment, ProposalDraft, CoverLetter, ScreeningAnswer (Pydantic).
- `ai/cost_tracker.py` — `CostTracker(BaseCallbackHandler)` context manager. **Exit now guards against masking exceptions** (storage failure inside `__exit__` is swallowed so original LLM exception propagates).
- `ai/prompts/` — relevance.py, enrichment.py, proposal.py (PROPOSAL_SYSTEM **rewritten to align with the structured schema**: list fields are `list[str]` not bullet-marked strings, `about_me` guidance inlined; ABOUT_ME_SYSTEM still defined as a separate constant for future reuse), cover_letter.py, screening.py. **All em-dashes replaced with comma+space.**
- `ai/relevance.py` — `check_relevance(job, setup, *, agent_run_store, parent_run_id=None, model="gpt-4o-mini") -> RelevanceCheck`. Uses `create_agent(model=, response_format=RelevanceCheck)`, reads `result["structured_response"]`. Wrapped in CostTracker with `agent_name="relevance"`, `trigger="per_job"`.
- `ai/enrichment.py` — same shape, returns `Enrichment`. agent_name="enrichment".
- `ai/proposal_gen.py` — `generate_proposal(job, portfolio_items, ...)` → ProposalDraft (model="gpt-4o" default), `generate_cover_letter(job, detected_client_name, ...)` → CoverLetter (model="gpt-4o" default). Both use structured output via response_format.

**Tests added (Tasks 7-10):**
- `tests/unit/domain/{__init__,test_scoring,test_risk,test_humanization}.py` — 15 tests
- `tests/integration/storage/{test_jobs,test_setups,test_orders,test_portfolio,test_agent_runs,test_connects_ledger,test_scrape_runs,test_enrichments}.py` — multiple tests, all passing
- `tests/unit/ai/{__init__,test_schemas}.py` — 3 tests
- `tests/integration/ai/{__init__,test_relevance}.py` — 1 live OpenAI test, passes

### Postgres state

Docker container `upwork_trading_pg` healthy on port 5432, user/pass `upwork`/`upwork`. Two databases: `upwork` (dev — has 001_init applied) and `upwork_test` (test fixture; schema dropped per test). `bin/migrate.py` has the `sys.path` shim and is working.

### .env state

Real values for `OPENAI_API_KEY`, `COMPOSIO_API_KEY`, `COMPOSIO_USER_ID`. Placeholder values for Discord (Task 11 needs a real `DISCORD_BOT_TOKEN` to actually run the bot, but the code can still be written without one).

### Legacy root files

Still present, still broken. Deleted in Task 12. **Task 11 ports the prompt content from `proposal.py`** — relevant constants already extracted into `ai/prompts/proposal.py` and `ai/prompts/screening.py` during Task 10. The legacy `proposal.py` is still at the repo root unmodified.

## Resume instructions

Drop this prompt into a fresh session:

```
We're mid-execution on the Phase 1 trading-system foundation plan. Continue with Task 11.

Branch: phase1-foundation (already checked out, do not merge to main)
Working dir: d:/Personal/Projects/computer-use
Plan: docs/superpowers/plans/2026-05-02-phase1-trading-system-foundation.md
Spec: docs/superpowers/specs/2026-05-02-trading-system-architecture-design.md
Handoff doc with full context: docs/superpowers/handoffs/2026-05-02-phase1-resume-task-11.md
Tasks 1-10 shipped (16 commits on the branch, verify with git log --oneline -16)
Postgres is running in Docker. Verify with docker compose ps; if down, docker compose up -d
Use superpowers:subagent-driven-development to execute remaining tasks (11 and 12)
For each task: implementer subagent → spec reviewer subagent → code-reviewer subagent → fix-up loop until both pass → mark complete → next task
Apply formal code-reviewer dispatch for tasks with real logic; skip for trivial config / dependency tasks.
Resume at Task 11: bidder/, scheduler/, bot/ wiring + Discord integration. Plan lines 3254-4163 (~900 lines, 13 sub-steps).
```

## Process notes for Task 11

- Task 11 is the largest. Plan lines 3254-4163. 13 sub-steps. Multiple modules wire together (bidder/scan_cycle, bidder/signal_pipeline, bidder/draft_pipeline, bidder/apply_executor; scheduler/main, scheduler/failure_pings; bot/bot, bot/alerts, bot/interaction_handler, bot/commands, bot/escalation; bin/import_seed_setup, bin/portfolio_import, bin/portfolio_add).
- **Strongly consider breaking Task 11 into 4-5 sub-dispatches** to keep individual implementer prompts tractable:
  1. `scheduler/failure_pings.py` (just exception classes; trivial; no review needed)
  2. `bidder/signal_pipeline.py` + `bidder/draft_pipeline.py` + `bidder/scan_cycle.py` + `bidder/apply_executor.py` (the four bidder pipelines)
  3. `bot/` package (Discord bot — needs discord.py)
  4. `scheduler/main.py` (the main loop wiring everything together)
  5. `bin/` seed scripts
- The plan provides verbatim code for most files. Where it doesn't, lean on the existing module signatures (storage stores, ai/* callers, domain types) — they're stable.
- The code-reviewer will likely flag missing `--really-submit` guard in `apply_executor.py` if it's not literal in the plan. CLAUDE.md is explicit: NEVER auto-submit without confirmation. Verify the implementation respects this.
- Discord integration may need `DISCORD_BOT_TOKEN` placeholder behavior (skip startup if it's the placeholder). Don't run the actual bot loop in tests.

## Process notes for Task 12

Plan lines 4164-end. ~100 lines. Steps:
1. Bring up full system (manual smoke)
2. Verify loop end-to-end (manual)
3. Verify connects budget enforcement (manual)
4. **Delete legacy files** at repo root (the long list from previous handoff). This will fix `tests/test_research_query.py` collection error.
5. Run full suite, expect green
6. Final commit

The legacy file list (from previous handoff): `upwork_driver.py`, `upwork_apply.py`, `upwork_research.py`, `proposal.py`, `notify.py`, `db.py`, `tools.py`, `agent.py`, `scheduler.py`, `view.py`, `dump.py`, `poke.py`, `post_linkedin.py`, `inspect_composer.py`, `debug_feed.py`, `debug_slices.py`, `main.py`, `reset_jobs.py`, `upwork_scan.py`.

Note: by Task 12, `proposal.py` is no longer needed even as a prompt source (Task 10 already ported the constants), so it's safe to delete.

## Decisions already made (don't re-litigate)

- All `create_agent` calls use `response_format=<PydanticSchema>` and read `result["structured_response"]`. NOT `with_structured_output`, NOT raw JSON parsing.
- `EnrichmentStore.upsert` ON CONFLICT DO UPDATE intentionally omits `raw_llm_response` (matches plan).
- `count_submitted_today` / `count_submitted_this_week` require tz-aware `now`. Production callers must pass `datetime.now(timezone.utc)` or equivalent.
- `CostTracker.__exit__` swallows storage failures so the original agent exception isn't masked.
- `PROPOSAL_SYSTEM` was rewritten in commit 4a1e5c1 to align with `ProposalDraft` schema (list fields are real lists, about_me guidance inlined). Don't revert to the legacy bullet-marker-string format.
- `apply_form.submit_proposal` clicks unconditionally; the never-auto-submit policy is the caller's responsibility (per CLAUDE.md). Task 11's `apply_executor.py` MUST enforce this guard.
- Em-dash hard rule applies to ALL prompt strings. Don't introduce new ones.
- Plan's `ai/prompts/cover_letter.py` and `ai/prompts/enrichment.py` had em-dashes in the source; they were corrected during Task 10. Preserve the corrected versions.
