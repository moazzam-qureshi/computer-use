"""Nudge engine: decides which Researcher findings DM the operator and when.

Rules (per spec §5.7):
- urgency='this_week' → immediate DM, one per finding
- urgency='this_month' → defer to daily digest (max 5 per digest)
- urgency='monitor'   → no action; operator can list_findings to see them
- Digest fires only when ≥24h since last digest AND backlog is non-empty

Status FSM contract (enforced by FindingStore):
- A finding goes 'new' → 'nudged' once we DM it (individual or in digest)
- Once 'nudged' it never re-fires unless operator dismisses + a new pass
  produces a new finding with the same dedup_key (which won't happen —
  dedup_key persists across status changes)

System_config keys this module reads/writes:
- 'researcher_last_digest_at'  → ISO8601 timestamp of last digest DM
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional

from ai.nudge_writer import write_digest, write_nudge
from assistant.prompts import truncate_for_discord
from storage.agent_runs import AgentRunStore
from storage.connection import Database
from storage.conversations import SystemConfigStore
from storage.findings import FindingStore, ResearcherFinding


_LAST_DIGEST_KEY = "researcher_last_digest_at"
_DIGEST_COOLDOWN_HOURS = 24
_DIGEST_MAX_FINDINGS = 5


# A typed callable so the engine doesn't import discord directly.
# Signature: (text: str) -> Awaitable[None]
DiscordSender = Callable[[str], Awaitable[None]]


@dataclass
class NudgeResult:
    immediate_sent: int = 0
    digest_sent: bool = False
    digest_finding_ids: list[int] = field(default_factory=list)
    deferred_to_digest: int = 0
    skipped_monitor: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "immediate_sent": self.immediate_sent,
            "digest_sent": self.digest_sent,
            "digest_finding_ids": self.digest_finding_ids,
            "deferred_to_digest": self.deferred_to_digest,
            "skipped_monitor": self.skipped_monitor,
            "errors": self.errors,
        }


def _digest_due(sysconfig: SystemConfigStore) -> bool:
    """True if it's been ≥ cooldown since last digest (or never sent)."""
    last_iso = sysconfig.get(_LAST_DIGEST_KEY)
    if not last_iso:
        return True
    try:
        last = datetime.fromisoformat(last_iso)
    except (ValueError, TypeError):
        return True  # unparseable → treat as never sent
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - last >= timedelta(hours=_DIGEST_COOLDOWN_HOURS)


async def _send(text: str, send: DiscordSender, errors: list[str]) -> bool:
    """Dispatch a DM (chunked for Discord's 2000-char cap). Catches and
    records failure; returns True iff any chunk was sent successfully."""
    try:
        for chunk in truncate_for_discord(text):
            await send(chunk)
        return True
    except Exception as e:  # noqa: BLE001
        errors.append(f"DM send failed: {type(e).__name__}: {e}")
        return False


async def process_new_findings(
    db: Database,
    send: DiscordSender,
    *,
    digest_cooldown_hours: int = _DIGEST_COOLDOWN_HOURS,
    digest_max: int = _DIGEST_MAX_FINDINGS,
) -> NudgeResult:
    """Pull all status='new' findings, decide which to DM now vs defer.

    Args:
        db: live Database.
        send: async callable that takes a string and DMs the operator.
              Brief-watcher pattern: pass a closure over the discord bot.
        digest_cooldown_hours: override for testing.
        digest_max: override for testing.

    Returns:
        NudgeResult summary.
    """
    sysconfig = SystemConfigStore(db)
    findings_store = FindingStore(db)
    agent_runs = AgentRunStore(db)
    result = NudgeResult()

    # Pull a generous window so we don't miss anything
    new_findings = findings_store.list_by_status("new", days_back=30, limit=200)

    # Bucket by urgency
    immediate = [f for f in new_findings if f.urgency == "this_week"]
    deferred = [f for f in new_findings if f.urgency == "this_month"]
    monitor = [f for f in new_findings if f.urgency == "monitor"]
    result.skipped_monitor = len(monitor)

    # ---- 1. Immediate DMs for this_week ----
    for finding in immediate:
        try:
            text = await asyncio.to_thread(
                write_nudge, finding, agent_run_store=agent_runs,
            )
        except Exception as e:  # noqa: BLE001
            result.errors.append(
                f"nudge_writer failed finding_id={finding.finding_id}: "
                f"{type(e).__name__}: {e}"
            )
            continue
        if await _send(text, send, result.errors):
            findings_store.mark_nudged(finding.finding_id)
            result.immediate_sent += 1

    # ---- 2. Daily digest for this_month (cooldown-gated) ----
    if not deferred:
        return result

    # Override cooldown if caller gave a different value (tests)
    cooldown = digest_cooldown_hours
    if cooldown != _DIGEST_COOLDOWN_HOURS:
        # Apply the override to the due-check logic locally without
        # mutating the module constant.
        last_iso = sysconfig.get(_LAST_DIGEST_KEY)
        due = True
        if last_iso:
            try:
                last = datetime.fromisoformat(last_iso)
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                due = datetime.now(timezone.utc) - last >= timedelta(hours=cooldown)
            except (ValueError, TypeError):
                pass
    else:
        due = _digest_due(sysconfig)

    if not due:
        result.deferred_to_digest = len(deferred)
        return result

    # Take up to digest_max, oldest first (highest finding_id first since
    # FindingStore.list_by_status returns DESC; reverse to get oldest first)
    batch = list(reversed(deferred))[:digest_max]
    try:
        digest_text = await asyncio.to_thread(
            write_digest, batch, agent_run_store=agent_runs,
        )
    except Exception as e:  # noqa: BLE001
        result.errors.append(
            f"digest_writer failed: {type(e).__name__}: {e}"
        )
        result.deferred_to_digest = len(deferred)
        return result

    if await _send(digest_text, send, result.errors):
        for f in batch:
            findings_store.mark_nudged(f.finding_id)
            result.digest_finding_ids.append(f.finding_id)
        result.digest_sent = True
        sysconfig.set(_LAST_DIGEST_KEY, datetime.now(timezone.utc).isoformat())
        # Anything we didn't fit in the batch stays deferred for tomorrow
        result.deferred_to_digest = max(0, len(deferred) - len(batch))

    return result
