# Upwork Trading System — Architecture & Design

**Date:** 2026-05-02
**Status:** Design complete, awaiting user approval before writing implementation plan
**Supersedes:** the schema-redesign sketch in the handoff doc `nope-i-wanted-us-zany-elephant.md`

---

## 1. Mission

> **BA** figures out what's out there and what we need to win.
> **Bidder** says "you wanted this — I found it — should I apply?"

The system is a single-user automated trading platform for the Upwork freelance market. The user is the only operator. The end goal is $50k earned through landed contracts by year-end. The system is **not** a product, will **not** be sold, and has no multi-tenancy, no authentication, and no public surface.

The system mirrors a small trading desk: a *market analyst* (BA) who studies the market and proposes setups, an *execution engine* (Bidder) who watches for setups firing and routes orders for approval, and a *risk manager* (the Connects budget enforcer) who keeps capital deployment within bounds.

## 2. Non-goals

- **No web UI, ever.** Discord is the only human interface. A web UI would just be a worse Discord. Reconsider only if a real need surfaces that Discord can't serve.
- **No multi-user support, no auth, no SaaS.** Single-tenant, single-laptop, single Upwork account.
- **No deployment to a remote server.** The laptop is the runtime — it has the human-driven Chrome session that UIA needs. A VPS cannot drive Chrome via UIA.
- **No replacement of the proven UIA substrate.** Apply automation, panel parsing, and clipboard URL capture are reliable — they stay deterministic. AI is added around them, not in them.

## 3. Vocabulary (the trading frame)

The system uses crypto-trading vocabulary because the workflow maps cleanly:

| Term | Meaning |
|---|---|
| **Market** | The Upwork job feed (and search results) |
| **Setup** | A standing strategy: filter rules + prose definition + pitch template + risk params + tier + auto-apply rule. The BA's primary output. |
| **Signal** | A scraped job that matches an active setup. Equivalent to "trigger fired." |
| **Order** | An apply attempt. The capital deployment (Connects + time + Doc generation cost). |
| **Position** | A submitted order awaiting outcome. Open until ghosted, declined, or hired. |
| **P&L / Outcome** | Funnel events on a position: viewed → replied → interviewed → hired (or declined / ghosted). |
| **Trade Journal** | Per-order audit record — every variable that could explain outcome variance. Serves as training data for the BA. |
| **Setup Performance** | Funnel rates + Connects spent + $ landed for a setup over a window. |
| **Backtest** | Running a proposed setup's filter against historical corpus to estimate match volume. |
| **Risk Management** | Connects caps (daily/weekly), per-setup allocation (Phase 3), auto-apply maturity gates. |

Terms intentionally **not** used (they don't carry weight in this market):
- "Stop loss" — once submitted, can't pull back
- "Take profit" — no partial close on a hire
- "Leverage" — n/a
- "Slippage" — only metaphorical

## 4. The agent boundary rule

> LangChain agents reason about *data*. The deterministic substrate acts on *Upwork*.
> Agents never touch Chrome. The substrate never makes judgment calls.

This is the load-bearing rule. It splits the system into two trust regions:

- **Deterministic substrate (high trust):** UIA tree reads, SendInput writes, panel parsing, apply form filling, clipboard capture. Proven reliable over hundreds of cycles. No LLM in the control flow.
- **AI layer (lower trust, sandboxed):** Single LLM calls for content generation; LangChain agents for analytical work. Agents have read-only tools against the corpus + journal + portfolio, plus write access only to *proposal tables* (not live state).

An agent's worst-case behavior is "writes a bad proposal to a queue waiting for human approval." It cannot burn Connects, cannot hit Upwork, cannot change a live setup, cannot fire an order.

## 5. Architecture

### 5.1 Surfaces

| Surface | Purpose |
|---|---|
| **Discord bot** | Real-time alerts (signals → action buttons), slash commands (outcome tracking, setup management, queries), conversational Q&A (routes to BA agent). The only human interface. |
| **Postgres CLI** | When you need to dig in directly: ad-hoc SQL, raw journal exploration. Used rarely once the bot Q&A is in. |
| **The laptop's Chrome** | Pure infrastructure. The bidder uses it. The user does not interact with it during automated cycles. |

### 5.2 The two loops

**Bidder loop (24/7, fast, deterministic):**

```
every N minutes (N tunable, default 3-5):
  refresh feed → scan top jobs → for each unseen job:
    open panel → parse panel → enrich (LLM extraction) → write to corpus
    score job against active setups (rule pre-filter + LLM tie-break for ambiguous)
    if matches one or more setups:
      capture URL → draft proposal Doc → draft cover letter → upload Mermaid
      write Signal row → write draft Order row (status=awaiting_approval)
      ping Discord with action buttons (Apply / Skip / Edit)
      start escalation timer per highest-tier matching setup
  close panel → next job
```

**BA loop (daily, slow, agent-based):**

```
once per day (and on demand via Discord):
  Setup Proposer agent runs:
    SQL the corpus over last N days
    cluster by tech / project_shape / budget band
    for each cluster: backtest hypothetical setup, score by size + portfolio fit + win-likelihood
    write top K to setup_proposals table
    post Discord summary with one message per proposal (✅/❌ react to approve/reject)
  Performance Reviewer agent runs:
    for each active setup: compute funnel (viewed/replied/hired) over last N days
    flag underperformers, propose retirement
    flag overperformers, propose tier upgrade or auto-apply enable
    write to setup_proposals (with action=retire / upgrade)
    post Discord summary
  Pitch Experiment Proposer agent runs (Phase 3):
    for setups with low reply rate but decent view rate
    propose A/B variants of cover letter / Doc opener
    write to pitch_proposals
```

The two loops communicate through Postgres. They do not call each other directly. The BA never blocks the Bidder; the Bidder never blocks the BA.

### 5.3 Module structure

The repo is reorganized into layers. Dependencies point inward (lower → higher layers cannot import upward).

```
substrate/         # UIA, input, Chrome control. Knows nothing about Upwork.
  observe.py       # tree walker (current observe.py, cleaned)
  act.py           # input primitives (current act.py, cleaned)
  pacing.py        # action budget
  launch_chrome.py # Chrome startup
  vision.py        # screenshot → bbox / list-questions

upwork/            # Upwork-specific knowledge. Uses substrate.
  feed.py          # feed-page navigation + card scanning
  panel.py         # panel parsing (anchors, regex, structural rules)
  apply_form.py    # apply-page form filling sequence
  clipboard_url.py # the Copy-to-clipboard click + clipboard read
  search.py        # search-driven crawler URL grammar (current upwork_research)

domain/            # Pure logic + types. No I/O.
  types.py         # dataclasses: Job, Setup, Signal, Order, Position, OutcomeEvent, Enrichment, ...
  scoring.py       # rule-DSL evaluator (job, setup) → match | no-match
  setups.py        # setup lifecycle, maturity rules, tier validation
  performance.py   # funnel computation, EV/Connect, expectancy
  risk.py          # connects-budget enforcement, override logging
  journal.py       # journal record assembly

storage/           # Postgres DAL. Implements protocols domain depends on.
  connection.py    # psycopg3 pool, settings-driven
  migrate.py       # plain SQL migration runner
  migrations/      # 001_init.sql, 002_*.sql, ...
  jobs.py          # JobStore implementation
  setups.py        # SetupStore implementation
  orders.py        # OrderStore implementation
  journal.py       # JournalStore implementation
  enrichments.py   # EnrichmentStore implementation
  proposals.py     # SetupProposalStore, PitchProposalStore
  agent_runs.py    # AgentRunStore (audit log for AI calls)
  portfolio.py     # PortfolioStore

ai/                # LLM clients, prompts, agents.
  client.py        # OpenAI client wrapper, cost tracking, retry
  prompts/         # versioned prompt strings (proposal, cover_letter, enrichment, ...)
  enrichment.py    # structured extraction from a job (single LLM call)
  proposal_gen.py  # proposal Doc + cover letter generation (current proposal.py, cleaned)
  agents/          # LangChain agents
    setup_proposer.py
    performance_reviewer.py
    pitch_experiment.py
    journal_qa.py     # the conversational Q&A agent
  tools.py         # read-only SQL + portfolio + journal tools for agents

external/          # Third-party service integrations (not Upwork). One module per service.
  gdocs.py         # Composio Google Docs + Drive (current gdocs.py, cleaned)
  mermaid.py       # mermaid.ink rendering (current mermaid.py, cleaned)
  discord_webhook.py # legacy webhook client if still needed; otherwise dropped
  openai.py        # thin wrapper used by ai/client.py

bidder/            # The 24/7 loop. Wires substrate + upwork + domain + ai.
  scan_cycle.py    # one feed-scan iteration
  signal_pipeline.py  # job → enrich → score → (if signal) draft → write Order
  draft_pipeline.py   # draft Doc + cover letter + upload Mermaid
  apply_executor.py   # takes an approved Order → runs upwork.apply_form → updates Order

ba/                # Periodic analytical work. Wires domain + ai.
  daily_run.py     # invokes proposer + reviewer
  on_demand.py     # entry point for conversational Q&A from bot

bot/               # Discord. Talks to bidder/ba/domain via public APIs.
  bot.py           # discord.py setup, gateway connection
  alerts.py        # rich messages with action buttons (real-time mode)
  commands.py      # slash command handlers (structured mode)
  conversation.py  # @mention / DM router → ba.on_demand
  escalation.py    # tier-based re-ping logic

scheduler/         # Top-level orchestration.
  main.py          # builds the graph, runs forever
  config.py        # Settings dataclass loaded from env
  failure_pings.py # standardized failure → Discord templates

tests/
  unit/            # pure logic, no I/O. <1 sec total. Run on every commit.
  integration/     # real Postgres + small LLM. Run nightly.
```

Three rules applied throughout:
1. Each module has one purpose.
2. Each layer's public API is the entry point modules at its top level.
3. Every cross-layer dependency goes through a Protocol defined in the consumer's layer.

### 5.4 The 4-layer AI architecture

| Layer | Where | Model | Pattern | Rough cost |
|---|---|---|---|---|
| **Tactical** | Bidder per-job: relevance tie-break | gpt-4o-mini | Single call, structured output | $0.0003/job |
| **Tactical** | Bidder per-job: proposal Doc | gpt-4o | Single call, prompt-engineered | $0.01/signal |
| **Tactical** | Bidder per-job: cover letter | gpt-4o | Single call | $0.005/signal |
| **Tactical** | Bidder per-job: screening Q draft | gpt-4o-mini | Single call per Q | $0.001/Q |
| **Structured extraction** | Per-job enrichment (one-shot, persisted) | gpt-4o-mini | Schema-constrained JSON | $0.001/job |
| **Analytical (agents)** | BA: setup proposer | gpt-4o | LangChain agent, multi-step, tool use | $0.10/run |
| **Analytical (agents)** | BA: performance reviewer | gpt-4o-mini | LangChain agent | $0.05/run |
| **Analytical (agents)** | BA: pitch experiment proposer (Phase 3) | gpt-4o | LangChain agent | $0.05/run |
| **Analytical (agents)** | Conversational Q&A | gpt-4o-mini default, gpt-4o on complex | LangChain agent, threaded reply | $0.01-0.10/Q |
| **Continuous learning (Phase 4+)** | Cover-letter retrieval, journal embeddings | text-embedding-3-small | Background batch | <$1/month |

**Agents share one tool set, defined in `ai/tools.py`:**

- `sql_query(read_only=True)` — arbitrary SELECT
- `read_portfolio()`
- `read_setup(id)`, `list_active_setups()`
- `read_orders(filters)`, `read_order_journal(order_id)`
- `compute_setup_performance(setup_id, window_days)`
- `backtest_setup(filter_dsl, window_days)` — runs filter against corpus, returns match count + sample
- `propose_setup(SetupProposal)` — writes to `setup_proposals` table
- `propose_pitch_variant(PitchProposal)` — writes to `pitch_proposals` table
- `propose_setup_action(setup_id, action='retire'|'upgrade'|'enable_auto_apply', reason)` — writes to `setup_proposals` table

**Explicitly forbidden in any agent's tool set:**
- Anything that fires an order, navigates Chrome, mutates an active setup, sends Connects, or writes to the journal directly. All such actions are gated through human approval (Discord) or deterministic code paths.

### 5.5 Reliability primitives

Every one of these is a hard requirement, not a nice-to-have:

1. **Idempotency on every external action.** Submitting an order writes `(idempotency_key, status='attempting')` *before* the form-submit click. If the click succeeds and the DB write fails, the next retry sees `attempting` and either confirms or aborts based on Upwork-side state — never double-submits.

2. **Failure visibility.** Every failure routes through `scheduler/failure_pings.py` with a typed error and a templated Discord message. No silent failures, no swallowed exceptions. Templates exist for: `LoginExpired`, `CloudflareWall`, `OpenAIQuotaExceeded`, `ComposioDown`, `PostgresUnavailable`, `ConnectsExhausted`, `OrderAlreadySubmitted`, `VisionModelFailed`, `ScreeningQuestionUnanswerable`, `ApplyFormChanged` (structure-shifted), and a generic `UnexpectedError` with full traceback.

3. **Backpressure.** Pacing budget already exists for the scanner. Add: OpenAI request budget (per-day cap), Composio call budget, Discord message rate limiter (escalation spam respects Discord's per-channel rate limit).

4. **State recoverability.** Postgres is the source of truth. In-memory state is reconstructible from DB on restart. The scheduler can be killed at any point and resumed with no double-action and no lost work. In-flight signals/draft orders/staged applies all have explicit DB status that survives reboot.

5. **AI audit trail.** Every LLM call writes to `agent_runs`: prompt, response, model, cost, duration, parent context (which signal / which agent run / which setup). When a setup proposer suggests something bad, you can read exactly what it saw.

6. **Human override everywhere.** Every automatic decision is reversible via Discord:
   - `/cancel <order_id>` — cancel an awaiting-approval or staged order
   - `/setup reject <proposal_id> <reason>` — reject a proposed setup
   - `/setup disable <setup_id>` — turn off an active setup without deleting it
   - `/risk override` — confirm a Connect cap override (logged)
   - `/journal annotate <order_id> <note>` — add human context to an order's journal

7. **Migrations only.** Schema changes only happen through numbered migration files in `storage/migrations/`. No `CREATE TABLE IF NOT EXISTS` on import. Code assumes schema exists.

## 6. Data model

The schema is 13 tables. Listed by purpose:

**Corpus (what we've seen in the market):**

```sql
jobs (
  job_id PK, url UNIQUE, title, description,
  budget_kind, budget_min, budget_max, budget_raw_text,
  duration, experience_level, hours_per_week,
  posted_at, scraped_first_at, source,
  client_country, client_city, client_member_since, client_payment_verified,
  client_rating, client_hires, client_total_spent_usd, client_avg_hourly_paid,
  proposals_count_at_first_scrape,
  raw_panel_json
)

scrape_events (
  id PK, job_id FK, scraped_at, source, proposals_count, notes
)

scrape_runs (
  run_id PK, started_at, finished_at, source, query_id FK NULL,
  jobs_seen, jobs_new, jobs_signaled, notes
)

queries (
  query_id PK, raw_text, query_term, filters_json, active, created_at
)

skills (
  skill_id PK, name UNIQUE, category, alias_of FK NULL  -- alias_of populated by daily LLM dedup
)

job_skills (
  job_id FK, skill_id FK, source ENUM('upwork_chip','description_extracted','enriched'),
  PRIMARY KEY (job_id, skill_id, source)
)

job_enrichments (
  job_id PK FK, prompt_version, enriched_at,
  extracted_tech text[], pain_points text[], red_flags text[], green_flags text[],
  project_shape ENUM, buyer_sophistication ENUM,
  raw_llm_response jsonb
)
```

**Strategy (what we're going to do):**

```sql
setups (
  setup_id PK, name UNIQUE, status ENUM('proposed','active','disabled','retired'),
  tier ENUM('quiet','normal','critical'),
  filter_dsl jsonb, prose_definition text,
  pitch_template_id FK, cover_letter_template_id FK,
  auto_apply_enabled boolean DEFAULT false,
  escalation_config jsonb,  -- { initial: 0, repings: [2,4], deadline: 5, on_deadline: 'auto_skip' }
  created_at, activated_at, retired_at, retired_reason
)

setup_proposals (
  proposal_id PK, proposed_at, agent_run_id FK,
  action ENUM('create','retire','upgrade','enable_auto_apply','update_filter'),
  setup_id FK NULL,  -- NULL for create, set for the others
  draft jsonb, reasoning text, status ENUM('pending','approved','rejected'),
  reviewed_at, reviewed_action, reviewed_note
)

pitch_templates (
  template_id PK, name, kind ENUM('doc','cover_letter','screening_answer'),
  prompt_version, body_template text, variables jsonb,
  parent_template_id FK NULL,  -- for A/B variants
  created_at, retired_at
)

pitch_proposals (
  proposal_id PK, proposed_at, agent_run_id FK,
  parent_template_id FK, draft_template_body, reasoning text,
  status, reviewed_at, reviewed_action
)
```

**Execution (what happened):**

```sql
signals (
  signal_id PK, job_id FK, fired_at,
  matched_setups jsonb,  -- [{setup_id, match_reason: 'rule'|'llm', score}]
  primary_setup_id FK,   -- the highest-tier match, drives pitch template + escalation
  market_state jsonb     -- proposals_at_signal, time_since_post, ...
)

orders (
  order_id PK, signal_id FK, job_id FK, setup_id FK,
  status ENUM('drafting','awaiting_approval','approved','staging','attempting','submitted','cancelled','failed'),
  bid_amount_usd, connects_spent,
  cover_letter_body, doc_url, screening_answers_json,
  drafted_at, approved_at, submitted_at, failed_reason,
  idempotency_key UNIQUE
)

outcome_events (
  id PK, order_id FK, event_type ENUM('viewed','replied','interviewed','hired','declined','ghosted'),
  observed_at, source ENUM('discord_manual','upwork_my_proposals_scrape','self_reported'),
  notes
)

connects_ledger (
  id PK, occurred_at, delta integer, reason text,
  balance_after, order_id FK NULL
)
```

**Portfolio (our ammo):**

```sql
portfolio_items (
  portfolio_id PK, name, summary, client_context, outcome,
  tech text[], relevance_tags text[], year_completed,
  added_at, last_used_at
)
```

**Audit (how the AI is reasoning):**

```sql
agent_runs (
  run_id PK, agent_name, started_at, finished_at, status,
  parent_run_id FK NULL,  -- for sub-runs / tool calls
  total_tokens, total_cost_usd,
  trigger ENUM('scheduled','discord_question','manual'),
  trigger_context jsonb,
  steps jsonb,  -- list of (step_type, prompt, response, tool_calls)
  output_summary text
)
```

**Schema migrations table** (managed by `migrate.py`):

```sql
schema_migrations (version int PK, applied_at)
```

That's 13 user-facing tables + 1 migrations table.

## 7. Discord bot — full surface

### 7.1 Real-time alerts (Bidder fires a signal)

Rich embed posted to channel. Example:

```
📡 Signal: 'RAG eval consultancies' setup matched (tier: critical)
Title: Build evaluation pipeline for our RAG system
Budget: $4,000-7,000 fixed | Posted: 3 min ago | Proposals: 4
Client: 🇺🇸 US, payment verified, 23 hires, $84k spent
Why matched: filter rules [skills∋rag, budget≥3000, payment_verified] + LLM 0.87 fit
Doc: <url>
Cover letter preview: "Hey, spent some time digging into your RAG eval ask. The bit about latency-vs-recall tradeoffs jumped out — that's where most teams get burned. Here's how I'd build it: <url>"
[Apply ✅] [Skip ❌] [Edit Cover Letter ✏️] [View Doc 📄]
```

Tier-based escalation per setup (re-ping at configured intervals, deadline action per setup config).

### 7.2 Slash commands

```
/queue                          — show awaiting-approval orders
/cancel <order_id>              — cancel an order before submission
/applied <order_id> <event>     — record outcome event (viewed|replied|interviewed|hired|declined|ghosted)
/journal <order_id>             — show full trade journal for an order
/setups                         — list active setups + funnel stats
/setup show <id>                — full setup definition + recent performance
/setup propose                  — trigger BA setup proposer agent on demand
/setup approve <proposal_id>    — approve a proposed setup
/setup reject <proposal_id> <reason>
/setup disable <setup_id>
/setup enable <setup_id>
/perf [days=7]                  — overall funnel stats over window
/connects                       — current week/day Connect spend + cap status
/risk override <order_id>       — confirm a cap override (must be reissued each time)
/queries                        — list crawler search queries
/health                         — system status: scheduler last tick, OpenAI quota, Postgres, Composio, Discord rate limit
```

### 7.3 Conversational Q&A

`@bot <question>` or DM the bot. Routes to `ai/agents/journal_qa.py`. Examples:

- "Why am I not getting replies on agent-infra setup?"
- "What's the highest-paying tech stack in my corpus right now?"
- "Which setups should I retire?"
- "Show me last week's trade journal"
- "How much did I spend on Connects this month and what's the EV per Connect?"

Agent runs with the read-only tool set, replies in-thread. Same `agent_runs` audit log as scheduled BA runs.

## 8. Risk management

### 8.1 Connects budget

- Budget: $100/month = ~$25/week = ~166 Connects/week
- Default caps: **3 orders/day, 10 orders/week** (16 Connects each)
- Hard enforcement in `domain/risk.py`. Bidder will not draft an order if cap would be exceeded; will instead post a Discord notification "would have signaled X, blocked by weekly cap (resets Mon)."
- **Override:** Discord `/risk override <order_id>` lets you confirm a single-use override. Override is logged in `connects_ledger.reason` for review.

### 8.2 Auto-apply maturity gates

A setup can have `auto_apply_enabled=true` only if:
- It has ≥10 submitted orders AND
- ≥1 reply event observed across those orders

Enforced in `domain/setups.py` at the moment of enabling. The Performance Reviewer agent can *propose* enabling auto-apply, but the gate is checked before the proposal is even allowed to be written.

### 8.3 Per-setup capital allocation (Phase 3)

Once ≥3 setups have ≥10 orders each, Risk Manager can switch from global caps to per-setup allocation. Until then, equal weighting.

## 9. Engineering principles (non-negotiable)

These are baked into every section of the implementation plan that follows from this spec:

1. **Layered architecture, dependencies point inward.** Cross-layer calls go through Protocols defined by the consumer. No `bidder/` reaching into `storage/internals/`.

2. **Pure functions where possible, side effects at the edges.** Parsing a panel, scoring a job, computing performance, drafting a prompt — all pure, all unit-testable in milliseconds.

3. **Typed everything.** Dataclasses for every domain object. No untyped dicts crossing module boundaries.

4. **Protocols for boundaries.** `JobStore`, `SetupStore`, `OrderStore`, `LLMClient`, `BrowserController`, `DiscordNotifier`. Domain code depends on protocols; concrete implementations live in `storage/`, `ai/`, `substrate/`, `bot/`. Tests substitute fakes.

5. **No module-level state, no module-level I/O.** No globals, no module-level Chrome handles, no `sys.stdout` rewires. Every object constructed explicitly. Scheduler builds the graph at startup.

6. **Migrations are the only way schema changes.** No runtime `CREATE TABLE IF NOT EXISTS`. Migration runner (`storage/migrate.py`) tracks applied versions; code assumes schema exists.

7. **Tests organized by layer.** `tests/unit/` runs in <1 sec total on every commit. `tests/integration/` runs nightly with real Postgres + small LLM calls.

8. **Configuration is explicit.** `Settings` dataclass loaded from env at startup, passed via constructors. No `os.getenv()` scattered through modules.

9. **Errors are typed.** `LoginExpired`, `CloudflareWall`, `ConnectsExhausted`, `OrderAlreadySubmitted`, etc. Caught at orchestration boundaries, surfaced through `failure_pings`. No bare `except`.

10. **Idempotency keys on every external action.** State written before the action, confirmed after.

11. **Rewrite under the new structure, not refactor in place.** Current files (`upwork_driver.py`, `upwork_apply.py`, `tools.py`, `agent.py`, `proposal.py`, etc.) are read for behavioral reference, then deleted as their behavior is ported to the new structure. Working *behavior* is preserved (panel parsing logic, apply form sequence, escalation patterns) but the *file shapes* are not. The current codebase is a working prototype; the new structure is the system.

12. **One thing per file.** No module that does feed parsing + panel parsing + judgment + URL capture + proposal triggering + Discord pinging + DB writes.

## 10. Phase rollout

Each phase ends in a working, deployed system. No phase ships half-built.

### Phase 1 — Foundation

**Estimate: 5-7 focused days. Estimates in this section are rough and not commitments.**

Goal: clean schema, clean module structure, Bidder ported and running, Discord bot with order approval working.

- Postgres in Docker Compose on the laptop
- `storage/migrations/001_init.sql` — full DDL for the 13 tables
- `storage/migrate.py` — migration runner
- New module structure scaffolded: `substrate/`, `upwork/`, `domain/`, `storage/`, `ai/`, `bidder/`, `bot/`, `scheduler/`
- All current `act.py`/`observe.py`/`pacing.py`/`vision.py` cleaned and moved to `substrate/`
- Panel parsing + apply form ported to `upwork/`
- Bidder loop ported to `bidder/`, writes through new DAL, no `seen_urls.txt`, no `upwork_driver.py`
- Single hardcoded "manual" setup so the bidder has something to match against (you write one by hand to start)
- Discord bot with: real-time alerts (Apply/Skip/Edit buttons), `/queue`, `/cancel`, `/applied`, `/connects`, `/health` slash commands
- Connects budget enforcement (hard caps) + override flow
- Failure-ping templates wired
- Idempotency keys on order submission
- Old files deleted: `upwork_driver.py`, `upwork_apply.py`, `tools.py`, `agent.py`, `proposal.py`, `gdocs.py`, `notify.py`, `scheduler.py`, `db.py`, `reset_jobs.py`, `view.py`, `post_linkedin.py`, `inspect_composer.py`, `debug_*.py`, `mermaid.py`, `upwork_research.py`. Their behavior lives in the new structure (note: `upwork_research.py` becomes `upwork/search.py` + `bidder/` invokes it on the daily crawler tick; the code shape changes, the search-URL grammar is preserved).
- One-time migration: `portfolio.json` → `portfolio_items` table. After import, `portfolio.json` is deleted; the DB is the source of truth. A small `bin/portfolio_add.py` CLI is provided for adding new portfolio items going forward.
- Tests: unit tests for every pure-logic module, integration tests for storage layer

**Phase 1 is verified end-to-end before Phase 2 starts.** Verification = scanner runs an hour, signals fire on the manual setup, Discord buttons work, an order is submitted via Discord-approved path, outcome event recorded via slash command.

### Phase 2 — BA agent layer

**Estimate: 4-5 focused days. Rough.**

Goal: BA agents propose setups daily, you approve via Discord. System starts learning.

- `ai/enrichment.py` runs on every scraped job
- `ai/agents/setup_proposer.py` — LangChain agent with read-only tool set
- `ai/agents/performance_reviewer.py`
- `ba/daily_run.py` invoked by scheduler
- Setup-management slash commands: `/setups`, `/setup show`, `/setup propose`, `/setup approve`, `/setup reject`, `/setup disable`, `/setup enable`
- Setup proposals posted to Discord with ✅/❌ reactions
- `agent_runs` audit log + `/journal <order_id>` showing all related agent runs
- Daily skills dedup pass

### Phase 3 — Conversational Q&A + pitch experiments + auto-apply

**Estimate: 3-4 focused days. Rough.**

- `ai/agents/journal_qa.py` — conversational agent
- `bot/conversation.py` — @mention / DM router
- `ai/agents/pitch_experiment.py` — proposes A/B variants
- `pitch_proposals` review flow in Discord
- Auto-apply enable flow (for setups past the maturity gate)
- Per-setup escalation tiers configurable via slash commands

### Phase 4 — Continuous learning (deferred, future)

- Embeddings of past successful cover letters → retrieved as few-shot examples in proposal generation
- Upwork inbox scraper for automated outcome detection (replaces some Discord slash commands)
- Per-setup capital allocation
- Web UI — only if a real need surfaces that Discord cannot serve

## 11. Open items deliberately deferred

- **Per-setup capital allocation:** waits for ≥3 setups with ≥10 orders each
- **Upwork inbox scraper for outcomes:** Discord slash commands cover this until volume justifies the UIA work on a different page structure
- **Web UI:** killed unless a real need surfaces
- **Embeddings retrieval for cover letters:** Phase 4
- **Multi-account / SaaS:** never

---

**End of design.** Awaiting user review before invoking writing-plans skill to create the Phase 1 implementation plan.
