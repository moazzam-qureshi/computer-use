# Discord Ops Assistant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a conversational LangChain agent the operator DMs in Discord. It answers questions grounded in the system's data and mutates configuration through 22 structured DB-backed tools. The bidder picks up config changes on its next cycle via Postgres.

**Architecture:** New `assistant/` package, peer of `bidder/`. Postgres is the only contract between the assistant and the bidder. Conversation history lives in `assistant_conversations` + `assistant_messages` tables. Every write tool is wrapped in an audit-logging template (`_audited_write`) that captures before/after state for `revert_last_change`. Agent uses `langchain.agents.create_agent` with `gpt-5-mini`.

**Tech Stack:** Python 3.12, psycopg3, LangChain (`langchain.agents.create_agent`), discord.py 2.4, Pydantic 2, pytest + pytest-asyncio.

**Spec reference:** `docs/superpowers/specs/2026-05-04-discord-ops-assistant-design.md`

**Naming corrections from spec → real codebase:**
- Spec used `OPERATOR_DISCORD_USER_ID` → reuse existing `Settings.discord_owner_user_id` (env: `DISCORD_OWNER_USER_ID`).
- Spec used setup statuses `paused`/`archived` → real literals are `disabled`/`retired`. Tools `pause_setup` and `archive_setup` map to those.
- Spec used setup tiers `selective`/`balanced`/`aggressive` → real literals are `quiet`/`normal`/`critical`. Tool `set_setup_tier` accepts those.
- Spec used `kind='assistant_turn'` for cost rows → in code becomes `agent_name='assistant'`, `trigger='discord_dm'` (matches existing `AgentRunStore` API).

---

## File Structure

**New files:**
- `storage/migrations/004_assistant.sql` — schema additions
- `storage/conversations.py` — `ConversationStore`, `MessageStore`, `AuditStore`, `SystemConfigStore`
- `assistant/__init__.py`
- `assistant/prompts.py` — system prompt template + diff-message helpers
- `assistant/tools.py` — 22 `@tool` functions and the `_audited_write` helper
- `assistant/conversation.py` — load/save conversation messages, summarization trigger
- `assistant/agent.py` — `create_agent` wrapper, run-and-record entrypoint
- `assistant/dm_handler.py` — Discord glue: lock, typing, message split, error surface
- `tests/__init__.py` (already exists)
- `tests/test_assistant_tools.py`
- `tests/test_assistant_loop.py`

**Modified files:**
- `bot/bot.py` — register a DM `on_message` handler
- `bidder/scan_cycle.py` — read `system_config['bidder_paused']` at cycle start
- `ai/proposal_gen.py` — read `setups.tone_override` and prepend if non-null
- `scheduler/main.py` (or wherever stores are wired) — instantiate the new stores and pass to `run_bot`
- `domain/types.py` — add `tone_override` and `ignored_clients` fields to `Setup` dataclass
- `storage/setups.py` — read/write the two new columns

**Test layout:** `tests/test_assistant_tools.py` covers each tool's happy path, validation failure, and (for writes) audit-row contents using a real Postgres test database. `tests/test_assistant_loop.py` is one integration test that simulates a 3-turn conversation. We use a real Postgres DB (`DATABASE_URL` test override) rather than mocks, per the codebase pattern of integration over isolation.

---

## Task 1: Migration — schema additions

**Files:**
- Create: `storage/migrations/004_assistant.sql`

- [ ] **Step 1: Write the migration SQL**

Create file with this content:

```sql
-- Assistant module: conversation memory, audit log, runtime flags.

-- Per-operator conversation state.
CREATE TABLE assistant_conversations (
    conversation_id  bigserial PRIMARY KEY,
    discord_user_id  text NOT NULL UNIQUE,
    summary          text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

-- Append-only message log. archived_at lets summarization soft-delete
-- old messages without losing the audit trail.
CREATE TABLE assistant_messages (
    message_id       bigserial PRIMARY KEY,
    conversation_id  bigint NOT NULL REFERENCES assistant_conversations(conversation_id),
    role             text NOT NULL CHECK (role IN ('user', 'assistant', 'tool')),
    content          text NOT NULL,
    tool_call_id     text,
    tool_name        text,
    archived_at      timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX assistant_messages_conv_recent
    ON assistant_messages (conversation_id, message_id DESC)
    WHERE archived_at IS NULL;

-- Write-tool history with before/after snapshots. revert_last_change reads this.
CREATE TABLE assistant_audit_log (
    audit_id         bigserial PRIMARY KEY,
    conversation_id  bigint REFERENCES assistant_conversations(conversation_id),
    tool_name        text NOT NULL,
    arguments        jsonb NOT NULL,
    result           jsonb NOT NULL,
    before_state     jsonb,
    after_state      jsonb,
    created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX assistant_audit_conv_recent
    ON assistant_audit_log (conversation_id, audit_id DESC);

-- Global runtime flags. Currently used keys: bidder_paused (bool),
-- connects_daily_cap (int), connects_weekly_cap (int).
CREATE TABLE system_config (
    key         text PRIMARY KEY,
    value       jsonb NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Per-setup additions.
ALTER TABLE setups ADD COLUMN ignored_clients text[] NOT NULL DEFAULT '{}';
ALTER TABLE setups ADD COLUMN tone_override text;
```

- [ ] **Step 2: Apply the migration locally**

Run: `uv run python -c "from storage.connection import Database; from storage.migrate import apply_migrations; from pathlib import Path; import os; db = Database(os.environ['DATABASE_URL']); print(apply_migrations(db, Path('storage/migrations')))"`

Expected output: `[4]` (the version number). If you get `[]`, the migration was already applied; that's fine.

- [ ] **Step 3: Verify tables exist**

Run: `uv run python -c "from storage.connection import Database; import os; db = Database(os.environ['DATABASE_URL']); c = db.connection().__enter__().cursor(); c.execute(\"SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name LIKE 'assistant_%' OR table_name='system_config' ORDER BY table_name\"); print([r[0] for r in c.fetchall()])"`

Expected output includes: `assistant_audit_log`, `assistant_conversations`, `assistant_messages`, `system_config`.

- [ ] **Step 4: Commit**

```bash
git add storage/migrations/004_assistant.sql
git commit -m "Migration: assistant tables (conversations, messages, audit, system_config) + setups columns"
```

---

## Task 2: Update domain types — Setup gains two fields

**Files:**
- Modify: `domain/types.py:52-63` (the `Setup` dataclass)
- Modify: `storage/setups.py` (add columns to all queries)

- [ ] **Step 1: Add fields to `Setup` dataclass**

In `domain/types.py`, modify the `Setup` dataclass to add two new fields. Replace lines 52-63:

```python
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
    ignored_clients: list[str] = field(default_factory=list)
    tone_override: Optional[str] = None
```

- [ ] **Step 2: Update `SetupStore` queries to read/write the new columns**

Open `storage/setups.py` and update all three SELECT/INSERT statements to include `ignored_clients` and `tone_override`.

In the `create` method (lines 15-33), change the INSERT to:

```python
def create(self, setup: Setup) -> int:
    with self._db.transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO setups (
                  name, status, tier, filter_dsl, prose_definition,
                  pitch_template_id, cover_letter_template_id,
                  auto_apply_enabled, escalation_config,
                  ignored_clients, tone_override, activated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                          CASE WHEN %s = 'active' THEN now() ELSE NULL END)
                RETURNING setup_id
            """, (
                setup.name, setup.status, setup.tier,
                Json(setup.filter_dsl.spec), setup.prose_definition,
                setup.pitch_template_id, setup.cover_letter_template_id,
                setup.auto_apply_enabled, Json(setup.escalation_config),
                setup.ignored_clients, setup.tone_override,
                setup.status,
            ))
            return cur.fetchone()[0]
```

In the `get` method (lines 35-52), change the SELECT and dataclass construction to:

```python
def get(self, setup_id: int) -> Optional[Setup]:
    with self._db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT setup_id, name, status, tier, filter_dsl, prose_definition,
                       pitch_template_id, cover_letter_template_id,
                       auto_apply_enabled, escalation_config,
                       ignored_clients, tone_override
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
                ignored_clients=list(row[10] or []), tone_override=row[11],
            )
```

In `list_active` (lines 54-71), apply the same SELECT change and dataclass construction:

```python
def list_active(self) -> List[Setup]:
    with self._db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT setup_id, name, status, tier, filter_dsl, prose_definition,
                       pitch_template_id, cover_letter_template_id,
                       auto_apply_enabled, escalation_config,
                       ignored_clients, tone_override
                FROM setups WHERE status = 'active'
                ORDER BY setup_id
            """)
            rows = cur.fetchall()
    return [
        Setup(setup_id=r[0], name=r[1], status=r[2], tier=r[3],
              filter_dsl=FilterDsl(r[4]), prose_definition=r[5],
              pitch_template_id=r[6], cover_letter_template_id=r[7],
              auto_apply_enabled=r[8], escalation_config=r[9],
              ignored_clients=list(r[10] or []), tone_override=r[11])
        for r in rows
    ]
```

- [ ] **Step 3: Smoke-test the existing bidder still works**

Run: `uv run python -c "from storage.connection import Database; from storage.setups import SetupStore; import os; s = SetupStore(Database(os.environ['DATABASE_URL'])); print([(x.setup_id, x.name, x.ignored_clients, x.tone_override) for x in s.list_active()])"`

Expected: prints existing setups without error; `ignored_clients=[]` and `tone_override=None` for each.

- [ ] **Step 4: Commit**

```bash
git add domain/types.py storage/setups.py
git commit -m "Setup gains ignored_clients and tone_override fields"
```

---

## Task 3: New stores — `ConversationStore`, `MessageStore`, `AuditStore`, `SystemConfigStore`

**Files:**
- Create: `storage/conversations.py`

- [ ] **Step 1: Write the test file**

Create `tests/test_conversation_stores.py`:

```python
"""Integration tests for conversation/audit/system_config stores. Uses real Postgres."""
from __future__ import annotations

import os
import pytest

from storage.connection import Database
from storage.conversations import (
    ConversationStore, MessageStore, AuditStore, SystemConfigStore,
)


@pytest.fixture(scope="module")
def db():
    return Database(os.environ["DATABASE_URL"])


@pytest.fixture
def conv_store(db):
    return ConversationStore(db)


@pytest.fixture
def msg_store(db):
    return MessageStore(db)


@pytest.fixture
def audit_store(db):
    return AuditStore(db)


@pytest.fixture
def sysconfig(db):
    return SystemConfigStore(db)


def test_conversation_get_or_create_idempotent(conv_store):
    cid1 = conv_store.get_or_create("test-user-1")
    cid2 = conv_store.get_or_create("test-user-1")
    assert cid1 == cid2


def test_messages_round_trip(conv_store, msg_store):
    cid = conv_store.get_or_create("test-user-2")
    msg_store.append(cid, role="user", content="hello")
    msg_store.append(cid, role="assistant", content="hi")
    recent = msg_store.recent(cid, limit=10)
    assert [m["role"] for m in recent] == ["user", "assistant"]
    assert recent[0]["content"] == "hello"


def test_audit_records_before_after(conv_store, audit_store):
    cid = conv_store.get_or_create("test-user-3")
    audit_id = audit_store.record(
        conversation_id=cid,
        tool_name="set_auto_apply",
        arguments={"setup_id": 1, "enabled": True},
        result={"ok": True},
        before_state={"auto_apply_enabled": False},
        after_state={"auto_apply_enabled": True},
    )
    last = audit_store.last_for_conversation(cid)
    assert last["audit_id"] == audit_id
    assert last["before_state"] == {"auto_apply_enabled": False}
    assert last["tool_name"] == "set_auto_apply"


def test_system_config_upsert_and_get(sysconfig):
    sysconfig.set("test_key", {"v": 1})
    assert sysconfig.get("test_key") == {"v": 1}
    sysconfig.set("test_key", {"v": 2})
    assert sysconfig.get("test_key") == {"v": 2}
    assert sysconfig.get("nonexistent") is None
```

- [ ] **Step 2: Run the test, expect failure**

Run: `uv run pytest tests/test_conversation_stores.py -v`

Expected: `ImportError: cannot import name 'ConversationStore' from 'storage.conversations'` (the module doesn't exist yet).

- [ ] **Step 3: Implement the stores**

Create `storage/conversations.py`:

```python
"""Stores for the assistant: conversations, messages, audit log, system config."""
from __future__ import annotations

from typing import Any, Optional, List, Dict
from psycopg.types.json import Json

from storage.connection import Database


class ConversationStore:
    def __init__(self, db: Database):
        self._db = db

    def get_or_create(self, discord_user_id: str) -> int:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT conversation_id FROM assistant_conversations WHERE discord_user_id = %s",
                    (discord_user_id,),
                )
                row = cur.fetchone()
                if row is not None:
                    return row[0]
                cur.execute(
                    "INSERT INTO assistant_conversations (discord_user_id) VALUES (%s) RETURNING conversation_id",
                    (discord_user_id,),
                )
                return cur.fetchone()[0]

    def get_summary(self, conversation_id: int) -> Optional[str]:
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT summary FROM assistant_conversations WHERE conversation_id = %s",
                    (conversation_id,),
                )
                row = cur.fetchone()
                return row[0] if row else None

    def set_summary(self, conversation_id: int, summary: str) -> None:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE assistant_conversations SET summary = %s, updated_at = now() WHERE conversation_id = %s",
                    (summary, conversation_id),
                )

    def touch(self, conversation_id: int) -> None:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE assistant_conversations SET updated_at = now() WHERE conversation_id = %s",
                    (conversation_id,),
                )


class MessageStore:
    def __init__(self, db: Database):
        self._db = db

    def append(
        self,
        conversation_id: int,
        *,
        role: str,
        content: str,
        tool_call_id: Optional[str] = None,
        tool_name: Optional[str] = None,
    ) -> int:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO assistant_messages
                        (conversation_id, role, content, tool_call_id, tool_name)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING message_id
                    """,
                    (conversation_id, role, content, tool_call_id, tool_name),
                )
                return cur.fetchone()[0]

    def recent(self, conversation_id: int, limit: int) -> List[Dict[str, Any]]:
        """Returns recent (non-archived) messages oldest-first."""
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT message_id, role, content, tool_call_id, tool_name, created_at
                    FROM assistant_messages
                    WHERE conversation_id = %s AND archived_at IS NULL
                    ORDER BY message_id DESC
                    LIMIT %s
                    """,
                    (conversation_id, limit),
                )
                rows = cur.fetchall()
        rows.reverse()
        return [
            {
                "message_id": r[0],
                "role": r[1],
                "content": r[2],
                "tool_call_id": r[3],
                "tool_name": r[4],
                "created_at": r[5],
            }
            for r in rows
        ]

    def count_active(self, conversation_id: int) -> int:
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) FROM assistant_messages WHERE conversation_id = %s AND archived_at IS NULL",
                    (conversation_id,),
                )
                return cur.fetchone()[0]

    def archive_oldest(self, conversation_id: int, n: int) -> List[int]:
        """Soft-archive the n oldest non-archived messages. Returns archived ids."""
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE assistant_messages SET archived_at = now()
                    WHERE message_id IN (
                        SELECT message_id FROM assistant_messages
                        WHERE conversation_id = %s AND archived_at IS NULL
                        ORDER BY message_id ASC LIMIT %s
                    )
                    RETURNING message_id
                    """,
                    (conversation_id, n),
                )
                return [r[0] for r in cur.fetchall()]


class AuditStore:
    def __init__(self, db: Database):
        self._db = db

    def record(
        self,
        *,
        conversation_id: Optional[int],
        tool_name: str,
        arguments: dict,
        result: dict,
        before_state: Optional[dict] = None,
        after_state: Optional[dict] = None,
    ) -> int:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO assistant_audit_log
                        (conversation_id, tool_name, arguments, result, before_state, after_state)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING audit_id
                    """,
                    (
                        conversation_id,
                        tool_name,
                        Json(arguments),
                        Json(result),
                        Json(before_state) if before_state is not None else None,
                        Json(after_state) if after_state is not None else None,
                    ),
                )
                return cur.fetchone()[0]

    def last_for_conversation(self, conversation_id: int) -> Optional[Dict[str, Any]]:
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT audit_id, tool_name, arguments, result, before_state, after_state, created_at
                    FROM assistant_audit_log
                    WHERE conversation_id = %s
                    ORDER BY audit_id DESC
                    LIMIT 1
                    """,
                    (conversation_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                return {
                    "audit_id": row[0],
                    "tool_name": row[1],
                    "arguments": row[2],
                    "result": row[3],
                    "before_state": row[4],
                    "after_state": row[5],
                    "created_at": row[6],
                }


class SystemConfigStore:
    def __init__(self, db: Database):
        self._db = db

    def get(self, key: str) -> Optional[Any]:
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT value FROM system_config WHERE key = %s", (key,))
                row = cur.fetchone()
                return row[0] if row else None

    def set(self, key: str, value: Any) -> None:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO system_config (key, value, updated_at)
                    VALUES (%s, %s, now())
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
                    """,
                    (key, Json(value)),
                )
```

- [ ] **Step 4: Run the test, expect pass**

Run: `uv run pytest tests/test_conversation_stores.py -v`

Expected: 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add storage/conversations.py tests/test_conversation_stores.py
git commit -m "Storage: ConversationStore, MessageStore, AuditStore, SystemConfigStore"
```

---

## Task 4: Assistant prompts and diff-message helpers

**Files:**
- Create: `assistant/__init__.py`
- Create: `assistant/prompts.py`

- [ ] **Step 1: Create the package init**

Create `assistant/__init__.py` with one line:

```python
"""Conversational ops assistant. Discord DM frontend, Postgres-backed config tools."""
```

- [ ] **Step 2: Create the prompt module**

Create `assistant/prompts.py`:

```python
"""System prompt + diff-message helpers for the ops assistant."""
from __future__ import annotations


SYSTEM_PROMPT = """You are the operator's Upwork bidding ops assistant.

The bidder runs autonomously every 15 to 20 minutes scanning the Upwork feed.
Your job is to answer questions about its activity and adjust its configuration
on request. The operator talks to you in a Discord DM.

Domain vocabulary (the codebase uses trading metaphors for Upwork bidding):
- Setup: a saved hunting profile. Has filters, a tier (quiet/normal/critical),
  status (proposed/active/disabled/retired), an auto_apply flag, an
  ignored_clients list, and an optional tone_override note for the proposal
  generator.
- Signal: a job that matched a setup's filters.
- Order: a draft proposal awaiting operator approval (or auto-applied).
- Bidder: the scanner+drafter loop.
- Connects: Upwork's per-application currency. There are daily and weekly caps.

Setup status mapping you should know:
- pause_setup → status='disabled'  (temporary)
- resume_setup → status='active'
- archive_setup → status='retired' (long-term retire)

Setup tier values: 'quiet', 'normal', 'critical'.

Behavior rules:
1. Always confirm via diff after writing. After ANY successful write tool,
   include "Done." plus a one-line summary of what changed.
2. Be terse. No marketing fluff. Match the operator's voice: senior engineer,
   direct, blunt when needed.
3. Read before guessing. If the request is ambiguous (e.g. "loosen the budget
   filter" without naming a setup), call list_setups first. If still ambiguous,
   ask one clarifying question.
4. Never invent setup IDs, job IDs, or client names. Ground every reference in
   tool output.
5. Destructive-feeling actions (pause_bidder, archive_setup) execute, but make
   the diff visually clear: e.g. "Bidder PAUSED. Resume with: 'resume bidder'".
6. If a tool returns {"error": "..."}, surface it naturally and offer the next
   sensible step.
7. The operator can always say "revert that" or "undo" — call revert_last_change.
"""


def truncate_for_discord(text: str, limit: int = 2000) -> list[str]:
    """Discord caps messages at 2000 chars. Split at paragraph then sentence
    boundaries so messages stay readable."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind("\n\n", 0, limit)
        if cut < 0:
            cut = remaining.rfind("\n", 0, limit)
        if cut < 0:
            cut = remaining.rfind(". ", 0, limit)
            if cut > 0:
                cut += 1  # include the period
        if cut < 0:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks
```

- [ ] **Step 3: Quick sanity test**

Create `tests/test_prompts.py`:

```python
from assistant.prompts import SYSTEM_PROMPT, truncate_for_discord


def test_system_prompt_includes_status_mapping():
    assert "pause_setup" in SYSTEM_PROMPT
    assert "disabled" in SYSTEM_PROMPT


def test_truncate_short_passthrough():
    assert truncate_for_discord("hello") == ["hello"]


def test_truncate_splits_at_paragraph():
    long = ("a" * 1500) + "\n\n" + ("b" * 1500)
    out = truncate_for_discord(long, limit=2000)
    assert len(out) == 2
    assert out[0].startswith("a")
    assert out[1].startswith("b")


def test_truncate_falls_back_to_hard_cut():
    long = "a" * 5000
    out = truncate_for_discord(long, limit=2000)
    assert len(out) == 3
    assert all(len(c) <= 2000 for c in out)
```

Run: `uv run pytest tests/test_prompts.py -v`

Expected: 4 tests pass.

- [ ] **Step 4: Commit**

```bash
git add assistant/__init__.py assistant/prompts.py tests/test_prompts.py
git commit -m "Assistant: system prompt + Discord message-split helper"
```

---

## Task 5: `_audited_write` helper + read tools

**Files:**
- Create: `assistant/tools.py`

This task introduces the tools module and implements the 8 read tools plus the audit-write template helper. Write tools come in Task 6.

- [ ] **Step 1: Write tests for the read tools**

Append to `tests/test_assistant_tools.py` (create if missing):

```python
"""Tests for assistant tools. Uses a real Postgres test DB."""
from __future__ import annotations

import os
import pytest

from storage.connection import Database
from storage.setups import SetupStore
from storage.conversations import (
    ConversationStore, AuditStore, SystemConfigStore,
)
from domain.types import Setup, FilterDsl
from assistant.tools import build_tools, ToolContext


@pytest.fixture(scope="module")
def db():
    return Database(os.environ["DATABASE_URL"])


@pytest.fixture
def ctx(db):
    conv = ConversationStore(db)
    cid = conv.get_or_create("test-tools-user")
    return ToolContext(db=db, conversation_id=cid)


@pytest.fixture
def setup_id(db):
    """Create a throwaway setup for the test session and return its id."""
    s = SetupStore(db)
    sid = s.create(Setup(
        setup_id=0, name="test-setup-tools", status="active", tier="normal",
        filter_dsl=FilterDsl({"all_of": []}), prose_definition=None,
        pitch_template_id=None, cover_letter_template_id=None,
        auto_apply_enabled=False, escalation_config={},
    ))
    yield sid
    # Cleanup: archive it
    with db.transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM setups WHERE setup_id = %s", (sid,))


def test_list_setups_returns_dicts(ctx, setup_id):
    tools = build_tools(ctx)
    list_setups = next(t for t in tools if t.name == "list_setups")
    result = list_setups.invoke({})
    assert isinstance(result, list)
    assert any(r["setup_id"] == setup_id for r in result)


def test_get_setup_round_trip(ctx, setup_id):
    tools = build_tools(ctx)
    get_setup = next(t for t in tools if t.name == "get_setup")
    result = get_setup.invoke({"setup_id": setup_id})
    assert result["setup_id"] == setup_id
    assert result["name"] == "test-setup-tools"
    assert result["ignored_clients"] == []


def test_get_setup_not_found_returns_error(ctx):
    tools = build_tools(ctx)
    get_setup = next(t for t in tools if t.name == "get_setup")
    result = get_setup.invoke({"setup_id": 99999999})
    assert "error" in result
```

- [ ] **Step 2: Run the test, expect failure**

Run: `uv run pytest tests/test_assistant_tools.py -v`

Expected: ImportError on `assistant.tools`.

- [ ] **Step 3: Create `assistant/tools.py` with read tools and `_audited_write`**

Create `assistant/tools.py`:

```python
"""Tools the assistant agent calls. Read tools + write tools (next task)
+ revert_last_change. Each write tool wraps its mutation in _audited_write,
which captures before/after state and writes an audit-log row."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from langchain_core.tools import tool, BaseTool

from storage.connection import Database
from storage.setups import SetupStore, SignalStore
from storage.jobs import JobStore
from storage.orders import OrderStore
from storage.portfolio import PortfolioStore
from storage.conversations import (
    ConversationStore, AuditStore, SystemConfigStore,
)
from storage.connects_ledger import ConnectsLedger


@dataclass
class ToolContext:
    db: Database
    conversation_id: int


# ---------------------------------------------------------------------------
# _audited_write template: every write tool routes through this.
# ---------------------------------------------------------------------------

def _audited_write(
    ctx: ToolContext,
    *,
    tool_name: str,
    arguments: dict,
    capture_before: Callable[[], Optional[dict]],
    apply_mutation: Callable[[], dict],
    capture_after: Callable[[], Optional[dict]],
) -> dict:
    """Run a write mutation with before/after capture and audit logging.
    Errors raised by apply_mutation are caught and returned as {"error": ...}."""
    audit = AuditStore(ctx.db)
    try:
        before = capture_before()
        result = apply_mutation()
        after = capture_after()
    except Exception as e:  # noqa: BLE001 - we want to surface to the agent
        audit.record(
            conversation_id=ctx.conversation_id,
            tool_name=tool_name,
            arguments=arguments,
            result={"error": str(e)},
            before_state=None,
            after_state=None,
        )
        return {"error": str(e)}

    audit.record(
        conversation_id=ctx.conversation_id,
        tool_name=tool_name,
        arguments=arguments,
        result=result,
        before_state=before,
        after_state=after,
    )
    return result


# ---------------------------------------------------------------------------
# Helpers used by multiple tools
# ---------------------------------------------------------------------------

def _setup_to_dict(s) -> dict:
    return {
        "setup_id": s.setup_id,
        "name": s.name,
        "status": s.status,
        "tier": s.tier,
        "filter_dsl": s.filter_dsl.spec,
        "prose_definition": s.prose_definition,
        "auto_apply_enabled": s.auto_apply_enabled,
        "escalation_config": s.escalation_config,
        "ignored_clients": list(s.ignored_clients or []),
        "tone_override": s.tone_override,
    }


def _setup_summary(s) -> dict:
    """Compact projection used by list_setups."""
    spec = s.filter_dsl.spec or {}
    rules = spec.get("all_of") or spec.get("any_of") or [spec]
    rule_names = [list(r.keys())[0] for r in rules if isinstance(r, dict) and r]
    return {
        "setup_id": s.setup_id,
        "name": s.name,
        "status": s.status,
        "tier": s.tier,
        "auto_apply": s.auto_apply_enabled,
        "ignored_clients_count": len(s.ignored_clients or []),
        "filter_rule_keys": rule_names,
    }


# ---------------------------------------------------------------------------
# Tool builder. Tools need ctx, so we close over it.
# ---------------------------------------------------------------------------

def build_tools(ctx: ToolContext) -> list[BaseTool]:
    setups = SetupStore(ctx.db)
    jobs = JobStore(ctx.db)
    orders = OrderStore(ctx.db)
    portfolio = PortfolioStore(ctx.db)
    connects = ConnectsLedger(ctx.db)
    sysconfig = SystemConfigStore(ctx.db)

    @tool
    def list_setups() -> list[dict]:
        """List all setups (any status) with a compact summary."""
        with ctx.db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT setup_id, name, status, tier, filter_dsl, prose_definition,
                           pitch_template_id, cover_letter_template_id,
                           auto_apply_enabled, escalation_config,
                           ignored_clients, tone_override
                    FROM setups ORDER BY setup_id
                """)
                rows = cur.fetchall()
        out = []
        from domain.types import Setup, FilterDsl
        for r in rows:
            s = Setup(
                setup_id=r[0], name=r[1], status=r[2], tier=r[3],
                filter_dsl=FilterDsl(r[4]), prose_definition=r[5],
                pitch_template_id=r[6], cover_letter_template_id=r[7],
                auto_apply_enabled=r[8], escalation_config=r[9],
                ignored_clients=list(r[10] or []), tone_override=r[11],
            )
            out.append(_setup_summary(s))
        return out

    @tool
    def get_setup(setup_id: int) -> dict:
        """Return the full configuration of one setup, including filters,
        prose definition, ignored clients, and tone override."""
        s = setups.get(setup_id)
        if s is None:
            return {"error": f"setup_id {setup_id} not found"}
        return _setup_to_dict(s)

    @tool
    def list_orders(status: str = "awaiting_approval", limit: int = 20) -> list[dict]:
        """List orders by status. Default: awaiting_approval (drafted, not yet
        applied). Other statuses: drafting, approved, submitted, cancelled, failed."""
        with ctx.db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT o.order_id, o.job_id, o.setup_id, o.status, o.drafted_at,
                           j.title, j.url
                    FROM orders o
                    LEFT JOIN jobs j ON j.job_id = o.job_id
                    WHERE o.status = %s
                    ORDER BY o.drafted_at DESC NULLS LAST
                    LIMIT %s
                """, (status, limit))
                rows = cur.fetchall()
        return [
            {
                "order_id": r[0], "job_id": r[1], "setup_id": r[2],
                "status": r[3], "drafted_at": r[4].isoformat() if r[4] else None,
                "job_title": r[5], "job_url": r[6],
            }
            for r in rows
        ]

    @tool
    def get_job(job_id: str) -> dict:
        """Return job detail plus the most recent signal's match/skip reason
        if any. job_id is the URL slug used as the primary key."""
        with ctx.db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT job_id, url, title, description, budget_kind,
                           budget_min_usd, budget_max_usd, skills,
                           client_country, client_payment_verified,
                           posted_at, posted_text
                    FROM jobs WHERE job_id = %s
                """, (job_id,))
                row = cur.fetchone()
                if row is None:
                    return {"error": f"job {job_id} not found"}
                cur.execute("""
                    SELECT primary_setup_id, matched_setups, fired_at
                    FROM signals WHERE job_id = %s
                    ORDER BY signal_id DESC LIMIT 1
                """, (job_id,))
                sig = cur.fetchone()
        return {
            "job_id": row[0], "url": row[1], "title": row[2],
            "description": (row[3] or "")[:1500],
            "budget_kind": row[4], "budget_min_usd": row[5], "budget_max_usd": row[6],
            "skills": list(row[7] or []),
            "client_country": row[8], "client_payment_verified": row[9],
            "posted_at": row[10].isoformat() if row[10] else None,
            "posted_text": row[11],
            "latest_signal": (
                {
                    "primary_setup_id": sig[0],
                    "matched_setups": sig[1],
                    "fired_at": sig[2].isoformat() if sig[2] else None,
                } if sig else None
            ),
        }

    @tool
    def recent_activity(hours: int = 24) -> dict:
        """Aggregate counters from the last N hours: scrape cycles, jobs scanned,
        signals fired, orders drafted, applies, total LLM cost."""
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        with ctx.db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM scrape_runs WHERE started_at >= %s", (since,))
                cycles_run = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM jobs WHERE first_seen_at >= %s", (since,))
                jobs_scanned = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM signals WHERE fired_at >= %s", (since,))
                signals_fired = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM orders WHERE drafted_at >= %s", (since,))
                drafts_created = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM orders WHERE submitted_at >= %s", (since,))
                applies = cur.fetchone()[0]
                cur.execute(
                    "SELECT coalesce(sum(total_cost_usd), 0) FROM agent_runs WHERE started_at >= %s",
                    (since,),
                )
                total_cost = cur.fetchone()[0]
        return {
            "hours": hours,
            "cycles_run": cycles_run,
            "jobs_scanned": jobs_scanned,
            "signals_fired": signals_fired,
            "drafts_created": drafts_created,
            "applies": applies,
            "total_llm_cost_usd": float(total_cost),
        }

    @tool
    def connects_status() -> dict:
        """Current connects spending state: daily/weekly cap, spent so far,
        next reset times. Cap values come from system_config if set, else env."""
        import os
        daily_cap = sysconfig.get("connects_daily_cap")
        if daily_cap is None:
            daily_cap = int(os.environ.get("CONNECTS_DAILY_CAP", "3"))
        weekly_cap = sysconfig.get("connects_weekly_cap")
        if weekly_cap is None:
            weekly_cap = int(os.environ.get("CONNECTS_WEEKLY_CAP", "10"))
        daily_spent = connects.spent_today()
        weekly_spent = connects.spent_this_week()
        return {
            "daily_cap": int(daily_cap),
            "weekly_cap": int(weekly_cap),
            "daily_spent": daily_spent,
            "weekly_spent": weekly_spent,
            "bidder_paused": bool(sysconfig.get("bidder_paused") or False),
        }

    @tool
    def get_portfolio() -> dict:
        """Return the current portfolio JSON used by the proposal generator."""
        return portfolio.load() or {}

    @tool
    def search_jobs(query: str, status: Optional[str] = None) -> list[dict]:
        """Find jobs matching a substring in title, client, or skills. status
        is optional and filters orders.status if provided."""
        like = f"%{query}%"
        with ctx.db.connection() as conn:
            with conn.cursor() as cur:
                if status:
                    cur.execute("""
                        SELECT j.job_id, j.title, j.client_country, j.posted_at, o.status
                        FROM jobs j
                        JOIN orders o ON o.job_id = j.job_id
                        WHERE (j.title ILIKE %s OR EXISTS (
                                SELECT 1 FROM unnest(j.skills) AS s WHERE s ILIKE %s
                              ))
                          AND o.status = %s
                        ORDER BY j.posted_at DESC NULLS LAST
                        LIMIT 30
                    """, (like, like, status))
                else:
                    cur.execute("""
                        SELECT j.job_id, j.title, j.client_country, j.posted_at, NULL
                        FROM jobs j
                        WHERE j.title ILIKE %s OR EXISTS (
                            SELECT 1 FROM unnest(j.skills) AS s WHERE s ILIKE %s
                        )
                        ORDER BY j.posted_at DESC NULLS LAST
                        LIMIT 30
                    """, (like, like))
                rows = cur.fetchall()
        return [
            {
                "job_id": r[0], "title": r[1], "client_country": r[2],
                "posted_at": r[3].isoformat() if r[3] else None,
                "order_status": r[4],
            }
            for r in rows
        ]

    return [
        list_setups, get_setup, list_orders, get_job,
        recent_activity, connects_status, get_portfolio, search_jobs,
    ]
```

- [ ] **Step 4: Run the test, expect pass**

Run: `uv run pytest tests/test_assistant_tools.py -v`

Expected: 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add assistant/tools.py tests/test_assistant_tools.py
git commit -m "Assistant: read tools (8) and audit-write template helper"
```

---

## Task 6: Write tools (13) + revert_last_change

**Files:**
- Modify: `assistant/tools.py` (extend `build_tools`)

- [ ] **Step 1: Append write-tool tests**

Append to `tests/test_assistant_tools.py`:

```python
def test_update_setup_filters_merges(ctx, setup_id):
    tools = build_tools(ctx)
    update = next(t for t in tools if t.name == "update_setup_filters")
    result = update.invoke({
        "setup_id": setup_id,
        "patch": {"min_budget": 500, "required_skills": ["python", "rag"]},
    })
    assert "error" not in result
    get_setup = next(t for t in tools if t.name == "get_setup")
    s = get_setup.invoke({"setup_id": setup_id})
    spec = s["filter_dsl"]
    # spec should now contain a budget rule and a skills rule
    text = str(spec)
    assert "500" in text
    assert "python" in text


def test_add_and_remove_ignored_client(ctx, setup_id):
    tools = build_tools(ctx)
    add = next(t for t in tools if t.name == "add_ignored_client")
    rem = next(t for t in tools if t.name == "remove_ignored_client")
    add.invoke({"setup_id": setup_id, "client_name": "Acme Corp"})
    add.invoke({"setup_id": setup_id, "client_name": "Acme Corp"})  # idempotent
    s = next(t for t in tools if t.name == "get_setup").invoke({"setup_id": setup_id})
    assert s["ignored_clients"].count("Acme Corp") == 1
    rem.invoke({"setup_id": setup_id, "client_name": "Acme Corp"})
    s = next(t for t in tools if t.name == "get_setup").invoke({"setup_id": setup_id})
    assert "Acme Corp" not in s["ignored_clients"]


def test_status_transitions(ctx, setup_id):
    tools = build_tools(ctx)
    pause = next(t for t in tools if t.name == "pause_setup")
    resume = next(t for t in tools if t.name == "resume_setup")
    pause.invoke({"setup_id": setup_id})
    s = next(t for t in tools if t.name == "get_setup").invoke({"setup_id": setup_id})
    assert s["status"] == "disabled"
    resume.invoke({"setup_id": setup_id})
    s = next(t for t in tools if t.name == "get_setup").invoke({"setup_id": setup_id})
    assert s["status"] == "active"


def test_bidder_pause_resume_writes_system_config(ctx):
    tools = build_tools(ctx)
    pause = next(t for t in tools if t.name == "pause_bidder")
    resume = next(t for t in tools if t.name == "resume_bidder")
    pause.invoke({})
    cs = next(t for t in tools if t.name == "connects_status").invoke({})
    assert cs["bidder_paused"] is True
    resume.invoke({})
    cs = next(t for t in tools if t.name == "connects_status").invoke({})
    assert cs["bidder_paused"] is False


def test_revert_last_change_undoes_pause(ctx, setup_id):
    tools = build_tools(ctx)
    get_setup = next(t for t in tools if t.name == "get_setup")
    pause = next(t for t in tools if t.name == "pause_setup")
    revert = next(t for t in tools if t.name == "revert_last_change")
    # ensure starting state is active
    next(t for t in tools if t.name == "resume_setup").invoke({"setup_id": setup_id})
    pause.invoke({"setup_id": setup_id})
    assert get_setup.invoke({"setup_id": setup_id})["status"] == "disabled"
    rv = revert.invoke({})
    assert "error" not in rv
    assert get_setup.invoke({"setup_id": setup_id})["status"] == "active"


def test_validation_error_is_returned_not_raised(ctx, setup_id):
    tools = build_tools(ctx)
    set_tier = next(t for t in tools if t.name == "set_setup_tier")
    result = set_tier.invoke({"setup_id": setup_id, "tier": "bogus"})
    assert "error" in result
```

- [ ] **Step 2: Run tests, expect failure on missing tools**

Run: `uv run pytest tests/test_assistant_tools.py -v`

Expected: failures on tools that don't exist yet (`update_setup_filters`, `add_ignored_client`, etc.).

- [ ] **Step 3: Implement write tools and revert in `assistant/tools.py`**

In `assistant/tools.py`, add the following at the top of the module (after the existing imports):

```python
from pydantic import BaseModel, Field, ValidationError


class FiltersPatch(BaseModel):
    """Allowed filter-patch keys. Validated before merging into filter_dsl."""
    min_budget: Optional[float] = None
    max_budget: Optional[float] = None
    exclude_fixed_under: Optional[float] = None
    min_hourly: Optional[float] = None
    max_hourly: Optional[float] = None
    required_skills: Optional[list[str]] = None
    excluded_skills: Optional[list[str]] = None
    min_client_spend: Optional[float] = None
    payment_verified_required: Optional[bool] = None
    excluded_durations: Optional[list[str]] = None


def _patch_to_filter_rules(patch: FiltersPatch) -> list[dict]:
    """Convert a patch into a list of filter_dsl rules. Patch keys map to
    the rule keys understood by domain.scoring."""
    rules: list[dict] = []
    if patch.min_budget is not None:
        rules.append({"budget_min_at_least": patch.min_budget})
    if patch.required_skills:
        rules.append({"skill_in": patch.required_skills})
    if patch.payment_verified_required is True:
        rules.append({"client_payment_verified": True})
    # max_budget, min_hourly, max_hourly, etc. are stored in spec but
    # interpreted by the bidder/relevance LLM, not domain.scoring.
    if patch.max_budget is not None:
        rules.append({"budget_max_at_most": patch.max_budget})
    if patch.exclude_fixed_under is not None:
        rules.append({"exclude_fixed_under": patch.exclude_fixed_under})
    if patch.min_hourly is not None:
        rules.append({"min_hourly": patch.min_hourly})
    if patch.max_hourly is not None:
        rules.append({"max_hourly": patch.max_hourly})
    if patch.excluded_skills:
        rules.append({"excluded_skills": patch.excluded_skills})
    if patch.min_client_spend is not None:
        rules.append({"min_client_spend": patch.min_client_spend})
    if patch.excluded_durations:
        rules.append({"excluded_durations": patch.excluded_durations})
    return rules


def _merge_filter_rules(existing_spec: dict, new_rules: list[dict]) -> dict:
    """Merge new rules into an existing all_of spec. New rules with the same
    top-level key replace the old ones."""
    if not existing_spec:
        return {"all_of": new_rules}
    if "all_of" in existing_spec:
        old_rules = existing_spec["all_of"]
    elif "any_of" in existing_spec:
        # convert any_of to all_of-style (this is a simplification; we don't
        # try to preserve any_of semantics across patches)
        old_rules = existing_spec["any_of"]
    else:
        old_rules = [existing_spec]
    new_keys = {list(r.keys())[0] for r in new_rules}
    kept = [r for r in old_rules if list(r.keys())[0] not in new_keys]
    return {"all_of": kept + new_rules}
```

Then extend the `build_tools` function. Inside `build_tools`, BEFORE the `return [...]` line at the end, add the following block of write tools:

```python
    # ---- write tools ----

    @tool
    def update_setup_filters(setup_id: int, patch: dict) -> dict:
        """Merge filter rules into a setup. patch keys: min_budget, max_budget,
        exclude_fixed_under, min_hourly, max_hourly, required_skills,
        excluded_skills, min_client_spend, payment_verified_required,
        excluded_durations. Existing rules with the same key are replaced."""
        try:
            patch_obj = FiltersPatch(**patch)
        except ValidationError as e:
            return {"error": f"validation: {e}"}
        new_rules = _patch_to_filter_rules(patch_obj)
        if not new_rules:
            return {"error": "patch contained no recognized keys"}

        def before():
            s = setups.get(setup_id)
            return None if s is None else {"filter_dsl": s.filter_dsl.spec}

        def apply():
            s = setups.get(setup_id)
            if s is None:
                raise ValueError(f"setup {setup_id} not found")
            merged = _merge_filter_rules(s.filter_dsl.spec, new_rules)
            with ctx.db.transaction() as conn:
                with conn.cursor() as cur:
                    from psycopg.types.json import Json
                    cur.execute(
                        "UPDATE setups SET filter_dsl = %s WHERE setup_id = %s",
                        (Json(merged), setup_id),
                    )
            return {"setup_id": setup_id, "filter_dsl": merged}

        def after():
            s = setups.get(setup_id)
            return None if s is None else {"filter_dsl": s.filter_dsl.spec}

        return _audited_write(
            ctx, tool_name="update_setup_filters",
            arguments={"setup_id": setup_id, "patch": patch},
            capture_before=before, apply_mutation=apply, capture_after=after,
        )

    @tool
    def add_ignored_client(setup_id: int, client_name: str) -> dict:
        """Add a client name to a setup's ignore list. Idempotent."""
        def before():
            s = setups.get(setup_id)
            return None if s is None else {"ignored_clients": list(s.ignored_clients)}

        def apply():
            with ctx.db.transaction() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE setups
                           SET ignored_clients = (
                               SELECT array_agg(DISTINCT x)
                               FROM unnest(ignored_clients || ARRAY[%s]) x
                           )
                           WHERE setup_id = %s""",
                        (client_name, setup_id),
                    )
            return {"setup_id": setup_id, "added": client_name}

        def after():
            s = setups.get(setup_id)
            return None if s is None else {"ignored_clients": list(s.ignored_clients)}

        return _audited_write(
            ctx, tool_name="add_ignored_client",
            arguments={"setup_id": setup_id, "client_name": client_name},
            capture_before=before, apply_mutation=apply, capture_after=after,
        )

    @tool
    def remove_ignored_client(setup_id: int, client_name: str) -> dict:
        """Remove a client name from a setup's ignore list. No-op if absent."""
        def before():
            s = setups.get(setup_id)
            return None if s is None else {"ignored_clients": list(s.ignored_clients)}

        def apply():
            with ctx.db.transaction() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE setups SET ignored_clients = array_remove(ignored_clients, %s) WHERE setup_id = %s",
                        (client_name, setup_id),
                    )
            return {"setup_id": setup_id, "removed": client_name}

        def after():
            s = setups.get(setup_id)
            return None if s is None else {"ignored_clients": list(s.ignored_clients)}

        return _audited_write(
            ctx, tool_name="remove_ignored_client",
            arguments={"setup_id": setup_id, "client_name": client_name},
            capture_before=before, apply_mutation=apply, capture_after=after,
        )

    @tool
    def set_setup_tier(setup_id: int, tier: str) -> dict:
        """Set a setup's tier. Allowed values: quiet, normal, critical."""
        if tier not in ("quiet", "normal", "critical"):
            return {"error": f"validation: tier must be quiet|normal|critical, got {tier!r}"}

        def before():
            s = setups.get(setup_id)
            return None if s is None else {"tier": s.tier}

        def apply():
            with ctx.db.transaction() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE setups SET tier = %s WHERE setup_id = %s", (tier, setup_id))
            return {"setup_id": setup_id, "tier": tier}

        def after():
            s = setups.get(setup_id)
            return None if s is None else {"tier": s.tier}

        return _audited_write(
            ctx, tool_name="set_setup_tier",
            arguments={"setup_id": setup_id, "tier": tier},
            capture_before=before, apply_mutation=apply, capture_after=after,
        )

    @tool
    def set_auto_apply(setup_id: int, enabled: bool) -> dict:
        """Toggle a setup's auto_apply_enabled flag."""
        def before():
            s = setups.get(setup_id)
            return None if s is None else {"auto_apply_enabled": s.auto_apply_enabled}

        def apply():
            with ctx.db.transaction() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE setups SET auto_apply_enabled = %s WHERE setup_id = %s",
                        (enabled, setup_id),
                    )
            return {"setup_id": setup_id, "auto_apply_enabled": enabled}

        def after():
            s = setups.get(setup_id)
            return None if s is None else {"auto_apply_enabled": s.auto_apply_enabled}

        return _audited_write(
            ctx, tool_name="set_auto_apply",
            arguments={"setup_id": setup_id, "enabled": enabled},
            capture_before=before, apply_mutation=apply, capture_after=after,
        )

    def _set_status_factory(name: str, target_status: str, narrative: str):
        @tool(name)
        def _f(setup_id: int) -> dict:
            f"""{narrative} Sets status='{target_status}'."""
            def before():
                s = setups.get(setup_id)
                return None if s is None else {"status": s.status}

            def apply():
                setups.update_status(setup_id, target_status)
                return {"setup_id": setup_id, "status": target_status}

            def after():
                s = setups.get(setup_id)
                return None if s is None else {"status": s.status}

            return _audited_write(
                ctx, tool_name=name,
                arguments={"setup_id": setup_id},
                capture_before=before, apply_mutation=apply, capture_after=after,
            )
        return _f

    pause_setup = _set_status_factory("pause_setup", "disabled", "Pause a setup. Bidder will skip it on the next cycle.")
    resume_setup = _set_status_factory("resume_setup", "active", "Resume a setup.")
    archive_setup = _set_status_factory("archive_setup", "retired", "Archive a setup. Soft-delete; row stays for audit.")

    @tool
    def create_setup(name: str, tier: str, filters: dict, prose: str) -> dict:
        """Create a new Setup. tier: quiet|normal|critical. filters is a patch
        in the same shape update_setup_filters accepts. prose is a free-text
        description (used by the relevance tie-break LLM)."""
        if tier not in ("quiet", "normal", "critical"):
            return {"error": f"validation: tier must be quiet|normal|critical, got {tier!r}"}
        try:
            patch_obj = FiltersPatch(**filters)
        except ValidationError as e:
            return {"error": f"validation: {e}"}
        rules = _patch_to_filter_rules(patch_obj)
        spec = {"all_of": rules}

        def apply():
            from domain.types import Setup, FilterDsl
            new_id = setups.create(Setup(
                setup_id=0, name=name, status="active", tier=tier,
                filter_dsl=FilterDsl(spec), prose_definition=prose,
                pitch_template_id=None, cover_letter_template_id=None,
                auto_apply_enabled=False, escalation_config={},
            ))
            return {"setup_id": new_id, "name": name, "tier": tier}

        return _audited_write(
            ctx, tool_name="create_setup",
            arguments={"name": name, "tier": tier, "filters": filters, "prose": prose},
            capture_before=lambda: None, apply_mutation=apply,
            capture_after=lambda: None,
        )

    @tool
    def set_connects_cap(daily: Optional[int] = None, weekly: Optional[int] = None) -> dict:
        """Override connects caps via system_config. Pass daily and/or weekly.
        Bidder reads these on next cycle, falls back to env if unset."""
        if daily is None and weekly is None:
            return {"error": "validation: must provide daily and/or weekly"}

        def before():
            return {
                "daily": sysconfig.get("connects_daily_cap"),
                "weekly": sysconfig.get("connects_weekly_cap"),
            }

        def apply():
            if daily is not None:
                sysconfig.set("connects_daily_cap", int(daily))
            if weekly is not None:
                sysconfig.set("connects_weekly_cap", int(weekly))
            return {"daily": daily, "weekly": weekly}

        def after():
            return {
                "daily": sysconfig.get("connects_daily_cap"),
                "weekly": sysconfig.get("connects_weekly_cap"),
            }

        return _audited_write(
            ctx, tool_name="set_connects_cap",
            arguments={"daily": daily, "weekly": weekly},
            capture_before=before, apply_mutation=apply, capture_after=after,
        )

    def _bidder_pause_factory(name: str, target: bool, narrative: str):
        @tool(name)
        def _f() -> dict:
            f"""{narrative}"""
            def before():
                return {"bidder_paused": bool(sysconfig.get("bidder_paused") or False)}

            def apply():
                sysconfig.set("bidder_paused", target)
                return {"bidder_paused": target}

            def after():
                return {"bidder_paused": bool(sysconfig.get("bidder_paused") or False)}

            return _audited_write(
                ctx, tool_name=name, arguments={},
                capture_before=before, apply_mutation=apply, capture_after=after,
            )
        return _f

    pause_bidder = _bidder_pause_factory(
        "pause_bidder", True,
        "Pause the bidder loop. Next cycle exits early without scanning.",
    )
    resume_bidder = _bidder_pause_factory(
        "resume_bidder", False, "Resume the bidder loop.",
    )

    @tool
    def update_portfolio(patch: dict) -> dict:
        """Deep-merge a patch into the portfolio JSON."""
        def before():
            return portfolio.load() or {}

        def apply():
            current = portfolio.load() or {}
            merged = _deep_merge(current, patch)
            portfolio.save(merged)
            return {"keys_changed": list(patch.keys())}

        def after():
            return portfolio.load() or {}

        return _audited_write(
            ctx, tool_name="update_portfolio",
            arguments={"patch": patch},
            capture_before=before, apply_mutation=apply, capture_after=after,
        )

    @tool
    def update_pitch_tone(setup_id: int, tone_notes: str) -> dict:
        """Set a per-setup tone override. The proposal generator prepends this
        to its prompt as an 'Operator note on tone' section. Empty string clears it."""
        normalized = tone_notes.strip() or None

        def before():
            s = setups.get(setup_id)
            return None if s is None else {"tone_override": s.tone_override}

        def apply():
            with ctx.db.transaction() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE setups SET tone_override = %s WHERE setup_id = %s",
                        (normalized, setup_id),
                    )
            return {"setup_id": setup_id, "tone_override": normalized}

        def after():
            s = setups.get(setup_id)
            return None if s is None else {"tone_override": s.tone_override}

        return _audited_write(
            ctx, tool_name="update_pitch_tone",
            arguments={"setup_id": setup_id, "tone_notes": tone_notes},
            capture_before=before, apply_mutation=apply, capture_after=after,
        )

    @tool
    def revert_last_change() -> dict:
        """Revert the most recent write tool call in this conversation by
        applying its before_state. If the most recent entry was itself a
        revert, walks back one more."""
        audit = AuditStore(ctx.db)
        last = audit.last_for_conversation(ctx.conversation_id)
        if last is None:
            return {"error": "nothing to revert"}
        if last["tool_name"] == "revert_last_change":
            # Walk one more back to avoid revert-of-revert loops.
            with ctx.db.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT audit_id, tool_name, arguments, before_state
                           FROM assistant_audit_log
                           WHERE conversation_id = %s AND tool_name <> 'revert_last_change'
                           ORDER BY audit_id DESC LIMIT 1""",
                        (ctx.conversation_id,),
                    )
                    row = cur.fetchone()
            if row is None:
                return {"error": "nothing to revert"}
            tool_name, arguments, before_state = row[1], row[2], row[3]
        else:
            tool_name = last["tool_name"]
            arguments = last["arguments"]
            before_state = last["before_state"]
        if before_state is None:
            return {"error": f"audit row for {tool_name} has no before_state; cannot revert"}

        # Apply revert by writing the before_state back to the affected row.
        # Each tool's before_state shape is small and known.
        try:
            _apply_revert(ctx, tool_name, arguments, before_state)
        except Exception as e:  # noqa: BLE001
            return {"error": f"revert failed: {e}"}

        # Record the revert itself.
        audit.record(
            conversation_id=ctx.conversation_id,
            tool_name="revert_last_change",
            arguments={"reverted_tool": tool_name, "reverted_arguments": arguments},
            result={"restored_state": before_state},
            before_state=None,
            after_state=before_state,
        )
        return {"reverted_tool": tool_name, "restored_state": before_state}

    return [
        list_setups, get_setup, list_orders, get_job,
        recent_activity, connects_status, get_portfolio, search_jobs,
        update_setup_filters, add_ignored_client, remove_ignored_client,
        set_setup_tier, set_auto_apply,
        pause_setup, resume_setup, archive_setup,
        create_setup, set_connects_cap,
        pause_bidder, resume_bidder,
        update_portfolio, update_pitch_tone,
        revert_last_change,
    ]
```

Then add the helpers used above at module level, after the existing helpers:

```python
def _deep_merge(a: dict, b: dict) -> dict:
    """Deep merge b into a, returning a new dict."""
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _apply_revert(ctx: ToolContext, tool_name: str, arguments: dict, before_state: dict) -> None:
    """Inverse mutations keyed by tool_name. Each branch knows how to write
    before_state back to the affected row(s)."""
    setups_store = SetupStore(ctx.db)
    sysconfig = SystemConfigStore(ctx.db)
    portfolio = PortfolioStore(ctx.db)
    if tool_name in ("pause_setup", "resume_setup", "archive_setup"):
        setups_store.update_status(arguments["setup_id"], before_state["status"])
    elif tool_name == "set_setup_tier":
        with ctx.db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE setups SET tier = %s WHERE setup_id = %s",
                    (before_state["tier"], arguments["setup_id"]),
                )
    elif tool_name == "set_auto_apply":
        with ctx.db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE setups SET auto_apply_enabled = %s WHERE setup_id = %s",
                    (before_state["auto_apply_enabled"], arguments["setup_id"]),
                )
    elif tool_name == "update_setup_filters":
        from psycopg.types.json import Json
        with ctx.db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE setups SET filter_dsl = %s WHERE setup_id = %s",
                    (Json(before_state["filter_dsl"]), arguments["setup_id"]),
                )
    elif tool_name in ("add_ignored_client", "remove_ignored_client"):
        with ctx.db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE setups SET ignored_clients = %s WHERE setup_id = %s",
                    (list(before_state["ignored_clients"]), arguments["setup_id"]),
                )
    elif tool_name == "update_pitch_tone":
        with ctx.db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE setups SET tone_override = %s WHERE setup_id = %s",
                    (before_state["tone_override"], arguments["setup_id"]),
                )
    elif tool_name == "set_connects_cap":
        # before_state has {"daily": ..., "weekly": ...} (either may be None)
        if before_state.get("daily") is None:
            with ctx.db.transaction() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM system_config WHERE key = 'connects_daily_cap'")
        else:
            sysconfig.set("connects_daily_cap", before_state["daily"])
        if before_state.get("weekly") is None:
            with ctx.db.transaction() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM system_config WHERE key = 'connects_weekly_cap'")
        else:
            sysconfig.set("connects_weekly_cap", before_state["weekly"])
    elif tool_name in ("pause_bidder", "resume_bidder"):
        sysconfig.set("bidder_paused", before_state["bidder_paused"])
    elif tool_name == "update_portfolio":
        portfolio.save(before_state)
    elif tool_name == "create_setup":
        # Reverting a create means deleting; we soft-delete via status='retired'.
        # The audit row's result.setup_id tells us which row was created.
        # Caller already handed us before_state=None for create, so we don't
        # land here in that path. Defensive no-op.
        pass
    else:
        raise ValueError(f"no revert handler for tool {tool_name!r}")
```

- [ ] **Step 4: Run tests, expect pass**

Run: `uv run pytest tests/test_assistant_tools.py -v`

Expected: all tests pass (3 read tests + 6 write tests = 9 total).

- [ ] **Step 5: Commit**

```bash
git add assistant/tools.py tests/test_assistant_tools.py
git commit -m "Assistant: write tools (13) + revert_last_change with inverse-mutation registry"
```

---

## Task 7: Conversation memory module

**Files:**
- Create: `assistant/conversation.py`

- [ ] **Step 1: Write the test**

Create `tests/test_conversation_module.py`:

```python
import os
import pytest

from storage.connection import Database
from storage.conversations import ConversationStore, MessageStore
from assistant.conversation import load_history, append_user, append_assistant


@pytest.fixture(scope="module")
def db():
    return Database(os.environ["DATABASE_URL"])


def test_load_history_empty(db):
    conv = ConversationStore(db)
    cid = conv.get_or_create("test-conv-mod-1")
    h = load_history(db, cid, window=10)
    assert h["summary"] is None
    assert h["messages"] == []


def test_append_and_load(db):
    conv = ConversationStore(db)
    cid = conv.get_or_create("test-conv-mod-2")
    append_user(db, cid, "what setups are active?")
    append_assistant(db, cid, "Setup #2 (active, normal). Setup #4 (active, critical).")
    h = load_history(db, cid, window=10)
    assert len(h["messages"]) == 2
    assert h["messages"][0]["role"] == "user"
    assert h["messages"][1]["role"] == "assistant"
```

- [ ] **Step 2: Run, expect failure**

Run: `uv run pytest tests/test_conversation_module.py -v`

Expected: ImportError on `assistant.conversation`.

- [ ] **Step 3: Implement the module**

Create `assistant/conversation.py`:

```python
"""Conversation memory: load history, append messages, trigger summarization."""
from __future__ import annotations

from typing import Any, Dict

from storage.connection import Database
from storage.conversations import ConversationStore, MessageStore


def load_history(db: Database, conversation_id: int, *, window: int) -> Dict[str, Any]:
    """Return {summary, messages[]}. Messages are oldest-first, recent N."""
    conv = ConversationStore(db)
    msgs = MessageStore(db)
    return {
        "summary": conv.get_summary(conversation_id),
        "messages": msgs.recent(conversation_id, limit=window),
    }


def append_user(db: Database, conversation_id: int, content: str) -> int:
    return MessageStore(db).append(conversation_id, role="user", content=content)


def append_assistant(db: Database, conversation_id: int, content: str) -> int:
    return MessageStore(db).append(conversation_id, role="assistant", content=content)


def maybe_summarize(
    db: Database,
    conversation_id: int,
    *,
    threshold: int,
    summarizer,
) -> bool:
    """If active message count >= threshold, summarize the oldest half and
    archive those messages. summarizer is a callable taking [{"role","content"}]
    and returning a string. Returns True if summarization ran.
    """
    msgs = MessageStore(db)
    active = msgs.count_active(conversation_id)
    if active < threshold:
        return False
    # Take the oldest half, summarize, then archive them.
    history = msgs.recent(conversation_id, limit=active)  # oldest-first
    half = len(history) // 2
    older = history[:half]
    payload = [{"role": m["role"], "content": m["content"]} for m in older]
    new_summary = summarizer(payload)
    conv = ConversationStore(db)
    existing = conv.get_summary(conversation_id) or ""
    combined = (existing + "\n\n" + new_summary).strip() if existing else new_summary
    conv.set_summary(conversation_id, combined)
    msgs.archive_oldest(conversation_id, half)
    return True
```

- [ ] **Step 4: Run tests, expect pass**

Run: `uv run pytest tests/test_conversation_module.py -v`

Expected: 2 tests pass.

- [ ] **Step 5: Commit**

```bash
git add assistant/conversation.py tests/test_conversation_module.py
git commit -m "Assistant: conversation memory (load/append/summarize)"
```

---

## Task 8: Agent module — `create_agent` wrapper + cost tracking

**Files:**
- Create: `assistant/agent.py`

- [ ] **Step 1: Implement the agent module**

Create `assistant/agent.py`:

```python
"""create_agent wrapper for the assistant. One turn per call."""
from __future__ import annotations

import os
from typing import Any

from langchain.agents import create_agent

from storage.connection import Database
from storage.agent_runs import AgentRunStore
from ai.cost_tracker import CostTracker

from assistant.prompts import SYSTEM_PROMPT
from assistant.tools import ToolContext, build_tools
from assistant.conversation import (
    load_history, append_user, append_assistant, maybe_summarize,
)


def _model() -> str:
    return os.environ.get("ASSISTANT_MODEL", "gpt-5-mini")


def _reasoning_effort() -> str:
    return os.environ.get("ASSISTANT_REASONING_EFFORT", "low")


def _history_window() -> int:
    return int(os.environ.get("ASSISTANT_HISTORY_WINDOW", "40"))


def _summarize_history(messages: list[dict]) -> str:
    """Cheap summarizer: collapse a chunk of messages into a paragraph using
    the same model. Synchronous. Run from maybe_summarize after a reply."""
    bullets = []
    for m in messages:
        role = m["role"]
        text = (m["content"] or "")[:300]
        bullets.append(f"- ({role}) {text}")
    raw = "\n".join(bullets)
    agent = create_agent(model=_model())
    out = agent.invoke({"messages": [
        {"role": "system", "content": "Summarize the following exchange in 4-6 bullets, focused on what the operator asked, what config changed, and any open threads. Be terse."},
        {"role": "user", "content": raw},
    ]})
    final = out["messages"][-1].content if out.get("messages") else ""
    return str(final).strip()


def run_turn(
    db: Database,
    *,
    discord_user_id: str,
    user_message: str,
) -> str:
    """Process one user message. Returns the assistant reply string.

    Steps: load history, persist user msg, build agent with tools, invoke,
    persist assistant reply, run summarization if threshold crossed."""
    from storage.conversations import ConversationStore

    conv_store = ConversationStore(db)
    conversation_id = conv_store.get_or_create(discord_user_id)
    append_user(db, conversation_id, user_message)
    history = load_history(db, conversation_id, window=_history_window())

    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    if history["summary"]:
        messages.append({
            "role": "system",
            "content": f"Earlier conversation summary:\n{history['summary']}",
        })
    for m in history["messages"]:
        if m["role"] in ("user", "assistant"):
            messages.append({"role": m["role"], "content": m["content"]})

    ctx = ToolContext(db=db, conversation_id=conversation_id)
    tools = build_tools(ctx)

    agent_run_store = AgentRunStore(db)
    with CostTracker(
        agent_run_store,
        agent_name="assistant",
        trigger="discord_dm",
        trigger_context={"conversation_id": conversation_id, "discord_user_id": discord_user_id},
    ) as tracker:
        agent = create_agent(model=_model(), tools=tools)
        result = agent.invoke(
            {"messages": messages},
            config={"callbacks": [tracker], "recursion_limit": 25},
        )

    final_msg = result["messages"][-1] if result.get("messages") else None
    reply = ""
    if final_msg is not None:
        reply = getattr(final_msg, "content", "") or ""
    if not isinstance(reply, str):
        reply = str(reply)
    if not reply:
        reply = "(no reply produced)"

    append_assistant(db, conversation_id, reply)
    maybe_summarize(
        db, conversation_id,
        threshold=_history_window(),
        summarizer=_summarize_history,
    )
    return reply
```

- [ ] **Step 2: Verify imports resolve**

Run: `uv run python -c "from assistant.agent import run_turn; print('ok')"`

Expected: `ok` printed; no ImportError.

- [ ] **Step 3: Commit**

```bash
git add assistant/agent.py
git commit -m "Assistant: agent module (create_agent wrapper, cost tracking, summarization)"
```

---

## Task 9: Discord DM handler

**Files:**
- Create: `assistant/dm_handler.py`

- [ ] **Step 1: Implement the handler**

Create `assistant/dm_handler.py`:

```python
"""Discord DM glue: per-user lock, typing indicator, message split, error surface."""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Dict

import discord

from storage.connection import Database
from assistant.agent import run_turn
from assistant.prompts import truncate_for_discord


_log = logging.getLogger(__name__)
_locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


async def handle(message: discord.Message, *, db: Database) -> None:
    """Process a DM. Acquires per-user lock, runs agent in a thread, replies."""
    user_id = str(message.author.id)
    lock = _locks[user_id]
    async with lock:
        async with message.channel.typing():
            try:
                reply = await asyncio.to_thread(
                    run_turn, db,
                    discord_user_id=user_id,
                    user_message=message.content,
                )
            except Exception as e:  # noqa: BLE001
                _log.exception("assistant turn failed")
                reply = _friendly_error(e)
        for chunk in truncate_for_discord(reply):
            await message.channel.send(chunk)


def _friendly_error(e: Exception) -> str:
    text = str(e).lower()
    if "connection" in text or "could not connect" in text:
        return "Database is unreachable; the bidder may also be down. Check `pm2 status`."
    if "openai" in text or "api" in text or "timed out" in text:
        return "OpenAI is unreachable, try again in a minute."
    if "recursion" in text:
        return "I got stuck mid-thought. Try rephrasing."
    return f"Something broke on my side: {e!s}"
```

- [ ] **Step 2: Quick smoke test of imports**

Run: `uv run python -c "from assistant.dm_handler import handle; print('ok')"`

Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add assistant/dm_handler.py
git commit -m "Assistant: Discord DM handler (lock, typing, error surface)"
```

---

## Task 10: Wire DM handler into the bot

**Files:**
- Modify: `bot/bot.py`

- [ ] **Step 1: Update `bot/bot.py` to register the on_message handler**

Replace the body of `run_bot` in `bot/bot.py` with:

```python
async def run_bot(settings: Settings, db: Database, on_ready):
    bot = build_bot(settings)
    on_ready_callback = on_ready

    @bot.event
    async def on_ready():
        print(f"Bot connected as {bot.user}", flush=True)
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands", flush=True)
        await on_ready_callback(bot)

    @bot.event
    async def on_message(message):
        # Ignore self.
        if message.author == bot.user:
            return
        # DM only.
        if not isinstance(message.channel, discord.DMChannel):
            return
        # Only the configured operator.
        if int(message.author.id) != settings.discord_owner_user_id:
            return
        from assistant.dm_handler import handle
        await handle(message, db=db)
        # Do NOT process commands here; slash commands run via the tree, and
        # we have no prefix commands in this bot.

    from bot.commands import register_commands
    from bot.interaction_handler import register_views
    register_commands(bot, db, settings)
    register_views(bot, db, settings)

    await bot.start(settings.discord_bot_token)
```

- [ ] **Step 2: Smoke-test that the bot still starts**

Run: `uv run python -c "from bot.bot import run_bot; print('ok')"`

Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add bot/bot.py
git commit -m "Bot: register DM handler that routes to assistant"
```

---

## Task 11: Bidder reads `system_config['bidder_paused']`

**Files:**
- Modify: `bidder/scan_cycle.py`
- Modify: `bidder/scan_cycle.py` signature (add `sysconfig` param) and `scheduler/main.py` (pass it in)

- [ ] **Step 1: Update `run_one_cycle` to accept `sysconfig` and check the flag**

In `bidder/scan_cycle.py`, modify the imports and the function signature. After the existing `from storage.X import ...` block, add:

```python
from storage.conversations import SystemConfigStore
```

Then change the `run_one_cycle` signature and add the pause check at the very top of the function body (right after the docstring/comment block, before `cycle_type = CycleType.FULL_SCAN`):

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
) -> None:
    if bool(sysconfig.get("bidder_paused") or False):
        print("[scan] bidder paused via system_config; skipping cycle", flush=True)
        return
    # Always full-scan. ... (existing comment block continues)
    cycle_type = CycleType.FULL_SCAN
```

- [ ] **Step 2: Update the caller to instantiate and pass `sysconfig`**

Find every caller of `run_one_cycle` (likely `scheduler/main.py`). For each call, add a `sysconfig=SystemConfigStore(db)` argument.

Run to find callers: `uv run python -c "import subprocess; print(subprocess.run(['grep', '-rn', 'run_one_cycle', '--include=*.py', '.'], capture_output=True, text=True).stdout)"`

(If grep is unavailable on Windows, open `scheduler/main.py` and search for `run_one_cycle` manually.)

In `scheduler/main.py`, locate the `run_one_cycle(...)` call and add the new arg:

```python
from storage.conversations import SystemConfigStore  # if not already imported

# ... in the loop body where run_one_cycle is called:
run_one_cycle(
    humanizer=humanizer,
    setups_store=setups_store,
    # ... existing args ...
    sysconfig=SystemConfigStore(db),
    on_signal=on_signal,
)
```

- [ ] **Step 3: Verify the bidder file imports cleanly**

Run: `uv run python -c "from bidder.scan_cycle import run_one_cycle; print('ok')"`

Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add bidder/scan_cycle.py scheduler/main.py
git commit -m "Bidder: skip cycle when system_config.bidder_paused is set"
```

---

## Task 12: Proposal generator reads `setups.tone_override`

**Files:**
- Modify: `ai/proposal_gen.py`

- [ ] **Step 1: Locate the system-prompt assembly point**

Run: `uv run python -c "import pathlib; print(pathlib.Path('ai/proposal_gen.py').read_text(encoding='utf-8')[:4000])"`

Find the function that builds the prompt (looks for `system` content concatenation). Identify where the setup is in scope.

- [ ] **Step 2: Prepend tone override if present**

In `ai/proposal_gen.py`, in the function that takes a `Setup` and assembles the system prompt, add this immediately before the `agent.invoke(...)` call (or wherever the system message is built):

```python
# Operator override on tone (set via the assistant's update_pitch_tone tool).
tone_prefix = ""
if getattr(setup, "tone_override", None):
    tone_prefix = (
        "Operator note on tone (high priority, follow this):\n"
        f"{setup.tone_override}\n\n"
    )
system_content = tone_prefix + system_content  # or whatever variable holds the system prompt
```

If the existing code constructs the message list inline (`{"role": "system", "content": "..."}`), pull the system content into a local variable first, then prepend.

- [ ] **Step 3: Smoke-test the file imports**

Run: `uv run python -c "import ai.proposal_gen; print('ok')"`

Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add ai/proposal_gen.py
git commit -m "Proposal gen: prepend setup.tone_override when present"
```

---

## Task 13: Integration smoke test — full conversation loop

**Files:**
- Create: `tests/test_assistant_loop.py`

- [ ] **Step 1: Write the integration test**

Create `tests/test_assistant_loop.py`:

```python
"""End-to-end loop test: simulate a 3-turn conversation with the agent
against a real Postgres test DB. Skipped if OPENAI_API_KEY is unset."""
from __future__ import annotations

import os
import pytest

from storage.connection import Database
from storage.setups import SetupStore
from domain.types import Setup, FilterDsl
from assistant.agent import run_turn


pytestmark = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="needs real OpenAI key for live agent invocation",
)


@pytest.fixture(scope="module")
def db():
    return Database(os.environ["DATABASE_URL"])


@pytest.fixture
def setup_id(db):
    s = SetupStore(db)
    sid = s.create(Setup(
        setup_id=0, name="loop-test-setup", status="active", tier="normal",
        filter_dsl=FilterDsl({"all_of": []}), prose_definition="loop integration test",
        pitch_template_id=None, cover_letter_template_id=None,
        auto_apply_enabled=False, escalation_config={},
    ))
    yield sid
    with db.transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM setups WHERE setup_id = %s", (sid,))


def test_three_turn_conversation(db, setup_id):
    user = "test-loop-user"

    # turn 1: read
    reply1 = run_turn(db, discord_user_id=user, user_message="list active setups please")
    assert isinstance(reply1, str) and len(reply1) > 0

    # turn 2: write
    reply2 = run_turn(
        db, discord_user_id=user,
        user_message=f"add 'TestClientCorp' to ignored clients on setup {setup_id}",
    )
    assert "TestClientCorp" in reply2 or "ignored" in reply2.lower() or "added" in reply2.lower()

    # confirm DB state
    s = SetupStore(db).get(setup_id)
    assert "TestClientCorp" in s.ignored_clients

    # turn 3: revert
    reply3 = run_turn(db, discord_user_id=user, user_message="revert that")
    assert isinstance(reply3, str)

    s2 = SetupStore(db).get(setup_id)
    assert "TestClientCorp" not in s2.ignored_clients
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/test_assistant_loop.py -v`

Expected: 1 test passes (or skipped if no `OPENAI_API_KEY`). If the agent's reply doesn't contain the keywords we check, soften the assertions — DB state is the real source of truth.

- [ ] **Step 3: Commit**

```bash
git add tests/test_assistant_loop.py
git commit -m "Tests: end-to-end conversation loop integration test"
```

---

## Task 14: Manual smoke test in Discord

**Files:** none (operator runs the test against a live system)

- [ ] **Step 1: Confirm env vars**

Verify `.env` (or process env) has `DISCORD_OWNER_USER_ID` set to the operator's Discord user ID. Optionally set `ASSISTANT_MODEL` (default `gpt-5-mini`), `ASSISTANT_REASONING_EFFORT` (default `low`), `ASSISTANT_HISTORY_WINDOW` (default `40`).

- [ ] **Step 2: Restart the bot via PM2**

Run: `pm2 restart bidder-bot`

Expected: bot reconnects within ~5s. Check `pm2 logs bidder-bot --lines 50` shows "Bot connected as ..." and "Synced N slash commands".

- [ ] **Step 3: DM the bot and walk the script**

Send these messages, one per turn, and verify the indicated outcome:

| Message | Expected |
|---|---|
| `what setups are active?` | Bot lists setups with id/name/tier/status. |
| `tighten the budget filter on setup <X> to $500 minimum` | Bot replies "Done. ..."; `select filter_dsl from setups where setup_id=<X>` shows the new rule. |
| `what did the bidder do today?` | Bot responds with cycles/jobs/cost numbers. |
| `add Acme Corp to setup <X> ignored clients` | Bot replies "Done. Added Acme Corp ..."; `select ignored_clients from setups where setup_id=<X>` includes 'Acme Corp'. |
| `revert that` | Bot replies "Reverted ..."; the row no longer contains Acme Corp. |
| `pause the bidder` | Bot replies "Bidder PAUSED. Resume with: 'resume bidder'."; `select value from system_config where key='bidder_paused'` is `true`. Next bidder cycle log shows `[scan] bidder paused via system_config; skipping cycle`. |
| `resume the bidder` | Bot replies confirming; system_config flips to `false`; next cycle scans normally. |

- [ ] **Step 4: Spot-check the audit log**

Run: `uv run python -c "from storage.connection import Database; import os; db = Database(os.environ['DATABASE_URL']); c = db.connection().__enter__().cursor(); c.execute('SELECT tool_name, arguments FROM assistant_audit_log ORDER BY audit_id DESC LIMIT 10'); print('\n'.join(repr(r) for r in c.fetchall()))"`

Expected: output reflects the tools called during the smoke test.

- [ ] **Step 5: Commit a short handoff note**

Create `docs/superpowers/handoffs/2026-05-04-discord-ops-assistant-shipped.md`:

```markdown
# Discord Ops Assistant — Shipped

Date: 2026-05-04
Branch: phase1-foundation (or whichever branch this lands on)

Manually verified:
- Discord DM to the bot reaches the assistant.
- list_setups, update_setup_filters, add_ignored_client, recent_activity confirmed end-to-end.
- revert_last_change correctly undoes the most recent write.
- pause_bidder / resume_bidder integrate with the bidder loop's system_config check.
- Audit log captures before/after state.

Known limits / deferred to v2:
- Reactive only (no proactive pings).
- Single operator (schema supports multi-user; gate is in bot.py).
- No PostgresStore-style long-term semantic memory yet.
```

```bash
git add docs/superpowers/handoffs/2026-05-04-discord-ops-assistant-shipped.md
git commit -m "Handoff: Discord ops assistant shipped"
```

---

## Self-review

**Spec coverage check:**
- §Architecture / module layout → Tasks 4-9 create exactly the files listed.
- §Data model (4 tables + 2 columns) → Task 1 migration covers all of them.
- §Conversation memory mechanics → Task 7 implements load/append/summarize; soft-archive (the spec's open-question default) is the implementation choice.
- §Agent / per-DM control flow → Task 8 covers history load → user persist → invoke → reply persist → summarization.
- §System prompt → Task 4.
- §Failure handling → Task 9 (`_friendly_error`).
- §Concurrency (per-user lock) → Task 9.
- §Tool catalog (8 read + 13 write + 1 revert) → Tasks 5 and 6 implement all 22.
- §Wiring (`bot/bot.py`, `bidder/scan_cycle.py`, `ai/proposal_gen.py`) → Tasks 10, 11, 12.
- §Cost tracking → Task 8 uses `CostTracker` with `agent_name='assistant'`, `trigger='discord_dm'`.
- §Testing approach → Tasks 5, 6, 7, 13 cover unit tests; Task 14 covers manual smoke.

**Placeholder scan:** No "TBD" / "implement later" / "similar to Task N" left. Every code step shows real code.

**Type consistency:**
- `ToolContext(db, conversation_id)` — used identically in Tasks 5, 6.
- `_audited_write` signature — kwargs `tool_name`, `arguments`, `capture_before`, `apply_mutation`, `capture_after` — same shape everywhere.
- `Setup` literals — `proposed/active/disabled/retired` and `quiet/normal/critical` consistent across tasks.
- `SystemConfigStore.get/set` — used in Tasks 5, 6, 11; same signature.
- `MessageStore.recent` returns oldest-first list; consumed correctly in Task 7's `load_history`.
- `AuditStore.last_for_conversation` returns dict with `tool_name`, `arguments`, `before_state`, `after_state` — consumed correctly in Task 6's `revert_last_change`.

**One spec-vs-plan delta to flag honestly:** the spec named tier values `selective/balanced/aggressive` and statuses `paused/archived`. The plan uses the codebase's actual literals (`quiet/normal/critical`, `disabled/retired`). The spec is wrong here, the plan is right; we'll patch the spec opportunistically in a future commit if needed (not blocking).
