# Researcher Module — Implementation Plan

**Date:** 2026-05-05
**Spec:** [2026-05-05-researcher-design.md](../specs/2026-05-05-researcher-design.md)
**Status:** Ready to build (after BA Task 4 commit)

## Goal

Ship the Researcher module in 11 sequential, independently-shippable commits. Each commit ends with a green test suite and a working incremental capability. Stopping after R-Task 5 (forensic LLM call works) gives you on-demand findings; R-Task 6+ adds the autonomous loop.

## Pre-flight

- Working dir: `d:\Personal\Projects\computer-use`
- Branch: continue on `phase1-foundation`
- Postgres: existing test DB; one new migration adds `researcher_findings`
- Models: gpt-5-mini for forensics + nudge text generation
- Substrate: shared with bidder (raised pacing default)
- Deployment: single VirtualBox Windows VM (no VPS migration)

## Build sequence

Each task: **what / files / acceptance test / commit message stub**.

---

### R-Task 0: Commit BA Task 4 + scorer fix (precursor)

**What:** Land the analyze_corpus + backtest_setup work and the 6-rule-key scorer fix as a clean commit before starting the Researcher track.

**Files (already written, uncommitted):**
- `ai/market_analysis.py` (new)
- `assistant/ba_tools.py` (extended)
- `assistant/prompts.py` (extended)
- `domain/scoring.py` (6 rule keys added)
- `tests/unit/ai/test_market_analysis.py` (new)
- `tests/integration/ai/test_market_analysis.py` (new)
- `tests/unit/domain/test_scoring.py` (12 new cases)
- `tests/test_assistant_tools.py` (3 new cases)

**Acceptance:** Tests already green (54 unit + 31 assistant + 8 integration when last run).

**Commit:** `BA: analyze_corpus + backtest_setup + scorer fix for 6 missing rule keys`

---

### R-Task 1: Migration for `researcher_findings` table

**What:** New migration `006_researcher_findings.sql` adding the table per spec §5.3.

**Files:**
- `storage/migrations/006_researcher_findings.sql` (new)
- `tests/integration/storage/test_findings_migration.py` (new) — applies migration, asserts table + indexes exist

**Acceptance:** `apply_migrations` against fresh DB → table exists with all columns and 3 indexes.

**Commit:** `Researcher: migration for researcher_findings table`

---

### R-Task 2: `FindingStore` — persistence + dedup

**What:** New `storage/findings.py` with `FindingStore` class. Methods:
- `insert(finding: ResearcherFinding) -> int` (returns finding_id; idempotent on dedup_key collision — returns existing ID)
- `get(finding_id) -> Optional[ResearcherFinding]`
- `list_by_status(status, limit=50) -> list[ResearcherFinding]`
- `update_status(finding_id, status, **dates)` — handles `nudged_at`, `dismissed_at`, `snoozed_until`
- `compute_dedup_key(finding_type, evidence_job_ids) -> str` (sha256, deterministic)

**Files:**
- `storage/findings.py` (new)
- `tests/integration/storage/test_findings.py` (new)

**Acceptance:**
- Insert + fetch round-trip
- Same dedup_key inserted twice → second returns existing ID, no duplicate row
- Status FSM: status='new' can transition to nudged/dismissed/snoozed; dates set correctly
- `list_by_status('new')` excludes nudged/dismissed/snoozed

**Commit:** `Researcher: FindingStore with idempotent dedup_key inserts`

---

### R-Task 3: `deep_search` — panel-level scan

**What:** Extend `upwork/search_driver.py` with `deep_search(query, filters, max_jobs)`. Reuses bidder's `upwork.panel.parse_panel` and the existing panel-open machinery.

**Files:**
- `upwork/search_driver.py` (extended)
- `tests/unit/upwork/test_search_driver.py` (extended) — mock observe + panel-parse interactions
- Manual smoke against live Upwork before merge (acceptance step in PR description)

**Logic:**
1. Build URL via `build_search_url`
2. Navigate, wait for results, focus, Ctrl+Home
3. Loop:
   a. Get visible result-card title hyperlinks (reuse `upwork/feed.py::_parse_visible_cards`-style helper, adapted to search-results layout)
   b. For each unprocessed title, click into panel
   c. `parse_panel` to get a `PanelData` (title, description, budget, skills, client trust, posted_at)
   d. Click "Copy to clipboard" to get the real Upwork URL
   e. Press Esc to close
   f. Build a `Job` from PanelData + URL
   g. Stop if `max_jobs` reached or two consecutive observations yield no new results

**Acceptance:**
- Unit: parse-panel mock returns expected `Job`
- Manual: `uv run python -c "from upwork.search_driver import deep_search; print(deep_search('RAG engineer', {'payment_verified': '1'}, max_jobs=3))"` returns 3 well-formed Job objects with full descriptions

**Commit:** `Researcher: deep_search captures full descriptions via panel-level UIA`

---

### R-Task 4: Corpus deep-write helper (`ingest_jobs`)

**What:** Add `MarketCorpusStore.ingest_jobs(jobs, source)` for deep-scan results. Distinct from `ingest_cards` — these have real URLs, so dedup is via `jobs.url UNIQUE` natively.

**Files:**
- `storage/market_corpus.py` (extended)
- `tests/integration/storage/test_market_corpus.py` (extended)

**Acceptance:**
- Same job (real URL) ingested twice → upsert path, no duplicate row
- Returns `{inserted: N, updated_existing: M}` matching count semantics of `ingest_cards`

**Commit:** `Researcher: ingest_jobs for deep-scan corpus writes (real-URL dedup)`

---

### R-Task 5: `find_specific_patterns` — the forensic LLM call

**What:** New `ai/agents/researcher.py::find_specific_patterns(jobs, portfolio, active_goal) -> list[JobForensicFinding]`. Single LLM call (gpt-5-mini) with structured output. Schema enforces specificity per spec §5.5.

**Files:**
- `ai/agents/__init__.py` (new, empty)
- `ai/agents/researcher.py` (new)
- `ai/prompts/researcher_forensics.py` (new) — the system prompt
- `ai/schemas.py` (extended with `JobForensicFinding`) — or new file if doesn't exist
- `tests/unit/ai/test_forensic_schema.py` (new)
- `tests/integration/ai/test_researcher.py` (new)

**Schema:** verbatim from spec §5.5.

**Validation step:** after LLM returns, verify every `evidence_job_ids` element exists in the input `jobs` list. Reject findings with hallucinated IDs.

**Acceptance:**
- Unit: `JobForensicFinding` rejects empty/short WHY fields, rejects empty `evidence_job_ids`
- Unit: validation step rejects findings with non-input job_ids
- Integration: feed a synthetic job set with a planted pattern (e.g. 5 near-identical Ragas job descriptions) → LLM returns at least one finding citing those job_ids
- Integration: feed an empty/random job set → LLM returns `[]` (no shallow filler)

**Commit:** `Researcher: find_specific_patterns LLM call with WHY-enforcing schema`

---

### R-Task 6: Researcher loop (`researcher/loop.py`)

**What:** The autonomous pass. Reads query portfolio, runs `deep_search` per query, ingests, calls `find_specific_patterns`, persists findings via `FindingStore`.

**Files:**
- `researcher/__init__.py` (new, empty)
- `researcher/query_portfolio.py` (new) — load/save portfolio in `system_config`, seed helper
- `researcher/loop.py` (new) — `run_researcher_pass(db)` entry point
- `tests/integration/researcher/test_loop.py` (new)

**Logic:** verbatim from spec §5.6.

**Acceptance:**
- Integration: synthetic query portfolio + monkeypatched `deep_search` returning canned jobs → pass completes, findings persisted
- Integration: same pass run twice → second run dedups, no duplicate findings
- Failure handling: `deep_search` raises → loop logs + continues to next query, doesn't crash

**Commit:** `Researcher: autonomous pass loop (deep_search + forensics + persist)`

---

### R-Task 7: Nudge engine

**What:** `researcher/nudge.py::process_new_findings(db, discord_send_fn)`. Decides which findings get DM'd immediately, batches the rest into daily digest.

**Files:**
- `researcher/nudge.py` (new)
- `ai/agents/nudge_writer.py` (new) — small LLM call that turns a finding into a 2-3 sentence DM
- `ai/prompts/nudge_writer.py` (new)
- `tests/unit/researcher/test_nudge.py` (new)
- `tests/integration/researcher/test_nudge.py` (new)

**Logic:** verbatim from spec §5.7.

**Acceptance:**
- Unit: `this_week` finding → immediate-DM branch; `this_month` → defer; `monitor` → no action
- Unit: digest fires only when ≥24h since last digest AND backlog non-empty
- Integration: monkeypatched discord_send → asserts correct findings sent + status updated

**Commit:** `Researcher: nudge engine (immediate this_week, daily this_month digest)`

---

### R-Task 8: Operator surface tools on assistant

**What:** 7 new tools per spec §5.9 added to `assistant/ba_tools.py`. All write tools route through `_audited_write` for revert support.

Tools:
- `list_findings(status?, urgency?, days_back=7)`
- `get_finding(finding_id)`
- `dismiss_finding(finding_id, reason?)`
- `snooze_finding(finding_id, days)`
- `add_research_query(query, payment_verified?, t?, hourly_rate?, amount?, proposals?, duration_v3?)`
- `remove_research_query(query)`
- `list_research_queries()`

`add_research_query` takes the same flat filter kwargs as `search_market` (no nested dicts — same gpt-5-mini-friendly pattern).

**Files:**
- `assistant/ba_tools.py` (extended)
- `assistant/prompts.py` (extended with Researcher tools section + finding-management workflow)
- `tests/test_assistant_tools.py` (extended)

**Acceptance:**
- Each tool round-trips against test DB
- Audit log captures status changes
- `revert_last_change` correctly reverses dismiss / snooze / portfolio adds

**Commit:** `Researcher: 7 operator-surface tools on assistant + revert support`

---

### R-Task 9: Scheduler wiring

**What:** Add Researcher pass to `scheduler/main.py` as a daily-at-5pm-local task. Also wire nudge engine to fire after every pass.

**Files:**
- `scheduler/main.py` (extended)
- `tests/integration/scheduler/test_researcher_schedule.py` (new)

**Logic:**
- New async task `researcher_loop()` that sleeps until next 5pm local, runs `run_researcher_pass(db)`, runs `process_new_findings(db, discord)`, sleeps a day
- On startup: if last pass was >36h ago (or never), run immediately (catch-up after downtime)
- Wrap in try/except → DM operator on failure with traceback, don't crash scheduler

**Acceptance:**
- Integration: invoke pass directly → end-to-end runs against synthetic portfolio
- Integration: scheduler starts, last pass timestamp absent → fires immediately
- Failure handling: pass raises → DM sent with traceback, scheduler still running

**Commit:** `Researcher: scheduler wiring (daily 5pm pass + nudge after)`

---

### R-Task 10: Pacing budget tool + global default raise

**What:** BA Task 9b — `set_pacing_budget(per_hour: int)` write tool. Substrate reads `system_config.pacing_budget_per_hour` on each cycle, falls back to env default. Bump default from 40 → 120.

**Files:**
- `substrate/pacing.py` (modified — read from sysconfig)
- `assistant/tools.py` (extended with `set_pacing_budget`)
- `tests/test_assistant_tools.py` (extended)

**Acceptance:**
- Tool sets sysconfig value → next pacing read reflects it
- Revert restores prior value
- Default = 120/hr if neither sysconfig nor env set

**Commit:** `Researcher: set_pacing_budget tool + raise global default to 120/hr`

---

### R-Task 11: BA Task 7b — operator-pulled proposers

**What:** `propose_project` and `propose_setup_from_corpus` tools. Operator-pulled (not autonomous). Same WHY-enforcing schemas as `JobForensicFinding`. `propose_setup_from_corpus` runs `backtest_setup` internally and embeds the count.

**Files:**
- `ai/agents/ba_proposer.py` (new) — `draft_project_brief`, `draft_setup_proposal`
- `ai/prompts/ba_project_brief.py` (new)
- `ai/prompts/ba_setup_proposal.py` (new)
- `ai/schemas.py` (extended with `ProjectGapBrief`, `SetupProposal`)
- `assistant/ba_tools.py` (extended)
- `tests/integration/ai/test_ba_proposers.py` (new)

**Logic:** as in BA spec §5.6 + §5.7 (the parts that survive — these are operator-pulled, not autonomous).

**Acceptance:**
- Integration: real corpus + portfolio + goal → both tools return well-formed structured output
- Integration: WHY-less LLM output → schema validation rejects, tool surfaces error
- Integration: `propose_setup_from_corpus` with theme that has 0 corpus matches → returns error with backtest_count=0 explanation

**Commit:** `Researcher: propose_project + propose_setup_from_corpus (operator-pulled)`

---

## Test gates between commits

After each commit:

1. `uv run pytest tests/unit -q` (sub-second)
2. `uv run pytest tests/integration -q -k '<relevant module>'`
3. Smoke: `uv run python -c "from assistant.tools import build_tools; print(len(build_tools(...)))"` to confirm no import errors

After R-Task 11:

1. Full suite: `uv run pytest -q`
2. Manual: restart scheduler in pm2 inside VirtualBox, observe 5pm Researcher pass, check DM lands for any `this_week` findings
3. Manual: in Discord, run `list_findings` → see results; `dismiss_finding 1` → see status update; `revert_last_change` → restored

## Out of scope for this plan

(For traceability — these are deferred and should not creep in.)

- Buyer identity tracking
- Per-job extraction + Python pattern detection (gpt-5-mini fallback)
- Multi-stage LLM (sonnet finals + mini first-pass)
- Agent-driven query portfolio curation
- Scheduling-window logic
- VPS migration
- Auto-application of findings
- Card-level Researcher passes
- Embedding-based clustering
- Multi-channel nudges

## Definition of done

- All 11 commits merged to `phase1-foundation`
- Full test suite green
- Manual smoke against live Upwork: at least one full Researcher pass completes, ≥1 finding produced, ≥1 nudge fired (or `monitor` urgency only — depends on what the corpus surfaces)
- System prompt updated; agent demonstrably uses the new tools when asked ("show me recent findings", "snooze #3 for a week")
- `MEMORY.md` index gets a new entry: `Researcher v1 shipped 2026-05-XX`

## Risks specific to execution

- **Search-page UIA structure drift from feed-page:** Upwork's search results page may have slightly different element naming than the feed. The deep_search implementation (R-Task 3) needs careful manual validation before merge. If the search-page panel-open mechanic doesn't match the feed exactly, expect a small adapter layer.
- **gpt-5-mini findings might be shallow despite schema:** the `min_length` validators force *length* but not *quality*. If findings come back as long but generic ("companies want AI agents"), the fallback architecture is per-job extraction + Python pattern detection. Schema infrastructure stays the same; only the LLM call shape changes.
- **Cold-start corpus problem:** Researcher's first few passes have nothing to compare against. Findings will be weak until ~2 weeks of corpus accumulates. Plan to manually run 5-10 `search_market` calls in the days right after R-Task 6 ships, to seed the corpus before the autonomous loop has been running long enough to have its own data.
- **Scheduler fires while VirtualBox is down:** spec says skip, no queueing. This loses a day's signal. Acceptable for v1 (we're optimizing for 80% capture, not 100%) but worth knowing.
- **Discord rate limit on digest day:** if many findings nudge on the same day, batched DMs could trip Discord's per-channel rate limit. Mitigation: digest is one DM with multiple findings inside, not one DM per finding.

## Sequencing rationale

The order is not arbitrary:

- **R-Task 1-2 (storage)** before anything else — findings need a home before they can be produced
- **R-Task 3-4 (deep scan + ingest)** before forensics — forensics needs full descriptions
- **R-Task 5 (forensics)** before loop — loop calls forensics
- **R-Task 6 (loop)** before nudges — nudges process loop output
- **R-Task 7 (nudges)** before operator surface — operator interacts with nudged findings
- **R-Task 8 (operator surface)** before scheduler — manual end-to-end before autonomous
- **R-Task 9 (scheduler)** before pacing tool — substrate budget pressure first appears here
- **R-Task 10 (pacing)** lands the operational dial right when it matters
- **R-Task 11 (proposers)** is independent of Researcher and can ship anytime; placed last because it's lower priority than the autonomous track

You could pause after R-Task 7 and have a working manual Researcher (call `run_researcher_pass()` from a script). R-Task 8+ adds the autonomous + agent-controlled layers.
