"""Proposal Doc body + cover letter.

Ports the legacy proposal.generate_doc_proposal pattern verbatim:
  - response_format={'type': 'json_object'} (raw JSON, no Pydantic schema)
  - _coerce() flattens whatever shape the LLM returned into clean strings
  - Pydantic model is built FROM the coerced fields, not used as the LLM contract

This is the legacy pattern because Pydantic structured_output is too rigid
when the LLM occasionally returns dicts instead of strings (or vice versa).
The legacy code shipped without ever crashing on parse errors.
"""
from __future__ import annotations

import json
import os
from typing import Optional

from openai import OpenAI

from ai.schemas import ProposalDraft, CoverLetter
from ai.prompts.proposal import PROPOSAL_SYSTEM, PROPOSAL_USER
from ai.prompts.cover_letter import COVER_LETTER_SYSTEM, COVER_LETTER_USER
from ai.cost_tracker import CostTracker
from storage.agent_runs import AgentRunStore
from domain.types import Job


def _strip_em_dashes(text: str) -> str:
    """Remove em-dashes (an LLM tell). Replace with comma + space."""
    if not text:
        return text
    out = text
    out = out.replace(" — ", ", ")
    out = out.replace(" – ", ", ")
    out = out.replace("—", ", ")
    out = out.replace("–", ", ")
    out = out.replace(" -- ", ", ")
    return out


def _coerce_to_string_list(value, default_items: list[str]) -> list[str]:
    """Accept whatever the LLM returned (list of strings, list of dicts, single
    string with newlines, or None) and return a clean list[str].

    Mirrors the legacy proposal._coerce() with one adjustment: returns a list
    instead of a single markdown string, because ProposalDraft expects list[str].
    """
    if value is None or value == "":
        return default_items
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if isinstance(item, str):
                s = item.strip()
                if s:
                    out.append(_strip_em_dashes(s))
            elif isinstance(item, dict):
                # {name: description} -> "name description"
                parts = []
                for k, v in item.items():
                    k_str = str(k).strip()
                    v_str = str(v).strip() if v is not None else ""
                    if v_str:
                        parts.append(f"{k_str} {v_str}".strip())
                    elif k_str:
                        parts.append(k_str)
                if parts:
                    out.append(_strip_em_dashes(" ".join(parts)))
            else:
                s = str(item).strip()
                if s:
                    out.append(_strip_em_dashes(s))
        return out or default_items
    # Single string with embedded newlines or bullets
    if isinstance(value, str):
        lines = [ln.strip().lstrip("-* ").strip() for ln in value.splitlines()]
        lines = [_strip_em_dashes(ln) for ln in lines if ln]
        return lines or default_items
    return default_items


def _coerce_to_string(value, default: str) -> str:
    if value is None:
        return default
    if isinstance(value, list):
        return _strip_em_dashes(" ".join(str(x).strip() for x in value if str(x).strip())) or default
    return _strip_em_dashes(str(value).strip()) or default


def generate_proposal(
    job: Job,
    portfolio_items: list,
    *,
    agent_run_store: AgentRunStore,
    parent_run_id: Optional[int] = None,
    model: str = "gpt-4o-mini",
) -> ProposalDraft:
    """One LLM call (raw JSON), then coerce into ProposalDraft."""
    portfolio_summary = "\n".join(
        f"- {p.get('name')}: {', '.join(p.get('relevance_tags') or [])}" for p in portfolio_items
    ) or "(no portfolio items provided)"
    user = PROPOSAL_USER.format(
        title=job.title,
        budget_text=_budget_text(job),
        skills=", ".join(job.skills or []),
        description=(job.description or "")[:5000],
        portfolio_summary=portfolio_summary,
    )

    # OpenAI requires the literal word 'json' to appear in the messages when
    # using response_format=json_object. Append a one-line trailing instruction.
    user_with_json_hint = user + "\n\nReturn ONLY a JSON object with the fields above. No preamble, no markdown fences."

    client = OpenAI()
    with CostTracker(
        agent_run_store,
        agent_name="proposal",
        trigger="per_job",
        trigger_context={"job_id": job.job_id},
        parent_run_id=parent_run_id,
    ):
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": PROPOSAL_SYSTEM},
                {"role": "user", "content": user_with_json_hint},
            ],
            temperature=0.4,
            response_format={"type": "json_object"},
            # Generous: a proposal has 8 fields including a 100-150 word about_me,
            # 3-5 phase descriptions, deliverables, timeline, questions, and a
            # multi-line Mermaid graph. Under ~3500 the model truncates the last
            # fields written (mermaid_diagram tends to be last) and our defaults
            # kick in, which produces the generic User->System->Result graph.
            max_tokens=6000,
        )

    raw = (resp.choices[0].message.content or "").strip()
    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = {}

    return ProposalDraft(
        title=_coerce_to_string(parsed.get("title"), job.title or "AI engineering proposal"),
        opener=_coerce_to_string(parsed.get("opener"), "Hey, spent some time digging into your post. Here's how I'd approach it."),
        approach_phases=_coerce_to_string_list(
            parsed.get("approach_phases") or parsed.get("approach"),
            [
                "**Phase 1: Discovery.** Review the current setup, profile what works and what does not. Output: an architecture map and a prioritised work list.",
                "**Phase 2: Core build.** Ship the smallest version that delivers real value. Output: a working system.",
                "**Phase 3: Iterate.** Tune based on actual usage.",
            ],
        ),
        deliverables=_coerce_to_string_list(
            parsed.get("deliverables"),
            [
                "Working system in production",
                "Documentation and clean handover",
                "One sync per week with progress demos",
            ],
        ),
        timeline=_coerce_to_string_list(
            parsed.get("timeline"),
            [
                "Week 1-2: Discovery and architecture",
                "Week 3-4: Core build",
                "Week 5+: Iterate based on real usage",
            ],
        ),
        questions=_coerce_to_string_list(
            parsed.get("questions"),
            [
                "What does success look like for this in 90 days?",
                "Any constraints I should know about (compliance, existing infra, team size)?",
            ],
        ),
        # No default for the diagram. A meaningful diagram requires real LLM
        # reasoning about THIS specific job; falling back to a generic
        # User->System->Result placeholder is worse than no diagram at all.
        mermaid_diagram=_coerce_to_string(parsed.get("mermaid_diagram"), ""),
        about_me=_coerce_to_string(parsed.get("about_me"), "I'm a senior AI engineer focused on production systems. Recent work spans agents, RAG, voice, and multi-tenant SaaS. I've shipped real systems for technical buyers and tend to ask the hard scoping questions early."),
    )


def generate_cover_letter(
    job: Job,
    detected_client_name: Optional[str],
    *,
    agent_run_store: AgentRunStore,
    parent_run_id: Optional[int] = None,
    model: str = "gpt-5-mini",
    system_prompt_override: Optional[str] = None,
) -> CoverLetter:
    """Per-job operator-voice cover letter via gpt-4o-mini.

    The system prompt locks the structure (hook, insight, questions, soft
    pivot, doc line, soft close, sign-off) and bans the AI-tells (em-dashes,
    corporate vocabulary, filler greetings, self-claims). The LLM fills the
    structure with a fresh insight + questions tailored to THIS job.

    The {{doc_url}} placeholder is preserved in the returned body and the
    caller (bidder.draft_pipeline.draft_order) substitutes the real URL after
    the Doc has been created.
    """
    user = COVER_LETTER_USER.format(
        title=job.title or "(no title)",
        budget_text=_budget_text(job),
        skills=", ".join(job.skills or []) or "(none listed)",
        description=(job.description or "(no description)")[:4000],
        client_name=detected_client_name or "(none, use 'Hey,')",
    )

    client = OpenAI()
    with CostTracker(
        agent_run_store,
        agent_name="cover_letter_gen",
        trigger="per_job",
        trigger_context={"job_id": job.job_id},
        parent_run_id=parent_run_id,
    ):
        # gpt-5-* reasoning models reject `temperature` and use
        # `max_completion_tokens`. gpt-4* models use `max_tokens` and accept
        # `temperature`. Branch on the model name so swapping models doesn't
        # require touching scheduler-level code.
        is_reasoning = model.startswith("gpt-5") or model.startswith("o1") or model.startswith("o3")
        if is_reasoning:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt_override or COVER_LETTER_SYSTEM},
                    {"role": "user", "content": user},
                ],
                max_completion_tokens=4000,
            )
        else:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt_override or COVER_LETTER_SYSTEM},
                    {"role": "user", "content": user},
                ],
                temperature=0.3,
                max_tokens=600,
            )

    body = (resp.choices[0].message.content or "").strip()
    body = _strip_em_dashes(body)

    # Defense in depth: if the LLM dropped the {{doc_url}} placeholder, the
    # downstream caller's str.replace() turns into a no-op and we ship a
    # cover letter without the doc link, which is the entire point of the
    # message. Append a doc line at the end if missing rather than fail.
    if "{{doc_url}}" not in body:
        body = body.rstrip() + "\n\nWrote up the full approach here if you want a look: {{doc_url}}\n\n- Moazzam"

    return CoverLetter(body=body)


def _budget_text(job: Job) -> str:
    if job.budget_min_usd is None and job.budget_max_usd is None:
        return "unknown"
    if job.budget_min_usd == job.budget_max_usd or job.budget_max_usd is None:
        return f"{job.budget_kind} ${job.budget_min_usd:.0f}"
    return f"{job.budget_kind} ${job.budget_min_usd:.0f}-${job.budget_max_usd:.0f}"
