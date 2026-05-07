"""LLM-backed compression of ResearcherFindings into Discord DMs.

Two entry points:
- write_nudge(finding) → 50-120 word DM for a single this_week finding
- write_digest(findings) → 150-300 word DM for several this_month findings

Both are gpt-5-mini, no tools, free-text output (no structured response —
the schema-enforcement work happened upstream when the finding was
created; here we just compress for human eyes).
"""
from __future__ import annotations

from typing import Optional

from langchain.agents import create_agent

from ai.cost_tracker import CostTracker
from ai.prompts.nudge_writer import (
    DIGEST_WRITER_SYSTEM, DIGEST_WRITER_USER,
    NUDGE_WRITER_SYSTEM, NUDGE_WRITER_USER,
)
from storage.agent_runs import AgentRunStore
from storage.findings import ResearcherFinding


def write_nudge(
    finding: ResearcherFinding,
    *,
    agent_run_store: AgentRunStore,
    parent_run_id: Optional[int] = None,
    model: str = "gpt-5-mini",
) -> str:
    """Compress one finding into a 50-120 word DM. Returns the DM text."""
    user = NUDGE_WRITER_USER.format(
        finding_id=finding.finding_id or "?",
        urgency=finding.urgency,
        finding_type=finding.finding_type,
        headline=finding.headline,
        why_specific=finding.why_specific,
        portfolio_tie=finding.portfolio_tie,
        suggested_action=finding.suggested_action,
        evidence_count=len(finding.evidence_job_ids),
    )
    with CostTracker(
        agent_run_store,
        agent_name="nudge_writer",
        trigger="researcher_pass",
        trigger_context={"finding_id": finding.finding_id},
        parent_run_id=parent_run_id,
    ) as tracker:
        agent = create_agent(model=model, tools=[])
        out = agent.invoke(
            {"messages": [
                {"role": "system", "content": NUDGE_WRITER_SYSTEM},
                {"role": "user", "content": user},
            ]},
            config={"callbacks": [tracker]},
        )
    msgs = out.get("messages") or []
    final = msgs[-1] if msgs else None
    text = getattr(final, "content", "") if final is not None else ""
    if not isinstance(text, str):
        text = str(text)
    return text.strip() or _fallback_nudge_text(finding)


def write_digest(
    findings: list[ResearcherFinding],
    *,
    agent_run_store: AgentRunStore,
    parent_run_id: Optional[int] = None,
    model: str = "gpt-5-mini",
) -> str:
    """Compress N this_month findings into one digest DM."""
    findings_block = "\n\n".join(
        f"#{f.finding_id} [{f.finding_type}]\n"
        f"  HEADLINE: {f.headline}\n"
        f"  WHY: {f.why_specific[:300]}\n"
        f"  PORTFOLIO: {f.portfolio_tie[:200]}\n"
        f"  ACTION: {f.suggested_action[:300]}"
        for f in findings
    )
    user = DIGEST_WRITER_USER.format(
        n=len(findings),
        findings_block=findings_block,
    )
    with CostTracker(
        agent_run_store,
        agent_name="nudge_digest_writer",
        trigger="researcher_pass",
        trigger_context={"finding_count": len(findings)},
        parent_run_id=parent_run_id,
    ) as tracker:
        agent = create_agent(model=model, tools=[])
        out = agent.invoke(
            {"messages": [
                {"role": "system", "content": DIGEST_WRITER_SYSTEM},
                {"role": "user", "content": user},
            ]},
            config={"callbacks": [tracker]},
        )
    msgs = out.get("messages") or []
    final = msgs[-1] if msgs else None
    text = getattr(final, "content", "") if final is not None else ""
    if not isinstance(text, str):
        text = str(text)
    return text.strip() or _fallback_digest_text(findings)


def _fallback_nudge_text(finding: ResearcherFinding) -> str:
    """If the LLM returns nothing, fall back to a deterministic minimal
    DM. Better than dropping the nudge silently."""
    return (
        f"Researcher #{finding.finding_id}: {finding.headline}\n"
        f"Reply 'tell me more about #{finding.finding_id}' for full brief."
    )


def _fallback_digest_text(findings: list[ResearcherFinding]) -> str:
    lines = [f"Researcher digest: {len(findings)} new findings today."]
    for f in findings:
        lines.append(f"- #{f.finding_id} {f.headline}")
    lines.append("Reply 'tell me more about #N' for any.")
    return "\n".join(lines)
