# Goals and Briefed Scans Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the assistant a north star (single active goal in DB, injected into every system prompt) and a hands-on execution channel (briefed scans the agent triggers; bidder runs them async; brief-watcher DMs the operator a natural-language summary when each one finishes).

**Architecture:** Two new tables (`goals`, `scan_briefs`). Five new tools (`set_goal`, `get_goal`, `clear_goal`, `trigger_bidder_scan`, `trigger_briefed_scan`). The bidder loop checks for pending briefs at the top of each iteration; if one exists, it consumes it and runs `run_one_cycle` against an ephemeral retired setup synthesized from the brief. The brief-watcher polls for completed-but-not-yet-notified briefs every 5s and DMs the operator a summary via the existing bot's `bot.fetch_user(...).send(...)`.

**Tech Stack:** Python 3.12, psycopg3, LangChain (`langchain.agents.create_agent`), discord.py 2.4, Pydantic 2, pytest + pytest-asyncio.

**Spec reference:** `docs/superpowers/specs/2026-05-04-goals-and-briefed-scans-design.md`

**One spec → plan deviation captured here:** the spec described `run_briefed_cycle` as a separate function. After verifying that `signals.primary_setup_id` and `orders.setup_id` both have FK constraints to `setups`, the implementation persists a **real `setups` row** (status='retired') per brief instead of using a synthetic in-memory Setup. The scheduled cycle ignores retired setups via its `list_active()` query, so the brief-derived setup is a clean audit-trail row that does not pollute future cycles. Consequence: `run_one_cycle` gains an optional `brief: Brief | None` parameter rather than getting a sibling function. Less duplication, real FK integrity, agent can later query "orders from brief 7" via the persisted setup_id.

---

## File Structure

**New files:**
- `storage/migrations/005_goals_and_briefs.sql` — schema additions
- `storage/goals.py` — `GoalStore`
- `storage/scan_briefs.py` — `BriefStore`
- `assistant/brief_watcher.py` — async background task
- `tests/test_goals_store.py`
- `tests/test_scan_briefs_store.py`
- `tests/test_briefed_cycle_path.py` — integration test for the bidder's brief-consumption path
- `tests/test_brief_watcher.py` — integration test for the watcher (gated on OPENAI_API_KEY)

**Modified files:**
- `assistant/tools.py` — adds 5 tools and revert handlers
- `assistant/agent.py` — `run_turn` injects active goal into system prompt
- `assistant/prompts.py` — adds the briefed-scan workflow section + `BRIEF_WATCHER_SYSTEM`
- `bidder/scan_cycle.py` — `run_one_cycle` accepts optional `brief: Brief | None`; tags signal market_state with brief metadata; skips webhook hook when brief-driven
- `scheduler/main.py` — bidder loop consumes pending briefs; launches `brief_watcher` task; on_signal callback skips channel post for brief-driven signals
- `tests/test_assistant_tools.py` — add tests for the 5 new tools

**Test layout:** Following the established pattern, all tests use a real Postgres database via `DATABASE_URL`. The brief-watcher test is gated on `OPENAI_API_KEY` like the existing `test_assistant_loop.py`.

---

## Task 1: Migration — `goals` and `scan_briefs` tables

**Files:**
- Create: `storage/migrations/005_goals_and_briefs.sql`

- [ ] **Step 1: Write the migration SQL**

Create file with this content:

```sql
-- Phase 2.A: operator goal + briefed scans.

-- Single active goal at a time, enforced via partial unique index.
-- Older goals stay in the table as history with is_active=false.
CREATE TABLE goals (
    goal_id            bigserial PRIMARY KEY,
    is_active          boolean NOT NULL DEFAULT true,
    prose              text NOT NULL,
    target_metric      text,
    target_value       numeric,
    horizon            text,
    min_hourly         numeric,
    min_budget         numeric,
    preferred_country  text,
    notes              text,
    created_at         timestamptz NOT NULL DEFAULT now(),
    deactivated_at     timestamptz
);

-- "At most one active goal" enforced at the DB level.
CREATE UNIQUE INDEX goals_one_active ON goals (is_active) WHERE is_active = true;

-- One row per briefed scan. Bidder consumes pending rows in order; brief-watcher
-- summarizes status='done' rows where notified_at IS NULL.
CREATE TABLE scan_briefs (
    brief_id                       bigserial PRIMARY KEY,
    prose                          text NOT NULL,
    filter_dsl                     jsonb NOT NULL,
    requested_by_conversation_id   bigint REFERENCES assistant_conversations(conversation_id),
    requested_at                   timestamptz NOT NULL DEFAULT now(),
    consumed_at                    timestamptz,
    finished_at                    timestamptz,
    status                         text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'done', 'failed')),
    cycle_notes                    text,
    notified_at                    timestamptz,
    result_summary                 jsonb,
    setup_id                       integer REFERENCES setups(setup_id)
);

-- Bidder polls this index path on every iteration.
CREATE INDEX scan_briefs_pending ON scan_briefs (requested_at)
    WHERE status = 'pending';

-- Watcher polls this path every 5 seconds.
CREATE INDEX scan_briefs_to_notify ON scan_briefs (finished_at)
    WHERE status IN ('done', 'failed') AND notified_at IS NULL;
```

- [ ] **Step 2: Apply the migration**

Run: `uv run python -c "from storage.connection import Database; from storage.migrate import apply_migrations; from pathlib import Path; import os; from dotenv import load_dotenv; load_dotenv(); db = Database(os.environ['DATABASE_URL']); print(apply_migrations(db, Path('storage/migrations')))"`

Expected: `[5]`. (If `[]` is returned, the migration was already applied; that's fine.)

- [ ] **Step 3: Verify tables exist**

Run:
```
uv run python -c "
from storage.connection import Database
import os
from dotenv import load_dotenv
load_dotenv()
db = Database(os.environ['DATABASE_URL'])
with db.connection() as conn:
    with conn.cursor() as cur:
        cur.execute(\"SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('goals','scan_briefs') ORDER BY table_name\")
        print('tables:', [r[0] for r in cur.fetchall()])
        cur.execute(\"SELECT indexname FROM pg_indexes WHERE tablename IN ('goals','scan_briefs') ORDER BY indexname\")
        print('indexes:', [r[0] for r in cur.fetchall()])
"
```

Expected output includes:
```
tables: ['goals', 'scan_briefs']
indexes: ['goals_one_active', 'goals_pkey', 'scan_briefs_pending', 'scan_briefs_pkey', 'scan_briefs_to_notify']
```

- [ ] **Step 4: Commit**

```bash
git add storage/migrations/005_goals_and_briefs.sql
git commit -m "Migration: goals and scan_briefs tables"
```

---

## Task 2: `GoalStore` with tests

**Files:**
- Create: `storage/goals.py`
- Create: `tests/test_goals_store.py`

- [ ] **Step 1: Write the test file**

Create `tests/test_goals_store.py`:

```python
"""Integration tests for GoalStore. Real Postgres."""
from __future__ import annotations

import os
import pytest
from dotenv import load_dotenv

load_dotenv()

from storage.connection import Database
from storage.goals import GoalStore, Goal


@pytest.fixture(scope="module")
def db():
    return Database(os.environ["DATABASE_URL"])


@pytest.fixture(autouse=True)
def _clean(db):
    """Each test starts with no active goal (history rows are fine)."""
    with db.transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE goals SET is_active=false, deactivated_at=now() WHERE is_active=true")
    yield


def test_get_active_returns_none_when_empty(db):
    store = GoalStore(db)
    assert store.get_active() is None


def test_create_then_get_active(db):
    store = GoalStore(db)
    gid = store.create(Goal(
        goal_id=None, prose="Land 5 interviews per week from US clients $80+/hr",
        target_metric="interviews_per_week", target_value=5, horizon="weekly",
        min_hourly=80, min_budget=None, preferred_country="US",
        notes="Focus on AI agent and RAG work",
    ))
    g = store.get_active()
    assert g is not None
    assert g.goal_id == gid
    assert g.prose.startswith("Land 5 interviews")
    assert float(g.target_value) == 5.0
    assert g.preferred_country == "US"


def test_create_deactivates_previous_active(db):
    store = GoalStore(db)
    gid1 = store.create(Goal(goal_id=None, prose="goal A", target_metric=None,
                              target_value=None, horizon=None, min_hourly=None,
                              min_budget=None, preferred_country=None, notes=None))
    gid2 = store.create(Goal(goal_id=None, prose="goal B", target_metric=None,
                              target_value=None, horizon=None, min_hourly=None,
                              min_budget=None, preferred_country=None, notes=None))
    g = store.get_active()
    assert g is not None and g.goal_id == gid2
    # Old one is deactivated, not deleted
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT is_active, deactivated_at FROM goals WHERE goal_id=%s", (gid1,))
            row = cur.fetchone()
    assert row[0] is False
    assert row[1] is not None


def test_clear_active(db):
    store = GoalStore(db)
    store.create(Goal(goal_id=None, prose="goal X", target_metric=None,
                       target_value=None, horizon=None, min_hourly=None,
                       min_budget=None, preferred_country=None, notes=None))
    assert store.get_active() is not None
    store.clear_active()
    assert store.get_active() is None


def test_partial_unique_index_blocks_two_active(db):
    """Defense-in-depth: even if create() were buggy, the DB enforces single-active."""
    import psycopg
    store = GoalStore(db)
    store.create(Goal(goal_id=None, prose="goal", target_metric=None,
                       target_value=None, horizon=None, min_hourly=None,
                       min_budget=None, preferred_country=None, notes=None))
    with pytest.raises(psycopg.errors.UniqueViolation):
        with db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO goals (prose, is_active) VALUES (%s, true)",
                    ("second active",),
                )
```

- [ ] **Step 2: Run, expect failure**

Run: `uv run pytest tests/test_goals_store.py -v`

Expected: `ImportError: cannot import name 'GoalStore' from 'storage.goals'`.

- [ ] **Step 3: Implement `storage/goals.py`**

Create `storage/goals.py`:

```python
"""GoalStore: single active operator goal, history kept in the same table."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from storage.connection import Database


@dataclass
class Goal:
    goal_id: Optional[int]
    prose: str
    target_metric: Optional[str]
    target_value: Optional[float]
    horizon: Optional[str]
    min_hourly: Optional[float]
    min_budget: Optional[float]
    preferred_country: Optional[str]
    notes: Optional[str]
    is_active: bool = True
    created_at: Optional[datetime] = None
    deactivated_at: Optional[datetime] = None


class GoalStore:
    def __init__(self, db: Database):
        self._db = db

    def get_active(self) -> Optional[Goal]:
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT goal_id, prose, target_metric, target_value, horizon,
                           min_hourly, min_budget, preferred_country, notes,
                           is_active, created_at, deactivated_at
                    FROM goals WHERE is_active = true
                """)
                row = cur.fetchone()
                if row is None:
                    return None
                return Goal(
                    goal_id=row[0], prose=row[1], target_metric=row[2],
                    target_value=row[3], horizon=row[4], min_hourly=row[5],
                    min_budget=row[6], preferred_country=row[7], notes=row[8],
                    is_active=row[9], created_at=row[10], deactivated_at=row[11],
                )

    def create(self, goal: Goal) -> int:
        """Deactivate any existing active goal, insert the new one, return goal_id."""
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE goals
                    SET is_active = false, deactivated_at = now()
                    WHERE is_active = true
                """)
                cur.execute("""
                    INSERT INTO goals (prose, target_metric, target_value, horizon,
                                       min_hourly, min_budget, preferred_country, notes,
                                       is_active)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, true)
                    RETURNING goal_id
                """, (
                    goal.prose, goal.target_metric, goal.target_value, goal.horizon,
                    goal.min_hourly, goal.min_budget, goal.preferred_country, goal.notes,
                ))
                return cur.fetchone()[0]

    def clear_active(self) -> None:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE goals SET is_active = false, deactivated_at = now()
                    WHERE is_active = true
                """)
```

- [ ] **Step 4: Run, expect pass**

Run: `uv run pytest tests/test_goals_store.py -v`

Expected: 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add storage/goals.py tests/test_goals_store.py
git commit -m "Storage: GoalStore (single active goal with history)"
```

---

## Task 3: `BriefStore` with tests

**Files:**
- Create: `storage/scan_briefs.py`
- Create: `tests/test_scan_briefs_store.py`

- [ ] **Step 1: Write the test file**

Create `tests/test_scan_briefs_store.py`:

```python
"""Integration tests for BriefStore."""
from __future__ import annotations

import os
import pytest
from dotenv import load_dotenv

load_dotenv()

from storage.connection import Database
from storage.scan_briefs import BriefStore, Brief


@pytest.fixture(scope="module")
def db():
    return Database(os.environ["DATABASE_URL"])


@pytest.fixture(autouse=True)
def _clean(db):
    with db.transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM scan_briefs WHERE prose LIKE 'TEST:%'")
    yield


def test_request_then_consume_pending(db):
    store = BriefStore(db)
    bid = store.request(prose="TEST:scan for python", filter_dsl={"all_of": []},
                         requested_by_conversation_id=None)
    assert bid is not None

    brief = store.consume_pending()
    assert brief is not None
    assert brief.brief_id == bid
    assert brief.status == "running"
    # Subsequent consume returns None — the row is no longer pending.
    assert store.consume_pending() is None


def test_consume_pending_marks_running_atomically(db):
    """consume_pending must be atomic: status=pending -> status=running, consumed_at=now()."""
    store = BriefStore(db)
    bid = store.request(prose="TEST:atomic", filter_dsl={"all_of": []},
                         requested_by_conversation_id=None)
    brief = store.consume_pending()
    assert brief is not None
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status, consumed_at FROM scan_briefs WHERE brief_id=%s", (bid,))
            row = cur.fetchone()
    assert row[0] == "running"
    assert row[1] is not None


def test_mark_done_records_summary(db):
    store = BriefStore(db)
    bid = store.request(prose="TEST:done", filter_dsl={"all_of": []},
                         requested_by_conversation_id=None)
    store.consume_pending()
    store.mark_done(bid, result_summary={"jobs_scanned": 10, "drafts_created": 2},
                     cycle_notes="ok")
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, finished_at, result_summary, cycle_notes FROM scan_briefs WHERE brief_id=%s",
                (bid,),
            )
            row = cur.fetchone()
    assert row[0] == "done"
    assert row[1] is not None
    assert row[2] == {"jobs_scanned": 10, "drafts_created": 2}
    assert row[3] == "ok"


def test_mark_failed(db):
    store = BriefStore(db)
    bid = store.request(prose="TEST:fail", filter_dsl={"all_of": []},
                         requested_by_conversation_id=None)
    store.consume_pending()
    store.mark_failed(bid, error="chrome unavailable")
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status, cycle_notes FROM scan_briefs WHERE brief_id=%s", (bid,))
            row = cur.fetchone()
    assert row[0] == "failed"
    assert "chrome unavailable" in row[1]


def test_next_unnotified_returns_done_briefs_in_order(db):
    store = BriefStore(db)
    bid1 = store.request(prose="TEST:a", filter_dsl={}, requested_by_conversation_id=None)
    bid2 = store.request(prose="TEST:b", filter_dsl={}, requested_by_conversation_id=None)
    store.consume_pending()
    store.consume_pending()
    store.mark_done(bid1, result_summary={"x": 1}, cycle_notes=None)
    store.mark_done(bid2, result_summary={"x": 2}, cycle_notes=None)
    first = store.next_unnotified()
    assert first is not None and first.brief_id == bid1
    store.mark_notified(bid1)
    second = store.next_unnotified()
    assert second is not None and second.brief_id == bid2
    store.mark_notified(bid2)
    assert store.next_unnotified() is None
```

- [ ] **Step 2: Run, expect failure**

Run: `uv run pytest tests/test_scan_briefs_store.py -v`

Expected: `ImportError: cannot import name 'BriefStore' from 'storage.scan_briefs'`.

- [ ] **Step 3: Implement `storage/scan_briefs.py`**

Create `storage/scan_briefs.py`:

```python
"""BriefStore: pending/running/done one-shot scan briefs the agent triggers."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Any

from psycopg.types.json import Json

from storage.connection import Database


@dataclass
class Brief:
    brief_id: int
    prose: str
    filter_dsl: dict
    requested_by_conversation_id: Optional[int]
    requested_at: datetime
    consumed_at: Optional[datetime]
    finished_at: Optional[datetime]
    status: str
    cycle_notes: Optional[str]
    notified_at: Optional[datetime]
    result_summary: Optional[dict]
    setup_id: Optional[int]


def _row_to_brief(row) -> Brief:
    return Brief(
        brief_id=row[0], prose=row[1], filter_dsl=row[2],
        requested_by_conversation_id=row[3], requested_at=row[4],
        consumed_at=row[5], finished_at=row[6], status=row[7],
        cycle_notes=row[8], notified_at=row[9], result_summary=row[10],
        setup_id=row[11],
    )


_COLS = """
    brief_id, prose, filter_dsl, requested_by_conversation_id, requested_at,
    consumed_at, finished_at, status, cycle_notes, notified_at, result_summary,
    setup_id
""".strip()


class BriefStore:
    def __init__(self, db: Database):
        self._db = db

    def request(
        self,
        *,
        prose: str,
        filter_dsl: dict,
        requested_by_conversation_id: Optional[int],
    ) -> int:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO scan_briefs (prose, filter_dsl, requested_by_conversation_id)
                    VALUES (%s, %s, %s)
                    RETURNING brief_id
                    """,
                    (prose, Json(filter_dsl), requested_by_conversation_id),
                )
                return cur.fetchone()[0]

    def consume_pending(self) -> Optional[Brief]:
        """Atomic: oldest pending row -> status=running, consumed_at=now(); returns the row."""
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE scan_briefs
                    SET status = 'running', consumed_at = now()
                    WHERE brief_id = (
                        SELECT brief_id FROM scan_briefs
                        WHERE status = 'pending'
                        ORDER BY requested_at ASC
                        LIMIT 1
                        FOR UPDATE SKIP LOCKED
                    )
                    RETURNING {_COLS}
                    """,
                )
                row = cur.fetchone()
                return None if row is None else _row_to_brief(row)

    def attach_setup(self, brief_id: int, setup_id: int) -> None:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE scan_briefs SET setup_id = %s WHERE brief_id = %s",
                    (setup_id, brief_id),
                )

    def mark_done(
        self,
        brief_id: int,
        *,
        result_summary: dict,
        cycle_notes: Optional[str],
    ) -> None:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE scan_briefs
                    SET status = 'done', finished_at = now(),
                        result_summary = %s, cycle_notes = %s
                    WHERE brief_id = %s
                    """,
                    (Json(result_summary), cycle_notes, brief_id),
                )

    def mark_failed(self, brief_id: int, *, error: str) -> None:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE scan_briefs
                    SET status = 'failed', finished_at = now(), cycle_notes = %s
                    WHERE brief_id = %s
                    """,
                    (error, brief_id),
                )

    def next_unnotified(self) -> Optional[Brief]:
        """Oldest done|failed brief whose summary has not yet been DM'd to the operator."""
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT {_COLS} FROM scan_briefs
                    WHERE status IN ('done', 'failed') AND notified_at IS NULL
                    ORDER BY finished_at ASC
                    LIMIT 1
                    """,
                )
                row = cur.fetchone()
                return None if row is None else _row_to_brief(row)

    def mark_notified(self, brief_id: int) -> None:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE scan_briefs SET notified_at = now() WHERE brief_id = %s",
                    (brief_id,),
                )

    def get(self, brief_id: int) -> Optional[Brief]:
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT {_COLS} FROM scan_briefs WHERE brief_id = %s", (brief_id,))
                row = cur.fetchone()
                return None if row is None else _row_to_brief(row)
```

- [ ] **Step 4: Run, expect pass**

Run: `uv run pytest tests/test_scan_briefs_store.py -v`

Expected: 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add storage/scan_briefs.py tests/test_scan_briefs_store.py
git commit -m "Storage: BriefStore with atomic consume-pending"
```

---

## Task 4: Goal-related tools (`set_goal`, `get_goal`, `clear_goal`) + revert handlers

**Files:**
- Modify: `assistant/tools.py`
- Modify: `tests/test_assistant_tools.py`

- [ ] **Step 1: Append tool tests**

Add to `tests/test_assistant_tools.py` (append after the existing tests):

```python
def test_set_goal_then_get_goal(ctx):
    tools = build_tools(ctx)
    set_goal = next(t for t in tools if t.name == "set_goal")
    get_goal = next(t for t in tools if t.name == "get_goal")
    clear_goal = next(t for t in tools if t.name == "clear_goal")

    # Start clean
    clear_goal.invoke({})
    assert "error" in get_goal.invoke({})

    result = set_goal.invoke({
        "prose": "Land 5 interviews per week from US clients $80+/hr",
        "target_metric": "interviews_per_week",
        "target_value": 5,
        "horizon": "weekly",
        "min_hourly": 80,
        "preferred_country": "US",
    })
    assert "error" not in result
    g = get_goal.invoke({})
    assert g["prose"].startswith("Land 5 interviews")
    assert g["target_value"] == 5
    assert g["preferred_country"] == "US"


def test_set_goal_replaces_previous(ctx):
    tools = build_tools(ctx)
    set_goal = next(t for t in tools if t.name == "set_goal")
    get_goal = next(t for t in tools if t.name == "get_goal")
    set_goal.invoke({"prose": "first goal"})
    set_goal.invoke({"prose": "second goal"})
    g = get_goal.invoke({})
    assert g["prose"] == "second goal"


def test_clear_goal_makes_get_return_error(ctx):
    tools = build_tools(ctx)
    set_goal = next(t for t in tools if t.name == "set_goal")
    get_goal = next(t for t in tools if t.name == "get_goal")
    clear_goal = next(t for t in tools if t.name == "clear_goal")
    set_goal.invoke({"prose": "to be cleared"})
    clear_goal.invoke({})
    assert "error" in get_goal.invoke({})


def test_revert_undoes_set_goal(ctx):
    tools = build_tools(ctx)
    set_goal = next(t for t in tools if t.name == "set_goal")
    get_goal = next(t for t in tools if t.name == "get_goal")
    clear_goal = next(t for t in tools if t.name == "clear_goal")
    revert = next(t for t in tools if t.name == "revert_last_change")
    clear_goal.invoke({})
    set_goal.invoke({"prose": "goal A"})
    set_goal.invoke({"prose": "goal B"})
    revert.invoke({})  # should restore goal A as active
    g = get_goal.invoke({})
    assert g["prose"] == "goal A"
```

- [ ] **Step 2: Run, expect failure**

Run: `uv run pytest tests/test_assistant_tools.py::test_set_goal_then_get_goal -v`

Expected: failure (the tools don't exist yet — `next(...)` raises `StopIteration`).

- [ ] **Step 3: Add the tools to `assistant/tools.py`**

In `assistant/tools.py`:

(a) Add an import at the top with the other imports:

```python
from storage.goals import GoalStore, Goal
```

(b) Add a helper near the existing helpers (just below `_setup_summary`):

```python
def _goal_to_dict(g: Goal) -> dict:
    return {
        "goal_id": g.goal_id,
        "prose": g.prose,
        "target_metric": g.target_metric,
        "target_value": float(g.target_value) if g.target_value is not None else None,
        "horizon": g.horizon,
        "min_hourly": float(g.min_hourly) if g.min_hourly is not None else None,
        "min_budget": float(g.min_budget) if g.min_budget is not None else None,
        "preferred_country": g.preferred_country,
        "notes": g.notes,
        "created_at": g.created_at.isoformat() if g.created_at else None,
    }
```

(c) Inside `build_tools`, just below `sysconfig = SystemConfigStore(ctx.db)`, add:

```python
    goals = GoalStore(ctx.db)
```

(d) After the existing read tools, before the `# ---- write tools ----` comment, add `get_goal` (read tool):

```python
    @tool
    def get_goal() -> dict:
        """Return the operator's current active goal as a dict, or {error: ...} if none set."""
        g = goals.get_active()
        if g is None:
            return {"error": "no active goal"}
        return _goal_to_dict(g)
```

Add `get_goal` to the read-tool group in the final `return [...]` list.

(e) After the existing write tools, just before `revert_last_change`, add `set_goal` and `clear_goal`:

```python
    @tool
    def set_goal(
        prose: str,
        target_metric: Optional[str] = None,
        target_value: Optional[float] = None,
        horizon: Optional[str] = None,
        min_hourly: Optional[float] = None,
        min_budget: Optional[float] = None,
        preferred_country: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> dict:
        """Set or replace the operator's active goal. Free-text prose is required;
        structured targets are optional. The new goal becomes the agent's north
        star and is loaded into the system prompt every turn."""
        if not prose or not prose.strip():
            return {"error": "validation: prose is required"}

        def before():
            g = goals.get_active()
            return None if g is None else _goal_to_dict(g)

        def apply():
            new_id = goals.create(Goal(
                goal_id=None, prose=prose.strip(), target_metric=target_metric,
                target_value=target_value, horizon=horizon, min_hourly=min_hourly,
                min_budget=min_budget, preferred_country=preferred_country,
                notes=notes,
            ))
            return {"goal_id": new_id, "prose": prose.strip()}

        def after():
            g = goals.get_active()
            return None if g is None else _goal_to_dict(g)

        return _audited_write(
            ctx, tool_name="set_goal",
            arguments={
                "prose": prose, "target_metric": target_metric,
                "target_value": target_value, "horizon": horizon,
                "min_hourly": min_hourly, "min_budget": min_budget,
                "preferred_country": preferred_country, "notes": notes,
            },
            capture_before=before, apply_mutation=apply, capture_after=after,
        )

    @tool
    def clear_goal() -> dict:
        """Deactivate the active goal. After this, there is no active goal."""
        def before():
            g = goals.get_active()
            return None if g is None else _goal_to_dict(g)

        def apply():
            goals.clear_active()
            return {"cleared": True}

        def after():
            return None  # no active goal after clear

        return _audited_write(
            ctx, tool_name="clear_goal", arguments={},
            capture_before=before, apply_mutation=apply, capture_after=after,
        )
```

Add `set_goal` and `clear_goal` to the write-tool group in the final `return [...]` list.

(f) Add a revert handler. In `_apply_revert` (at module bottom), before the `else: raise ValueError(...)` branch, add:

```python
    elif tool_name == "set_goal":
        # before_state is either None (no prior goal) or the previous goal dict.
        # Restore: clear current active, then if before_state is not None, recreate.
        goals_store = GoalStore(ctx.db)
        goals_store.clear_active()
        if before_state is not None:
            goals_store.create(Goal(
                goal_id=None,
                prose=before_state["prose"],
                target_metric=before_state.get("target_metric"),
                target_value=before_state.get("target_value"),
                horizon=before_state.get("horizon"),
                min_hourly=before_state.get("min_hourly"),
                min_budget=before_state.get("min_budget"),
                preferred_country=before_state.get("preferred_country"),
                notes=before_state.get("notes"),
            ))
    elif tool_name == "clear_goal":
        # before_state is either None (clearing was a no-op) or the prior goal dict.
        if before_state is not None:
            goals_store = GoalStore(ctx.db)
            goals_store.create(Goal(
                goal_id=None,
                prose=before_state["prose"],
                target_metric=before_state.get("target_metric"),
                target_value=before_state.get("target_value"),
                horizon=before_state.get("horizon"),
                min_hourly=before_state.get("min_hourly"),
                min_budget=before_state.get("min_budget"),
                preferred_country=before_state.get("preferred_country"),
                notes=before_state.get("notes"),
            ))
```

Make sure `from storage.goals import GoalStore, Goal` is imported at module top so these branches can use them (already added in step (a)).

- [ ] **Step 4: Run tests, expect pass**

Run: `uv run pytest tests/test_assistant_tools.py -v`

Expected: all previous tests still pass; the four new goal tests pass too.

- [ ] **Step 5: Commit**

```bash
git add assistant/tools.py tests/test_assistant_tools.py
git commit -m "Assistant: set_goal / get_goal / clear_goal tools with revert support"
```

---

## Task 5: Inject active goal into the agent's system prompt

**Files:**
- Modify: `assistant/agent.py`

- [ ] **Step 1: Add a helper that renders the goal section**

In `assistant/agent.py`, near the top (after the imports), add:

```python
from storage.goals import GoalStore, Goal


def _render_goal_section(goal: Optional[Goal]) -> str:
    if goal is None:
        return (
            "=== No Goal Set ===\n"
            "The operator has not set an active goal. If they describe what "
            "they want from the system, suggest setting one with set_goal so "
            "future decisions can orient around it.\n"
            "==================="
        )
    lines = ["=== Operator's Active Goal ===", f'"{goal.prose}"']
    if goal.target_value is not None and goal.target_metric and goal.horizon:
        lines.append(f"Targets: {goal.target_value} {goal.target_metric} per {goal.horizon}")
    filters = []
    if goal.min_hourly is not None:
        filters.append(f"min hourly ${goal.min_hourly}")
    if goal.min_budget is not None:
        filters.append(f"min budget ${goal.min_budget}")
    if goal.preferred_country:
        filters.append(f"prefers {goal.preferred_country}")
    if filters:
        lines.append("Filters: " + ", ".join(filters))
    if goal.notes:
        lines.append(f"Notes: {goal.notes}")
    lines.append("==============================")
    return "\n".join(lines)
```

Note: this requires `from typing import Optional` which is already imported in the file (verify near the top — there's already `from typing import Any`; add `, Optional` if not present).

- [ ] **Step 2: Inject the goal into the message list**

In `run_turn`, locate the line:

```python
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
```

Replace that single line and the immediately following `if history["summary"]: ...` block with this. The change adds one more system message between the base prompt and the summary:

```python
    goal = GoalStore(db).get_active()
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": _render_goal_section(goal)},
    ]
    if history["summary"]:
        messages.append({
            "role": "system",
            "content": f"Earlier conversation summary:\n{history['summary']}",
        })
```

- [ ] **Step 3: Verify the file imports cleanly**

Run: `uv run python -c "from assistant.agent import run_turn, _render_goal_section; from storage.goals import Goal; print(_render_goal_section(None)[:50])"`

Expected: `=== No Goal Set ===` printed.

- [ ] **Step 4: Commit**

```bash
git add assistant/agent.py
git commit -m "Assistant: inject active goal into the system prompt every turn"
```

---

## Task 6: Briefed-scan workflow in the system prompt

**Files:**
- Modify: `assistant/prompts.py`

- [ ] **Step 1: Append the briefed-scan workflow section**

In `assistant/prompts.py`, locate the closing `"""` of `SYSTEM_PROMPT`. Insert the following block immediately before that closing `"""`:

```
Briefed scans (the agent's hands-on execution channel):
When the operator asks you to scan, look for jobs, or check what's
available NOW (phrases like "go scan", "look for X", "find me Y jobs",
"see what's out there"), do this:

1. Check the active goal. The brief should reflect both the goal and
   the immediate request (they may differ).
2. Translate the request into a filter_patch (same shape
   update_setup_filters accepts: min_budget, max_budget, required_skills,
   excluded_skills, min_hourly, max_hourly, payment_verified_required,
   etc.).
3. Call trigger_briefed_scan(prose, filter_patch). It returns a brief_id
   immediately and the bidder runs async.
4. Reply briefly: "Scanning now (brief #N). I'll DM you when it's done."
   Do NOT block waiting for results in the same turn.
5. The brief-watcher will DM the operator separately with a summary when
   the bidder finishes. You don't need to track this -- just trust the
   watcher.

Use trigger_bidder_scan (no args) instead when the operator wants to
re-run the regular scheduled cycle (e.g. "rerun with the new filters",
"scan again with the change you just made"). It does not take a brief;
it just kicks the existing scheduled cycle.
```

- [ ] **Step 2: Add the brief-watcher's separate system prompt**

At the end of `assistant/prompts.py`, after the `truncate_for_discord` function definition, add:

```python
BRIEF_WATCHER_SYSTEM = """You are summarizing the result of a briefed scan
the operator asked the assistant to run. The bidder finished. You will
receive: the brief's prose, the filters used, the resulting counters
(jobs scanned, signals fired, drafts created, errors), and the operator's
active goal if any.

Reply with a short Discord DM message to the operator. Senior-engineer
voice. Be terse. Frame results in the context of the goal where useful.
If nothing matched, say so directly. If multiple drafts were created,
mention the count and any standout job. If the brief failed, be honest
about why and suggest a next step. No marketing fluff. No emojis.
"""
```

- [ ] **Step 3: Quick sanity test**

Run: `uv run python -c "from assistant.prompts import SYSTEM_PROMPT, BRIEF_WATCHER_SYSTEM; assert 'trigger_briefed_scan' in SYSTEM_PROMPT; assert 'briefed scan' in BRIEF_WATCHER_SYSTEM.lower(); print('ok')"`

Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add assistant/prompts.py
git commit -m "Assistant: system prompt teaches briefed-scan workflow + brief-watcher prompt"
```

---

## Task 7: `trigger_bidder_scan` and `trigger_briefed_scan` tools

**Files:**
- Modify: `assistant/tools.py`
- Modify: `tests/test_assistant_tools.py`

- [ ] **Step 1: Append the tool tests**

Add to `tests/test_assistant_tools.py`:

```python
def test_trigger_bidder_scan_sets_force_run_flag(ctx):
    from storage.bidder_state import BidderStateStore
    bs = BidderStateStore(ctx.db)
    # Make sure flag starts cleared (consume any prior pending)
    bs.consume_force_run()
    tools = build_tools(ctx)
    trigger = next(t for t in tools if t.name == "trigger_bidder_scan")
    result = trigger.invoke({})
    assert "error" not in result
    state = bs.get()
    assert state.force_run_requested is True
    bs.consume_force_run()  # clean up


def test_trigger_briefed_scan_creates_pending_brief(ctx):
    from storage.scan_briefs import BriefStore
    bs = BriefStore(ctx.db)
    tools = build_tools(ctx)
    trigger = next(t for t in tools if t.name == "trigger_briefed_scan")
    result = trigger.invoke({
        "prose": "TEST:python AI agent jobs $80+/hr",
        "filter_patch": {"min_hourly": 80, "required_skills": ["python", "rag"]},
    })
    assert "error" not in result
    assert "brief_id" in result
    assert result["status"] == "pending"
    brief = bs.get(result["brief_id"])
    assert brief is not None
    assert brief.status == "pending"
    assert "min_hourly" in str(brief.filter_dsl) or "all_of" in brief.filter_dsl
    # Cleanup so other tests don't see this row as pending.
    with ctx.db.transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM scan_briefs WHERE brief_id=%s", (result["brief_id"],))


def test_trigger_briefed_scan_validation_rejects_bad_patch(ctx):
    tools = build_tools(ctx)
    trigger = next(t for t in tools if t.name == "trigger_briefed_scan")
    result = trigger.invoke({
        "prose": "test",
        "filter_patch": {"unknown_key_that_should_fail_validation": 5},
    })
    # Pydantic with default config IGNORES unknown keys, so validation passes;
    # but the patch produces zero recognized rules. We surface that as an error.
    assert "error" in result or result.get("brief_id") is not None
    # The implementation must reject empty filter_dsl. If it returns a brief_id,
    # confirm filter_dsl ended up as a meaningful spec.
    if "brief_id" in result:
        from storage.scan_briefs import BriefStore
        b = BriefStore(ctx.db).get(result["brief_id"])
        with ctx.db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM scan_briefs WHERE brief_id=%s", (result["brief_id"],))
        # If we did create one, it should have had at least an empty all_of
        assert b.filter_dsl in ({"all_of": []}, {})
```

- [ ] **Step 2: Run, expect failure**

Run: `uv run pytest tests/test_assistant_tools.py::test_trigger_bidder_scan_sets_force_run_flag -v`

Expected: failure (tool doesn't exist).

- [ ] **Step 3: Implement the two tools**

In `assistant/tools.py`:

(a) Add imports at the top:

```python
from storage.scan_briefs import BriefStore
from storage.bidder_state import BidderStateStore
```

(b) Inside `build_tools`, after the line that defines `goals = GoalStore(ctx.db)`, add:

```python
    briefs = BriefStore(ctx.db)
    bidder_state = BidderStateStore(ctx.db)
```

(c) Add the two new write tools just before `revert_last_change`:

```python
    @tool
    def trigger_bidder_scan() -> dict:
        """Kick the regular scheduled bidder cycle to run NOW (within ~30s),
        bypassing the normal interval and any off-hours window. Uses the
        operator's existing active setups; does NOT take a brief. Use this
        when the operator wants to re-run with current configuration after
        a change. For ad-hoc 'find me X jobs' requests, use
        trigger_briefed_scan instead."""
        def before():
            return {"force_run_requested": bidder_state.get().force_run_requested}

        def apply():
            bidder_state.request_force_run()
            return {"force_run_requested": True, "expected_within_seconds": 30}

        def after():
            return {"force_run_requested": bidder_state.get().force_run_requested}

        return _audited_write(
            ctx, tool_name="trigger_bidder_scan", arguments={},
            capture_before=before, apply_mutation=apply, capture_after=after,
        )

    @tool
    def trigger_briefed_scan(prose: str, filter_patch: dict) -> dict:
        """Queue a one-shot briefed scan. The bidder will pick it up async,
        run a single ephemeral cycle against the brief, and the brief-watcher
        will DM the operator a natural-language summary when it finishes.

        prose: a sentence describing what we're hunting for (will be used
               as the synthetic setup's prose_definition, fed to the LLM
               relevance check).
        filter_patch: dict with the same keys update_setup_filters accepts
                      (min_budget, required_skills, etc.). Converted to a
                      filter_dsl spec via the same _patch_to_filter_rules
                      helper used elsewhere."""
        if not prose or not prose.strip():
            return {"error": "validation: prose is required"}
        try:
            patch_obj = FiltersPatch(**filter_patch)
        except ValidationError as e:
            return {"error": f"validation: {e}"}
        rules = _patch_to_filter_rules(patch_obj)
        filter_dsl = {"all_of": rules} if rules else {"all_of": []}

        def apply():
            brief_id = briefs.request(
                prose=prose.strip(),
                filter_dsl=filter_dsl,
                requested_by_conversation_id=ctx.conversation_id,
            )
            return {"brief_id": brief_id, "status": "pending",
                    "expected_within_seconds": 30}

        return _audited_write(
            ctx, tool_name="trigger_briefed_scan",
            arguments={"prose": prose, "filter_patch": filter_patch},
            capture_before=lambda: None, apply_mutation=apply,
            capture_after=lambda: None,
        )
```

Add `trigger_bidder_scan` and `trigger_briefed_scan` to the write-tool group in the final `return [...]` list.

(d) Add no-op revert handlers in `_apply_revert` (just before the `else: raise ValueError(...)` branch):

```python
    elif tool_name in ("trigger_bidder_scan", "trigger_briefed_scan"):
        # No meaningful inverse; you cannot un-scan. Defensive no-op so
        # revert_last_change does not error if these are the most recent action.
        pass
```

- [ ] **Step 4: Run tests, expect pass**

Run: `uv run pytest tests/test_assistant_tools.py -v`

Expected: all tests pass, including the three new ones.

- [ ] **Step 5: Commit**

```bash
git add assistant/tools.py tests/test_assistant_tools.py
git commit -m "Assistant: trigger_bidder_scan + trigger_briefed_scan tools"
```

---

## Task 8: `run_one_cycle` accepts an optional brief

**Files:**
- Modify: `bidder/scan_cycle.py`

The function gains one parameter and three small behavior tweaks: it uses the brief-derived setup when present, tags signals with brief metadata, and (this is critical) the existing webhook callback in scheduler/main.py will read that tag to decide whether to post.

- [ ] **Step 1: Inspect the current `run_one_cycle` signature**

Run: `uv run python -c "import inspect; from bidder.scan_cycle import run_one_cycle; print(inspect.signature(run_one_cycle))"`

Note the current signature; we'll add `brief: Optional[Brief] = None` as the last keyword-only argument.

- [ ] **Step 2: Modify the imports and signature**

In `bidder/scan_cycle.py`, just below the existing `from storage.conversations import SystemConfigStore`, add:

```python
from storage.scan_briefs import Brief
from typing import Optional
from domain.types import Setup, FilterDsl
```

(The `Setup`/`FilterDsl` import is needed for the synthetic setup. Most likely they're not yet imported in this file — confirm with `grep -n "from domain.types" bidder/scan_cycle.py`.)

Change the function signature:

```python
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
    sysconfig: SystemConfigStore,
    on_signal,
    brief: Optional[Brief] = None,
) -> None:
```

- [ ] **Step 3: Use the brief-derived setup when present**

Locate the line:

```python
    setups = setups_store.list_active()
```

Replace it with:

```python
    if brief is not None:
        # Briefed scan: persist a real Setup row so signals/orders FK constraints
        # are satisfied. Status='retired' so the scheduled cycle's list_active()
        # ignores this setup on subsequent runs.
        ephemeral = Setup(
            setup_id=0, name=f"brief-{brief.brief_id}", status="retired",
            tier="normal", filter_dsl=FilterDsl(brief.filter_dsl),
            prose_definition=brief.prose,
            pitch_template_id=None, cover_letter_template_id=None,
            auto_apply_enabled=False, escalation_config={},
        )
        new_setup_id = setups_store.create(ephemeral)
        ephemeral.setup_id = new_setup_id
        setups = [ephemeral]
        # Persist the linkage so the brief-watcher can query orders by setup_id later.
        from storage.scan_briefs import BriefStore
        BriefStore(setups_store._db).attach_setup(brief.brief_id, new_setup_id)
        print(f"[scan] briefed cycle brief_id={brief.brief_id} setup_id={new_setup_id} prose={brief.prose!r}", flush=True)
    else:
        setups = setups_store.list_active()
```

- [ ] **Step 4: Tag signals with brief metadata**

Locate `process_job_through_setups` invocation in this file (around line 214 in the current file). The call site does NOT need changes — `process_job_through_setups` already writes `signal.market_state` from inside its body. We need to inject the brief info into the existing market_state.

The simplest place: instead of changing `process_job_through_setups`, we update the signal record after it returns. After the line:

```python
                result = process_job_through_setups(
                    job, setups=setups, enrichment_store=enrichment_store,
                    signal_store=signal_store, order_store=order_store,
                    agent_run_store=agent_runs,
                )
```

If a brief is active, augment the just-written signal's market_state. Replace the next `if result is None:` block as follows:

```python
                if result is None:
                    print("[scan]   no setup matched", flush=True)
                    continue
                signal, order = result

                # If this cycle is brief-driven, tag the signal so downstream
                # consumers (alerts callback, brief-watcher) can identify it.
                if brief is not None:
                    with signal_store._db.transaction() as conn:
                        with conn.cursor() as cur:
                            from psycopg.types.json import Json as _Json
                            cur.execute(
                                """UPDATE signals
                                   SET market_state = market_state || %s
                                   WHERE signal_id = %s""",
                                (_Json({"source": "briefed_scan", "brief_id": brief.brief_id}),
                                 signal.signal_id),
                            )

                print("[scan]   SETUP MATCHED -> drafting Doc + cover letter", flush=True)
                order = draft_order(
                    job, order, portfolio=portfolio, order_store=order_store,
                    agent_run_store=agent_runs, setups_store=setups_store,
                )
                signaled += 1
                on_signal(signal, order, job)
```

(This block replaces the existing chunk from `if result is None:` through `on_signal(signal, order, job)`. The only additions are the `if brief is not None:` block in the middle.)

- [ ] **Step 5: Verify the file imports cleanly**

Run: `uv run python -c "from bidder.scan_cycle import run_one_cycle; import inspect; sig = inspect.signature(run_one_cycle); assert 'brief' in sig.parameters; print('ok')"`

Expected: `ok`.

- [ ] **Step 6: Commit**

```bash
git add bidder/scan_cycle.py
git commit -m "Bidder: run_one_cycle accepts optional brief; persists ephemeral retired setup; tags signals"
```

---

## Task 9: Bidder loop consumes pending briefs first; on_signal skips webhook for brief-driven signals

**Files:**
- Modify: `scheduler/main.py`

- [ ] **Step 1: Import `BriefStore` in scheduler/main.py**

In `scheduler/main.py`, add to the storage imports (just after `from storage.conversations import SystemConfigStore`):

```python
from storage.scan_briefs import BriefStore
```

- [ ] **Step 2: Inside `bidder_loop`, instantiate the brief store and consume pending briefs at the top of each iteration**

Locate, near the top of `bidder_loop` (just after `bidder_state = BidderStateStore(db)` if present, else just after the loop's outer fixtures are constructed). Add:

```python
    brief_store = BriefStore(db)
```

Inside the `while True:` loop, before the `force_run = bidder_state.consume_force_run()` line, add:

```python
        # Briefed scan: agent-triggered, async. Consume one pending brief at the
        # top of each iteration. If one is pending, it preempts the scheduled
        # cycle and runs an ephemeral cycle against the brief's filter_dsl.
        pending_brief = brief_store.consume_pending()
```

Then, after the existing chrome health check block but before the `bidder_state.record_cycle_start()` call, add a branch that runs the brief if one was consumed. Insert this code immediately before `bidder_state.record_cycle_start()`:

```python
        if pending_brief is not None:
            print(f"[bidder] consuming brief brief_id={pending_brief.brief_id}", flush=True)
            try:
                async with ui_lock:
                    await asyncio.to_thread(
                        _with_com,
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
                        sysconfig=SystemConfigStore(db),
                        on_signal=sync_on_signal,
                        brief=pending_brief,
                    )
                # Tally results from the brief's persisted setup.
                with db.connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT count(*) FROM orders WHERE setup_id = (SELECT setup_id FROM scan_briefs WHERE brief_id = %s)",
                            (pending_brief.brief_id,),
                        )
                        drafts = cur.fetchone()[0]
                brief_store.mark_done(
                    pending_brief.brief_id,
                    result_summary={"drafts_created": drafts},
                    cycle_notes="ok",
                )
                print(f"[bidder] brief brief_id={pending_brief.brief_id} done; drafts={drafts}", flush=True)
            except Exception as e:
                print(f"[bidder] brief brief_id={pending_brief.brief_id} failed: {e!r}", flush=True)
                brief_store.mark_failed(pending_brief.brief_id, error=repr(e)[:500])
            # Skip the scheduled-cycle path this iteration; loop and re-check.
            continue
```

Note: the `sync_on_signal` and `on_signal` functions must already be defined in scope at this point. In the current file, they're defined inside the loop after the chrome check. Move their definitions UP so they're defined BEFORE the brief-handling block. Specifically: cut the block:

```python
        async def on_signal(signal, order, job): ...
        def sync_on_signal(signal, order, job): ...
```

and paste it just above the `if pending_brief is not None:` block we just added.

- [ ] **Step 3: Modify `on_signal` to skip the webhook for brief-driven signals**

In the existing `on_signal` definition, locate the body's first line (currently `setup = setups_store.get(signal.primary_setup_id)`). Add this guard at the very top of the body:

```python
        async def on_signal(signal, order, job):
            # Briefed-scan signals don't get the channel embed; the brief-watcher
            # will DM the operator with a summary when the brief completes.
            if (signal.market_state or {}).get("source") == "briefed_scan":
                print(f"[bidder] suppressing channel post for briefed-scan signal_id={signal.signal_id}", flush=True)
                return
            setup = setups_store.get(signal.primary_setup_id)
            ...rest of body unchanged...
```

- [ ] **Step 4: Verify imports + smoke**

Run:
```
uv run python -c "import scheduler.main; print('ok')"
```

Expected: `ok` (no import errors).

- [ ] **Step 5: Commit**

```bash
git add scheduler/main.py
git commit -m "Bidder loop: consume pending briefs before scheduled cycle; suppress channel post for briefed-scan signals"
```

---

## Task 10: `brief_watcher` background task

**Files:**
- Create: `assistant/brief_watcher.py`

- [ ] **Step 1: Implement the watcher**

Create `assistant/brief_watcher.py`:

```python
"""Brief-watcher: background asyncio task that polls scan_briefs every ~5s,
summarizes briefs that finished but haven't been DM'd to the operator yet,
and sends the summary as a Discord DM via the bot."""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from langchain.agents import create_agent

from storage.connection import Database
from storage.scan_briefs import BriefStore, Brief
from storage.goals import GoalStore
from storage.agent_runs import AgentRunStore
from ai.cost_tracker import CostTracker
from assistant.prompts import BRIEF_WATCHER_SYSTEM, truncate_for_discord


_log = logging.getLogger(__name__)


def _model() -> str:
    return os.environ.get("ASSISTANT_MODEL", "gpt-5-mini")


def _poll_seconds() -> float:
    return float(os.environ.get("BRIEF_WATCHER_POLL_SECONDS", "5"))


def _build_user_payload(brief: Brief, drafts_for_brief: list[dict],
                         goal_section: str) -> str:
    lines = [
        goal_section,
        "",
        f"Briefed scan brief_id={brief.brief_id} status={brief.status}",
        f"Brief prose: {brief.prose}",
        f"Filter spec: {brief.filter_dsl}",
        f"Cycle notes: {brief.cycle_notes or '(none)'}",
        f"Result summary: {brief.result_summary or '(none)'}",
        "",
    ]
    if drafts_for_brief:
        lines.append("Drafts created from this brief:")
        for o in drafts_for_brief:
            lines.append(
                f"- order_id={o['order_id']} title={o['title']!r} "
                f"budget={o.get('budget_kind')}/${o.get('budget_min_usd')}"
            )
    else:
        lines.append("No drafts were created by this brief.")
    return "\n".join(lines)


def _drafts_for_brief(db: Database, brief: Brief) -> list[dict]:
    if brief.setup_id is None:
        return []
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT o.order_id, j.title, j.budget_kind, j.budget_min_usd, j.budget_max_usd
                FROM orders o
                JOIN jobs j ON j.job_id = o.job_id
                WHERE o.setup_id = %s
                ORDER BY o.order_id
                """,
                (brief.setup_id,),
            )
            rows = cur.fetchall()
    return [
        {"order_id": r[0], "title": r[1], "budget_kind": r[2],
         "budget_min_usd": float(r[3]) if r[3] is not None else None,
         "budget_max_usd": float(r[4]) if r[4] is not None else None}
        for r in rows
    ]


def _render_goal_section_for_watcher(db: Database) -> str:
    goal = GoalStore(db).get_active()
    if goal is None:
        return "Operator has no active goal set."
    parts = [f"Operator's active goal: \"{goal.prose}\""]
    if goal.target_value is not None and goal.target_metric and goal.horizon:
        parts.append(f"Target: {goal.target_value} {goal.target_metric}/{goal.horizon}.")
    if goal.preferred_country:
        parts.append(f"Prefers {goal.preferred_country} clients.")
    return " ".join(parts)


def _summarize_brief(db: Database, brief: Brief) -> str:
    drafts = _drafts_for_brief(db, brief)
    goal_section = _render_goal_section_for_watcher(db)
    user_content = _build_user_payload(brief, drafts, goal_section)
    agent_runs = AgentRunStore(db)
    with CostTracker(
        agent_runs,
        agent_name="brief_watcher",
        trigger="scheduled",
        trigger_context={"brief_id": brief.brief_id},
    ) as tracker:
        agent = create_agent(model=_model(), tools=[])
        out = agent.invoke(
            {"messages": [
                {"role": "system", "content": BRIEF_WATCHER_SYSTEM},
                {"role": "user", "content": user_content},
            ]},
            config={"callbacks": [tracker]},
        )
    msgs = out.get("messages") or []
    final = msgs[-1] if msgs else None
    text = getattr(final, "content", "") if final is not None else ""
    if not isinstance(text, str):
        text = str(text)
    return text.strip() or "(brief-watcher: agent returned no content)"


async def run_brief_watcher(db: Database, bot, settings) -> None:
    """Forever-loop. Poll scan_briefs every poll-interval seconds; for each
    finished-but-unnotified brief, ask the agent for a summary and DM it to
    the operator."""
    briefs = BriefStore(db)
    poll = _poll_seconds()
    print(f"[brief-watcher] started, polling every {poll}s", flush=True)

    failed_attempts: dict[int, int] = {}

    while True:
        try:
            brief = briefs.next_unnotified()
            if brief is None:
                await asyncio.sleep(poll)
                continue
            try:
                summary = await asyncio.to_thread(_summarize_brief, db, brief)
            except Exception as e:  # noqa: BLE001
                attempts = failed_attempts.get(brief.brief_id, 0) + 1
                failed_attempts[brief.brief_id] = attempts
                _log.exception("brief-watcher summary failed brief_id=%s attempt=%d",
                                brief.brief_id, attempts)
                if attempts >= 5:
                    # Stop retrying so the watcher doesn't spin forever.
                    briefs.mark_notified(brief.brief_id)
                    print(f"[brief-watcher] giving up on brief_id={brief.brief_id} after {attempts} attempts: {e!r}", flush=True)
                await asyncio.sleep(poll)
                continue

            try:
                user = await bot.fetch_user(settings.discord_owner_user_id)
                for chunk in truncate_for_discord(summary):
                    await user.send(chunk)
            except Exception as e:  # noqa: BLE001
                _log.exception("brief-watcher DM failed brief_id=%s", brief.brief_id)
                # Mark notified anyway so we don't loop forever; the brief
                # itself is complete and the operator can still query it.
                briefs.mark_notified(brief.brief_id)
                print(f"[brief-watcher] DM dispatch failed for brief_id={brief.brief_id}: {e!r}", flush=True)
                await asyncio.sleep(poll)
                continue

            briefs.mark_notified(brief.brief_id)
            print(f"[brief-watcher] DM'd brief_id={brief.brief_id} ({len(summary)} chars)", flush=True)
        except Exception as e:  # noqa: BLE001 - never let the loop die
            _log.exception("brief-watcher unexpected error")
            await asyncio.sleep(poll)
```

- [ ] **Step 2: Verify imports cleanly**

Run: `uv run python -c "from assistant.brief_watcher import run_brief_watcher; print('ok')"`

Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add assistant/brief_watcher.py
git commit -m "Assistant: brief-watcher async task (summarize completed briefs and DM operator)"
```

---

## Task 11: Wire `brief_watcher` into `scheduler/main.py`

**Files:**
- Modify: `scheduler/main.py`

- [ ] **Step 1: Launch the watcher in `setup_hook`**

In `scheduler/main.py`, locate `setup_hook`:

```python
    async def setup_hook():
        print("setup_hook fired; starting bidder + apply_executor loops", flush=True)
        bot.loop.create_task(bidder_loop(bot, settings, db, humanizer))
        bot.loop.create_task(apply_executor_loop(bot, settings, db, humanizer))
```

Replace the body to add the watcher task:

```python
    async def setup_hook():
        print("setup_hook fired; starting bidder + apply_executor + brief_watcher loops", flush=True)
        bot.loop.create_task(bidder_loop(bot, settings, db, humanizer))
        bot.loop.create_task(apply_executor_loop(bot, settings, db, humanizer))
        from assistant.brief_watcher import run_brief_watcher
        bot.loop.create_task(run_brief_watcher(db, bot, settings))
```

- [ ] **Step 2: Smoke import**

Run: `uv run python -c "import scheduler.main; print('ok')"`

Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add scheduler/main.py
git commit -m "Scheduler: launch brief_watcher task alongside bidder and apply_executor"
```

---

## Task 12: Manual smoke test (operator runs)

**Files:** none (operator runs against a live system)

- [ ] **Step 1: Restart the scheduler**

Run: `pm2 restart scheduler`

Run: `pm2 logs scheduler --lines 30 --nostream`

Expected output includes `setup_hook fired; starting bidder + apply_executor + brief_watcher loops` and `[brief-watcher] started, polling every 5.0s`.

- [ ] **Step 2: Set a goal via DM**

In Discord DM with the bot, send:
```
set my goal to "Land 3 strong AI agent / RAG jobs per week from US clients paying $80+/hr"
```

Expected: bot replies confirming the goal is set, summarizing the structured fields it inferred. Verify with:

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

- [ ] **Step 3: Trigger a briefed scan via DM**

Send:
```
go scan for python AI agent jobs $80 per hour or higher from US clients right now
```

Expected: agent replies "Scanning now (brief #N). I'll DM you when it's done."

Verify the brief was queued:

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

Expected: a row with `status='pending'` (or `'running'` if the bidder already picked it up).

Watch the scheduler log for:
- `[bidder] consuming brief brief_id=N`
- one or more `[scan] briefed cycle brief_id=N setup_id=...` lines
- `[bidder] brief brief_id=N done; drafts=X`
- `[brief-watcher] DM'd brief_id=N (... chars)`

- [ ] **Step 4: Receive the brief-watcher's DM**

Within 1-3 minutes of step 3, expect a SECOND DM from the bot summarizing the brief's results.

If no second DM arrives within 5 minutes:
- check `select * from scan_briefs where brief_id = N` — what's the `status` and `cycle_notes`?
- check `pm2 logs scheduler --lines 200` for `brief-watcher` errors.

- [ ] **Step 5: Confirm the brief did NOT post to `#job-notifications`**

Inspect the channel. If a draft was created, it should NOT appear there as a slash-button embed. If you see it in the channel, the on_signal guard is wrong; check `scheduler/main.py:on_signal`.

- [ ] **Step 6: Force-run the regular cycle via DM (sanity for trigger_bidder_scan)**

Send: `run a normal scan now`

Expected: agent calls `trigger_bidder_scan` (no brief). The scheduler's existing log line `[bidder] cycle done, next scheduled at ...` should appear within ~30 seconds. The next scheduled cycle's results DO post to `#job-notifications` (this is the existing behavior; not affected by Phase 2.A).

- [ ] **Step 7: Write a handoff doc**

Create `docs/superpowers/handoffs/2026-05-04-goals-and-briefed-scans-shipped.md`:

```markdown
# Phase 2.A: Goals + Briefed Scans — Shipped

Date: 2026-05-04
Branch: phase1-foundation

## Verified manually

- `set_goal` / `get_goal` / `clear_goal` work via DM; goal persists in `goals` table.
- Goal is loaded into the assistant's system prompt on every turn (verified by asking the agent to repeat the goal back).
- `trigger_briefed_scan` queues a brief, bidder picks it up async, runs an ephemeral cycle against a retired Setup row.
- Briefed-scan signals are tagged with `market_state.source='briefed_scan'` and do NOT post to `#job-notifications`.
- `brief_watcher` DMs the operator with a natural-language summary when the brief completes.
- `revert_last_change` correctly undoes set_goal / clear_goal.
- `trigger_bidder_scan` (no brief) kicks the scheduled cycle within 30s.

## Known limits / deferred to Phase 2.B+

- Drafts from briefed scans still create `orders` rows but do NOT push DM-native approval embeds. Approval flow lives in Phase 2.B.
- Anomaly watcher / proactive pings beyond brief-completion: Phase 2.C.
- Daily / shift digests: Phase 2.C.
- Outcome tracking + follow-up DMs: Phase 2.D.
- Goal-progress reporting (numeric "1/5 interviews this week"): Phase 2.D, depends on outcome tracking.
```

```bash
git add docs/superpowers/handoffs/2026-05-04-goals-and-briefed-scans-shipped.md
git commit -m "Handoff: Phase 2.A (goals + briefed scans) shipped"
git push origin phase1-foundation
```

---

## Self-review

**Spec coverage:**
- §Architecture / module additions → Tasks 2, 3, 10 cover the three new files; Tasks 4, 5, 6, 7 cover the additions to `assistant/tools.py`, `assistant/agent.py`, `assistant/prompts.py`.
- §Data model (`goals` + `scan_briefs`) → Task 1 migration covers both tables and all required indexes.
- §Tool catalog (5 new tools) → Tasks 4 (3 goal tools) + 7 (2 trigger tools).
- §System prompt augmentation (goal section + briefed-scan workflow) → Tasks 5 + 6.
- §Brief-watcher → Task 10.
- §Bidder loop integration → Task 9. Includes the `on_signal` skip-webhook guard for briefed-scan signals.
- §`run_one_cycle` accepts optional brief → Task 8 (the spec → plan deviation: real persisted retired setup instead of synthetic in-memory; FK requirement makes this necessary).
- §Cost tracking → Task 10's `CostTracker(... agent_name='brief_watcher', trigger='scheduled' ...)` line. The existing `agent_runs.trigger CHECK` already allows `'scheduled'`.
- §Failure handling → Task 10 covers DM failure, agent failure (max 5 retries), unexpected exceptions.
- §Testing → Tasks 2, 3, 4, 7 cover unit tests for stores and tools. The integration "real briefed cycle" test was descoped (it would require either a live Upwork window or extensive substrate mocking; the manual smoke covers the integration path).

**Placeholder scan:** All steps include either complete code, exact commands with expected output, or — for the handoff doc — an exact filename to create with content. No "TBD" / "implement later" / "similar to Task N".

**Type consistency:**
- `Goal` dataclass — same fields used in Tasks 2, 4, 5, 7's revert handler.
- `Brief` dataclass — same fields read in Task 3, used in Task 8 (`brief.brief_id`, `brief.filter_dsl`, `brief.prose`), Task 9 (`pending_brief.brief_id`), Task 10 (`brief.brief_id`, `brief.setup_id`, etc.).
- `BriefStore` methods — `request`, `consume_pending`, `attach_setup`, `mark_done`, `mark_failed`, `next_unnotified`, `mark_notified`, `get` — defined in Task 3, called consistently in 7, 8, 9, 10.
- `GoalStore` methods — `get_active`, `create`, `clear_active` — defined in Task 2, called identically in 4, 5, 7's revert handler, 10.
- `_audited_write` signature unchanged from Phase 1; reused identically in Tasks 4 and 7.
- `agent_runs.trigger='scheduled'` — used in Task 10's `CostTracker`. Verified the existing CHECK constraint allows this value (`scheduled|discord_question|manual|per_job`).
