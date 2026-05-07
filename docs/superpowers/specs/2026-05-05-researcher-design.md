# Researcher Module — Design

**Date:** 2026-05-05
**Status:** Spec, ready for implementation
**Predecessor:** [2026-05-05 BA module design](./2026-05-05-ba-module-design.md). The BA spec's foundational tools (search driver, corpus, analyze_corpus, backtest_setup) are shipped (Tasks 1-4). This spec **replaces BA Tasks 5-9** with a different intelligence layer.

## 1. Why this spec replaces BA Tasks 5-9

The BA spec assumed market intelligence = aggregate statistics + LLM narrative on top. After the operator pushed back ("20% increase in AI Jobs is useless information"), it became clear what was actually wanted: **specific insights from specific jobs, not category statistics**.

Examples of what's *useless*:
- "AI jobs are up 20%"
- "RAG is the most common skill this week"
- "Median budget is $75/hr"

Examples of what's *useful*:
- "Three different RAG-eval jobs this week explicitly want Ragas + LangSmith. Your portfolio mentions LangSmith but not Ragas. Build a Ragas demo this week and you can credibly bid all three plus whoever follows the template."
- "8 jobs this week mention 'we tried Vapi/Bland/Retell and it didn't work.' Custom voice-build window is open. Pitch should lead with 'I've seen what fails with hosted platforms.'"
- "N8N + OpenAI combo: 0 jobs last week, 7 this week, all $1500-3000 fixed. YouTube tutorial probably dropped. 1-2 week tactical window."

These require **reading job descriptions** and **cross-referencing against your portfolio**, not aggregating metadata. The BA spec's design (per-skill counts, budget percentiles, country breakdowns) cannot produce these. So instead of building Tasks 5-9 as written, we build a different track that does.

## 2. Mission

> The Researcher reads jobs, finds specific actionable patterns, ties each finding to your portfolio, and DMs you when something's worth your time.

Not statistics. Not categories. Specific findings tied to specific evidence jobs, with specific suggested actions.

## 3. Operator decisions baked into this design

Resolved in the design conversation, non-negotiable for v1:

| Decision | Choice |
|---|---|
| Buyer identity tracking | **Out.** Upwork's exposed metadata isn't reliable enough; false positives would corrupt findings. |
| Deep-scan budget | One deep pass per day per active query. ~30-60 min of substrate/day across 3-5 queries. |
| Forensic LLM | gpt-5-mini. Open-ended cross-doc reasoning, single big prompt. Schema enforces specificity. |
| Nudge cadence | Agent-defaulted. "this week" urgency → immediate DM. "this month" → daily digest. |
| Deployment | Single-machine. Researcher + bidder + apply_executor share the VirtualBox VM. No VPS migration. |
| Pacing budget | Raise global default 40/hr → 120/hr. No scheduling-window logic. Both loops share freely. |

## 4. Non-goals for v1

- No buyer fingerprinting / `buyers` table
- No category-level statistical reporting (the existing `analyze_corpus` tool stays for setup-tuning workflows but is not the Researcher's output)
- No multi-stage LLM pipeline (per-job extraction → Python detection — deferred fallback if gpt-5-mini findings turn out shallow)
- No VPS migration / split architecture (later, separate concern)
- No scheduling-window logic for substrate contention (raise budget instead)
- No auto-application of findings (Researcher proposes; operator disposes)
- No second LLM agent — Researcher is a scheduled task that calls into structured-output LLM helpers, not a tool-using agent
- No automated portfolio editing — findings can suggest "build X" but never modify portfolio.json

## 5. Architecture

### 5.1 Two qualitative shifts from the BA spec

**Shift 1: deep scans, not card scans.** The card-level scans the BA spec ships capture only a ~200-char snippet. To detect content patterns ("8 jobs mention failing Vapi") you need the full description, which means the bidder's panel-level parse. The Researcher's primary scan tool is a new `search_market_deep` that opens the panel for each result.

**Shift 2: LLM is the analysis engine, not a narrative generator.** The BA spec used LLM only to write friendly summaries on top of pure SQL aggregates. The Researcher feeds full job descriptions to an LLM and asks it to find specific cross-job patterns. This is the bet — that a well-prompted gpt-5-mini with a strict schema can produce specific findings that aggregate statistics cannot.

### 5.2 Module layout

```
upwork/
  search_driver.py          # EXISTS: card-level. Extend with deep_search().

storage/
  market_corpus.py          # EXISTS. Extend with full-description ingestion.
  findings.py               # NEW: ResearcherFinding rows, dedup, snooze, dismiss.

ai/
  market_analysis.py        # EXISTS (analyze_corpus, backtest).
  agents/
    researcher.py           # NEW: find_specific_patterns LLM call.
    ba_proposer.py          # NEW: from BA Task 7b — operator-pulled propose_project.
  prompts/
    researcher_forensics.py # NEW: the system prompt for find_specific_patterns.

researcher/
  loop.py                   # NEW: scheduled task. Runs deep scans + forensics
                            # for each active query, persists findings.
  nudge.py                  # NEW: decides which findings to DM, batches lower
                            # urgency into daily digest, dedups against last week.
  query_portfolio.py        # NEW: which queries the Researcher runs.
                            # v1: operator-curated list in DB (+ migration).

assistant/
  ba_tools.py               # EXISTS. Extend with finding-management tools.
```

### 5.3 Data model — one new table

```sql
CREATE TABLE researcher_findings (
  finding_id        bigserial PRIMARY KEY,
  detected_at       timestamptz NOT NULL DEFAULT now(),
  finding_type      text NOT NULL,   -- enum, see §5.5
  headline          text NOT NULL,
  why_specific      text NOT NULL,
  portfolio_tie     text NOT NULL,
  suggested_action  text NOT NULL,
  urgency           text NOT NULL,   -- 'this_week' | 'this_month' | 'monitor'
  evidence_job_ids  text[] NOT NULL,
  raw_llm_response  jsonb,           -- audit trail
  status            text NOT NULL DEFAULT 'new',
                                     -- 'new' | 'nudged' | 'dismissed'
                                     --     | 'snoozed_until_<date>'
  nudged_at         timestamptz,
  dismissed_at      timestamptz,
  snoozed_until     timestamptz,
  dedup_key         text NOT NULL    -- hash of (finding_type, sorted evidence_jobs)
                                     -- prevents the same finding firing twice
                                     -- across consecutive Researcher passes
);

CREATE INDEX idx_findings_status ON researcher_findings (status);
CREATE INDEX idx_findings_dedup ON researcher_findings (dedup_key);
CREATE INDEX idx_findings_detected ON researcher_findings (detected_at DESC);
```

Plus a small system_config-backed query portfolio (no new table — uses existing `system_config` keyed by `researcher_query_portfolio` storing JSON).

### 5.4 The deep-scan extension

`upwork/search_driver.py::deep_search(query, filters, max_jobs)`:

1. Same URL navigation as `search()` (uses `build_search_url`)
2. Wait for results
3. For each visible result card (up to max_jobs):
   a. Click into the result panel (reuse bidder's panel-open machinery)
   b. Walk the panel UIA tree (reuse `upwork.panel.parse_panel`)
   c. Extract full description, real Upwork URL via clipboard click, full skills list, full client trust info
   d. Capture as `Job` (not `CardResult`) — same shape the bidder produces
   e. Press Esc to close panel, advance to next card
4. Return `list[Job]`

Reuses everything the bidder already does. The only new code is the loop wrapper around `parse_panel` driven by the search-results page rather than the feed.

Per-job time: ~15s (panel open + UIA walk + URL capture). For 30-50 jobs/query: 7-12 min. Acceptable inside the raised pacing budget.

Corpus writes go through `storage/market_corpus.py::ingest_jobs` (new function — distinct from `ingest_cards` because deep-scan jobs have real URLs, so dedup is via `jobs.url UNIQUE` not synthetic-id hash).

### 5.5 The forensic LLM call

`ai/agents/researcher.py::find_specific_patterns(jobs, portfolio, active_goal)`:

Single LLM call. Inputs:
- `jobs`: the full description text + metadata for the deep-scanned set (typically 30-50 jobs in one pass)
- `portfolio`: full `portfolio.json` (so the LLM knows what you've built and can tie findings to gaps/strengths)
- `active_goal`: the operator's current goal (so threshold language is goal-relative)

The system prompt explicitly instructs:

- Find specific patterns, not category statistics. "AI jobs up 20%" is wrong shape; "5 jobs explicitly want Ragas" is right shape.
- Every finding must cite specific evidence_job_ids (not "many jobs" — actual IDs)
- Every finding must reference the portfolio (gap, strength, or "no fit")
- Every finding must suggest a specific action ("build a Ragas demo" — not "consider RAG")
- Every finding must have a goal-fit explanation if relevant
- If nothing's specific enough to surface, return empty list. Better to say nothing than to invent patterns.

Returns: `list[JobForensicFinding]`.

```python
class JobForensicFinding(BaseModel):
    finding_type: Literal[
        "emerging_template",         # near-identical job descriptions
        "failure_mode_pattern",      # multiple jobs cite the same failed thing
        "tech_combo_emergence",      # specific tech combination spiking
        "specific_stack_demand",     # explicit ask for a specific stack
        "budget_anomaly",            # budget shift in a niche worth noting
        "geographic_cluster",        # geographic concentration worth noting
    ]
    headline: str = Field(min_length=20)
        # one-line specific summary, e.g. "5 jobs this week want Ragas + LangSmith"
    why_specific: str = Field(min_length=40)
        # cite job evidence: which jobs, what specifically they say
    portfolio_tie: str = Field(min_length=30)
        # cite portfolio.json items by name; gap, strength, or "no fit"
    suggested_action: str = Field(min_length=30)
        # specific next step
    urgency: Literal["this_week", "this_month", "monitor"]
    evidence_job_ids: list[str] = Field(min_length=2)
        # at least 2 — single-job "patterns" aren't patterns
```

Schema does the work of forcing specificity. Empty/shallow strings → `ValidationError` → finding rejected, not persisted.

### 5.6 The Researcher loop

`researcher/loop.py::run_researcher_pass()`:

```
1. Read query_portfolio from system_config.
   v1: operator-curated. Default seed: 4 queries pulled from the active goal
       and portfolio (e.g. "AI agent developer", "RAG engineer",
       "voice AI", whatever's in goal.prose tokens).

2. For each active query:
   a. Call upwork.search_driver.deep_search(query, filters, max_jobs=40)
      with pacing budget shared with the bidder
   b. Persist jobs to corpus via storage.market_corpus.ingest_jobs(...)
   c. Call ai.agents.researcher.find_specific_patterns(
          jobs=just-ingested,
          portfolio=load_portfolio(),
          active_goal=goals.get_active())

3. For each returned finding:
   a. Compute dedup_key = sha256(finding_type + sorted(evidence_job_ids))
   b. If dedup_key already in researcher_findings (any status), skip
   c. Otherwise insert with status='new'

4. Hand off to nudge engine (next section).
```

Schedule: **once per day**, late afternoon (~5pm local). Runs all queries serially. Total runtime budget: 30-60 min substrate + ~30s LLM per query.

If the Windows VM is unreachable when the schedule fires, skip — no queueing, no catch-up. The next day's pass will pick up the latest signal. (We're optimizing for 80% capture, not 100%.)

### 5.7 The nudge engine

`researcher/nudge.py::process_new_findings()`:

Runs after every Researcher pass.

```
1. Pull all findings with status='new'.

2. For each finding:
   a. If urgency = 'this_week' → immediate DM to operator,
      mark status='nudged', set nudged_at=now()
   b. If urgency = 'this_month' → defer to daily digest
   c. If urgency = 'monitor' → leave as 'new', operator can list

3. After processing: if any 'this_month' findings are pending AND
   it's been ≥24h since the last digest → fire digest DM with up to
   5 findings, oldest first, mark all as 'nudged'.
```

DM text generation: a small structured-output LLM call (gpt-5-mini, ~$0.001) that takes a finding and writes a 2-3 sentence Discord message in the assistant's voice. Single LLM call, structured output, schema requires citing the specific evidence and suggested action.

The DM lands in the same channel as the assistant. Operator can reply *"tell me more about #N"* — assistant has tools to fetch finding details (§5.9).

### 5.8 Query portfolio management (v1)

System_config key `researcher_query_portfolio` stores a JSON list:

```json
[
  {"query": "AI agent developer",
   "filters": {"payment_verified": "1", "hourly_rate": "60-"},
   "added_at": "2026-05-05T10:00:00Z",
   "added_by": "seed"},
  {"query": "RAG engineer",
   "filters": {"payment_verified": "1", "t": "0", "hourly_rate": "50-"},
   "added_at": "2026-05-05T10:00:00Z",
   "added_by": "seed"},
  ...
]
```

v1 portfolio ops:
- `seed_researcher_portfolio()` (one-time bootstrap from active goal + portfolio.json keywords)
- Operator-managed via assistant tools (added in §5.9)
- No agent-driven curation in v1 (deferred — see §10)

### 5.9 Operator surface (assistant tools)

5 new tools on the assistant agent:

| Tool | What it does |
|---|---|
| `list_findings(status?, urgency?, days_back=7)` | List recent findings; default = unfinished from last 7 days |
| `get_finding(finding_id)` | Full detail of one finding incl. raw evidence jobs |
| `dismiss_finding(finding_id, reason?)` | Mark finding dismissed; won't re-surface |
| `snooze_finding(finding_id, days)` | Hide for N days, then re-surface as 'new' |
| `add_research_query(query, filters?)` | Add a query to the portfolio |
| `remove_research_query(query)` | Remove a query from the portfolio |
| `list_research_queries()` | Show what the Researcher is scanning |

All write tools route through `_audited_write` so `revert_last_change` works. Same pattern as every other write tool.

### 5.10 Pacing budget change

Default global budget raises from 40/hr to **120/hr**. The new `set_pacing_budget` tool (BA Task 9b) lets operator tune up/down via the agent.

Bidder + Researcher share this freely. We're betting that:
- Bidder peak load is ~40/hr (current default, never hit ceiling)
- Researcher peak load is ~50/hr in concentrated bursts during a deep pass
- Combined peak is well under 120/hr
- If it isn't, operator raises further (or we add scheduling-window logic later)

## 6. What stays from the BA plan

Three pieces from the original BA plan stay, but reframed as **operator-pulled tools** (the Researcher being the autonomous counterpart):

| BA task | Reframed |
|---|---|
| Task 5b — WHY-enforcing schemas | Reused; same schema philosophy applies to `JobForensicFinding`. |
| Task 7b — `propose_project` / `propose_setup_from_corpus` | On-demand operator tools for "okay I have time to build something next week, what should it be?" workflows. Different from findings — these are deliberate operator-pulled briefs, not autonomous nudges. |
| Task 9b — `set_pacing_budget` | Still needed. Shared budget makes operator tuning more important, not less. |

What's **dropped** from the BA plan:
- Weekly Monday nudge (replaced by event-driven Researcher nudges)
- `find_portfolio_gaps` (clusters-then-LLM-severity) — Researcher's portfolio_tie field gives this per-finding instead

## 7. Reliability primitives

Inherited:
- All write tools audit + revert via existing machinery
- Pacing enforced at substrate layer
- Failures surface as `{"error": ...}` from tools; loop failures DM operator with traceback
- Idempotent corpus writes (existing `JobStore` upsert + new `ingest_jobs` for real URLs)
- Goal as single source of truth (Researcher reads `goals.get_active()` every pass)

Specific to Researcher:
- **Schema validation as the specificity gate.** Findings missing required WHY fields are rejected at parse time, not surfaced.
- **Dedup_key prevents repeat nudges.** Same finding type + same evidence jobs → same key → skip.
- **Empty findings are explicit.** LLM is instructed to return `[]` if nothing's specific enough; no shallow filler.
- **Status FSM:** `new → nudged | dismissed | snoozed_until_<date>`. Snooze auto-promotes back to `new` after the date.
- **No silent skips.** If a deep scan fails, log + DM the operator, don't quietly drop the query.

## 8. Test strategy

| Layer | Coverage |
|---|---|
| `deep_search` | Unit: parse panels from saved UIA dumps. Manual smoke against live Upwork. |
| `ingest_jobs` | Integration: real-URL dedup; same-URL twice → no duplicate row. |
| `JobForensicFinding` schema | Unit: every WHY field rejected when empty/short. Empty `evidence_job_ids` rejected. |
| `find_specific_patterns` | Integration: small synthetic job set with a planted pattern → LLM finds it. Real corpus → outputs validate against schema. |
| Researcher loop | Integration: end-to-end on synthetic corpus, asserts finding rows persisted with correct dedup_key. |
| Nudge engine | Unit: urgency → action mapping. Integration: digest batches correctly across multi-day windows. |
| Operator surface | Integration: list/dismiss/snooze tools round-trip. Audit log captures status changes. |
| Pacing | Integration: `set_pacing_budget` writes through to substrate. |

## 9. Risk register

| Risk | Mitigation |
|---|---|
| gpt-5-mini's cross-doc reasoning produces shallow findings | Schema enforces minimum specificity. Operator dismisses shallow ones; we observe dismissal rate over the first 2 weeks. If high → switch to per-job extraction + Python pattern detection (the deferred Implication 1 fallback). |
| Researcher eats too much pacing budget, bidder starves | Raised default to 120/hr. If contention persists, operator raises further or we add scheduling windows. |
| Same finding fires repeatedly across passes | dedup_key on (finding_type + sorted evidence_jobs). Once persisted, never re-fired. |
| Findings cite jobs the operator can't open | All evidence_job_ids reference the corpus; deep-scanned jobs have real Upwork URLs. `get_finding` returns URLs. |
| Deep scan trips Upwork's anti-bot heuristics | Same risk as bidder. Pacing layer handles. If it becomes a problem, reduce queries-per-day or scan-jobs-per-query before doing anything more invasive. |
| LLM hallucinates evidence_job_ids that aren't in the input | Validation step: every returned `evidence_job_ids` element must be in the input set. Mismatches → rejected as invalid finding. |
| Findings backlog if operator stops responding | `monitor` urgency findings stay `new`; operator can `list_findings` and triage in batches. No spam pressure. |
| Query portfolio drifts away from operator's actual goals | v1 is operator-curated; agent tools let operator add/remove. Agent-driven curation deferred. |

## 10. Operational model

### Daily flow

- **5pm local:** Researcher pass fires. Deep-scans 4 queries, ~30-50 jobs each. Total: ~40 min of substrate, ~$0.05 LLM cost ($0.01/query × 4 + tiny nudge costs).
- **5:45pm:** Forensics complete. New findings persisted. Nudge engine runs.
- **5:46pm:** Any `this_week` findings → immediate DM. `this_month` findings → defer.
- **Daily ~8pm:** Digest fires if `this_month` backlog has anything in it.

### Weekly flow

- Operator reads findings throughout the week, dismisses or acts on them
- `monitor` findings accumulate as a queryable backlog (`list_findings status=new urgency=monitor`)
- Operator can adjust query portfolio at any time (`add_research_query`, `remove_research_query`)

### How findings tie to existing system

- **Bidder:** Researcher writes to the same `jobs` table. Bidder reads from it. No interaction needed — they share the corpus.
- **Goal:** Researcher reads `goals.get_active()` every pass; findings cite goal fit.
- **Portfolio:** Researcher reads `portfolio.json` every pass; findings cite specific items.
- **Setups:** Researcher does NOT auto-create setups. A finding can suggest "make a setup for X"; operator runs `propose_setup_from_corpus` (BA Task 7b) explicitly.
- **Apply:** Findings never trigger applies. They surface opportunities; operator and bidder execute.

## 11. What's deferred (explicit non-goals revisited)

These are out of scope for v1 and tracked here so they don't creep in:

- Buyer identity tracking
- Per-job extraction + Python pattern detection (gpt-5-mini fallback architecture)
- Multi-stage LLM pipeline (sonnet for finals, mini for first-pass)
- Agent-driven query portfolio curation
- Scheduling-window logic for substrate contention
- Auto-application of findings
- VPS migration / split architecture
- Card-level Researcher passes (cheaper but lossy — defer until we observe the deep-scan budget pain in practice)
- Embedding-based description-similarity clustering (deferred fallback if "emerging_template" findings underperform)
- Multi-channel nudges (Slack, email) — DM only

## 12. Appendix: relationship to existing modules

| Existing module | Relationship to Researcher |
|---|---|
| `upwork/search.py` | Reused as-is for URL grammar |
| `upwork/search_driver.py::search` (card-level) | Untouched; Researcher uses new `deep_search` |
| `upwork/panel.py` | Reused — same panel parser the bidder uses |
| `storage/jobs.py::JobStore.upsert` | Reused for deep-scanned jobs (real URLs, ON CONFLICT (job_id) handles dedup) |
| `storage/market_corpus.py::ingest_cards` | Untouched; sibling `ingest_jobs` added for deep-scan path |
| `ai/market_analysis.py::analyze_corpus` | Stays. Operator-pulled tool for setup-tuning. NOT the Researcher's output. |
| `ai/market_analysis.py::backtest_filter_dsl` | Stays. Used by `propose_setup_from_corpus`. |
| `domain/scoring.py` | Untouched; recently extended with 6 missing rule keys (Task 4 fix) |
| `assistant/tools.py` | Extended with finding-management + query-portfolio tools |
| `assistant/prompts.py` | Extended with Researcher tools section |
| Bidder loops | Untouched. Share corpus with Researcher; no other interaction. |
