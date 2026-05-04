"""Brief-watcher: background asyncio task that polls scan_briefs every ~5s,
summarizes briefs that finished but haven't been DM'd to the operator yet,
and sends the summary as a Discord DM via the bot."""
from __future__ import annotations

import asyncio
import logging
import os

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
