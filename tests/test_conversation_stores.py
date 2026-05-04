"""Integration tests for conversation/audit/system_config stores. Uses real Postgres."""
from __future__ import annotations

import os
import pytest
from dotenv import load_dotenv

load_dotenv()

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
    assert [m["role"] for m in recent[-2:]] == ["user", "assistant"]
    # Last two messages should be the ones we appended.
    assert recent[-2]["content"] == "hello"
    assert recent[-1]["content"] == "hi"


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
