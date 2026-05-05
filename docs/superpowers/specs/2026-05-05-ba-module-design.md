# BA Module v1 — Design

**Date:** 2026-05-05
**Status:** Spec, ready for implementation
**Predecessor:** [2026-05-02 trading-system architecture](./2026-05-02-trading-system-architecture-design.md) — §1, §5.2, §5.3, §5.4 originally specified the BA layer. This document supersedes the BA portion of that spec.

## 1. Why this spec supersedes the original

The original BA design (May 2) predates the Discord ops assistant. It assumed:

- BA was a separate scheduled multi-step LangChain agent
- BA proposals landed in a `setup_proposals` table for ✅/❌ react-button approval
- BA had its own tool set distinct from the bidder
- The operator had no conversational surface to talk to the analyst

Since then we shipped the Discord ops assistant (2026-05-04), goals + briefed scans (2026-05-04), and the sniper bidder (2026-05-05). The assistant is now a working conversational interface with 38 tools spanning read, write, goals, briefs, and revert. Building a second LangChain agent with overlapping read tools would duplicate machinery for no gain.

This spec rewires BA as **capabilities on the existing assistant agent**, plus one scheduled nudge.

## 2. Mission (unchanged)

> BA figures out what's out there and what we need to win.
> Bidder says "you wanted this — I found it — should I apply?"

The BA's job is market intelligence — corpus analysis, demand signal detection, portfolio gap identification, and setup proposals grounded in evidence. It never auto-applies, never auto-creates setups, never blocks the bidder.

## 3. Operator decisions baked into this design

These were resolved in the design conversation and are non-negotiable for v1:

| Decision | Choice |
|---|---|
| Architecture | BA-as-tools on existing assistant + weekly conversational nudge |
| Search fidelity | Card-level only (title, snippet, budget, posted-time, skills, country) |
| Pacing | Single shared budget, raised manually when BA needs more |
| Thresholds | Driven by active goal, never hardcoded — "high-ticket" = `goal.min_hourly` / `goal.min_budget` |
| Portfolio mode | Suggest *new projects to build* (not which existing items to feature) |
| Project brief | Brief + WHY (demand evidence + portfolio gap + goal tie). No build-time estimate. |
| Setup proposals | In v1 (not deferred). Same WHY-required schema. Backtested before proposing. |
| Approval flow | Conversational — agent proposes in chat, operator says yes, agent calls `create_setup` |
| Nudge channel | DM (same as the assistant). No dedicated channel. |

## 4. Non-goals for v1

- No `setup_proposals` table — proposals are structured chat replies, applied via existing `create_setup`
- No second LLM agent — BA is tools on the assistant
- No pitch experimenter (Phase 3 in the original spec)
- No auto-clustering with hidden heuristics — operator picks the theme, agent analyzes within it
- No panel-open per result card during BA scans
- No new pacing system — share the existing budget
- No build-time estimates in project briefs
- No multi-tenant anything

## 5. Architecture

### 5.1 Tool taxonomy

Six new tools registered on the assistant agent. Three are pure SQL (no LLM), three are LLM-backed with strict structured output.

| Tool | Pure SQL? | Returns |
|---|---|---|
| `search_market(query, filters?)` | No (UIA) | `{scanned, new_to_corpus, sample}` |
| `analyze_corpus(window_days=30, source_pattern?)` | Yes | skill/budget/volume/country/verified stats |
| `backtest_setup(filter_patch, window_days=30)` | Yes | `{match_count, total, sample}` |
| `find_portfolio_gaps(window_days=30)` | Mostly SQL + 1 LLM call | clusters with gap severity |
| `propose_project(theme, window_days=30)` | LLM | `ProjectGapBrief` |
| `propose_setup_from_corpus(theme, filter_hint?)` | LLM (calls `backtest_setup` internally) | `SetupProposal` |

### 5.2 Module layout

```
upwork/
  search.py               # EXISTS: URL grammar (build_search_url, ALLOWED_FILTER_KEYS)
  search_driver.py        # NEW: card-level UIA driver. Navigates URL, parses cards.

storage/
  market_corpus.py        # NEW: thin wrapper over `jobs` table for BA reads.
                          # Writes go through existing JobStore with source='ba:<query>'.

ai/
  market_analysis.py      # NEW: pure SQL aggregations. No LLM.
  agents/
    ba_proposer.py        # NEW: structured-output LLM calls.
                          #   - rank_gap_severity (used by find_portfolio_gaps)
                          #   - draft_project_brief (used by propose_project)
                          #   - draft_setup_proposal (used by propose_setup_from_corpus)

ba/
  weekly_nudge.py         # NEW: scheduler task. Runs Monday 9am local.

assistant/
  ba_tools.py             # NEW: the 6 tools. Imported by assistant/tools.py
                          # and added to build_tools() return list.
```

### 5.3 No schema changes

The `jobs` table already has a `source` column. BA scans write `source='ba:<query>'` (e.g. `'ba:RAG engineer|payment_verified=1,t=0,hourly_rate=50-'`). The bidder's existing rows keep whatever source they currently have (`'feed'` or similar).

Reading: the `analyze_corpus` tool accepts `source_pattern` (SQL `LIKE`) so the agent can scope analysis to BA scans only, bidder scans only, or all corpus.

One stateful artifact for the weekly nudge: store the last nudge text in `system_config` under key `ba_last_nudge_text` so the LLM can avoid repeating themes week-over-week. Already-existing `SystemConfigStore` handles this — no migration.

### 5.4 Search driver — card-level parse

`upwork/search_driver.py` exposes one function:

```python
@dataclass
class CardResult:
    title: str
    snippet: str               # short description from the card
    budget_kind: str            # 'hourly' | 'fixed' | 'unknown'
    budget_min_usd: Optional[float]
    budget_max_usd: Optional[float]
    posted_text: str            # raw, e.g. "12 minutes ago"
    posted_at: Optional[datetime]   # parsed from posted_text
    skills: list[str]
    client_country: Optional[str]
    payment_verified: Optional[bool]   # if visible on card
    job_url: Optional[str]      # captured from card href if accessible

def search(query: str, filters: dict, max_cards: int = 30) -> list[CardResult]: ...
```

Implementation reuses the substrate (`substrate/observe.py`, `substrate/act.py`):

1. `build_search_url(query, filters)` → URL
2. `act.navigate(url)` then wait for results to render
3. `observe.tree(...)` → walk the results list
4. Parse each card with regex anchors (same approach as the panel parser in `upwork/panel.py`, but card-shaped)
5. Stop after `max_cards` or when no more cards visible

No panel-open. No clipboard URL capture. Card-level only. The `job_url` field is best-effort from a visible href; if the card doesn't expose one, leave it None.

Pacing: every navigate / scroll routes through existing `pacing` budget. BA scans share the budget with the bidder. If BA needs more, operator raises it via the new `set_pacing_budget` tool (see §5.7).

### 5.5 Corpus writes — idempotency

`storage/market_corpus.py::ingest_cards(cards: list[CardResult], source: str)`:

For each card:
- If `job_url` is present: dedupe by URL (existing `jobs.url UNIQUE` constraint handles this)
- If `job_url` is absent: dedupe by `(title, posted_text, source)` tuple
- New rows: insert with `source='ba:<query>'`, `scraped_first_at=now()`
- Existing rows: bump a `last_seen_at` field if it exists (otherwise no-op)

Returns `(inserted, skipped_duplicate)` counts.

### 5.6 The structured output schemas (WHY enforcement)

This is the load-bearing constraint of the whole design. Pydantic refuses any output missing the WHY fields. The LLM literally cannot ship a brief without citing demand, gap, and goal-fit.

```python
from pydantic import BaseModel, Field
from typing import Literal

class ProjectGapBrief(BaseModel):
    title: str = Field(min_length=5)
    one_line_pitch: str = Field(min_length=20)
    why_demand: str = Field(min_length=30,
        description="Cite corpus evidence: N jobs in window, $X-Y budget range, posting velocity")
    why_gap: str = Field(min_length=30,
        description="Cite portfolio.json items by name; what's missing")
    why_goal_fit: str = Field(min_length=30,
        description="Cite the active goal's min_hourly / min_budget / preferred_country")
    relevance_tags: list[str] = Field(min_length=1)

class SetupProposal(BaseModel):
    name: str = Field(min_length=3)
    tier: Literal['quiet', 'normal', 'critical']
    filter_dsl: dict
    prose: str = Field(min_length=30)
    backtest_count: int = Field(ge=0)
    why_demand: str = Field(min_length=30)
    why_gap: str = Field(min_length=30)
    why_goal_fit: str = Field(min_length=30)

class GapCluster(BaseModel):
    cluster_name: str
    sample_jobs: list[str]   # job titles
    demand_count: int
    portfolio_coverage_score: float   # 0.0-1.0, deterministic
    gap_severity: Literal['low', 'medium', 'high', 'critical']
    rationale: str = Field(min_length=20)
```

Validation tests assert that synthetic LLM outputs missing any `why_*` field are rejected by pydantic.

### 5.7 Pacing tool

New tool on the assistant: `set_pacing_budget(per_hour: int)`. Writes to `system_config` under `pacing_budget_per_hour`. The substrate's pacing module reads this on each cycle, falls back to the env default (40) if unset.

Operator usage: *"bump pacing to 100/hr, BA needs to scan five queries"* → tool call → next BA scan has more headroom.

This is a simpler shape than `set_connects_cap` (single int, no daily/weekly distinction). Auditable + revertable like all other write tools.

### 5.8 Weekly nudge flow

`ba/weekly_nudge.py`, scheduled by `scheduler/main.py` to fire Monday 9am local:

```
def run_weekly_nudge():
    goal = goals.get_active()
    analysis = analyze_corpus(window_days=7)
    gaps = find_portfolio_gaps(window_days=30)   # 30d for stability
    last_nudge = sysconfig.get("ba_last_nudge_text") or ""

    nudge_text = ai.weekly_nudge_llm(
        goal=goal,
        analysis=analysis,
        gaps=gaps[:3],
        last_nudge=last_nudge,
    )
    if nudge_text.strip() == "QUIET_WEEK":
        return  # nothing notable
    discord.dm_operator(nudge_text)
    sysconfig.set("ba_last_nudge_text", nudge_text)
```

LLM prompt explicitly instructs:

- One paragraph, conversational tone (matches assistant voice)
- Lead with the most actionable signal
- Cite the goal explicitly when relevant
- If nothing's actionable, return literal `QUIET_WEEK`
- Don't repeat the theme of `last_nudge` unless demand has materially changed

The DM lands in the same channel as the assistant, so the operator can reply *"tell me more about X"* and the assistant has all 6 BA tools to dig deeper. The conversation continuation requires no extra wiring — the assistant agent is already the DM handler.

### 5.9 System prompt additions

`assistant/prompts.py` gets a new section:

```
BA tools (market intelligence):
- search_market(query, filters?) — UIA scan an Upwork search URL, write cards
  to corpus. Filters: payment_verified, t (0=hourly/1=fixed), hourly_rate,
  amount, proposals, duration_v3.
- analyze_corpus(window_days?, source_pattern?) — pure SQL aggregation.
- backtest_setup(filter_patch, window_days?) — count corpus matches before
  proposing a setup.
- find_portfolio_gaps(window_days?) — clusters where demand is high and
  portfolio coverage is low.
- propose_project(theme) — generate a project brief with required WHY fields.
- propose_setup_from_corpus(theme) — generate a setup proposal with
  backtest count and required WHY fields.

When using BA tools:
- The active goal IS the threshold definition. "High-ticket" = whatever
  goal.min_hourly or goal.min_budget says today. If the goal changes
  mid-conversation, BA outputs change with it.
- Never propose a setup without backtesting. Cite the count.
- Never propose a project without citing demand evidence + portfolio gap +
  goal fit. The schema enforces this; don't fight it.
- Card-level scans are cheap. Use them liberally. Deep panel scans don't
  exist in BA — that's the bidder's job.
```

## 6. Reliability primitives

Inherited from existing system:

- **Audit log**: every write tool (`search_market` writes, `set_pacing_budget`) goes through `_audited_write` for revert support
- **Pacing**: shared budget enforced at substrate layer, no BA-specific bypass
- **Failure surfacing**: BA tools return `{"error": "..."}` like every other tool — no silent failures
- **Goal as single source of truth**: agent prompt loads `get_goal()` every turn, BA tools read from same store
- **Idempotent corpus writes**: re-running the same query doesn't double-write

Specific to BA:

- **Structured output validation**: pydantic schemas reject WHY-less briefs at parse time, not at presentation time
- **Backtest before propose**: `propose_setup_from_corpus` calls `backtest_setup` internally and embeds the count; refuses to propose if `backtest_count == 0`
- **Quiet weeks are explicit**: nudge LLM must return literal `QUIET_WEEK` to skip; no silent skip from low-confidence output

## 7. What ships, what doesn't

**Ships in v1:**

- 6 new tools on the assistant
- Card-level search driver
- Pure-SQL analytical layer
- WHY-enforcing structured outputs
- Weekly Monday DM nudge
- Goal-driven thresholds throughout
- `set_pacing_budget` tool

**Deferred to v1.1+:**

- Pitch experimenter agent (cover-letter A/B variants)
- Performance reviewer agent (funnel review, retire/upgrade proposals)
- Deep panel scans during BA crawls
- Per-source pacing budgets
- Auto-clustering without operator-supplied theme
- `setup_proposals` staging table (only revisit if conversational approval breaks down)
- Build-time estimates in project briefs (operator explicitly excluded)

## 8. Test strategy

| Layer | Coverage |
|---|---|
| `upwork/search_driver.py` | Unit: parse cards from saved UIA dump (`tests/unit/upwork/test_search_driver.py`). Integration: drive one real query manually before merge. |
| `storage/market_corpus.py` | Integration: same query twice → no duplicates. |
| `ai/market_analysis.py` | Unit: aggregations on synthetic corpus. Edge cases: empty corpus, single-week window. |
| `backtest_setup` | Unit: known patch + synthetic corpus → known count. Verifies parity with bidder's scoring. |
| Schemas | Unit: synthetic outputs missing each WHY field → pydantic ValidationError. |
| `find_portfolio_gaps` / `propose_project` / `propose_setup_from_corpus` | Integration: real corpus, real portfolio, end-to-end. |
| `ba/weekly_nudge.py` | Integration: nudge runs end-to-end, posts to test DM channel. Unit: prompt receives goal + analysis + last-week text correctly. |
| Pacing tool | Integration: setting budget changes substrate behavior on next cycle. |

Existing test infrastructure (`tests/unit/`, `tests/integration/`, `pytest`, real Postgres test DB) is reused.

## 9. Risk register

| Risk | Mitigation |
|---|---|
| Card-level parse misses a budget chip Upwork renders weirdly | Same risk as the bidder's panel parser. Mitigated by saved-dump unit tests. Card-level is *less* fragile than panel because there are fewer optional fields. |
| LLM returns valid pydantic output but with shallow WHY ("there is demand for this") | `min_length` on each WHY field forces some specificity. Prompt explicitly instructs to cite numbers + portfolio item names + goal fields. Not enforceable structurally; flagged for human review of early outputs. |
| BA scans eat the bidder's pacing budget mid-cycle | Operator can raise via `set_pacing_budget`. If this becomes a chronic problem in practice, split into bidder/BA budgets in v1.1. |
| Weekly nudge becomes spammy if the LLM is over-eager | `QUIET_WEEK` escape hatch + `last_nudge` repeat-detection. Operator can also tell the agent "quiet down the Monday nudge" → the assistant adds a system_config flag. |
| Filter URL grammar drifts (Upwork changes URL params) | Existing 15 tests in `tests/test_research_query.py` plus integration test on every BA tool merge. If Upwork breaks the grammar, BA scans fail loudly. |
| `propose_setup_from_corpus` backtest is 0 | Tool refuses to return a proposal with `backtest_count == 0`. Returns `{"error": "no corpus matches; widen the theme or scan more"}`. |

## 10. Operational model

### How the operator uses BA day-to-day

- **Asking what's out there**: *"Scan for high-end RAG work this week"* → agent calls `search_market("RAG engineer", {payment_verified: 1, hourly_rate: "60-"})` → corpus updates → agent summarizes.
- **Trend questions**: *"What's the budget distribution looking like for AI agent work this month?"* → `analyze_corpus(source_pattern='ba:%agent%')` → agent answers.
- **Setup proposals**: *"Should we have a dedicated setup for voice AI?"* → agent calls `find_portfolio_gaps`, `analyze_corpus`, then `propose_setup_from_corpus("voice AI")` → presents proposal with backtest + WHY fields → operator says yes → agent calls `create_setup`.
- **Project briefs**: *"What should I build next?"* → agent calls `find_portfolio_gaps` then `propose_project` on the top cluster → operator reads the brief and decides.
- **Monday morning**: agent DMs unprompted with the most actionable signal of the week.

### How BA fits with existing infrastructure

- **Goal**: read by every BA tool; `min_hourly` / `min_budget` / `preferred_country` are the threshold language
- **Briefed scans**: separate concept (one-shot bidder run); BA does NOT replace them. Operator can still trigger ad-hoc bidder hunts via `trigger_briefed_scan`.
- **Sniper bidder**: untouched; BA writes to corpus, sniper reads from corpus during detection
- **Revert**: BA write tools (`search_market`, `set_pacing_budget`) participate in audit log and `revert_last_change`
- **Setups**: `propose_setup_from_corpus` produces a proposal; operator approves; existing `create_setup` writes the row. Same lifecycle as manual setup creation.

## 11. Open questions deferred to implementation

These came up in the design conversation and are documented for the implementer to revisit:

1. **Card-skill extraction reliability**: Upwork sometimes renders skills as chips, sometimes as inline text. The card parser will try both; if skill capture rate falls below ~70% on real queries, we revisit and may need a deeper parse.
2. **Cluster definition in `find_portfolio_gaps`**: v1 uses a simple grouping by overlapping skill tokens (e.g. `{rag, llamaindex, pinecone}` → one cluster). If clusters are too coarse or too fragmented, swap to a small embedding-based clustering pass. Defer until we see real data.
3. **Goal-less operator**: if `get_goal()` returns no active goal, BA tools fall back to broad analysis without threshold framing. The agent's prompt instructs it to nudge the operator: *"You don't have an active goal; want to set one before I dig in?"*

## 12. Appendix: filter grammar (already shipped)

For reference, the 6 keys that `search_market` accepts (from [upwork/search.py](../../upwork/search.py)):

| Key | Values | Meaning |
|---|---|---|
| `payment_verified` | `1` | Payment-verified clients only |
| `t` | `0` (hourly) / `1` (fixed) | Job type |
| `hourly_rate` | `25-35`, `50-` | $/hr range, open-ended OK |
| `amount` | `0-99` / `100-499` / `500-999` / `1000-4999` / `5000-` | Fixed-price tier |
| `proposals` | `0-4` / `5-9` / `10-14` / `15-19` / `20-49` | Competition bucket |
| `duration_v3` | `week` / `month` / `semester` / `ongoing` | <1mo / 1-3mo / 3-6mo / 6mo+ |

URL: `https://www.upwork.com/nx/search/jobs/?q=<urlencoded>&sort=relevance%2Bdesc&<filter>=<value>...`

15 unit tests in `tests/test_research_query.py` already cover URL construction parity with Upwork's own URLs.
