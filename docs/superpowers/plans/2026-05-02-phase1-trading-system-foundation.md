# Phase 1: Trading System Foundation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current SQLite + ad-hoc-modules prototype with the layered Postgres-backed trading system from the spec, with a working Discord bot that approves orders end-to-end against one hardcoded seed setup.

**Architecture:** Postgres in Docker on the laptop. New module layout (`substrate/`, `upwork/`, `domain/`, `storage/`, `ai/`, `external/`, `bidder/`, `bot/`, `scheduler/`). All LLM access through LangChain `create_agent` with pydantic `response_format`. Deterministic UIA substrate keeps Upwork interaction. Discord bot (discord.py, full gateway) is the only human interface. Humanization layer governs timing/voice/cycle-mix to appear human-but-aggressive.

**Tech Stack:** Python 3.12+, psycopg3, Postgres 16 (Docker), LangChain (`create_agent`), pydantic v2, discord.py 2.x, pytest, pytest-asyncio, existing `uiautomation` substrate, Composio (Google Docs), `pyautogui` (only for clipboard via `act.read_clipboard`).

**Spec reference:** `docs/superpowers/specs/2026-05-02-trading-system-architecture-design.md`

**Plan style:** Each task ends with a commit. Tests are written before implementation (TDD). Each task produces something independently verifiable.

---

## File Structure (target end state)

This is what the repo looks like after Phase 1 ships. Tasks below build it up incrementally.

```
substrate/
  __init__.py
  observe.py            # ported from existing observe.py, no changes to logic
  act.py                # ported from existing act.py
  pacing.py             # ported from existing pacing.py
  vision.py             # ported from existing vision.py
  launch_chrome.py      # ported from existing launch_chrome.py

upwork/
  __init__.py
  feed.py               # feed-page navigation + top-of-feed scan
  panel.py              # panel parse (regex anchors, structural rules) — extracted from upwork_driver.py
  apply_form.py         # apply-page form filling — extracted from upwork_apply.py
  clipboard_url.py      # Copy-to-clipboard click + clipboard read — extracted from upwork_driver.py
  search.py             # search URL grammar — ported from upwork_research.py

domain/
  __init__.py
  types.py              # dataclasses: Job, Setup, Signal, Order, OutcomeEvent, Enrichment, FilterDsl, ...
  scoring.py            # FilterDsl evaluation: (Job, Setup) -> MatchResult
  setups.py             # setup lifecycle, maturity gate
  performance.py        # funnel computation
  risk.py               # connects budget enforcement
  humanization.py       # diurnal envelope, cycle mix, voice variants, order spacing

storage/
  __init__.py
  connection.py         # psycopg3 connection pool, settings-driven
  migrate.py            # migration runner
  migrations/
    001_init.sql        # full DDL for all 14 tables
  jobs.py               # JobStore implementation
  setups.py             # SetupStore implementation
  orders.py             # OrderStore + OutcomeEventStore
  enrichments.py        # EnrichmentStore
  portfolio.py          # PortfolioStore
  agent_runs.py         # AgentRunStore
  connects_ledger.py    # ConnectsLedgerStore
  scrape_runs.py        # ScrapeRunStore + ScrapeEventStore

ai/
  __init__.py
  schemas.py            # pydantic models: RelevanceCheck, Enrichment, ProposalDraft, CoverLetter, ScreeningAnswer
  cost_tracker.py       # LangChain BaseCallbackHandler -> writes to agent_runs
  prompts/
    __init__.py
    relevance.py        # prompt strings
    enrichment.py
    proposal.py
    cover_letter.py
    screening.py
  relevance.py          # create_agent for relevance tie-break
  enrichment.py         # create_agent for structured job extraction
  proposal_gen.py       # create_agent for Doc body + cover letter

external/
  __init__.py
  gdocs.py              # Composio Google Docs + Drive — ported from gdocs.py
  mermaid.py            # mermaid.ink rendering — ported from mermaid.py

bidder/
  __init__.py
  scan_cycle.py         # one feed scan iteration
  signal_pipeline.py    # job -> enrich -> score -> (if signal) draft + write Order
  draft_pipeline.py     # draft Doc + cover letter + upload Mermaid
  apply_executor.py     # approved Order -> upwork.apply_form -> update Order

bot/
  __init__.py
  bot.py                # discord.py setup, gateway, dispatch
  alerts.py             # rich signal messages with Apply/Skip buttons
  commands.py           # slash commands: /queue, /cancel, /applied, /connects, /health
  interaction_handler.py  # button click handlers
  escalation.py         # tier-based re-ping logic

scheduler/
  __init__.py
  main.py               # builds graph, runs forever
  config.py             # Settings dataclass loaded from env
  failure_pings.py      # typed errors -> Discord templates

bin/
  portfolio_import.py   # one-time: portfolio.json -> portfolio_items table
  portfolio_add.py      # CLI: add a portfolio item interactively

docker-compose.yml      # Postgres 16
.env.example            # documents required env vars

tests/
  unit/
    domain/             # scoring, performance, risk, humanization
    upwork/             # panel parsing fixtures
    ai/                 # schema validation, prompt assembly
  integration/
    storage/            # real Postgres via fixture
    bidder/             # signal pipeline against real Postgres + mocked LLM
```

**Files deleted at end of Phase 1** (after verification gate passes):
`upwork_driver.py`, `upwork_apply.py`, `upwork_research.py`, `tools.py`, `agent.py`, `proposal.py`, `gdocs.py` (root), `mermaid.py` (root), `notify.py`, `scheduler.py` (root), `db.py`, `reset_jobs.py`, `view.py`, `post_linkedin.py`, `inspect_composer.py`, `debug_feed.py`, `debug_slices.py`, `upwork.db`, `seen_urls.txt` (already gone), `jobs_store.py` (already gone), `portfolio.json` (after import), `relevant_jobs.md`, `results.md`, `output.md`, `apply_dump*.txt`, `composer_dump.txt`, `last_dump.json`, `scan.log`.

`act.py`/`observe.py`/`pacing.py`/`vision.py`/`launch_chrome.py` at the root get **moved** into `substrate/` (not deleted then rewritten — preserve the proven logic).

---

## Task 1: Docker Postgres + .env scaffolding

**Files:**
- Create: `docker-compose.yml`
- Create: `.env.example`
- Modify: `.gitignore` (add `postgres-data/`)
- Create: `bin/up.sh`, `bin/down.sh`

- [ ] **Step 1: Write docker-compose.yml**

Create `docker-compose.yml`:

```yaml
services:
  postgres:
    image: postgres:16-alpine
    container_name: upwork_trading_pg
    environment:
      POSTGRES_USER: upwork
      POSTGRES_PASSWORD: upwork
      POSTGRES_DB: upwork
    ports:
      - "5432:5432"
    volumes:
      - ./postgres-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U upwork -d upwork"]
      interval: 5s
      timeout: 3s
      retries: 10
    restart: unless-stopped
```

- [ ] **Step 2: Write .env.example**

Create `.env.example`:

```
# Existing (keep)
OPENAI_API_KEY=sk-...
COMPOSIO_API_KEY=...
COMPOSIO_USER_ID=...
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...

# New for Phase 1
DATABASE_URL=postgresql://upwork:upwork@localhost:5432/upwork
DISCORD_BOT_TOKEN=
DISCORD_CHANNEL_ID=
DISCORD_OWNER_USER_ID=
LOCAL_TIMEZONE=America/Toronto

# Risk caps (defaults applied if not set)
CONNECTS_DAILY_CAP=3
CONNECTS_WEEKLY_CAP=10
```

- [ ] **Step 3: Update .gitignore**

Add to `.gitignore`:

```
postgres-data/
```

- [ ] **Step 4: Convenience scripts**

Create `bin/up.sh`:

```bash
#!/usr/bin/env bash
docker compose up -d
docker compose exec -T postgres pg_isready -U upwork -d upwork
echo "Postgres ready at localhost:5432"
```

Create `bin/down.sh`:

```bash
#!/usr/bin/env bash
docker compose down
```

Make them executable: `chmod +x bin/up.sh bin/down.sh` (skip on Windows; just `bash bin/up.sh`).

- [ ] **Step 5: Verify**

Run: `docker compose up -d`
Then: `docker compose exec postgres psql -U upwork -d upwork -c "SELECT 1;"`
Expected: `?column?\n----------\n        1\n(1 row)`

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml .env.example .gitignore bin/up.sh bin/down.sh
git commit -m "Add Postgres docker compose + env scaffolding"
```

---

## Task 2: Add new dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add dependencies**

Edit `pyproject.toml`. The `[project]` section's `dependencies` list should become:

```toml
dependencies = [
    "composio>=0.12.0",
    "langchain-openai>=1.2.1",
    "langchain[openai]>=1.2.17",
    "openai>=2.33.0",
    "pillow>=12.2.0",
    "pyautogui>=0.9.54",
    "python-dotenv>=1.2.2",
    "uiautomation>=2.0.29",
    "psycopg[binary,pool]>=3.2.0",
    "discord.py>=2.4.0",
    "pydantic>=2.9.0",
    "pytz>=2024.2",
]
```

And `[dependency-groups]` `dev`:

```toml
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.24.0",
]
```

- [ ] **Step 2: Install**

Run: `uv sync`
Expected: completes without errors, lock file updated.

- [ ] **Step 3: Verify imports**

Run: `uv run python -c "import psycopg; import discord; import pydantic; print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "Add psycopg, discord.py, pydantic, pytest-asyncio"
```

---

## Task 3: Settings + storage/connection.py

**Files:**
- Create: `scheduler/__init__.py`
- Create: `scheduler/config.py`
- Create: `storage/__init__.py`
- Create: `storage/connection.py`
- Create: `tests/__init__.py`
- Create: `tests/unit/__init__.py`
- Create: `tests/unit/test_config.py`
- Create: `tests/integration/__init__.py`
- Create: `tests/integration/conftest.py`

- [ ] **Step 1: Write the failing test for Settings**

Create `tests/unit/test_config.py`:

```python
import os
import pytest
from scheduler.config import Settings, MissingEnvError


def test_settings_loads_from_environ(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h:5432/d")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("COMPOSIO_API_KEY", "comp-test")
    monkeypatch.setenv("COMPOSIO_USER_ID", "user-test")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "12345")
    monkeypatch.setenv("DISCORD_OWNER_USER_ID", "67890")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x/y")
    monkeypatch.setenv("LOCAL_TIMEZONE", "America/Toronto")
    s = Settings.from_env()
    assert s.database_url == "postgresql://u:p@h:5432/d"
    assert s.discord_channel_id == 12345
    assert s.connects_daily_cap == 3
    assert s.connects_weekly_cap == 10


def test_settings_raises_on_missing_required(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk")
    monkeypatch.setenv("COMPOSIO_API_KEY", "c")
    monkeypatch.setenv("COMPOSIO_USER_ID", "u")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "t")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "1")
    monkeypatch.setenv("DISCORD_OWNER_USER_ID", "2")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://x")
    monkeypatch.setenv("LOCAL_TIMEZONE", "America/Toronto")
    with pytest.raises(MissingEnvError, match="DATABASE_URL"):
        Settings.from_env()
```

- [ ] **Step 2: Run, verify FAIL**

Run: `uv run pytest tests/unit/test_config.py -v`
Expected: FAIL with ImportError (module doesn't exist yet).

- [ ] **Step 3: Implement Settings**

Create `scheduler/__init__.py` (empty file).

Create `scheduler/config.py`:

```python
"""Settings loaded from environment. Loaded once at startup; passed via constructors."""
from __future__ import annotations

import os
from dataclasses import dataclass


class MissingEnvError(RuntimeError):
    pass


def _required(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise MissingEnvError(f"Required env var {name} is not set")
    return val


def _int_with_default(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw else default


@dataclass(frozen=True)
class Settings:
    database_url: str
    openai_api_key: str
    composio_api_key: str
    composio_user_id: str
    discord_bot_token: str
    discord_channel_id: int
    discord_owner_user_id: int
    discord_webhook_url: str
    local_timezone: str
    connects_daily_cap: int
    connects_weekly_cap: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=_required("DATABASE_URL"),
            openai_api_key=_required("OPENAI_API_KEY"),
            composio_api_key=_required("COMPOSIO_API_KEY"),
            composio_user_id=_required("COMPOSIO_USER_ID"),
            discord_bot_token=_required("DISCORD_BOT_TOKEN"),
            discord_channel_id=int(_required("DISCORD_CHANNEL_ID")),
            discord_owner_user_id=int(_required("DISCORD_OWNER_USER_ID")),
            discord_webhook_url=_required("DISCORD_WEBHOOK_URL"),
            local_timezone=_required("LOCAL_TIMEZONE"),
            connects_daily_cap=_int_with_default("CONNECTS_DAILY_CAP", 3),
            connects_weekly_cap=_int_with_default("CONNECTS_WEEKLY_CAP", 10),
        )
```

- [ ] **Step 4: Run, verify PASS**

Run: `uv run pytest tests/unit/test_config.py -v`
Expected: 2 passed.

- [ ] **Step 5: Implement storage/connection.py**

Create `storage/__init__.py` (empty).

Create `storage/connection.py`:

```python
"""Postgres connection pool. Single pool for the whole process."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg_pool import ConnectionPool


class Database:
    """Owns the connection pool. Constructed at scheduler startup, passed to stores."""

    def __init__(self, dsn: str, min_size: int = 1, max_size: int = 10):
        self._pool = ConnectionPool(dsn, min_size=min_size, max_size=max_size, open=True)

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection]:
        with self._pool.connection() as conn:
            yield conn

    @contextmanager
    def transaction(self) -> Iterator[psycopg.Connection]:
        """Wraps a unit-of-work in a transaction."""
        with self._pool.connection() as conn:
            with conn.transaction():
                yield conn

    def close(self) -> None:
        self._pool.close()
```

- [ ] **Step 6: Write integration conftest**

Create `tests/__init__.py` (empty).
Create `tests/unit/__init__.py` (empty).
Create `tests/integration/__init__.py` (empty).

Create `tests/integration/conftest.py`:

```python
"""Integration test fixtures. Require Postgres at $TEST_DATABASE_URL or fall back to default Docker URL."""
from __future__ import annotations

import os
import pytest
import psycopg
from storage.connection import Database


def _test_dsn() -> str:
    return os.getenv("TEST_DATABASE_URL", "postgresql://upwork:upwork@localhost:5432/upwork_test")


@pytest.fixture(scope="session")
def ensure_test_db():
    """Create the test database if it doesn't exist. Run once per session."""
    admin_dsn = "postgresql://upwork:upwork@localhost:5432/postgres"
    test_db = "upwork_test"
    try:
        with psycopg.connect(admin_dsn, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (test_db,))
                if cur.fetchone() is None:
                    cur.execute(f'CREATE DATABASE {test_db}')
    except psycopg.OperationalError as e:
        pytest.skip(f"Postgres not available at localhost:5432 — start with `docker compose up -d`: {e}")
    yield


@pytest.fixture
def db(ensure_test_db):
    """Per-test database. Each test runs in a savepoint that's rolled back."""
    database = Database(_test_dsn(), min_size=1, max_size=2)
    yield database
    database.close()
```

- [ ] **Step 7: Verify integration test infrastructure**

Run: `uv run pytest tests/integration/ -v --collect-only`
Expected: collects without error (no tests yet, but no crash).

- [ ] **Step 8: Commit**

```bash
git add scheduler/ storage/ tests/
git commit -m "Add Settings + Database connection pool + test fixtures"
```

---

## Task 4: Migration runner + 001_init.sql

**Files:**
- Create: `storage/migrate.py`
- Create: `storage/migrations/001_init.sql`
- Create: `tests/integration/storage/__init__.py`
- Create: `tests/integration/storage/test_migrate.py`
- Create: `bin/migrate.py`

- [ ] **Step 1: Write the failing test for migration runner**

Create `tests/integration/storage/__init__.py` (empty).

Create `tests/integration/storage/test_migrate.py`:

```python
import pytest
from pathlib import Path
from storage.migrate import apply_migrations, applied_versions

MIGRATIONS_DIR = Path(__file__).parents[3] / "storage" / "migrations"


def test_first_apply_creates_schema_and_records_versions(db):
    # Wipe before, in case prior run left state
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
            conn.commit()

    apply_migrations(db, MIGRATIONS_DIR)

    versions = applied_versions(db)
    assert 1 in versions, "migration 001 should be recorded"

    # Spot-check a couple of tables exist
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.jobs');")
            assert cur.fetchone()[0] is not None
            cur.execute("SELECT to_regclass('public.setups');")
            assert cur.fetchone()[0] is not None
            cur.execute("SELECT to_regclass('public.orders');")
            assert cur.fetchone()[0] is not None


def test_idempotent_reapply_does_nothing(db):
    apply_migrations(db, MIGRATIONS_DIR)
    apply_migrations(db, MIGRATIONS_DIR)  # second call should be a no-op
    versions = applied_versions(db)
    assert versions.count(1) == 1
```

- [ ] **Step 2: Run, verify FAIL**

Run: `uv run pytest tests/integration/storage/test_migrate.py -v`
Expected: FAIL (module + migration file don't exist).

- [ ] **Step 3: Write the migration file**

Create `storage/migrations/001_init.sql`. Full schema follows; matches Section 6 of the spec exactly:

```sql
-- 001_init.sql — initial schema
-- Created: 2026-05-02

CREATE TABLE IF NOT EXISTS schema_migrations (
  version integer PRIMARY KEY,
  applied_at timestamptz NOT NULL DEFAULT now()
);

-- ============ CORPUS ============

CREATE TABLE jobs (
  job_id text PRIMARY KEY,                 -- canonical Upwork job id (from URL)
  url text NOT NULL UNIQUE,
  title text NOT NULL,
  description text,
  budget_kind text,                        -- 'fixed' | 'hourly' | NULL if unknown
  budget_min_usd numeric,
  budget_max_usd numeric,
  budget_raw_text text,
  duration text,
  experience_level text,
  hours_per_week text,
  posted_at timestamptz,
  scraped_first_at timestamptz NOT NULL DEFAULT now(),
  source text NOT NULL,                    -- 'feed' | 'search:<query_term>'
  client_country text,
  client_city text,
  client_member_since text,
  client_payment_verified boolean,
  client_rating numeric,
  client_hires integer,
  client_total_spent_usd numeric,
  client_avg_hourly_paid numeric,
  proposals_count_at_first_scrape integer,
  raw_panel_json jsonb
);

CREATE INDEX idx_jobs_scraped_first_at ON jobs (scraped_first_at DESC);
CREATE INDEX idx_jobs_source ON jobs (source);

CREATE TABLE scrape_events (
  id bigserial PRIMARY KEY,
  job_id text NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
  scraped_at timestamptz NOT NULL DEFAULT now(),
  source text NOT NULL,
  proposals_count integer,
  notes text
);

CREATE INDEX idx_scrape_events_job_id ON scrape_events (job_id);

CREATE TABLE scrape_runs (
  run_id bigserial PRIMARY KEY,
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  source text NOT NULL,
  query_id integer,                        -- FK added below
  jobs_seen integer NOT NULL DEFAULT 0,
  jobs_new integer NOT NULL DEFAULT 0,
  jobs_signaled integer NOT NULL DEFAULT 0,
  notes text
);

CREATE TABLE queries (
  query_id serial PRIMARY KEY,
  raw_text text NOT NULL,
  query_term text NOT NULL,
  filters_json jsonb,
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE scrape_runs
  ADD CONSTRAINT fk_scrape_runs_query FOREIGN KEY (query_id) REFERENCES queries(query_id);

CREATE TABLE skills (
  skill_id serial PRIMARY KEY,
  name text NOT NULL UNIQUE,
  category text,
  alias_of integer REFERENCES skills(skill_id),
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE job_skills (
  job_id text NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
  skill_id integer NOT NULL REFERENCES skills(skill_id),
  source text NOT NULL,                    -- 'upwork_chip' | 'description_extracted' | 'enriched'
  PRIMARY KEY (job_id, skill_id, source)
);

CREATE INDEX idx_job_skills_skill ON job_skills (skill_id);

CREATE TABLE job_enrichments (
  job_id text PRIMARY KEY REFERENCES jobs(job_id) ON DELETE CASCADE,
  prompt_version text NOT NULL,
  enriched_at timestamptz NOT NULL DEFAULT now(),
  extracted_tech text[] NOT NULL DEFAULT '{}',
  pain_points text[] NOT NULL DEFAULT '{}',
  red_flags text[] NOT NULL DEFAULT '{}',
  green_flags text[] NOT NULL DEFAULT '{}',
  project_shape text,
  buyer_sophistication text,
  raw_llm_response jsonb
);

-- ============ STRATEGY ============

CREATE TABLE pitch_templates (
  template_id serial PRIMARY KEY,
  name text NOT NULL,
  kind text NOT NULL,                      -- 'doc' | 'cover_letter' | 'screening_answer'
  prompt_version text NOT NULL,
  body_template text NOT NULL,
  variables jsonb NOT NULL DEFAULT '{}'::jsonb,
  parent_template_id integer REFERENCES pitch_templates(template_id),
  created_at timestamptz NOT NULL DEFAULT now(),
  retired_at timestamptz
);

CREATE TABLE setups (
  setup_id serial PRIMARY KEY,
  name text NOT NULL UNIQUE,
  status text NOT NULL CHECK (status IN ('proposed','active','disabled','retired')),
  tier text NOT NULL CHECK (tier IN ('quiet','normal','critical')),
  filter_dsl jsonb NOT NULL,
  prose_definition text,
  pitch_template_id integer REFERENCES pitch_templates(template_id),
  cover_letter_template_id integer REFERENCES pitch_templates(template_id),
  auto_apply_enabled boolean NOT NULL DEFAULT false,
  escalation_config jsonb NOT NULL DEFAULT '{"initial":0,"repings":[],"deadline":null,"on_deadline":"queue"}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  activated_at timestamptz,
  retired_at timestamptz,
  retired_reason text
);

CREATE TABLE setup_proposals (
  proposal_id serial PRIMARY KEY,
  proposed_at timestamptz NOT NULL DEFAULT now(),
  agent_run_id bigint,                     -- FK added later (after agent_runs exists)
  action text NOT NULL CHECK (action IN ('create','retire','upgrade','enable_auto_apply','update_filter')),
  setup_id integer REFERENCES setups(setup_id),
  draft jsonb NOT NULL,
  reasoning text,
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
  reviewed_at timestamptz,
  reviewed_action text,
  reviewed_note text
);

CREATE TABLE pitch_proposals (
  proposal_id serial PRIMARY KEY,
  proposed_at timestamptz NOT NULL DEFAULT now(),
  agent_run_id bigint,
  parent_template_id integer NOT NULL REFERENCES pitch_templates(template_id),
  draft_template_body text NOT NULL,
  reasoning text,
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
  reviewed_at timestamptz,
  reviewed_action text
);

-- ============ EXECUTION ============

CREATE TABLE signals (
  signal_id bigserial PRIMARY KEY,
  job_id text NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
  fired_at timestamptz NOT NULL DEFAULT now(),
  matched_setups jsonb NOT NULL,           -- [{setup_id, match_reason: 'rule'|'llm', score}]
  primary_setup_id integer NOT NULL REFERENCES setups(setup_id),
  market_state jsonb NOT NULL              -- proposals_at_signal, time_since_post_minutes, ...
);

CREATE INDEX idx_signals_job ON signals (job_id);
CREATE INDEX idx_signals_setup ON signals (primary_setup_id);

CREATE TABLE orders (
  order_id bigserial PRIMARY KEY,
  signal_id bigint NOT NULL REFERENCES signals(signal_id),
  job_id text NOT NULL REFERENCES jobs(job_id),
  setup_id integer NOT NULL REFERENCES setups(setup_id),
  status text NOT NULL CHECK (status IN ('drafting','awaiting_approval','approved','staging','attempting','submitted','cancelled','failed')),
  bid_amount_usd numeric,
  connects_spent integer,
  cover_letter_body text,
  doc_url text,
  screening_answers_json jsonb,
  drafted_at timestamptz,
  approved_at timestamptz,
  submitted_at timestamptz,
  failed_reason text,
  idempotency_key text NOT NULL UNIQUE
);

CREATE INDEX idx_orders_status ON orders (status);
CREATE INDEX idx_orders_setup ON orders (setup_id);
CREATE INDEX idx_orders_submitted_at ON orders (submitted_at DESC);

CREATE TABLE outcome_events (
  id bigserial PRIMARY KEY,
  order_id bigint NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
  event_type text NOT NULL CHECK (event_type IN ('submitted','viewed','replied','interviewed','hired','declined','ghosted')),
  observed_at timestamptz NOT NULL DEFAULT now(),
  source text NOT NULL CHECK (source IN ('discord_manual','upwork_my_proposals_scrape','self_reported')),
  notes text
);

CREATE INDEX idx_outcome_events_order ON outcome_events (order_id);

CREATE TABLE connects_ledger (
  id bigserial PRIMARY KEY,
  occurred_at timestamptz NOT NULL DEFAULT now(),
  delta integer NOT NULL,
  reason text NOT NULL,
  balance_after integer,
  order_id bigint REFERENCES orders(order_id)
);

CREATE INDEX idx_connects_ledger_occurred_at ON connects_ledger (occurred_at DESC);

-- ============ PORTFOLIO ============

CREATE TABLE portfolio_items (
  portfolio_id serial PRIMARY KEY,
  name text NOT NULL,
  summary text,
  client_context text,
  outcome text,
  tech text[] NOT NULL DEFAULT '{}',
  relevance_tags text[] NOT NULL DEFAULT '{}',
  year_completed integer,
  added_at timestamptz NOT NULL DEFAULT now(),
  last_used_at timestamptz
);

-- ============ AUDIT ============

CREATE TABLE agent_runs (
  run_id bigserial PRIMARY KEY,
  agent_name text NOT NULL,
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  status text NOT NULL DEFAULT 'running' CHECK (status IN ('running','succeeded','failed')),
  parent_run_id bigint REFERENCES agent_runs(run_id),
  total_tokens integer,
  total_cost_usd numeric,
  trigger text NOT NULL CHECK (trigger IN ('scheduled','discord_question','manual','per_job')),
  trigger_context jsonb,
  steps jsonb NOT NULL DEFAULT '[]'::jsonb,
  output_summary text
);

CREATE INDEX idx_agent_runs_started ON agent_runs (started_at DESC);
CREATE INDEX idx_agent_runs_agent ON agent_runs (agent_name);

ALTER TABLE setup_proposals
  ADD CONSTRAINT fk_setup_proposals_run FOREIGN KEY (agent_run_id) REFERENCES agent_runs(run_id);

ALTER TABLE pitch_proposals
  ADD CONSTRAINT fk_pitch_proposals_run FOREIGN KEY (agent_run_id) REFERENCES agent_runs(run_id);
```

- [ ] **Step 4: Implement migrate.py**

Create `storage/migrate.py`:

```python
"""Migration runner. Plain-SQL files in storage/migrations/, numbered 001_*, 002_*, ..."""
from __future__ import annotations

import re
from pathlib import Path
from typing import List

from storage.connection import Database


_MIG_RE = re.compile(r"^(\d{3})_.*\.sql$")


def _discover(migrations_dir: Path) -> List[tuple[int, Path]]:
    out = []
    for p in sorted(migrations_dir.iterdir()):
        m = _MIG_RE.match(p.name)
        if m:
            out.append((int(m.group(1)), p))
    return out


def applied_versions(db: Database) -> List[int]:
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'schema_migrations'
            """)
            if cur.fetchone() is None:
                return []
            cur.execute("SELECT version FROM schema_migrations ORDER BY version")
            return [r[0] for r in cur.fetchall()]


def apply_migrations(db: Database, migrations_dir: Path) -> List[int]:
    """Apply any not-yet-applied migrations in order. Returns versions applied this call."""
    already = set(applied_versions(db))
    applied_now: List[int] = []
    for version, path in _discover(migrations_dir):
        if version in already:
            continue
        sql = path.read_text(encoding="utf-8")
        with db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s)",
                    (version,),
                )
        applied_now.append(version)
    return applied_now
```

- [ ] **Step 5: Run, verify PASS**

Run: `uv run pytest tests/integration/storage/test_migrate.py -v`
Expected: 2 passed.

- [ ] **Step 6: CLI runner**

Create `bin/migrate.py`:

```python
"""Apply migrations against the configured DATABASE_URL. Run: uv run bin/migrate.py"""
from __future__ import annotations

import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from scheduler.config import Settings
from storage.connection import Database
from storage.migrate import apply_migrations, applied_versions

MIGRATIONS_DIR = Path(__file__).parent.parent / "storage" / "migrations"


def main() -> int:
    settings = Settings.from_env()
    db = Database(settings.database_url)
    try:
        already = applied_versions(db)
        print(f"Already applied: {already}")
        applied = apply_migrations(db, MIGRATIONS_DIR)
        if applied:
            print(f"Applied this run: {applied}")
        else:
            print("Nothing to apply.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 7: Verify against the real DB**

Make sure `.env` is set with at least the required vars (use placeholders for the Discord ones for now: `DISCORD_BOT_TOKEN=placeholder`, etc.)

Run: `uv run bin/migrate.py`
Expected: `Already applied: []` then `Applied this run: [1]`

Run again: `uv run bin/migrate.py`
Expected: `Already applied: [1]` then `Nothing to apply.`

- [ ] **Step 8: Commit**

```bash
git add storage/migrate.py storage/migrations/ bin/migrate.py tests/integration/storage/
git commit -m "Add migration runner + 001_init.sql with full schema"
```

---

## Task 5: Move substrate/ + upwork/ modules (no behavior change)

This task is mechanical: move existing files to new locations and fix imports. No logic changes. Existing manual smoke tests still pass.

**Files:**
- Move: `act.py` → `substrate/act.py`
- Move: `observe.py` → `substrate/observe.py`
- Move: `pacing.py` → `substrate/pacing.py`
- Move: `vision.py` → `substrate/vision.py`
- Move: `launch_chrome.py` → `substrate/launch_chrome.py`
- Create: `substrate/__init__.py`
- Create: `upwork/__init__.py`
- Create: `upwork/panel.py` (extracted from `upwork_driver.py`)
- Create: `upwork/feed.py` (extracted from `upwork_driver.py`)
- Create: `upwork/clipboard_url.py` (extracted from `upwork_driver.py`)
- Create: `upwork/apply_form.py` (extracted from `upwork_apply.py`)
- Create: `upwork/search.py` (renamed from `upwork_research.py` core logic)
- Create: `external/__init__.py`
- Move: `gdocs.py` → `external/gdocs.py`
- Move: `mermaid.py` → `external/mermaid.py`

- [ ] **Step 1: Create substrate/ package and move files**

Create `substrate/__init__.py` (empty).

Move (with `git mv` for proper rename tracking):

```bash
git mv act.py substrate/act.py
git mv observe.py substrate/observe.py
git mv pacing.py substrate/pacing.py
git mv vision.py substrate/vision.py
git mv launch_chrome.py substrate/launch_chrome.py
```

- [ ] **Step 2: Fix internal imports inside substrate**

Search every file in `substrate/` for `import act`, `import observe`, `import pacing`, `import vision`. Replace with relative or absolute references:

- `from . import observe` (within substrate/) OR
- `from substrate import observe` (anywhere outside)

Specifically check `substrate/act.py` — it imports `pacing`. Change to `from substrate import pacing` (or relative `from . import pacing`).

`substrate/launch_chrome.py` — standalone, may not import others. Verify.

- [ ] **Step 3: Smoke-test substrate import**

Run: `uv run python -c "from substrate import observe, act, pacing, vision, launch_chrome; print('substrate ok')"`
Expected: `substrate ok`

- [ ] **Step 4: Create external/ package and move services**

Create `external/__init__.py` (empty).

```bash
git mv gdocs.py external/gdocs.py
git mv mermaid.py external/mermaid.py
```

Update internal imports in `external/gdocs.py` (it imports `mermaid`) → `from external import mermaid`.

- [ ] **Step 5: Create upwork/ package skeleton**

Create `upwork/__init__.py` (empty).

For each of `panel.py`, `feed.py`, `clipboard_url.py`, `apply_form.py`, `search.py` — create the file with this header:

```python
"""[module purpose] — extracted from the legacy upwork_driver.py / upwork_apply.py / upwork_research.py."""
```

These will be filled in Task 6. For now they're empty placeholders so imports resolve.

- [ ] **Step 6: Verify nothing else breaks**

Run: `uv run python -c "from substrate import act, observe; from external import gdocs, mermaid; print('ok')"`
Expected: `ok`. Composio imports may emit warnings about pinned versions; that's fine.

- [ ] **Step 7: Commit**

```bash
git add substrate/ external/ upwork/
git commit -m "Move substrate primitives + external services to dedicated packages"
```

**Note:** The legacy root files (`upwork_driver.py`, `upwork_apply.py`, `proposal.py`, `notify.py`, `db.py`, `tools.py`, `agent.py`, `scheduler.py`, etc.) still exist and still import the old paths. They're broken now — that's expected. They get deleted in Task 12. Until then, do not run them.

---

## Task 6: Extract upwork/ panel + feed + apply_form + clipboard_url logic

This task moves the actual logic out of the legacy `upwork_driver.py` and `upwork_apply.py` into focused modules. Each module is unit-testable.

**Files:**
- Modify: `upwork/panel.py`
- Modify: `upwork/feed.py`
- Modify: `upwork/clipboard_url.py`
- Modify: `upwork/apply_form.py`
- Modify: `upwork/search.py`
- Create: `tests/unit/upwork/__init__.py`
- Create: `tests/unit/upwork/test_panel.py`

The detailed extraction is mostly mechanical line-by-line copying with import updates. The acceptance test is:

- [ ] **Step 1: Write a unit test for panel parsing using a captured fixture**

The legacy `upwork_driver.py` has a function that parses a panel-observed-elements list into a structured `PanelData`. Find that function (search for "def parse_panel" or similar). Capture one real panel observation as a JSON fixture by running the legacy code once with `--dump-panel` (or instrument briefly).

For the test, use a hand-built minimal element list rather than a real fixture if no fixture is captured:

Create `tests/unit/upwork/__init__.py` (empty).
Create `tests/unit/upwork/test_panel.py`:

```python
"""Unit test: panel parser handles the structural anchors deterministically."""
from upwork.panel import parse_panel, PanelData


def _mk_elements():
    """Minimal mocked element list with the structural shapes the parser keys on."""
    # The exact shape depends on the Element dataclass from substrate.observe.
    # Use SimpleNamespace as a stand-in if the real class is heavy.
    from types import SimpleNamespace

    def el(name, role="text", bounds=(0, 0, 100, 30)):
        return SimpleNamespace(name=name, role=role, bounds=bounds, value=None)

    return [
        el("Build evaluation pipeline for our RAG system", role="text", bounds=(1300, 100, 1900, 130)),
        el("Posted 3 minutes ago", role="text"),
        el("Fixed-price"),
        el("$4,000.00"),
        el("Skills and Expertise"),
        el("RAG"),
        el("Pinecone"),
        el("Python"),
        el("About the client"),
        el("Payment verified"),
        el("United States"),
        el("Some long description text " * 20, role="text", bounds=(1300, 500, 1900, 800)),
        el("Copy to clipboard", role="button", bounds=(1820, 220, 1896, 246)),
    ]


def test_parses_title_budget_skills_description():
    p = parse_panel(_mk_elements())
    assert isinstance(p, PanelData)
    assert "evaluation pipeline" in p.title.lower()
    assert p.budget_kind == "fixed"
    assert p.budget_min_usd == 4000
    assert "RAG" in p.skills or "rag" in [s.lower() for s in p.skills]
    assert p.client_payment_verified is True
    assert "long description" in p.description
```

- [ ] **Step 2: Run, verify FAIL**

Run: `uv run pytest tests/unit/upwork/test_panel.py -v`
Expected: FAIL (parse_panel not defined).

- [ ] **Step 3: Implement panel.py**

Open the legacy `upwork_driver.py`. Find the panel-parsing logic (the section that converts observed panel elements into title/budget/skills/etc). Copy that logic into `upwork/panel.py`. Adapt to expose:

```python
"""Panel parser — converts observed elements from the right-side detail panel into PanelData."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional


@dataclass
class PanelData:
    title: str
    posted_text: Optional[str]
    budget_kind: Optional[str]            # 'fixed' | 'hourly'
    budget_min_usd: Optional[float]
    budget_max_usd: Optional[float]
    budget_raw_text: Optional[str]
    duration: Optional[str]
    experience_level: Optional[str]
    hours_per_week: Optional[str]
    skills: List[str] = field(default_factory=list)
    description: str = ""
    client_country: Optional[str] = None
    client_city: Optional[str] = None
    client_member_since: Optional[str] = None
    client_payment_verified: Optional[bool] = None
    client_rating: Optional[float] = None
    client_hires: Optional[int] = None
    client_total_spent_usd: Optional[float] = None
    client_avg_hourly_paid: Optional[float] = None
    proposals_count: Optional[int] = None


_POSTED_RE = re.compile(r"^\s*\d+\s*(minute|hour|day|week|month)s?\s*ago\s*$", re.I)
_MONEY_RE = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)")


def parse_panel(elements: Iterable) -> PanelData:
    """Parse a panel's observed elements. Returns a PanelData with as many fields filled as possible."""
    elems = list(elements)

    # Title: longest 'text' element high up in the panel (top of right column).
    text_elems = [e for e in elems if getattr(e, "role", "") == "text" and e.name and e.name.strip()]
    title_candidates = [e for e in text_elems if e.bounds[1] < 200]
    if title_candidates:
        title = max(title_candidates, key=lambda e: len(e.name)).name.strip()
    else:
        title = (text_elems[0].name.strip() if text_elems else "")

    # Posted-time
    posted_text = next((e.name.strip() for e in text_elems if _POSTED_RE.match(e.name or "")), None)

    # Budget
    budget_kind = None
    budget_raw = None
    budget_min = budget_max = None
    for e in text_elems:
        n = e.name.strip()
        if n.lower().startswith("fixed-price"):
            budget_kind = "fixed"
            budget_raw = n
        elif n.lower().startswith("hourly"):
            budget_kind = "hourly"
            budget_raw = n
    # Find money values near the budget label
    money_values = []
    for e in text_elems:
        for m in _MONEY_RE.finditer(e.name or ""):
            money_values.append(float(m.group(1).replace(",", "")))
    if money_values:
        budget_min = min(money_values)
        budget_max = max(money_values) if len(set(money_values)) > 1 else None

    # Skills: text elements after the "Skills and Expertise" anchor, before the next section header.
    skills: List[str] = []
    in_skills = False
    SECTION_HEADERS = {"about the client", "activity on this job", "skills and expertise", "job link"}
    for e in text_elems:
        n = (e.name or "").strip().lower()
        if n == "skills and expertise":
            in_skills = True
            continue
        if in_skills:
            if n in SECTION_HEADERS or len(e.name) > 80:
                in_skills = False
                continue
            if 1 <= len(e.name) <= 60:
                skills.append(e.name.strip())

    # Description: longest text body (>= 200 chars) below the title.
    body_candidates = [e for e in text_elems if len(e.name) >= 200 and e.bounds[1] > 200]
    description = max(body_candidates, key=lambda e: len(e.name)).name if body_candidates else ""

    # Client info
    client_payment_verified = any("payment verified" in (e.name or "").lower() for e in text_elems)

    # Country (heuristic: short text in client section, capitalized, no digits)
    # Conservative: find a known short country name in the trailing text elements.
    KNOWN_COUNTRIES = {
        "United States", "United Kingdom", "Canada", "Australia", "Germany", "India",
        "Pakistan", "France", "Spain", "Italy", "Netherlands", "Sweden", "Brazil",
        "Singapore", "Japan", "Saudi Arabia", "United Arab Emirates", "Israel"
    }
    client_country = next((e.name.strip() for e in text_elems if e.name.strip() in KNOWN_COUNTRIES), None)

    return PanelData(
        title=title,
        posted_text=posted_text,
        budget_kind=budget_kind,
        budget_min_usd=budget_min,
        budget_max_usd=budget_max,
        budget_raw_text=budget_raw,
        duration=None,
        experience_level=None,
        hours_per_week=None,
        skills=skills,
        description=description,
        client_country=client_country,
        client_payment_verified=client_payment_verified or None,
    )
```

This is the **starter** parser; preserves the structural anchors named in CLAUDE.md. Refine in later iterations as more fixtures are captured.

- [ ] **Step 4: Run, verify PASS**

Run: `uv run pytest tests/unit/upwork/test_panel.py -v`
Expected: 1 passed.

- [ ] **Step 5: Implement feed.py, clipboard_url.py, apply_form.py, search.py**

For each of the four modules below, port the corresponding logic from the legacy file. These don't get unit tests in Phase 1 (they touch UIA — covered by integration smoke later); the goal is just clean extraction.

**`upwork/feed.py`** — feed page navigation (refresh, click "Most Recent", scroll to top, iterate top-of-feed cards). Source lines: search `upwork_driver.py` for the feed-handling section (around the "Ctrl+R" + "Most Recent" sequence). Function signatures:

```python
def refresh_feed(window_title: str) -> None: ...
def click_most_recent_tab(window_title: str) -> None: ...
def top_of_feed_cards(window_title: str, max_cards: int) -> list: ...
def open_card_panel(card) -> bool: ...   # clicks the card title; returns True if panel opened
def close_panel(window_title: str) -> None: ...   # presses Esc
```

**`upwork/clipboard_url.py`** — Copy-to-clipboard click + clipboard read with bounds validation. Source: search `upwork_driver.py` for "Copy to clipboard". Function:

```python
def capture_url_from_open_panel(window_title: str) -> Optional[str]: ...
```

Bounds validation rules (preserve from legacy): button x ≥ 1200, y ≥ 200, click dead-center (no jitter). If validation fails, return None.

**`upwork/apply_form.py`** — apply-page sequence. Source: `upwork_apply.py`. Functions:

```python
def navigate_to_apply(window_title: str, job_url: str) -> None: ...
def wait_for_form_or_login(window_title: str, timeout_s: int = 30) -> str: ...
    # returns 'ready' | 'login_required' | 'cloudflare'
def paste_cover_letter(window_title: str, text: str) -> None: ...
def answer_screening_questions(window_title: str, answers: dict[str, str]) -> None: ...
def select_never_for_rate_increase(window_title: str) -> None: ...
def fill_bid_amount(window_title: str, amount_usd: float) -> None: ...
# Note: NO submit function. Submission is a separate explicit call only after human approval.
def submit_proposal(window_title: str) -> None: ...
```

The hard rule from CLAUDE.md (apply driver NEVER auto-submits without human approval) is enforced at the *caller* level (the `bidder/apply_executor.py`), not here. This module only exposes the function; whether it gets called is a higher-layer decision.

**`upwork/search.py`** — search URL grammar from `upwork_research.py`. Functions:

```python
def parse_query_line(raw: str) -> tuple[str, dict]:  # (query_term, filters)
def build_search_url(query_term: str, filters: dict) -> str: ...
```

- [ ] **Step 6: Smoke import test**

Run: `uv run python -c "from upwork import panel, feed, clipboard_url, apply_form, search; print('upwork ok')"`
Expected: `upwork ok`

- [ ] **Step 7: Run all tests**

Run: `uv run pytest tests/ -v`
Expected: all unit tests pass; integration tests run if Postgres is up.

- [ ] **Step 8: Commit**

```bash
git add upwork/ tests/unit/upwork/
git commit -m "Extract upwork-specific logic into focused modules"
```

---

## Task 7: domain/types.py + scoring.py + risk.py

**Files:**
- Create: `domain/__init__.py`
- Create: `domain/types.py`
- Create: `domain/scoring.py`
- Create: `domain/risk.py`
- Create: `tests/unit/domain/__init__.py`
- Create: `tests/unit/domain/test_scoring.py`
- Create: `tests/unit/domain/test_risk.py`

- [ ] **Step 1: Write the failing test for scoring**

Create `tests/unit/domain/__init__.py` (empty).

Create `tests/unit/domain/test_scoring.py`:

```python
"""Filter-DSL evaluation: given a Job and a Setup, decide if it matches and why."""
import pytest
from domain.types import Job, Setup, FilterDsl
from domain.scoring import score_job_against_setup, MatchResult


def _setup(filter_dsl: dict) -> Setup:
    return Setup(
        setup_id=1,
        name="test",
        status="active",
        tier="normal",
        filter_dsl=FilterDsl(filter_dsl),
        prose_definition=None,
        pitch_template_id=None,
        cover_letter_template_id=None,
        auto_apply_enabled=False,
        escalation_config={},
    )


def _job(**overrides) -> Job:
    base = dict(
        job_id="~012345",
        url="https://www.upwork.com/jobs/~012345",
        title="Build a RAG system",
        description="We need RAG, Pinecone, eval pipeline",
        budget_kind="fixed",
        budget_min_usd=3000.0,
        budget_max_usd=5000.0,
        skills=["RAG", "Pinecone", "Python"],
        client_payment_verified=True,
        client_country="United States",
    )
    base.update(overrides)
    return Job(**base)


def test_skill_in_match():
    setup = _setup({"any_of": [{"skill_in": ["pinecone", "rag", "weaviate"]}]})
    result = score_job_against_setup(_job(), setup)
    assert result.matched is True
    assert "skill_in" in result.matched_rules


def test_budget_floor_excludes():
    setup = _setup({"all_of": [{"budget_min_at_least": 10000}]})
    result = score_job_against_setup(_job(), setup)
    assert result.matched is False


def test_payment_verified_required():
    setup = _setup({"all_of": [{"client_payment_verified": True}]})
    assert score_job_against_setup(_job(), setup).matched is True
    assert score_job_against_setup(_job(client_payment_verified=False), setup).matched is False


def test_combined_all_of_skill_budget_verified():
    setup = _setup({
        "all_of": [
            {"skill_in": ["rag", "pinecone"]},
            {"budget_min_at_least": 1500},
            {"client_payment_verified": True},
        ]
    })
    assert score_job_against_setup(_job(), setup).matched is True


def test_no_match_returns_match_false_with_reason():
    setup = _setup({"all_of": [{"skill_in": ["solidity"]}]})
    result = score_job_against_setup(_job(), setup)
    assert result.matched is False
    assert "skill_in" in result.unmet_rules
```

- [ ] **Step 2: Run, verify FAIL**

Run: `uv run pytest tests/unit/domain/test_scoring.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement domain/types.py**

Create `domain/__init__.py` (empty).

Create `domain/types.py`:

```python
"""Domain types. Pure data, no I/O. These cross module boundaries."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Optional


JobId = str
SetupId = int
OrderId = int
SignalId = int


@dataclass
class FilterDsl:
    """Wraps a filter spec. The spec is a dict of the form:
        { "all_of": [rule, ...] } or { "any_of": [rule, ...] } or a single rule
    Each rule is a single-key dict like {"skill_in": ["rag"]} or {"budget_min_at_least": 1500}.
    Supported rule keys (Phase 1):
        skill_in: [str]                       — case-insensitive match against job.skills
        budget_min_at_least: number           — job.budget_min_usd or budget_max_usd >= n
        budget_kind_in: ["fixed","hourly"]
        client_payment_verified: bool
        client_country_in: [str]
        description_matches: str (regex, case-insensitive)
    """
    spec: dict


@dataclass
class Job:
    job_id: JobId
    url: str
    title: str
    description: Optional[str] = None
    budget_kind: Optional[str] = None
    budget_min_usd: Optional[float] = None
    budget_max_usd: Optional[float] = None
    skills: list[str] = field(default_factory=list)
    client_country: Optional[str] = None
    client_payment_verified: Optional[bool] = None
    client_rating: Optional[float] = None
    client_hires: Optional[int] = None
    client_total_spent_usd: Optional[float] = None
    posted_at: Optional[datetime] = None
    proposals_count_at_first_scrape: Optional[int] = None


@dataclass
class Setup:
    setup_id: SetupId
    name: str
    status: Literal["proposed", "active", "disabled", "retired"]
    tier: Literal["quiet", "normal", "critical"]
    filter_dsl: FilterDsl
    prose_definition: Optional[str]
    pitch_template_id: Optional[int]
    cover_letter_template_id: Optional[int]
    auto_apply_enabled: bool
    escalation_config: dict


@dataclass
class MatchResult:
    matched: bool
    matched_rules: list[str] = field(default_factory=list)
    unmet_rules: list[str] = field(default_factory=list)
    score: float = 0.0          # 0..1; reserved for LLM tie-break in Phase 2


@dataclass
class Signal:
    signal_id: Optional[SignalId]
    job_id: JobId
    primary_setup_id: SetupId
    matched_setups: list[dict]  # [{setup_id, match_reason: 'rule', matched_rules: [...]}]
    fired_at: Optional[datetime]
    market_state: dict


@dataclass
class Order:
    order_id: Optional[OrderId]
    signal_id: SignalId
    job_id: JobId
    setup_id: SetupId
    status: Literal["drafting", "awaiting_approval", "approved", "staging", "attempting", "submitted", "cancelled", "failed"]
    bid_amount_usd: Optional[float]
    connects_spent: Optional[int]
    cover_letter_body: Optional[str]
    doc_url: Optional[str]
    screening_answers_json: Optional[dict]
    drafted_at: Optional[datetime]
    approved_at: Optional[datetime]
    submitted_at: Optional[datetime]
    failed_reason: Optional[str]
    idempotency_key: str


@dataclass
class OutcomeEvent:
    id: Optional[int]
    order_id: OrderId
    event_type: Literal["submitted", "viewed", "replied", "interviewed", "hired", "declined", "ghosted"]
    observed_at: datetime
    source: Literal["discord_manual", "upwork_my_proposals_scrape", "self_reported"]
    notes: Optional[str]
```

- [ ] **Step 4: Implement scoring.py**

Create `domain/scoring.py`:

```python
"""Filter-DSL evaluation. Pure function: (Job, Setup) -> MatchResult."""
from __future__ import annotations

import re
from typing import Any

from domain.types import Job, Setup, MatchResult, FilterDsl


def _eval_rule(rule: dict, job: Job) -> tuple[str, bool]:
    """Evaluate a single-key rule against a job. Returns (rule_name, matched)."""
    if len(rule) != 1:
        raise ValueError(f"Rule must have exactly one key: {rule!r}")
    [(key, val)] = rule.items()

    if key == "skill_in":
        wanted = {s.lower() for s in val}
        have = {s.lower() for s in (job.skills or [])}
        return key, bool(wanted & have)

    if key == "budget_min_at_least":
        b = job.budget_min_usd if job.budget_min_usd is not None else job.budget_max_usd
        return key, (b is not None and b >= val)

    if key == "budget_kind_in":
        return key, (job.budget_kind in val)

    if key == "client_payment_verified":
        return key, (job.client_payment_verified == val)

    if key == "client_country_in":
        return key, (job.client_country in val)

    if key == "description_matches":
        if not job.description:
            return key, False
        return key, bool(re.search(val, job.description, re.I))

    raise ValueError(f"Unknown rule key: {key!r}")


def score_job_against_setup(job: Job, setup: Setup) -> MatchResult:
    spec = setup.filter_dsl.spec
    matched_rules: list[str] = []
    unmet_rules: list[str] = []

    if "all_of" in spec:
        all_ok = True
        for rule in spec["all_of"]:
            name, ok = _eval_rule(rule, job)
            if ok:
                matched_rules.append(name)
            else:
                unmet_rules.append(name)
                all_ok = False
        return MatchResult(matched=all_ok, matched_rules=matched_rules, unmet_rules=unmet_rules)

    if "any_of" in spec:
        any_ok = False
        for rule in spec["any_of"]:
            name, ok = _eval_rule(rule, job)
            if ok:
                matched_rules.append(name)
                any_ok = True
            else:
                unmet_rules.append(name)
        return MatchResult(matched=any_ok, matched_rules=matched_rules, unmet_rules=unmet_rules)

    # Single rule
    name, ok = _eval_rule(spec, job)
    if ok:
        matched_rules.append(name)
    else:
        unmet_rules.append(name)
    return MatchResult(matched=ok, matched_rules=matched_rules, unmet_rules=unmet_rules)
```

- [ ] **Step 5: Run, verify scoring PASS**

Run: `uv run pytest tests/unit/domain/test_scoring.py -v`
Expected: 5 passed.

- [ ] **Step 6: Write the failing test for risk**

Create `tests/unit/domain/test_risk.py`:

```python
"""Connects budget enforcement."""
import pytest
from datetime import datetime, timedelta, timezone
from domain.risk import RiskCaps, OrderTimestamps, can_submit_order, RiskBlocked


def _now():
    return datetime(2026, 5, 6, 14, 0, tzinfo=timezone.utc)  # Wednesday


def test_under_caps_allows():
    caps = RiskCaps(daily=3, weekly=10)
    ts = OrderTimestamps(submitted_today=2, submitted_this_week=5, last_submission=None)
    decision = can_submit_order(ts, caps, _now())
    assert decision.allowed is True


def test_daily_cap_blocks():
    caps = RiskCaps(daily=3, weekly=10)
    ts = OrderTimestamps(submitted_today=3, submitted_this_week=5, last_submission=None)
    decision = can_submit_order(ts, caps, _now())
    assert decision.allowed is False
    assert "daily" in decision.reason


def test_weekly_cap_blocks():
    caps = RiskCaps(daily=3, weekly=10)
    ts = OrderTimestamps(submitted_today=1, submitted_this_week=10, last_submission=None)
    decision = can_submit_order(ts, caps, _now())
    assert decision.allowed is False
    assert "weekly" in decision.reason


def test_min_gap_since_last_submission():
    caps = RiskCaps(daily=3, weekly=10, min_gap_seconds=30)
    last = _now() - timedelta(seconds=10)
    ts = OrderTimestamps(submitted_today=1, submitted_this_week=2, last_submission=last)
    decision = can_submit_order(ts, caps, _now())
    assert decision.allowed is False
    assert "gap" in decision.reason
    assert decision.retry_after_seconds is not None
    assert decision.retry_after_seconds >= 19  # ~20 left
```

- [ ] **Step 7: Run, verify FAIL**

Run: `uv run pytest tests/unit/domain/test_risk.py -v`
Expected: FAIL.

- [ ] **Step 8: Implement risk.py**

Create `domain/risk.py`:

```python
"""Connects budget enforcement. Pure logic — caller passes in current timestamps; risk decides."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional


@dataclass(frozen=True)
class RiskCaps:
    daily: int
    weekly: int
    min_gap_seconds: int = 30


@dataclass
class OrderTimestamps:
    submitted_today: int
    submitted_this_week: int
    last_submission: Optional[datetime]


@dataclass
class RiskDecision:
    allowed: bool
    reason: Optional[str] = None
    retry_after_seconds: Optional[int] = None


class RiskBlocked(Exception):
    pass


def can_submit_order(ts: OrderTimestamps, caps: RiskCaps, now: datetime) -> RiskDecision:
    if ts.submitted_today >= caps.daily:
        return RiskDecision(False, reason=f"daily cap reached ({caps.daily})")
    if ts.submitted_this_week >= caps.weekly:
        return RiskDecision(False, reason=f"weekly cap reached ({caps.weekly})")
    if ts.last_submission is not None:
        elapsed = (now - ts.last_submission).total_seconds()
        if elapsed < caps.min_gap_seconds:
            remaining = int(caps.min_gap_seconds - elapsed)
            return RiskDecision(False, reason=f"min-gap not yet elapsed", retry_after_seconds=remaining)
    return RiskDecision(True)
```

- [ ] **Step 9: Run, verify risk PASS**

Run: `uv run pytest tests/unit/domain/test_risk.py -v`
Expected: 4 passed.

- [ ] **Step 10: Commit**

```bash
git add domain/ tests/unit/domain/
git commit -m "Add domain types + scoring + risk (pure logic, fully tested)"
```

---

## Task 8: domain/humanization.py

**Files:**
- Create: `domain/humanization.py`
- Create: `tests/unit/domain/test_humanization.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/domain/test_humanization.py`:

```python
"""Humanization: timing distributions, voice variants, cycle mix decisions.

These tests use a seeded RNG so the distribution is checkable.
"""
import random
from datetime import datetime, timezone
from collections import Counter
from domain.humanization import (
    Humanizer,
    DiurnalEnvelope,
    default_envelope,
    CycleType,
)


def test_scan_interval_is_within_active_range():
    h = Humanizer(rng=random.Random(42), envelope=default_envelope())
    samples = [h.sample_scan_interval(active=True) for _ in range(1000)]
    assert all(120 <= s <= 720 for s in samples), "active scans should be 2-12 min"
    avg = sum(samples) / len(samples)
    assert 240 <= avg <= 480, f"avg {avg} should be ~5 min"


def test_scan_interval_off_hours_is_longer():
    h = Humanizer(rng=random.Random(42), envelope=default_envelope())
    samples = [h.sample_scan_interval(active=False) for _ in range(1000)]
    assert all(s >= 1800 for s in samples), "off-hours scans should be ≥30 min"


def test_cycle_type_mix_distribution():
    h = Humanizer(rng=random.Random(42), envelope=default_envelope())
    counts = Counter(h.sample_cycle_type() for _ in range(10000))
    total = sum(counts.values())
    # Allow ±3% slack
    assert 0.57 <= counts[CycleType.FULL_SCAN] / total <= 0.63
    assert 0.17 <= counts[CycleType.SKIM_ONLY] / total <= 0.23
    assert 0.07 <= counts[CycleType.PANEL_SKIM] / total <= 0.13


def test_signoff_variant_uses_all_three_over_many_calls():
    h = Humanizer(rng=random.Random(42), envelope=default_envelope())
    seen = {h.sample_signoff_variant("Moazzam") for _ in range(100)}
    assert len(seen) == 3


def test_envelope_active_during_workday_local_time():
    env = default_envelope()
    # 10 AM Wednesday should be 100% active
    assert env.activity_at(datetime(2026, 5, 6, 10, 0, tzinfo=timezone.utc).hour, weekday=2) == 1.0
    # 3 AM should be very low
    assert env.activity_at(3, weekday=2) <= 0.1


def test_should_take_diversion_cycle_returns_bool():
    h = Humanizer(rng=random.Random(42), envelope=default_envelope())
    decisions = [h.should_take_diversion_cycle(recent_diversions=0) for _ in range(1000)]
    rate = sum(decisions) / len(decisions)
    assert 0.03 <= rate <= 0.07, "diversion rate should be ~5%"
```

- [ ] **Step 2: Run, verify FAIL**

Run: `uv run pytest tests/unit/domain/test_humanization.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement humanization.py**

Create `domain/humanization.py`:

```python
"""Humanization: timing distributions and behavioral mix decisions.

Pure logic, deterministic when seeded. Consumed by scheduler, bidder, apply executor.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class CycleType(str, Enum):
    FULL_SCAN = "full_scan"
    SKIM_ONLY = "skim_only"
    PANEL_SKIM = "panel_skim"
    SIDE_TRIP = "side_trip"
    NO_OP = "no_op"


_CYCLE_WEIGHTS = {
    CycleType.FULL_SCAN: 0.60,
    CycleType.SKIM_ONLY: 0.20,
    CycleType.PANEL_SKIM: 0.10,
    CycleType.SIDE_TRIP: 0.05,
    CycleType.NO_OP: 0.05,
}


@dataclass
class DiurnalEnvelope:
    """Hour-of-day -> activity multiplier (0..1). Weekend dampening factor applied separately."""
    by_hour: dict[int, float]
    weekend_multiplier: float = 0.6

    def activity_at(self, hour: int, weekday: int) -> float:
        base = self.by_hour.get(hour, 0.5)
        if weekday >= 5:  # Saturday=5, Sunday=6
            return base * self.weekend_multiplier
        return base


def default_envelope() -> DiurnalEnvelope:
    return DiurnalEnvelope(by_hour={
        0: 0.05, 1: 0.05, 2: 0.05, 3: 0.05, 4: 0.05, 5: 0.05, 6: 0.05,
        7: 0.40, 8: 0.60,
        9: 1.00, 10: 1.00, 11: 1.00,
        12: 0.40, 13: 0.40,
        14: 1.00, 15: 1.00, 16: 1.00, 17: 1.00,
        18: 0.70, 19: 0.70, 20: 0.70, 21: 0.70,
        22: 0.30, 23: 0.30,
    })


@dataclass
class Humanizer:
    rng: random.Random = field(default_factory=lambda: random.Random())
    envelope: DiurnalEnvelope = field(default_factory=default_envelope)

    def sample_scan_interval(self, active: bool) -> int:
        """Seconds until the next scheduler tick should fire."""
        if active:
            # Bimodal: 80% 240-480s focused, 15% 120-240s eager, 5% 480-720s distracted.
            r = self.rng.random()
            if r < 0.80:
                return self.rng.randint(240, 480)
            elif r < 0.95:
                return self.rng.randint(120, 240)
            else:
                return self.rng.randint(480, 720)
        else:
            # Off-hours: 30 min to 4 hours
            return self.rng.randint(1800, 14400)

    def sample_panel_dwell(self) -> float:
        """Seconds spent 'reading' a panel before scoring/closing."""
        return self.rng.uniform(4.0, 15.0)

    def sample_review_pause(self, text_length: int) -> float:
        """Seconds to 'review' a pasted block of text."""
        base = max(3.0, min(15.0, text_length / 80))
        return base + self.rng.uniform(-1.5, 4.0)

    def sample_apply_form_pauses(self) -> dict[str, float]:
        return {
            "initial_idle": self.rng.uniform(8.0, 30.0),
            "after_paste": self.rng.uniform(5.0, 15.0),
            "before_screening": self.rng.uniform(5.0, 15.0),
            "final_review": self.rng.uniform(10.0, 20.0),
        }

    def sample_signoff_variant(self, name: str) -> str:
        choices = [f"- {name}", f"Cheers, {name}", f"Best, {name}"]
        return self.rng.choice(choices)

    def sample_greeting_variant(self, client_name: Optional[str]) -> Optional[str]:
        if not client_name:
            return None
        choices = [f"Hey {client_name},", f"Hi {client_name} —", None]
        return self.rng.choice(choices)

    def sample_cycle_type(self) -> CycleType:
        types = list(_CYCLE_WEIGHTS.keys())
        weights = [_CYCLE_WEIGHTS[t] for t in types]
        return self.rng.choices(types, weights=weights, k=1)[0]

    def should_take_diversion_cycle(self, recent_diversions: int) -> bool:
        """5% baseline; reduce if we just did several."""
        base = 0.05
        adj = max(0.01, base - recent_diversions * 0.02)
        return self.rng.random() < adj

    def should_abandon_apply(self) -> bool:
        return self.rng.random() < 0.05

    def is_active_now(self, now: datetime) -> bool:
        """Roll against the diurnal envelope."""
        local_hour = now.hour
        weekday = now.weekday()
        activity = self.envelope.activity_at(local_hour, weekday)
        return self.rng.random() < activity
```

- [ ] **Step 4: Run, verify PASS**

Run: `uv run pytest tests/unit/domain/test_humanization.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add domain/humanization.py tests/unit/domain/test_humanization.py
git commit -m "Add humanization layer (diurnal envelope, cycle mix, voice variants)"
```

---

## Task 9: storage layer (jobs, setups, orders, portfolio, etc.)

This task implements the DAL. Each store is small and focused. Tests use the integration fixture from Task 3.

**Files:**
- Create: `storage/jobs.py`
- Create: `storage/setups.py`
- Create: `storage/orders.py`
- Create: `storage/portfolio.py`
- Create: `storage/agent_runs.py`
- Create: `storage/connects_ledger.py`
- Create: `storage/scrape_runs.py`
- Create: `storage/enrichments.py`
- Create: `tests/integration/storage/test_jobs.py`
- Create: `tests/integration/storage/test_setups.py`
- Create: `tests/integration/storage/test_orders.py`
- Create: `tests/integration/storage/test_portfolio.py`

For brevity, I'll show one store fully (jobs) and outline the others — the pattern is identical.

- [ ] **Step 1: Failing test for JobStore**

Create `tests/integration/storage/test_jobs.py`:

```python
import pytest
from datetime import datetime, timezone
from storage.migrate import apply_migrations
from storage.jobs import JobStore
from domain.types import Job
from pathlib import Path


MIG = Path(__file__).parents[3] / "storage" / "migrations"


@pytest.fixture
def fresh_db(db):
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        conn.commit()
    apply_migrations(db, MIG)
    return db


def _job() -> Job:
    return Job(
        job_id="~abc123",
        url="https://www.upwork.com/jobs/~abc123",
        title="Build a RAG eval pipeline",
        description="we need eval",
        budget_kind="fixed",
        budget_min_usd=4000.0,
        budget_max_usd=4000.0,
        skills=["RAG", "Pinecone"],
        client_country="United States",
        client_payment_verified=True,
    )


def test_upsert_and_fetch(fresh_db):
    store = JobStore(fresh_db)
    store.upsert(_job(), source="feed", raw_panel={})
    fetched = store.get("~abc123")
    assert fetched is not None
    assert fetched.title.startswith("Build a RAG")
    assert fetched.skills == ["RAG", "Pinecone"]


def test_upsert_idempotent(fresh_db):
    store = JobStore(fresh_db)
    store.upsert(_job(), source="feed", raw_panel={})
    store.upsert(_job(), source="feed", raw_panel={})
    with fresh_db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM jobs")
            assert cur.fetchone()[0] == 1


def test_is_known(fresh_db):
    store = JobStore(fresh_db)
    assert store.is_known("~abc123") is False
    store.upsert(_job(), source="feed", raw_panel={})
    assert store.is_known("~abc123") is True
```

- [ ] **Step 2: Run, verify FAIL**

Run: `uv run pytest tests/integration/storage/test_jobs.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement JobStore**

Create `storage/jobs.py`:

```python
"""JobStore: write/read jobs + their skills."""
from __future__ import annotations

import json
from typing import Optional

from psycopg.types.json import Json

from domain.types import Job
from storage.connection import Database


class JobStore:
    def __init__(self, db: Database):
        self._db = db

    def upsert(self, job: Job, source: str, raw_panel: dict) -> None:
        """Insert or update a job (and its skills) by job_id. Idempotent."""
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO jobs (
                      job_id, url, title, description,
                      budget_kind, budget_min_usd, budget_max_usd,
                      source, client_country, client_payment_verified,
                      client_rating, client_hires, client_total_spent_usd,
                      proposals_count_at_first_scrape, raw_panel_json
                    ) VALUES (
                      %s, %s, %s, %s,
                      %s, %s, %s,
                      %s, %s, %s,
                      %s, %s, %s,
                      %s, %s
                    )
                    ON CONFLICT (job_id) DO UPDATE SET
                      url = EXCLUDED.url,
                      title = EXCLUDED.title,
                      description = COALESCE(EXCLUDED.description, jobs.description),
                      budget_kind = COALESCE(EXCLUDED.budget_kind, jobs.budget_kind),
                      budget_min_usd = COALESCE(EXCLUDED.budget_min_usd, jobs.budget_min_usd),
                      budget_max_usd = COALESCE(EXCLUDED.budget_max_usd, jobs.budget_max_usd),
                      raw_panel_json = EXCLUDED.raw_panel_json
                """, (
                    job.job_id, job.url, job.title, job.description,
                    job.budget_kind, job.budget_min_usd, job.budget_max_usd,
                    source, job.client_country, job.client_payment_verified,
                    job.client_rating, job.client_hires, job.client_total_spent_usd,
                    job.proposals_count_at_first_scrape, Json(raw_panel),
                ))
                # Upsert skills
                for skill_name in job.skills or []:
                    cur.execute("""
                        INSERT INTO skills (name) VALUES (%s)
                        ON CONFLICT (name) DO NOTHING
                    """, (skill_name,))
                    cur.execute("""
                        INSERT INTO job_skills (job_id, skill_id, source)
                        SELECT %s, skill_id, 'upwork_chip' FROM skills WHERE name = %s
                        ON CONFLICT DO NOTHING
                    """, (job.job_id, skill_name))

    def get(self, job_id: str) -> Optional[Job]:
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT job_id, url, title, description,
                           budget_kind, budget_min_usd, budget_max_usd,
                           client_country, client_payment_verified,
                           client_rating, client_hires, client_total_spent_usd,
                           posted_at, proposals_count_at_first_scrape
                    FROM jobs WHERE job_id = %s
                """, (job_id,))
                row = cur.fetchone()
                if row is None:
                    return None
                cur.execute("""
                    SELECT s.name FROM job_skills js JOIN skills s ON s.skill_id = js.skill_id
                    WHERE js.job_id = %s
                """, (job_id,))
                skills = [r[0] for r in cur.fetchall()]
        return Job(
            job_id=row[0], url=row[1], title=row[2], description=row[3],
            budget_kind=row[4], budget_min_usd=float(row[5]) if row[5] is not None else None,
            budget_max_usd=float(row[6]) if row[6] is not None else None,
            client_country=row[7], client_payment_verified=row[8],
            client_rating=float(row[9]) if row[9] is not None else None,
            client_hires=row[10],
            client_total_spent_usd=float(row[11]) if row[11] is not None else None,
            posted_at=row[12], proposals_count_at_first_scrape=row[13],
            skills=skills,
        )

    def is_known(self, job_id: str) -> bool:
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM jobs WHERE job_id = %s", (job_id,))
                return cur.fetchone() is not None
```

- [ ] **Step 4: Run, verify PASS**

Run: `uv run pytest tests/integration/storage/test_jobs.py -v`
Expected: 3 passed.

- [ ] **Step 5: Implement SetupStore + SignalStore (follow same pattern: test → fail → implement → pass)**

Create `tests/integration/storage/test_setups.py`:

```python
import pytest
from pathlib import Path
from storage.migrate import apply_migrations
from storage.setups import SetupStore
from domain.types import Setup, FilterDsl

MIG = Path(__file__).parents[3] / "storage" / "migrations"


@pytest.fixture
def fresh_db(db):
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        conn.commit()
    apply_migrations(db, MIG)
    return db


def _setup(name: str, **overrides) -> Setup:
    base = dict(
        setup_id=None, name=name, status="active", tier="normal",
        filter_dsl=FilterDsl({"all_of": [{"client_payment_verified": True}]}),
        prose_definition="test", pitch_template_id=None, cover_letter_template_id=None,
        auto_apply_enabled=False, escalation_config={"initial": 0, "repings": [], "deadline": None, "on_deadline": "queue"},
    )
    base.update(overrides)
    return Setup(**base)


def test_create_and_list_active(fresh_db):
    store = SetupStore(fresh_db)
    sid = store.create(_setup("test-1"))
    assert sid > 0
    store.create(_setup("test-2", status="disabled"))
    active = store.list_active()
    assert len(active) == 1
    assert active[0].name == "test-1"


def test_get_round_trips_filter_dsl(fresh_db):
    store = SetupStore(fresh_db)
    sid = store.create(_setup("test-1", filter_dsl=FilterDsl({"any_of": [{"skill_in": ["rag"]}]})))
    fetched = store.get(sid)
    assert fetched.filter_dsl.spec == {"any_of": [{"skill_in": ["rag"]}]}
```

Implement `storage/setups.py`:

```python
"""SetupStore + SignalStore."""
from __future__ import annotations

from typing import Optional, List
from psycopg.types.json import Json

from domain.types import Setup, Signal, FilterDsl
from storage.connection import Database


class SetupStore:
    def __init__(self, db: Database):
        self._db = db

    def create(self, setup: Setup) -> int:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO setups (
                      name, status, tier, filter_dsl, prose_definition,
                      pitch_template_id, cover_letter_template_id,
                      auto_apply_enabled, escalation_config, activated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                              CASE WHEN %s = 'active' THEN now() ELSE NULL END)
                    RETURNING setup_id
                """, (
                    setup.name, setup.status, setup.tier,
                    Json(setup.filter_dsl.spec), setup.prose_definition,
                    setup.pitch_template_id, setup.cover_letter_template_id,
                    setup.auto_apply_enabled, Json(setup.escalation_config),
                    setup.status,
                ))
                return cur.fetchone()[0]

    def get(self, setup_id: int) -> Optional[Setup]:
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT setup_id, name, status, tier, filter_dsl, prose_definition,
                           pitch_template_id, cover_letter_template_id,
                           auto_apply_enabled, escalation_config
                    FROM setups WHERE setup_id = %s
                """, (setup_id,))
                row = cur.fetchone()
                if row is None:
                    return None
                return Setup(
                    setup_id=row[0], name=row[1], status=row[2], tier=row[3],
                    filter_dsl=FilterDsl(row[4]), prose_definition=row[5],
                    pitch_template_id=row[6], cover_letter_template_id=row[7],
                    auto_apply_enabled=row[8], escalation_config=row[9],
                )

    def list_active(self) -> List[Setup]:
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT setup_id, name, status, tier, filter_dsl, prose_definition,
                           pitch_template_id, cover_letter_template_id,
                           auto_apply_enabled, escalation_config
                    FROM setups WHERE status = 'active'
                    ORDER BY setup_id
                """)
                rows = cur.fetchall()
        return [
            Setup(setup_id=r[0], name=r[1], status=r[2], tier=r[3],
                  filter_dsl=FilterDsl(r[4]), prose_definition=r[5],
                  pitch_template_id=r[6], cover_letter_template_id=r[7],
                  auto_apply_enabled=r[8], escalation_config=r[9])
            for r in rows
        ]

    def update_status(self, setup_id: int, new_status: str) -> None:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE setups SET status = %s WHERE setup_id = %s", (new_status, setup_id))


class SignalStore:
    def __init__(self, db: Database):
        self._db = db

    def create(self, signal: Signal) -> int:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO signals (job_id, primary_setup_id, matched_setups, market_state)
                    VALUES (%s, %s, %s, %s)
                    RETURNING signal_id
                """, (signal.job_id, signal.primary_setup_id,
                      Json(signal.matched_setups), Json(signal.market_state)))
                return cur.fetchone()[0]
```

Run: `uv run pytest tests/integration/storage/test_setups.py -v` — Expected: 2 passed.

- [ ] **Step 6: Implement OrderStore + OutcomeEventStore**

Create `tests/integration/storage/test_orders.py`:

```python
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from storage.migrate import apply_migrations
from storage.setups import SetupStore, SignalStore
from storage.jobs import JobStore
from storage.orders import OrderStore, OutcomeEventStore
from domain.types import Job, Setup, Signal, FilterDsl, Order

MIG = Path(__file__).parents[3] / "storage" / "migrations"


@pytest.fixture
def fresh_db(db):
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        conn.commit()
    apply_migrations(db, MIG)
    return db


def _seed(fresh_db) -> tuple[int, int, str]:
    """Seed a job + setup + signal; return (signal_id, setup_id, job_id)."""
    JobStore(fresh_db).upsert(
        Job(job_id="~j1", url="https://x", title="T", skills=[]), source="feed", raw_panel={},
    )
    setup_id = SetupStore(fresh_db).create(Setup(
        setup_id=None, name="s1", status="active", tier="normal",
        filter_dsl=FilterDsl({"all_of": []}), prose_definition=None,
        pitch_template_id=None, cover_letter_template_id=None,
        auto_apply_enabled=False, escalation_config={},
    ))
    signal_id = SignalStore(fresh_db).create(Signal(
        signal_id=None, job_id="~j1", primary_setup_id=setup_id,
        matched_setups=[], fired_at=None, market_state={},
    ))
    return signal_id, setup_id, "~j1"


def test_create_draft_and_get(fresh_db):
    signal_id, setup_id, job_id = _seed(fresh_db)
    store = OrderStore(fresh_db)
    order = Order(
        order_id=None, signal_id=signal_id, job_id=job_id, setup_id=setup_id,
        status="drafting", bid_amount_usd=None, connects_spent=None,
        cover_letter_body=None, doc_url=None, screening_answers_json=None,
        drafted_at=None, approved_at=None, submitted_at=None, failed_reason=None,
        idempotency_key="key-1",
    )
    oid = store.create_draft(order)
    fetched = store.get(oid)
    assert fetched.status == "drafting"
    assert fetched.idempotency_key == "key-1"


def test_idempotency_key_unique(fresh_db):
    signal_id, setup_id, job_id = _seed(fresh_db)
    store = OrderStore(fresh_db)
    o1 = Order(order_id=None, signal_id=signal_id, job_id=job_id, setup_id=setup_id,
               status="drafting", bid_amount_usd=None, connects_spent=None,
               cover_letter_body=None, doc_url=None, screening_answers_json=None,
               drafted_at=None, approved_at=None, submitted_at=None, failed_reason=None,
               idempotency_key="dup")
    store.create_draft(o1)
    import psycopg
    with pytest.raises(psycopg.errors.UniqueViolation):
        store.create_draft(o1)


def test_count_submitted_today(fresh_db):
    signal_id, setup_id, job_id = _seed(fresh_db)
    store = OrderStore(fresh_db)
    now = datetime.now(timezone.utc)
    # Insert 2 submitted today, 1 yesterday
    for i, when in enumerate([now, now - timedelta(hours=1), now - timedelta(days=1)]):
        with fresh_db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO orders (signal_id, job_id, setup_id, status, submitted_at, idempotency_key)
                    VALUES (%s, %s, %s, 'submitted', %s, %s)
                """, (signal_id, job_id, setup_id, when, f"k{i}"))
    assert store.count_submitted_today(now) == 2
    assert store.count_submitted_this_week(now) == 3


def test_outcome_event_record(fresh_db):
    signal_id, setup_id, job_id = _seed(fresh_db)
    store = OrderStore(fresh_db)
    oid = store.create_draft(Order(
        order_id=None, signal_id=signal_id, job_id=job_id, setup_id=setup_id,
        status="drafting", bid_amount_usd=None, connects_spent=None,
        cover_letter_body=None, doc_url=None, screening_answers_json=None,
        drafted_at=None, approved_at=None, submitted_at=None, failed_reason=None,
        idempotency_key="oe1",
    ))
    oe = OutcomeEventStore(fresh_db)
    oe.record(order_id=oid, event_type="viewed", source="discord_manual", notes=None)
    events = oe.list_for_order(oid)
    assert len(events) == 1
    assert events[0].event_type == "viewed"
```

Implement `storage/orders.py`:

```python
"""OrderStore + OutcomeEventStore."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import List, Optional
from psycopg.types.json import Json

from domain.types import Order, OutcomeEvent
from storage.connection import Database


class OrderStore:
    def __init__(self, db: Database):
        self._db = db

    def create_draft(self, order: Order) -> int:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO orders (
                      signal_id, job_id, setup_id, status,
                      bid_amount_usd, connects_spent,
                      cover_letter_body, doc_url, screening_answers_json,
                      drafted_at, idempotency_key
                    ) VALUES (%s, %s, %s, %s,
                              %s, %s,
                              %s, %s, %s,
                              now(), %s)
                    RETURNING order_id
                """, (
                    order.signal_id, order.job_id, order.setup_id, order.status,
                    order.bid_amount_usd, order.connects_spent,
                    order.cover_letter_body, order.doc_url,
                    Json(order.screening_answers_json) if order.screening_answers_json else None,
                    order.idempotency_key,
                ))
                return cur.fetchone()[0]

    def update_status(self, order_id: int, new_status: str) -> None:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                if new_status == "submitted":
                    cur.execute("UPDATE orders SET status = %s, submitted_at = now() WHERE order_id = %s",
                                (new_status, order_id))
                elif new_status == "approved":
                    cur.execute("UPDATE orders SET status = %s, approved_at = now() WHERE order_id = %s",
                                (new_status, order_id))
                else:
                    cur.execute("UPDATE orders SET status = %s WHERE order_id = %s",
                                (new_status, order_id))

    def set_drafted(self, order_id: int, cover_letter_body: str, doc_url: str, screening_answers_json: Optional[dict]) -> None:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE orders
                    SET cover_letter_body = %s, doc_url = %s, screening_answers_json = %s, drafted_at = now()
                    WHERE order_id = %s
                """, (cover_letter_body, doc_url,
                      Json(screening_answers_json) if screening_answers_json else None, order_id))

    def mark_submitted(self, order_id: int) -> None:
        self.update_status(order_id, "submitted")

    def get(self, order_id: int) -> Optional[Order]:
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT order_id, signal_id, job_id, setup_id, status,
                           bid_amount_usd, connects_spent,
                           cover_letter_body, doc_url, screening_answers_json,
                           drafted_at, approved_at, submitted_at, failed_reason, idempotency_key
                    FROM orders WHERE order_id = %s
                """, (order_id,))
                r = cur.fetchone()
                if r is None:
                    return None
        return Order(
            order_id=r[0], signal_id=r[1], job_id=r[2], setup_id=r[3], status=r[4],
            bid_amount_usd=float(r[5]) if r[5] is not None else None,
            connects_spent=r[6], cover_letter_body=r[7], doc_url=r[8],
            screening_answers_json=r[9],
            drafted_at=r[10], approved_at=r[11], submitted_at=r[12],
            failed_reason=r[13], idempotency_key=r[14],
        )

    def list_by_status(self, status: str) -> List[Order]:
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT order_id FROM orders WHERE status = %s ORDER BY order_id", (status,))
                ids = [r[0] for r in cur.fetchall()]
        return [self.get(oid) for oid in ids]

    def count_submitted_today(self, now: datetime) -> int:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM orders WHERE status = 'submitted' AND submitted_at >= %s", (start,))
                return cur.fetchone()[0]

    def count_submitted_this_week(self, now: datetime) -> int:
        start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM orders WHERE status = 'submitted' AND submitted_at >= %s", (start,))
                return cur.fetchone()[0]

    def last_submitted_at(self) -> Optional[datetime]:
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT MAX(submitted_at) FROM orders WHERE status = 'submitted'")
                return cur.fetchone()[0]


class OutcomeEventStore:
    def __init__(self, db: Database):
        self._db = db

    def record(self, order_id: int, event_type: str, source: str, notes: Optional[str]) -> None:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO outcome_events (order_id, event_type, source, notes)
                    VALUES (%s, %s, %s, %s)
                """, (order_id, event_type, source, notes))

    def list_for_order(self, order_id: int) -> List[OutcomeEvent]:
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, order_id, event_type, observed_at, source, notes
                    FROM outcome_events WHERE order_id = %s ORDER BY observed_at
                """, (order_id,))
                rows = cur.fetchall()
        return [OutcomeEvent(id=r[0], order_id=r[1], event_type=r[2], observed_at=r[3], source=r[4], notes=r[5])
                for r in rows]
```

Run: `uv run pytest tests/integration/storage/test_orders.py -v` — Expected: 4 passed.

- [ ] **Step 7: Implement PortfolioStore**

Create `tests/integration/storage/test_portfolio.py`:

```python
import pytest
from pathlib import Path
from storage.migrate import apply_migrations
from storage.portfolio import PortfolioStore

MIG = Path(__file__).parents[3] / "storage" / "migrations"


@pytest.fixture
def fresh_db(db):
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        conn.commit()
    apply_migrations(db, MIG)
    return db


def test_add_and_list_all(fresh_db):
    store = PortfolioStore(fresh_db)
    store.add(name="P1", summary=None, client_context="ctx", outcome="out",
              tech=["python", "fastapi"], relevance_tags=["rag"], year_completed=2025)
    items = store.list_all()
    assert len(items) == 1
    assert items[0]["name"] == "P1"


def test_list_matching_tags(fresh_db):
    store = PortfolioStore(fresh_db)
    store.add(name="P1", summary=None, client_context="", outcome="",
              tech=[], relevance_tags=["rag", "vector-search"], year_completed=None)
    store.add(name="P2", summary=None, client_context="", outcome="",
              tech=[], relevance_tags=["voice", "elevenlabs"], year_completed=None)
    rag_matches = store.list_matching_tags(["rag", "pinecone"])
    assert len(rag_matches) == 1
    assert rag_matches[0]["name"] == "P1"
```

Implement `storage/portfolio.py`:

```python
"""PortfolioStore — list-of-dict surface for Phase 1 simplicity."""
from __future__ import annotations

from typing import List, Optional
from storage.connection import Database


class PortfolioStore:
    def __init__(self, db: Database):
        self._db = db

    def add(self, *, name: str, summary: Optional[str], client_context: Optional[str],
            outcome: Optional[str], tech: list[str], relevance_tags: list[str],
            year_completed: Optional[int]) -> int:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO portfolio_items (name, summary, client_context, outcome, tech, relevance_tags, year_completed)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING portfolio_id
                """, (name, summary, client_context, outcome, tech, relevance_tags, year_completed))
                return cur.fetchone()[0]

    def list_all(self) -> List[dict]:
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT portfolio_id, name, summary, client_context, outcome, tech, relevance_tags, year_completed
                    FROM portfolio_items ORDER BY portfolio_id
                """)
                rows = cur.fetchall()
        return [dict(portfolio_id=r[0], name=r[1], summary=r[2], client_context=r[3],
                     outcome=r[4], tech=r[5], relevance_tags=r[6], year_completed=r[7])
                for r in rows]

    def list_matching_tags(self, tags: list[str]) -> List[dict]:
        if not tags:
            return self.list_all()
        lowered = [t.lower() for t in tags]
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT portfolio_id, name, summary, client_context, outcome, tech, relevance_tags, year_completed
                    FROM portfolio_items
                    WHERE EXISTS (
                        SELECT 1 FROM unnest(relevance_tags) tag
                        WHERE LOWER(tag) = ANY(%s)
                    )
                    ORDER BY portfolio_id
                """, (lowered,))
                rows = cur.fetchall()
        return [dict(portfolio_id=r[0], name=r[1], summary=r[2], client_context=r[3],
                     outcome=r[4], tech=r[5], relevance_tags=r[6], year_completed=r[7])
                for r in rows]
```

Run: `uv run pytest tests/integration/storage/test_portfolio.py -v` — Expected: 2 passed.

- [ ] **Step 8: Implement AgentRunStore, ConnectsLedgerStore, ScrapeRunStore, EnrichmentStore**

These four are smaller and follow the same pattern. For each: write a minimal happy-path test, implement, verify pass. Code shells:

`storage/agent_runs.py`:

```python
from __future__ import annotations
from typing import Optional
from psycopg.types.json import Json
from storage.connection import Database


class AgentRunStore:
    def __init__(self, db: Database):
        self._db = db

    def start(self, *, agent_name: str, trigger: str, trigger_context: dict, parent_run_id: Optional[int] = None) -> int:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO agent_runs (agent_name, trigger, trigger_context, parent_run_id)
                    VALUES (%s, %s, %s, %s) RETURNING run_id
                """, (agent_name, trigger, Json(trigger_context), parent_run_id))
                return cur.fetchone()[0]

    def finish(self, *, run_id: int, status: str, total_tokens: Optional[int],
               total_cost_usd: Optional[float], output_summary: Optional[str]) -> None:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE agent_runs SET finished_at = now(), status = %s,
                                          total_tokens = %s, total_cost_usd = %s,
                                          output_summary = %s
                    WHERE run_id = %s
                """, (status, total_tokens, total_cost_usd, output_summary, run_id))
```

`storage/connects_ledger.py`:

```python
from __future__ import annotations
from datetime import datetime
from typing import Optional
from storage.connection import Database


class ConnectsLedgerStore:
    def __init__(self, db: Database):
        self._db = db

    def record(self, *, delta: int, reason: str, balance_after: Optional[int],
               order_id: Optional[int]) -> None:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO connects_ledger (delta, reason, balance_after, order_id)
                    VALUES (%s, %s, %s, %s)
                """, (delta, reason, balance_after, order_id))

    def spent_in_window(self, start: datetime, end: datetime) -> int:
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT COALESCE(SUM(-delta), 0) FROM connects_ledger
                    WHERE delta < 0 AND occurred_at >= %s AND occurred_at < %s
                """, (start, end))
                return cur.fetchone()[0]
```

`storage/scrape_runs.py`:

```python
from __future__ import annotations
from typing import Optional
from storage.connection import Database


class ScrapeRunStore:
    def __init__(self, db: Database):
        self._db = db

    def start(self, *, source: str, query_id: Optional[int] = None) -> int:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO scrape_runs (source, query_id) VALUES (%s, %s) RETURNING run_id
                """, (source, query_id))
                return cur.fetchone()[0]

    def update_counts(self, run_id: int, *, jobs_seen: int, jobs_new: int, jobs_signaled: int) -> None:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE scrape_runs SET jobs_seen = %s, jobs_new = %s, jobs_signaled = %s
                    WHERE run_id = %s
                """, (jobs_seen, jobs_new, jobs_signaled, run_id))

    def finish(self, run_id: int, *, notes: Optional[str] = None) -> None:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE scrape_runs SET finished_at = now(), notes = %s WHERE run_id = %s
                """, (notes, run_id))
```

`storage/enrichments.py`:

```python
from __future__ import annotations
from typing import Optional
from psycopg.types.json import Json
from storage.connection import Database
from ai.schemas import Enrichment


class EnrichmentStore:
    def __init__(self, db: Database):
        self._db = db

    def upsert(self, job_id: str, enrichment: Enrichment, *, prompt_version: str = "2026-05-02-v1",
               raw_llm_response: Optional[dict] = None) -> None:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO job_enrichments (
                      job_id, prompt_version, extracted_tech, pain_points, red_flags, green_flags,
                      project_shape, buyer_sophistication, raw_llm_response
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (job_id) DO UPDATE SET
                      prompt_version = EXCLUDED.prompt_version,
                      extracted_tech = EXCLUDED.extracted_tech,
                      pain_points = EXCLUDED.pain_points,
                      red_flags = EXCLUDED.red_flags,
                      green_flags = EXCLUDED.green_flags,
                      project_shape = EXCLUDED.project_shape,
                      buyer_sophistication = EXCLUDED.buyer_sophistication,
                      enriched_at = now()
                """, (
                    job_id, prompt_version,
                    enrichment.extracted_tech, enrichment.pain_points,
                    enrichment.red_flags, enrichment.green_flags,
                    enrichment.project_shape, enrichment.buyer_sophistication,
                    Json(raw_llm_response) if raw_llm_response else None,
                ))

    def get(self, job_id: str) -> Optional[Enrichment]:
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT extracted_tech, pain_points, red_flags, green_flags,
                           project_shape, buyer_sophistication
                    FROM job_enrichments WHERE job_id = %s
                """, (job_id,))
                r = cur.fetchone()
                if r is None:
                    return None
        return Enrichment(
            extracted_tech=r[0] or [], pain_points=r[1] or [],
            red_flags=r[2] or [], green_flags=r[3] or [],
            project_shape=r[4], buyer_sophistication=r[5],
        )
```

Write a minimal smoke test for each (e.g. for `EnrichmentStore`: insert an Enrichment, fetch it back, assert equality). Run: `uv run pytest tests/integration/storage/ -v` — Expected: all pass.

- [ ] **Step 6: Run all storage tests**

Run: `uv run pytest tests/integration/storage/ -v`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add storage/ tests/integration/storage/
git commit -m "Implement DAL: jobs, setups, orders, portfolio, agent_runs, connects, scrape_runs, enrichments"
```

---

## Task 10: ai/ layer (single-shot create_agent calls)

**Files:**
- Create: `ai/__init__.py`
- Create: `ai/schemas.py`
- Create: `ai/cost_tracker.py`
- Create: `ai/prompts/__init__.py`
- Create: `ai/prompts/relevance.py`
- Create: `ai/prompts/enrichment.py`
- Create: `ai/prompts/proposal.py`
- Create: `ai/prompts/cover_letter.py`
- Create: `ai/prompts/screening.py`
- Create: `ai/relevance.py`
- Create: `ai/enrichment.py`
- Create: `ai/proposal_gen.py`
- Create: `tests/unit/ai/__init__.py`
- Create: `tests/unit/ai/test_schemas.py`
- Create: `tests/integration/ai/__init__.py`
- Create: `tests/integration/ai/test_relevance.py`

- [ ] **Step 1: Schemas**

Create `ai/__init__.py` (empty). Create `ai/schemas.py`:

```python
"""Pydantic models used as `response_format` in create_agent calls. One source of truth."""
from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel, Field


class RelevanceCheck(BaseModel):
    """Tie-break for the rule pre-filter."""
    relevant: bool = Field(description="Is this job actually a fit for the setup, beyond the surface rule match?")
    score: float = Field(ge=0.0, le=1.0, description="Confidence 0..1")
    reasoning: str = Field(description="One or two sentences explaining the call")


class Enrichment(BaseModel):
    extracted_tech: list[str] = Field(default_factory=list, description="Technologies/tools mentioned, normalized lowercase")
    pain_points: list[str] = Field(default_factory=list, description="What the client is struggling with")
    red_flags: list[str] = Field(default_factory=list, description="vague-spec, unrealistic-budget, scope-creep, etc.")
    green_flags: list[str] = Field(default_factory=list, description="specific outcome, named tech, etc.")
    project_shape: Optional[Literal["greenfield-build", "fix-existing", "audit", "integration", "ongoing-retainer", "prototype", "mvp", "scale-up"]] = None
    buyer_sophistication: Optional[Literal["technical-founder", "nontechnical-founder", "agency", "enterprise", "recruiter"]] = None


class ProposalDraft(BaseModel):
    """Doc body for the Google Doc proposal."""
    title: str = Field(description="6-12 word outcome line; not the raw job title")
    opener: str = Field(description="2-3 sentence opener with sharp specific insight")
    approach_phases: list[str] = Field(description="3-5 phases with bold-name + 1-2 sentences each")
    deliverables: list[str] = Field(description="3-6 concrete deliverables")
    timeline: list[str] = Field(description="3-5 week-by-week or phase-by-phase bullets")
    questions: list[str] = Field(description="2-3 sharp clarifying questions")
    mermaid_diagram: str = Field(description="A complete mermaid graph definition; never empty")
    about_me: str = Field(description="100-150 words tailored to job, picking 2-3 most relevant past projects")


class CoverLetter(BaseModel):
    """The 35-word Discord/Upwork cover letter that links to the Doc."""
    body: str = Field(description="The full cover letter text including the Doc URL placeholder {{doc_url}}")


class ScreeningAnswer(BaseModel):
    answer: str = Field(description="A direct, conversational answer to a screening question")
```

- [ ] **Step 2: Validate schemas**

Create `tests/unit/ai/__init__.py` (empty).
Create `tests/unit/ai/test_schemas.py`:

```python
from ai.schemas import RelevanceCheck, Enrichment, ProposalDraft, CoverLetter

def test_relevance_check_round_trip():
    r = RelevanceCheck(relevant=True, score=0.85, reasoning="strong fit")
    assert r.relevant is True
    assert r.score == 0.85

def test_enrichment_defaults():
    e = Enrichment()
    assert e.extracted_tech == []
    assert e.project_shape is None

def test_proposal_draft_required_fields():
    p = ProposalDraft(
        title="Build it",
        opener="hey",
        approach_phases=["a", "b", "c"],
        deliverables=["d"],
        timeline=["w1"],
        questions=["q?"],
        mermaid_diagram="graph LR\nA-->B",
        about_me="me",
    )
    assert p.title == "Build it"
```

Run: `uv run pytest tests/unit/ai/test_schemas.py -v`
Expected: 3 passed.

- [ ] **Step 3: Cost tracker callback**

Create `ai/cost_tracker.py`:

```python
"""LangChain callback that records every LLM call into agent_runs."""
from __future__ import annotations

from typing import Any, Optional

from langchain_core.callbacks import BaseCallbackHandler
from storage.agent_runs import AgentRunStore


class CostTracker(BaseCallbackHandler):
    def __init__(self, store: AgentRunStore, agent_name: str, trigger: str, trigger_context: dict, parent_run_id: Optional[int] = None):
        self._store = store
        self._agent_name = agent_name
        self._trigger = trigger
        self._trigger_context = trigger_context
        self._parent = parent_run_id
        self._run_id: Optional[int] = None
        self._steps: list[dict] = []
        self._total_tokens = 0
        self._total_cost = 0.0

    def __enter__(self) -> "CostTracker":
        self._run_id = self._store.start(
            agent_name=self._agent_name,
            trigger=self._trigger,
            trigger_context=self._trigger_context,
            parent_run_id=self._parent,
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        status = "succeeded" if exc is None else "failed"
        self._store.finish(
            run_id=self._run_id,
            status=status,
            total_tokens=self._total_tokens,
            total_cost_usd=self._total_cost,
            output_summary=None,
        )

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        usage = getattr(response, "llm_output", None) or {}
        token_usage = usage.get("token_usage", {}) if isinstance(usage, dict) else {}
        self._total_tokens += int(token_usage.get("total_tokens", 0) or 0)
        # Cost calculation deferred to Phase 2 once we wire model-name -> $/token table

    @property
    def run_id(self) -> int:
        assert self._run_id is not None
        return self._run_id
```

- [ ] **Step 4: Prompt strings**

Create `ai/prompts/__init__.py` (empty).

Create `ai/prompts/relevance.py`:

```python
RELEVANCE_SYSTEM = """You judge whether a freelance job posting is actually a good fit for a defined setup, beyond the surface rule match.

The setup describes the kind of work we want. The job has matched the rules but you decide if it's a real fit:
- a fresh client posting their first job at $1000 to "build me an AI" is NOT a real fit even if rules matched.
- a sophisticated buyer with a clear scoped problem at the budget floor IS a real fit.

Be strict. Only return relevant=true if you'd want a senior engineer to actually pitch this.
"""

RELEVANCE_USER = """SETUP: {setup_name}
SETUP DESCRIPTION: {setup_prose}

JOB:
Title: {title}
Budget: {budget_text}
Posted: {posted_text}
Client: {client_summary}
Skills: {skills}

Description:
{description}

Decide.
"""
```

Create `ai/prompts/enrichment.py`:

```python
ENRICHMENT_SYSTEM = """You extract structured data from an Upwork job posting. Return a JSON object matching the Enrichment schema. Be specific and conservative — empty arrays are fine if signals are absent."""

ENRICHMENT_USER = """JOB POSTING:
Title: {title}
Budget: {budget_text}
Skills: {skills}

Description:
{description}
"""

ENRICHMENT_PROMPT_VERSION = "2026-05-02-v1"
```

Create `ai/prompts/proposal.py`, `ai/prompts/cover_letter.py`, `ai/prompts/screening.py` by porting the existing system prompts from `proposal.py` (the legacy module). Specifically:

- `proposal.py` legacy has `DOC_PROPOSAL_SYSTEM` → goes into `ai/prompts/proposal.py` as `PROPOSAL_SYSTEM` and `PROPOSAL_USER` (with the mermaid + ban-list + voice rules from CLAUDE.md preserved verbatim).
- `proposal.py` legacy has `ABOUT_ME_SYSTEM` → goes into a separate constant, called as part of the ProposalDraft generation (single `create_agent` call returning ProposalDraft can include both — adjust the `about_me` field to be filled in the same response).

Cover letter prompt (from CLAUDE.md, locked formula):

```python
COVER_LETTER_SYSTEM = """You write a 35-word Upwork cover letter. The formula is locked:

"Hey [Name], I spent some time going over your job description.
Here's how I would approach it: {{doc_url}}
Reply with a good time and we can hop on a 20-minute call.
- Moazzam"

If client name is not detected, drop the comma+name and use "Hey,". The {{doc_url}} placeholder MUST be present verbatim — do not fill it.

NO em-dashes. NO emojis. NO marketing phrases. Conversational, direct."""

COVER_LETTER_USER = """JOB:
Title: {title}
Detected client name (if any): {client_name}

Generate the cover letter."""
```

- [ ] **Step 5: Implement ai/relevance.py**

Create `ai/relevance.py`:

```python
"""Relevance tie-break: single create_agent call with structured output."""
from __future__ import annotations

from langchain.agents import create_agent

from ai.schemas import RelevanceCheck
from ai.prompts.relevance import RELEVANCE_SYSTEM, RELEVANCE_USER
from ai.cost_tracker import CostTracker
from storage.agent_runs import AgentRunStore
from domain.types import Job, Setup


def check_relevance(
    job: Job,
    setup: Setup,
    *,
    agent_run_store: AgentRunStore,
    parent_run_id: int | None = None,
    model: str = "gpt-4o-mini",
) -> RelevanceCheck:
    user = RELEVANCE_USER.format(
        setup_name=setup.name,
        setup_prose=setup.prose_definition or "(no prose definition)",
        title=job.title,
        budget_text=_budget_text(job),
        posted_text="recent",
        client_summary=_client_summary(job),
        skills=", ".join(job.skills or []),
        description=(job.description or "")[:3000],
    )
    with CostTracker(
        agent_run_store,
        agent_name="relevance",
        trigger="per_job",
        trigger_context={"job_id": job.job_id, "setup_id": setup.setup_id},
        parent_run_id=parent_run_id,
    ) as tracker:
        agent = create_agent(model=model, response_format=RelevanceCheck)
        result = agent.invoke(
            {"messages": [
                {"role": "system", "content": RELEVANCE_SYSTEM},
                {"role": "user", "content": user},
            ]},
            config={"callbacks": [tracker]},
        )
    return result["structured_response"]


def _budget_text(job: Job) -> str:
    if job.budget_min_usd is None and job.budget_max_usd is None:
        return "unknown"
    if job.budget_min_usd == job.budget_max_usd or job.budget_max_usd is None:
        return f"{job.budget_kind} ${job.budget_min_usd:.0f}"
    return f"{job.budget_kind} ${job.budget_min_usd:.0f}-${job.budget_max_usd:.0f}"


def _client_summary(job: Job) -> str:
    parts = []
    if job.client_country:
        parts.append(job.client_country)
    if job.client_payment_verified:
        parts.append("payment verified")
    if job.client_total_spent_usd:
        parts.append(f"${job.client_total_spent_usd:.0f} spent")
    return ", ".join(parts) or "unknown"
```

- [ ] **Step 6: Implement ai/enrichment.py and ai/proposal_gen.py**

`ai/enrichment.py` — same shape as `relevance.py`, returning `Enrichment` schema, called once per scraped job, persisted via `EnrichmentStore`.

`ai/proposal_gen.py` — two `create_agent` calls (or one bigger one returning `ProposalDraft` which already includes `about_me`):

```python
def generate_proposal(
    job: Job,
    portfolio_items: list,
    *,
    agent_run_store: AgentRunStore,
    parent_run_id: int | None = None,
    model: str = "gpt-4o",
) -> ProposalDraft:
    ...

def generate_cover_letter(
    job: Job,
    detected_client_name: str | None,
    *,
    agent_run_store: AgentRunStore,
    parent_run_id: int | None = None,
    model: str = "gpt-4o",
) -> CoverLetter:
    ...
```

Port the prompt content from the legacy `proposal.py`.

- [ ] **Step 7: Integration test for relevance (real LLM)**

Create `tests/integration/ai/__init__.py` (empty).
Create `tests/integration/ai/test_relevance.py`:

```python
"""Hits real OpenAI. Skipped if OPENAI_API_KEY missing."""
import os
import pytest
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from storage.migrate import apply_migrations
from storage.agent_runs import AgentRunStore
from ai.relevance import check_relevance
from domain.types import Job, Setup, FilterDsl

pytestmark = pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="needs OPENAI_API_KEY")
MIG = Path(__file__).parents[3] / "storage" / "migrations"


@pytest.fixture
def fresh_db(db):
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        conn.commit()
    apply_migrations(db, MIG)
    return db


def test_relevance_call_completes_and_records_run(fresh_db):
    job = Job(
        job_id="~test1", url="https://x", title="Build a RAG eval pipeline",
        description="We have a RAG system in production with retrieval recall around 60%. Need someone to build evaluation harness with golden datasets.",
        budget_kind="fixed", budget_min_usd=5000.0, budget_max_usd=8000.0,
        skills=["RAG", "Pinecone", "Python"],
        client_payment_verified=True, client_country="United States",
    )
    setup = Setup(
        setup_id=1, name="rag-eval-shops", status="active", tier="normal",
        filter_dsl=FilterDsl({"all_of": []}),
        prose_definition="We bid on RAG evaluation work for technical buyers with real production systems.",
        pitch_template_id=None, cover_letter_template_id=None,
        auto_apply_enabled=False, escalation_config={},
    )
    runs = AgentRunStore(fresh_db)
    result = check_relevance(job, setup, agent_run_store=runs)
    assert isinstance(result.relevant, bool)
    assert 0.0 <= result.score <= 1.0
    assert result.reasoning
    # Run was recorded
    with fresh_db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*), MAX(agent_name) FROM agent_runs")
            count, name = cur.fetchone()
            assert count == 1
            assert name == "relevance"
```

- [ ] **Step 8: Run AI tests**

Run: `uv run pytest tests/unit/ai/ tests/integration/ai/ -v`
Expected: unit pass; integration pass (or skip if no OPENAI_API_KEY).

- [ ] **Step 9: Commit**

```bash
git add ai/ tests/unit/ai/ tests/integration/ai/
git commit -m "ai/ layer: schemas, cost tracker, prompts, single-shot create_agent calls"
```

---

## Task 11: bidder/, scheduler/, bot/ — wiring + Discord integration

This is the largest task. It wires the layers into the running system.

**Files:**
- Create: `bidder/__init__.py`, `bidder/scan_cycle.py`, `bidder/signal_pipeline.py`, `bidder/draft_pipeline.py`, `bidder/apply_executor.py`
- Create: `scheduler/main.py`, `scheduler/failure_pings.py`
- Create: `bot/__init__.py`, `bot/bot.py`, `bot/alerts.py`, `bot/commands.py`, `bot/interaction_handler.py`, `bot/escalation.py`
- Create: `bin/import_seed_setup.py` (one-time: insert the manual seed setup)
- Create: `bin/portfolio_import.py` (one-time: portfolio.json → DB)
- Create: `bin/portfolio_add.py` (CLI: add portfolio item)

- [ ] **Step 1: Implement scheduler/failure_pings.py**

Create `scheduler/failure_pings.py`:

```python
"""Typed errors that route to Discord templates."""
from __future__ import annotations


class SystemFailure(Exception):
    template: str = "Unknown failure: {detail}"
    def __init__(self, detail: str = ""):
        super().__init__(detail)
        self.detail = detail


class LoginExpired(SystemFailure):
    template = "🔐 Upwork login expired. Need re-auth: {detail}"


class CloudflareWall(SystemFailure):
    template = "🛡️ Cloudflare wall hit on {detail}"


class OpenAIQuotaExceeded(SystemFailure):
    template = "💸 OpenAI quota exceeded: {detail}"


class ComposioDown(SystemFailure):
    template = "📄 Composio request failed: {detail}"


class PostgresUnavailable(SystemFailure):
    template = "🗄️ Postgres unavailable: {detail}"


class ConnectsExhausted(SystemFailure):
    template = "🪙 Connects cap reached: {detail}"


class OrderAlreadySubmitted(SystemFailure):
    template = "🔁 Order already submitted (idempotency): {detail}"


class VisionModelFailed(SystemFailure):
    template = "👁️ Vision model failed: {detail}"


class ScreeningQuestionUnanswerable(SystemFailure):
    template = "❓ Couldn't answer screening question: {detail}"


class ApplyFormChanged(SystemFailure):
    template = "🧱 Apply form structure shifted, parser needs update: {detail}"
```

- [ ] **Step 2: Implement bidder/signal_pipeline.py**

Create `bidder/__init__.py` (empty).

Create `bidder/signal_pipeline.py`:

```python
"""Job → enrich → score → (if matches setup) signal + draft Order."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from domain.types import Job, Setup, Signal, Order, MatchResult
from domain.scoring import score_job_against_setup
from storage.jobs import JobStore
from storage.setups import SetupStore, SignalStore
from storage.orders import OrderStore
from storage.enrichments import EnrichmentStore
from storage.agent_runs import AgentRunStore
from ai.enrichment import enrich_job
from ai.relevance import check_relevance


def process_job_through_setups(
    job: Job,
    *,
    setups: list[Setup],
    job_store: JobStore,
    enrichment_store: EnrichmentStore,
    signal_store: SignalStore,
    order_store: OrderStore,
    agent_run_store: AgentRunStore,
) -> Optional[tuple[Signal, Order]]:
    """For one job: enrich, then score against each active setup, return Signal+Order if any match.

    Returns None if no setup matched. Signal+Order are written to DB; Order is in 'drafting' status.
    """
    # 1. Enrich (always, for corpus value) — only if not yet enriched
    if enrichment_store.get(job.job_id) is None:
        enrichment = enrich_job(job, agent_run_store=agent_run_store)
        enrichment_store.upsert(job.job_id, enrichment)

    # 2. Score against each active setup
    matches: list[tuple[Setup, MatchResult]] = []
    for setup in setups:
        result = score_job_against_setup(job, setup)
        if result.matched:
            # 2a. LLM tie-break for ambiguous cases (Phase 1: always run if rules matched)
            relevance = check_relevance(job, setup, agent_run_store=agent_run_store)
            if relevance.relevant:
                matches.append((setup, result))

    if not matches:
        return None

    # 3. Pick primary setup: highest tier (critical > normal > quiet), tie-break by id
    TIER_ORDER = {"critical": 0, "normal": 1, "quiet": 2}
    matches.sort(key=lambda m: (TIER_ORDER[m[0].tier], m[0].setup_id))
    primary_setup = matches[0][0]

    matched_setups_payload = [
        {"setup_id": s.setup_id, "match_reason": "rule+llm", "matched_rules": r.matched_rules}
        for s, r in matches
    ]

    # 4. Write Signal
    signal = Signal(
        signal_id=None,
        job_id=job.job_id,
        primary_setup_id=primary_setup.setup_id,
        matched_setups=matched_setups_payload,
        fired_at=datetime.now(timezone.utc),
        market_state={
            "proposals_count": job.proposals_count_at_first_scrape,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    signal_id = signal_store.create(signal)
    signal.signal_id = signal_id

    # 5. Create draft Order
    order = Order(
        order_id=None,
        signal_id=signal_id,
        job_id=job.job_id,
        setup_id=primary_setup.setup_id,
        status="drafting",
        bid_amount_usd=None,
        connects_spent=None,
        cover_letter_body=None,
        doc_url=None,
        screening_answers_json=None,
        drafted_at=None,
        approved_at=None,
        submitted_at=None,
        failed_reason=None,
        idempotency_key=str(uuid.uuid4()),
    )
    order_id = order_store.create_draft(order)
    order.order_id = order_id

    return signal, order
```

(`OrderStore.create_signal` is added in Task 9 — make sure that's in the OrderStore interface.)

- [ ] **Step 3: Implement bidder/draft_pipeline.py**

```python
"""Take a draft Order; generate Doc + cover letter; update Order to awaiting_approval."""
from __future__ import annotations

from external import gdocs, mermaid
from ai.proposal_gen import generate_proposal, generate_cover_letter
from storage.orders import OrderStore
from storage.portfolio import PortfolioStore
from storage.agent_runs import AgentRunStore
from domain.types import Job, Order


def draft_order(
    job: Job,
    order: Order,
    *,
    portfolio: PortfolioStore,
    order_store: OrderStore,
    agent_run_store: AgentRunStore,
) -> Order:
    portfolio_items = portfolio.list_matching_tags(job.skills or [])
    proposal = generate_proposal(job, portfolio_items, agent_run_store=agent_run_store)
    # Render mermaid → upload Drive → create Doc → embed image
    doc_url = gdocs.create_doc_with_diagram(proposal, mermaid_source=proposal.mermaid_diagram)
    cover_letter = generate_cover_letter(job, detected_client_name=None, agent_run_store=agent_run_store)
    body = cover_letter.body.replace("{{doc_url}}", doc_url)
    order_store.set_drafted(
        order_id=order.order_id,
        cover_letter_body=body,
        doc_url=doc_url,
        screening_answers_json=None,  # filled when apply page is opened
    )
    order_store.update_status(order.order_id, "awaiting_approval")
    order.status = "awaiting_approval"
    order.cover_letter_body = body
    order.doc_url = doc_url
    return order
```

(`gdocs.create_doc_with_diagram` is the new public façade over the legacy `gdocs.py` workflow — implement as a thin wrapper that calls the existing functions in the right sequence.)

- [ ] **Step 4: Implement bidder/scan_cycle.py**

```python
"""One scan-cycle of the feed."""
from __future__ import annotations

from substrate import act
from upwork import feed, panel, clipboard_url
from storage.jobs import JobStore
from storage.setups import SetupStore, SignalStore
from storage.orders import OrderStore
from storage.enrichments import EnrichmentStore
from storage.portfolio import PortfolioStore
from storage.agent_runs import AgentRunStore
from storage.scrape_runs import ScrapeRunStore
from domain.humanization import Humanizer, CycleType
from bidder.signal_pipeline import process_job_through_setups
from bidder.draft_pipeline import draft_order


WINDOW = "Upwork"


def run_one_cycle(
    *,
    humanizer: Humanizer,
    setups_store: SetupStore,
    signal_store: SignalStore,
    job_store: JobStore,
    order_store: OrderStore,
    enrichment_store: EnrichmentStore,
    portfolio: PortfolioStore,
    agent_runs: AgentRunStore,
    scrape_runs: ScrapeRunStore,
    on_signal,                        # callback(signal, order, job) for Discord
) -> None:
    cycle_type = humanizer.sample_cycle_type()
    run_id = scrape_runs.start(source=f"feed:{cycle_type.value}")

    if cycle_type == CycleType.NO_OP:
        scrape_runs.finish(run_id, notes="no-op cycle")
        return

    feed.refresh_feed(WINDOW)
    feed.click_most_recent_tab(WINDOW)
    cards = feed.top_of_feed_cards(WINDOW, max_cards=10)

    if cycle_type == CycleType.SKIM_ONLY:
        scrape_runs.update_counts(run_id, jobs_seen=len(cards), jobs_new=0, jobs_signaled=0)
        scrape_runs.finish(run_id, notes="skim only")
        return

    n_panels = 2 if cycle_type == CycleType.PANEL_SKIM else len(cards)
    setups = setups_store.list_active()

    seen, new, signaled = 0, 0, 0
    for card in cards[:n_panels]:
        seen += 1
        if not feed.open_card_panel(card):
            continue
        elements = panel_observe_elements(WINDOW)   # see helper below
        url = clipboard_url.capture_url_from_open_panel(WINDOW)
        feed.close_panel(WINDOW)
        if url is None:
            continue
        job_id = _job_id_from_url(url)
        if job_store.is_known(job_id):
            continue
        new += 1
        parsed = panel.parse_panel(elements)
        job = _to_job(job_id, url, parsed)
        job_store.upsert(job, source="feed", raw_panel={})

        if cycle_type == CycleType.PANEL_SKIM:
            continue  # skim cycles never signal

        result = process_job_through_setups(
            job, setups=setups, job_store=job_store, enrichment_store=enrichment_store,
            signal_store=signal_store, order_store=order_store, agent_run_store=agent_runs,
        )
        if result is None:
            continue
        signal, order = result
        order = draft_order(
            job, order, portfolio=portfolio, order_store=order_store, agent_run_store=agent_runs,
        )
        signaled += 1
        on_signal(signal, order, job)

    scrape_runs.update_counts(run_id, jobs_seen=seen, jobs_new=new, jobs_signaled=signaled)
    scrape_runs.finish(run_id, notes=cycle_type.value)


def panel_observe_elements(window_title: str):
    from substrate import observe
    return observe.observe(window_title=window_title, include_unnamed=True).elements


def _job_id_from_url(url: str) -> str:
    # Upwork URLs look like .../jobs/Some-Title_~012345abcdef/
    import re
    m = re.search(r"_~([\w]+)", url)
    return m.group(1) if m else url


def _to_job(job_id: str, url: str, parsed) -> "Job":
    from domain.types import Job
    return Job(
        job_id=job_id,
        url=url,
        title=parsed.title,
        description=parsed.description,
        budget_kind=parsed.budget_kind,
        budget_min_usd=parsed.budget_min_usd,
        budget_max_usd=parsed.budget_max_usd,
        skills=parsed.skills,
        client_country=parsed.client_country,
        client_payment_verified=parsed.client_payment_verified,
    )
```

- [ ] **Step 5: Implement bidder/apply_executor.py**

```python
"""Take an approved Order; run the apply form sequence; mark submitted/failed."""
from __future__ import annotations

import time
from datetime import datetime, timezone

from substrate import act
from upwork import apply_form
from storage.orders import OrderStore
from storage.connects_ledger import ConnectsLedgerStore
from domain.humanization import Humanizer
from domain.types import Order
from scheduler.failure_pings import (
    LoginExpired, CloudflareWall, ApplyFormChanged, OrderAlreadySubmitted, ConnectsExhausted,
)


def execute_approved_order(
    order: Order,
    job_url: str,
    *,
    order_store: OrderStore,
    connects_ledger: ConnectsLedgerStore,
    humanizer: Humanizer,
    bid_amount_usd: float,
    connects_cost: int = 16,
    really_submit: bool = False,
) -> None:
    if order.status == "submitted":
        raise OrderAlreadySubmitted(f"order {order.order_id}")

    order_store.update_status(order.order_id, "staging")
    apply_form.navigate_to_apply(window_title="Upwork", job_url=job_url)
    state = apply_form.wait_for_form_or_login(window_title="Upwork", timeout_s=30)
    if state == "login_required":
        order_store.update_status(order.order_id, "failed")
        raise LoginExpired(f"navigated to {job_url}")
    if state == "cloudflare":
        order_store.update_status(order.order_id, "failed")
        raise CloudflareWall(f"navigated to {job_url}")

    pauses = humanizer.sample_apply_form_pauses()
    time.sleep(pauses["initial_idle"])
    apply_form.paste_cover_letter(window_title="Upwork", text=order.cover_letter_body or "")
    time.sleep(pauses["after_paste"])

    if order.screening_answers_json:
        time.sleep(pauses["before_screening"])
        apply_form.answer_screening_questions(window_title="Upwork", answers=order.screening_answers_json)

    apply_form.select_never_for_rate_increase(window_title="Upwork")
    apply_form.fill_bid_amount(window_title="Upwork", amount_usd=bid_amount_usd)
    time.sleep(pauses["final_review"])

    order_store.update_status(order.order_id, "attempting")

    if not really_submit:
        # Phase 1 default: stop short of submit, leave form on screen for user
        order_store.update_status(order.order_id, "awaiting_approval")
        order.status = "awaiting_approval"
        order.failed_reason = "really_submit=False (Phase 1 default)"
        return

    apply_form.submit_proposal(window_title="Upwork")
    order_store.mark_submitted(order.order_id)
    connects_ledger.record(delta=-connects_cost, reason=f"order {order.order_id}", balance_after=None, order_id=order.order_id)
```

- [ ] **Step 6: Implement bot/bot.py**

```python
"""Discord bot entry point. Builds the bot, registers commands, runs gateway."""
from __future__ import annotations

import asyncio
import discord
from discord.ext import commands
from discord import app_commands

from scheduler.config import Settings
from storage.connection import Database


def build_bot(settings: Settings) -> commands.Bot:
    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix="!", intents=intents)
    return bot


async def run_bot(settings: Settings, db: Database, on_ready):
    bot = build_bot(settings)

    @bot.event
    async def on_ready():
        print(f"Bot connected as {bot.user}")
        # Sync slash commands
        await bot.tree.sync()
        await on_ready_callback(bot)

    on_ready_callback = on_ready

    # Register commands and view handlers
    from bot.commands import register_commands
    from bot.interaction_handler import register_views
    register_commands(bot, db, settings)
    register_views(bot, db, settings)

    await bot.start(settings.discord_bot_token)
```

- [ ] **Step 7: Implement bot/alerts.py**

```python
"""Rich signal alerts with Apply/Skip buttons."""
from __future__ import annotations

import discord
from discord.ui import View, Button


class OrderApprovalView(View):
    def __init__(self, order_id: int, doc_url: str, timeout: float | None = None):
        super().__init__(timeout=timeout)
        self.order_id = order_id
        # discord.py 2.x: dynamic button labels
        self.apply_btn = Button(label="Apply ✅", style=discord.ButtonStyle.success, custom_id=f"apply:{order_id}")
        self.skip_btn = Button(label="Skip ❌", style=discord.ButtonStyle.danger, custom_id=f"skip:{order_id}")
        self.doc_btn = Button(label="View Doc 📄", style=discord.ButtonStyle.link, url=doc_url)
        self.add_item(self.apply_btn)
        self.add_item(self.skip_btn)
        self.add_item(self.doc_btn)


def build_signal_embed(*, setup_name: str, tier: str, title: str, budget_text: str,
                       posted_text: str, client_summary: str, why_matched: str,
                       cover_letter_preview: str) -> discord.Embed:
    embed = discord.Embed(
        title=f"📡 Signal: '{setup_name}' (tier: {tier})",
        description=title,
        color=0x00C853 if tier == "critical" else 0x2962FF,
    )
    embed.add_field(name="Budget", value=budget_text, inline=True)
    embed.add_field(name="Posted", value=posted_text, inline=True)
    embed.add_field(name="Client", value=client_summary, inline=False)
    embed.add_field(name="Why matched", value=why_matched, inline=False)
    embed.add_field(name="Cover letter preview", value=cover_letter_preview[:1000], inline=False)
    return embed
```

- [ ] **Step 8: Implement bot/interaction_handler.py**

```python
"""Wire button click events to Order state changes."""
from __future__ import annotations

import discord
from discord.ext import commands
from storage.connection import Database
from storage.orders import OrderStore
from scheduler.config import Settings


def register_views(bot: commands.Bot, db: Database, settings: Settings):
    @bot.event
    async def on_interaction(interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        custom_id = interaction.data.get("custom_id", "")
        if ":" not in custom_id:
            return
        action, order_id_str = custom_id.split(":", 1)
        order_id = int(order_id_str)
        store = OrderStore(db)

        if action == "apply":
            store.update_status(order_id, "approved")
            await interaction.response.send_message(
                f"✅ Order {order_id} approved. Apply executor will pick it up.",
                ephemeral=True,
            )
        elif action == "skip":
            store.update_status(order_id, "cancelled")
            await interaction.response.send_message(
                f"❌ Order {order_id} skipped.",
                ephemeral=True,
            )
```

- [ ] **Step 9: Implement bot/commands.py (slash commands)**

```python
"""Phase 1 slash commands: /queue, /cancel, /applied, /connects, /health."""
from __future__ import annotations

from datetime import datetime, timezone
import discord
from discord import app_commands
from discord.ext import commands

from storage.connection import Database
from storage.orders import OrderStore, OutcomeEventStore
from storage.connects_ledger import ConnectsLedgerStore
from scheduler.config import Settings


def register_commands(bot: commands.Bot, db: Database, settings: Settings):

    @bot.tree.command(description="Show orders awaiting approval")
    async def queue(interaction: discord.Interaction):
        store = OrderStore(db)
        orders = store.list_by_status("awaiting_approval")
        if not orders:
            await interaction.response.send_message("Queue is empty.")
            return
        lines = [f"#{o.order_id} — setup={o.setup_id} — drafted at {o.drafted_at}" for o in orders]
        await interaction.response.send_message("Awaiting approval:\n" + "\n".join(lines))

    @bot.tree.command(description="Cancel an order before submission")
    @app_commands.describe(order_id="Order ID")
    async def cancel(interaction: discord.Interaction, order_id: int):
        store = OrderStore(db)
        order = store.get(order_id)
        if order is None:
            await interaction.response.send_message(f"Order {order_id} not found.", ephemeral=True)
            return
        if order.status in ("submitted",):
            await interaction.response.send_message(f"Order {order_id} already submitted, cannot cancel.", ephemeral=True)
            return
        store.update_status(order_id, "cancelled")
        await interaction.response.send_message(f"Cancelled order {order_id}.")

    @bot.tree.command(description="Record an outcome event for an order")
    @app_commands.describe(order_id="Order ID", event="One of: viewed, replied, interviewed, hired, declined, ghosted")
    async def applied(interaction: discord.Interaction, order_id: int, event: str):
        ALLOWED = {"viewed", "replied", "interviewed", "hired", "declined", "ghosted"}
        if event not in ALLOWED:
            await interaction.response.send_message(f"event must be one of {ALLOWED}", ephemeral=True)
            return
        oe = OutcomeEventStore(db)
        oe.record(order_id=order_id, event_type=event, source="discord_manual", notes=None)
        await interaction.response.send_message(f"Recorded {event} for order {order_id}.")

    @bot.tree.command(description="Show this week's Connects spend vs cap")
    async def connects(interaction: discord.Interaction):
        store = OrderStore(db)
        now = datetime.now(timezone.utc)
        today_count = store.count_submitted_today(now)
        week_count = store.count_submitted_this_week(now)
        await interaction.response.send_message(
            f"Today: {today_count}/{settings.connects_daily_cap}  |  "
            f"This week: {week_count}/{settings.connects_weekly_cap}"
        )

    @bot.tree.command(description="System health: scheduler tick, DB, OpenAI quota")
    async def health(interaction: discord.Interaction):
        # Phase 1: just verify DB reachable
        try:
            with db.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.fetchone()
            await interaction.response.send_message("✅ DB reachable. Scheduler status: see logs.")
        except Exception as e:
            await interaction.response.send_message(f"❌ DB error: {e}")
```

- [ ] **Step 10: Implement scheduler/main.py**

```python
"""Top-level. Builds the graph, runs the Bidder loop + the Discord bot in parallel."""
from __future__ import annotations

import asyncio
import random
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

import discord
from scheduler.config import Settings
from storage.connection import Database
from storage.jobs import JobStore
from storage.setups import SetupStore, SignalStore
from storage.orders import OrderStore, OutcomeEventStore
from storage.enrichments import EnrichmentStore
from storage.portfolio import PortfolioStore
from storage.agent_runs import AgentRunStore
from storage.scrape_runs import ScrapeRunStore
from storage.connects_ledger import ConnectsLedgerStore
from domain.humanization import Humanizer, default_envelope
from domain.risk import RiskCaps, OrderTimestamps, can_submit_order
from bidder.scan_cycle import run_one_cycle
from bidder.apply_executor import execute_approved_order
from bot.bot import build_bot
from bot.commands import register_commands
from bot.interaction_handler import register_views
from bot.alerts import build_signal_embed, OrderApprovalView


async def bidder_loop(bot, settings: Settings, db: Database, humanizer: Humanizer):
    # Wait for bot ready
    await bot.wait_until_ready()
    channel = bot.get_channel(settings.discord_channel_id)

    setups_store = SetupStore(db)
    signal_store = SignalStore(db)
    job_store = JobStore(db)
    order_store = OrderStore(db)
    enrichment_store = EnrichmentStore(db)
    portfolio_store = PortfolioStore(db)
    agent_runs = AgentRunStore(db)
    scrape_runs = ScrapeRunStore(db)
    connects = ConnectsLedgerStore(db)
    risk_caps = RiskCaps(daily=settings.connects_daily_cap, weekly=settings.connects_weekly_cap)

    while True:
        now = datetime.now(timezone.utc)
        if not humanizer.is_active_now(now):
            interval = humanizer.sample_scan_interval(active=False)
            await asyncio.sleep(interval)
            continue

        async def on_signal(signal, order, job):
            setup = setups_store.get(signal.primary_setup_id)
            embed = build_signal_embed(
                setup_name=setup.name, tier=setup.tier, title=job.title,
                budget_text=f"{job.budget_kind} ${job.budget_min_usd or 0:.0f}",
                posted_text="recent", client_summary=job.client_country or "?",
                why_matched=", ".join([m["matched_rules"][0] if m.get("matched_rules") else "" for m in signal.matched_setups]),
                cover_letter_preview=order.cover_letter_body or "",
            )
            view = OrderApprovalView(order_id=order.order_id, doc_url=order.doc_url or "")
            await channel.send(embed=embed, view=view)

        # Need to dispatch on_signal across the asyncio boundary; for Phase 1 keep it simple:
        # run_one_cycle is synchronous, schedule the alert via bot.loop.create_task in the callback.
        def sync_on_signal(signal, order, job):
            asyncio.run_coroutine_threadsafe(on_signal(signal, order, job), bot.loop)

        try:
            await asyncio.to_thread(
                run_one_cycle,
                humanizer=humanizer,
                setups_store=setups_store,
                signal_store=signal_store,
                job_store=job_store,
                order_store=order_store,
                enrichment_store=enrichment_store,
                portfolio=portfolio_store,
                agent_runs=agent_runs,
                scrape_runs=scrape_runs,
                on_signal=sync_on_signal,
            )
        except Exception as e:
            await channel.send(f"⚠️ Bidder cycle failed: {e!r}")

        interval = humanizer.sample_scan_interval(active=True)
        await asyncio.sleep(interval)


async def apply_executor_loop(bot, settings: Settings, db: Database, humanizer: Humanizer):
    """Polls for approved orders and runs them."""
    await bot.wait_until_ready()
    channel = bot.get_channel(settings.discord_channel_id)
    order_store = OrderStore(db)
    connects = ConnectsLedgerStore(db)
    job_store = JobStore(db)

    while True:
        approved = order_store.list_by_status("approved")
        for order in approved:
            job = job_store.get(order.job_id)
            try:
                # Phase 1: never auto-submit. Stage form, leave for human, ping Discord.
                await asyncio.to_thread(
                    execute_approved_order,
                    order, job.url,
                    order_store=order_store, connects_ledger=connects,
                    humanizer=humanizer, bid_amount_usd=order.bid_amount_usd or 100.0,
                    really_submit=False,
                )
                await channel.send(f"🟡 Order #{order.order_id} staged on apply page. Click Submit manually.")
            except Exception as e:
                await channel.send(f"⚠️ Apply executor failed for order #{order.order_id}: {e!r}")
        await asyncio.sleep(20)


async def main():
    settings = Settings.from_env()
    db = Database(settings.database_url)
    humanizer = Humanizer(rng=random.Random(), envelope=default_envelope())

    bot = build_bot(settings)
    register_commands(bot, db, settings)
    register_views(bot, db, settings)

    bot.loop.create_task(bidder_loop(bot, settings, db, humanizer))
    bot.loop.create_task(apply_executor_loop(bot, settings, db, humanizer))

    await bot.start(settings.discord_bot_token)


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 11: Seed scripts**

Create `bin/portfolio_import.py`:

```python
"""One-time: portfolio.json → portfolio_items table. Idempotent (checks name uniqueness)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from scheduler.config import Settings
from storage.connection import Database
from storage.portfolio import PortfolioStore


def main() -> int:
    settings = Settings.from_env()
    db = Database(settings.database_url)
    store = PortfolioStore(db)

    portfolio_path = Path("portfolio.json")
    data = json.loads(portfolio_path.read_text(encoding="utf-8"))
    existing = {p["name"] for p in store.list_all()}
    inserted = 0
    for project in data["projects"]:
        if project["name"] in existing:
            continue
        # tech in portfolio.json is a freeform sentence, not a list — store as single-element array
        # so the portfolio_items.tech column is non-empty and queryable.
        tech_field = project.get("tech")
        tech_array = [tech_field] if isinstance(tech_field, str) else (tech_field or [])
        store.add(
            name=project["name"],
            summary=None,
            client_context=project["client_context"],
            outcome=project["outcome"],
            tech=tech_array,
            relevance_tags=project.get("relevance_tags", []),
            year_completed=None,
        )
        inserted += 1
    print(f"Inserted {inserted} portfolio items.")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Create `bin/import_seed_setup.py`:

```python
"""One-time: insert the manual seed setup so the Bidder has something to match against in Phase 1.

The seed setup is wide-net: matches on any of Moazzam's strike-zone skills, requires payment-verified
clients and budget ≥ $1500. Tier=normal, escalation=quiet, auto_apply=disabled.
"""
from __future__ import annotations

import json
import sys
from dotenv import load_dotenv

load_dotenv()

from scheduler.config import Settings
from storage.connection import Database
from storage.setups import SetupStore
from domain.types import FilterDsl, Setup


SEED_SETUP_NAME = "wide-net-ai-strike-zone"

SEED_FILTER = {
    "all_of": [
        {"skill_in": [
            "ai-agents", "ai agents", "agents",
            "rag", "agentic-rag", "agentic rag",
            "langchain", "langgraph",
            "openai", "claude", "anthropic", "gemini",
            "mcp", "model context protocol",
            "voice", "voice-agent", "voice agent", "elevenlabs",
            "multi-tenant", "multi-tenancy",
            "document-intelligence", "document intelligence",
            "vector-search", "vector search", "pinecone", "qdrant", "opensearch",
            "fastapi", "next.js", "nextjs",
            "ai", "llm", "large language model",
        ]},
        {"client_payment_verified": True},
        {"budget_min_at_least": 1500},
    ]
}

SEED_PROSE = """We bid on production AI engineering work for technical buyers: agents, RAG/document-intelligence systems, voice agents, MCP servers, multi-tenant AI SaaS. Strike zone is $1500+ fixed-price OR hourly retainer with payment-verified clients. We avoid: pure prompt-engineering tasks, "build me ChatGPT" vague briefs, no-budget tire-kickers, sub-$1500 throwaways."""


def main() -> int:
    settings = Settings.from_env()
    db = Database(settings.database_url)
    store = SetupStore(db)

    existing = next((s for s in store.list_active() if s.name == SEED_SETUP_NAME), None)
    if existing is not None:
        print(f"Seed setup {SEED_SETUP_NAME} already exists (id={existing.setup_id}).")
        return 0

    setup = Setup(
        setup_id=None,
        name=SEED_SETUP_NAME,
        status="active",
        tier="normal",
        filter_dsl=FilterDsl(SEED_FILTER),
        prose_definition=SEED_PROSE,
        pitch_template_id=None,
        cover_letter_template_id=None,
        auto_apply_enabled=False,
        escalation_config={"initial": 0, "repings": [], "deadline": None, "on_deadline": "queue"},
    )
    setup_id = store.create(setup)
    print(f"Created seed setup id={setup_id}.")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

(`SetupStore.create` accepts a Setup with `setup_id=None` and returns the new id; ensure that's the API in Task 9.)

- [ ] **Step 12: Manual smoke test of the bot**

This step is manual; it validates the bot connects and slash commands register.

Run: `uv run python -m scheduler.main`

In Discord:
- Wait for `Bot connected as ...` to print in the terminal
- In Discord, type `/health` — should respond `✅ DB reachable.`
- Type `/queue` — should respond `Queue is empty.`
- Type `/connects` — should respond `Today: 0/3 | This week: 0/10`

Stop with Ctrl+C.

- [ ] **Step 13: Commit**

```bash
git add bidder/ scheduler/ bot/ bin/portfolio_import.py bin/import_seed_setup.py
git commit -m "Wire bidder + scheduler + Discord bot; add seed scripts"
```

---

## Task 12: End-to-end verification + delete legacy files

This is the Phase 1 acceptance gate. After this task, Phase 1 is shipped.

- [ ] **Step 1: Bring up the full system**

```bash
docker compose up -d
uv run bin/migrate.py
uv run bin/portfolio_import.py
uv run bin/import_seed_setup.py
uv run launch_chrome.py --profile "Moazzam" --url "https://www.upwork.com/nx/find-work/" --kill-existing
uv run python -m scheduler.main
```

- [ ] **Step 2: Verify the loop end-to-end**

Watch Discord for ~30-60 minutes. Expected outcomes:
- At least one cycle reports a no-op or skim (cycle-mix is working)
- At least one cycle scrapes jobs and writes to DB (verify with: `docker compose exec postgres psql -U upwork -d upwork -c "SELECT COUNT(*) FROM jobs;"`)
- If a job matches the seed setup: signal embed appears in Discord with Apply/Skip buttons
- Click Apply → bot acknowledges → apply executor stages the form (Chrome should focus to the apply page, fill cover letter, fill bid)
- Submit manually in Chrome
- Run `/applied <order_id> viewed` in Discord → row appears in `outcome_events` (verify: `... -c "SELECT * FROM outcome_events;"`)

If any step fails, the failure should appear in Discord (via the `channel.send` error catches), and the order's status reflects what happened.

- [ ] **Step 3: Verify connects budget enforcement**

Manually insert 10 fake `submitted` orders this week to test cap enforcement:

```bash
docker compose exec postgres psql -U upwork -d upwork -c "
  INSERT INTO orders (signal_id, job_id, setup_id, status, submitted_at, idempotency_key)
  SELECT 1, 'fake', 1, 'submitted', now() - (n * interval '1 hour'), 'fake-' || n
  FROM generate_series(1, 10) n;
"
```

Then run `/connects` in Discord → should show `This week: 10/10`.

(Roll back: `... -c "DELETE FROM orders WHERE job_id = 'fake';"`)

- [ ] **Step 4: Delete legacy files**

Once verification passes, delete the old code:

```bash
git rm upwork_driver.py upwork_apply.py upwork_research.py
git rm tools.py agent.py proposal.py notify.py reset_jobs.py
git rm scheduler.py db.py view.py post_linkedin.py inspect_composer.py
git rm debug_feed.py debug_slices.py upwork_scan.py main.py
git rm portfolio.json upwork.db relevant_jobs.md results.md output.md
git rm apply_dump.txt apply_dump_2.txt apply_dump_3.txt composer_dump.txt last_dump.json scan.log
```

(`act.py`, `observe.py`, `pacing.py`, `vision.py`, `launch_chrome.py`, `gdocs.py`, `mermaid.py` should already be moved to `substrate/` and `external/` from Task 5 — they no longer exist at root.)

- [ ] **Step 5: Run all tests one more time**

```bash
uv run pytest tests/ -v
```

Expected: all unit tests pass; integration tests pass (or skip cleanly if Postgres/OpenAI unavailable in CI).

- [ ] **Step 6: Final commit**

```bash
git add -A
git commit -m "Phase 1 ship: delete legacy modules, system runs on new architecture"
```

---

## Phase 1 known limitations (deferred to Phase 2)

These are known and acceptable for the foundation; Phase 2 addresses them:

- **Persistent views after bot restart:** `OrderApprovalView` is built per-signal; if the bot restarts after a signal fires but before you click Apply, the buttons stop working. Phase 2 wires `bot.add_view()` on startup with stable `custom_id`s reconstructed from the `awaiting_approval` orders, fixing this.
- **Apply executor polling lag:** the executor polls every 20s for `approved` orders. Tap Apply → up to 20s before the laptop takes over. Acceptable for Phase 1 (you're not racing the second).
- **Screening question answers are not pre-drafted:** Phase 1 punts screening Q drafting to runtime — when the apply executor opens the apply page, screening Qs are discovered and answered live (re-using the existing `vision.list_visible_questions` + LLM). This means the `screening_answers_json` on the Order is still empty at signal time. Phase 2 does an early-discovery pass during draft.
- **No auto-apply:** `really_submit=False` is hardcoded in Phase 1. Apply executor stages the form (cover letter pasted, bid filled) but stops. You manually click Submit in Chrome. Phase 3 wires per-setup auto-apply once setups have maturity data.
- **`/cancel` doesn't recall an in-flight executor:** if the executor is mid-staging when you `/cancel`, the form is left half-filled. The order's status flips to `cancelled`, but the page on screen is yours to clean up. Phase 2 adds an interruption checkpoint.
- **No conversation Q&A bot:** `@bot <question>` does nothing in Phase 1. Phase 3.

## Summary of what Phase 1 delivers

After this plan executes:
- Postgres in Docker holds the single source of truth (13 tables)
- Module structure follows the spec's layered architecture; no module-level state, no circular deps
- One seed setup is active, scoped to Moazzam's actual strike zone
- Bidder runs 24/7 with humanized timing (cycle mix, diurnal envelope, voice variants, order spacing)
- Discord bot dispatches signal alerts with Apply/Skip buttons
- Slash commands cover the Phase 1 surface: `/queue`, `/cancel`, `/applied`, `/connects`, `/health`
- Connects budget is enforced via `domain/risk.py`
- All LLM access goes through LangChain `create_agent` with pydantic response_format
- Every LLM call is recorded in `agent_runs` with cost tracking hook
- The legacy mess is deleted; new structure is the system

Phase 2 (BA agent layer) builds on this foundation.
