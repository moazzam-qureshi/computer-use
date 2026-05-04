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
    and returning a string. Returns True if summarization ran."""
    msgs = MessageStore(db)
    active = msgs.count_active(conversation_id)
    if active < threshold:
        return False
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
