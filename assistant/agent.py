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


def _history_window() -> int:
    return int(os.environ.get("ASSISTANT_HISTORY_WINDOW", "40"))


def _summarize_history(messages: list[dict]) -> str:
    """Cheap summarizer: collapse a chunk of messages into bullets using the
    same model. Synchronous; called from maybe_summarize after a reply."""
    bullets = []
    for m in messages:
        role = m["role"]
        text = (m["content"] or "")[:300]
        bullets.append(f"- ({role}) {text}")
    raw = "\n".join(bullets)
    agent = create_agent(model=_model(), tools=[])
    out = agent.invoke({"messages": [
        {"role": "system", "content": "Summarize the following exchange in 4-6 bullets, focused on what the operator asked, what config changed, and any open threads. Be terse."},
        {"role": "user", "content": raw},
    ]})
    msgs = out.get("messages") or []
    if not msgs:
        return ""
    final = msgs[-1].content if hasattr(msgs[-1], "content") else str(msgs[-1])
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
        trigger="discord_question",
        trigger_context={"conversation_id": conversation_id, "discord_user_id": discord_user_id},
    ) as tracker:
        agent = create_agent(model=_model(), tools=tools)
        result = agent.invoke(
            {"messages": messages},
            config={"callbacks": [tracker], "recursion_limit": 25},
        )

    msgs = result.get("messages") or []
    final_msg = msgs[-1] if msgs else None
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
