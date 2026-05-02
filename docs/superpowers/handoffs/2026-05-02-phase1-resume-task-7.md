# Handoff: Resume Phase 1 at Task 7

**Created:** 2026-05-02
**Branch:** `phase1-foundation` (do NOT merge to main)
**Working dir:** `d:\Personal\Projects\computer-use`
**Plan:** `docs/superpowers/plans/2026-05-02-phase1-trading-system-foundation.md`
**Spec:** `docs/superpowers/specs/2026-05-02-trading-system-architecture-design.md`

## Status

Tasks 1-6 shipped. Tasks 7-12 remain.

### Commits on `phase1-foundation` (newest first)

```
507d49c Extract upwork-specific logic into focused modules        (Task 6)
4d48e3f Move substrate primitives + external services             (Task 5)
0e50ad1 Task 4 fixup: idempotency test self-contained             (Task 4 review fix)
dd5b078 Add migration runner + 001_init.sql with full schema      (Task 4)
f845e71 Task 3 fixups                                             (pre-existing)
d000774 Add Settings + Database connection pool + test fixtures   (pre-existing)
8a3b294 Add psycopg, discord.py, pydantic, pytest-asyncio          (pre-existing)
5f93f9a Add Postgres docker compose + env scaffolding              (pre-existing)
6fdba8f Add Phase 1 implementation plan                            (pre-existing)
```

### What's on disk now

- `storage/` — `connection.py` (Database pool, psycopg3), `migrate.py` (discover + apply numbered SQL), `migrations/001_init.sql` (19 tables, 13 indexes, all CHECK constraints + FKs).
- `bin/migrate.py` — CLI runner. **Has a 2-line `sys.path` shim** at the top because the script is run as a path (`uv run bin/migrate.py`), not a module. Don't remove the shim.
- `substrate/` — `act.py`, `observe.py`, `pacing.py`, `vision.py`, `launch_chrome.py`, `__init__.py`. Internal cross-imports converted to `from substrate import X` style.
- `external/` — `gdocs.py`, `mermaid.py`, `__init__.py`. `gdocs.py` updated to `from external import mermaid`.
- `upwork/` — five real modules (no longer placeholders): `panel.py` (parser, verbatim from plan), `feed.py`, `clipboard_url.py` (preserves x≥1200 / y≥200 / dead-center-click rules), `apply_form.py` (7 functions; `submit_proposal` clicks unconditionally — caller enforces never-auto-submit policy), `search.py`.
- `tests/integration/storage/test_migrate.py` — 2 tests, both self-contained (each drops public schema first).
- `tests/unit/upwork/test_panel.py` — 1 test.
- `scheduler/config.py` — `Settings.from_env()` frozen dataclass.

### Postgres state

- Docker: `upwork_trading_pg` container, healthy, port 5432, user/pass `upwork`/`upwork`.
- Databases: `upwork` (dev — has migrations applied), `upwork_test` (test fixture — schema dropped per test).
- `bin/migrate.py` already applied `001_init` to `upwork`. Confirmed working.

### Test state

- `uv run pytest tests/ -v` — 37 passed when excluding `tests/test_research_query.py`.
- `tests/test_research_query.py` has a **pre-existing collection error** because legacy `upwork_research.py` at repo root imports the old top-level `act` module (broken since Task 5). This is expected and gets resolved in Task 12 (legacy file deletion). Do NOT try to fix it.

### Legacy root files (still present, broken, deleted in Task 12)

`upwork_driver.py`, `upwork_apply.py`, `upwork_research.py`, `proposal.py`, `notify.py`, `db.py`, `tools.py`, `agent.py`, `scheduler.py`, `view.py`, `dump.py`, `poke.py`, `post_linkedin.py`, `inspect_composer.py`, `debug_feed.py`, `debug_slices.py`, `main.py`, `reset_jobs.py`, `upwork_scan.py`. All import old top-level `act`/`observe`/etc — they're broken now. Don't run them.

### .env state

Real values for `OPENAI_API_KEY`, `COMPOSIO_API_KEY`, `COMPOSIO_USER_ID`. Placeholder values (added during Task 4 to satisfy `Settings.from_env()`): `DISCORD_BOT_TOKEN=placeholder`, `DISCORD_CHANNEL_ID=0`, `DISCORD_OWNER_USER_ID=0`, `DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/placeholder`, `LOCAL_TIMEZONE=America/Toronto`, `DATABASE_URL=postgresql://upwork:upwork@localhost:5432/upwork`.

## Resume instructions

Drop this prompt into a fresh session:

```
We're mid-execution on the Phase 1 trading-system foundation plan. Continue with Task 7.

Branch: phase1-foundation (already checked out — do not merge to main)
Working dir: d:/Personal/Projects/computer-use
Plan: docs/superpowers/plans/2026-05-02-phase1-trading-system-foundation.md
Spec: docs/superpowers/specs/2026-05-02-trading-system-architecture-design.md
Handoff doc with full context: docs/superpowers/handoffs/2026-05-02-phase1-resume-task-7.md
Tasks 1-6 already shipped (8 commits on the branch — verify with git log --oneline -10)
Postgres is running in Docker. Verify with docker compose ps; if down, docker compose up -d
Use superpowers:subagent-driven-development to execute remaining tasks (7 through 12)
For each task: implementer subagent → spec reviewer subagent → code-reviewer subagent → fix-up loop until both reviews pass → mark complete → next task
Skip the formal code-reviewer dispatch for trivial tasks (pure config / dependency manifests). Apply it for any task with real logic.
Resume at Task 7: domain/types.py + scoring.py + risk.py
```

## Process notes from this session (worth carrying forward)

- The `superpowers:subagent-driven-development` skill workflow worked well: implementer → spec reviewer → code reviewer per task. Use it.
- Implementer subagents handle plans of ~250 lines comfortably. For Task 9 (storage layer, ~890 plan lines) consider breaking into sub-tasks (one store at a time) if the implementer struggles or returns BLOCKED.
- Don't paste the entire plan into implementer prompts — extract just the relevant task lines + necessary cross-context (existing module signatures, schema field names, etc.).
- Ignore Phase-2-polish minor issues from code reviewers (defensive guards on edge cases, optional return-type signals, additional test cases). Only fix Critical and Important findings that are about correctness or load-bearing invariants.
- Each task burns 30-90K tokens. Plan accordingly; consider a fresh session every 2-3 tasks for the larger ones (Tasks 9 and 11 in particular).

## Things that decisions were made on (don't re-litigate)

- `panel.py` is the "starter parser" from the plan, intentionally minimal. Don't enrich it.
- `apply_form.submit_proposal` clicks unconditionally; never-auto-submit policy is the caller's job (per CLAUDE.md and the plan).
- `bin/migrate.py` keeps its `sys.path` shim (alternatives like `[project.scripts]` entry points or `python -m` would work but require restructuring not in scope).
- `DATABASE_URL` points at `upwork`, not `upwork_dev`. The plan said "likely upwork_dev" but didn't strictly require it.
- `tests/test_research_query.py` collection error stays until Task 12 — it's the legacy file, not the new code.
