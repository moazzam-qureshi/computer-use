"""Pydantic models used as `response_format` in create_agent calls. One source of truth."""
from __future__ import annotations

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field, field_validator


def _coerce_to_string_list(v: Any) -> list[str]:
    """Coerce a list whose items may be strings OR dicts/other into list[str].

    LLMs occasionally ignore the schema's list[str] hint and return list[dict]
    where each dict is a {name: description} pair. Rather than crash on the
    Pydantic validation error, flatten the dict into a single string so the
    proposal still gets generated. Keeps the pipeline robust.
    """
    if not isinstance(v, list):
        raise ValueError(f"expected a list, got {type(v).__name__}")
    out: list[str] = []
    for item in v:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            # Flatten {name: description} -> "name description". If multiple
            # keys, join key/value pairs with ' - '.
            parts = []
            for k, val in item.items():
                if val:
                    parts.append(f"{k} {val}".strip())
                else:
                    parts.append(str(k).strip())
            out.append(" ".join(parts) if parts else str(item))
        else:
            out.append(str(item))
    return out


class Enrichment(BaseModel):
    extracted_tech: list[str] = Field(default_factory=list, description="Technologies/tools mentioned, normalized lowercase")
    pain_points: list[str] = Field(default_factory=list, description="What the client is struggling with")
    red_flags: list[str] = Field(default_factory=list, description="vague-spec, unrealistic-budget, scope-creep, etc.")
    green_flags: list[str] = Field(default_factory=list, description="specific outcome, named tech, etc.")
    project_shape: Optional[Literal["greenfield-build", "fix-existing", "audit", "integration", "ongoing-retainer", "prototype", "mvp", "scale-up"]] = None
    buyer_sophistication: Optional[Literal["technical-founder", "nontechnical-founder", "individual", "agency", "enterprise", "recruiter"]] = None


class RelevanceCheck(BaseModel):
    """Tie-break for the rule pre-filter."""
    relevant: bool = Field(description="Is this job actually a fit for the setup, beyond the surface rule match?")
    score: float = Field(ge=0.0, le=1.0, description="Confidence 0..1")
    reasoning: str = Field(description="One or two sentences explaining the call")
    application_flags: list[str] = Field(
        default_factory=list,
        description=(
            "Non-blocking application-format requirements the operator should know about "
            "when reviewing the proposal in Discord. These do NOT cause rejection (the "
            "post is otherwise relevant), but require manual attention. Examples: "
            "'portfolio bundle requested', 'NDA required before kickoff', "
            "'4 specific screening questions to answer', 'must answer in Spanish', "
            "'specific timezone overlap (PST)'. If none, return an empty list."
        ),
    )


class ProposalDraft(BaseModel):
    """Doc body for the Google Doc proposal."""
    title: str = Field(description="6-12 word outcome line; not the raw job title")
    opener: str = Field(description="2-3 sentence opener with sharp specific insight")
    approach_phases: list[str] = Field(
        description=(
            "3-5 STRINGS. Each item is ONE complete sentence describing a phase "
            "of work. Each string starts with a bolded phase name in markdown "
            "(e.g. '**Phase 1: Discovery** - we walk through your existing "
            "stack and lock in the integration points.'). DO NOT return objects, "
            "key-value pairs, or nested structures. Just plain strings."
        )
    )
    deliverables: list[str] = Field(description="3-6 strings; each is one concrete deliverable")
    timeline: list[str] = Field(description="3-5 strings; each is one week-by-week or phase-by-phase bullet")
    questions: list[str] = Field(description="2-3 strings; each is one sharp clarifying question")
    mermaid_diagram: str = Field(description="A complete mermaid graph definition; never empty")
    about_me: str = Field(description="100-150 words tailored to job, picking 2-3 most relevant past projects")

    # Robustness: gpt-4o-mini sometimes returns list[dict] instead of list[str]
    # for these fields. Coerce dicts/non-strings into strings rather than fail
    # the whole proposal and leave the order stuck in 'drafting' forever.
    _coerce_approach = field_validator("approach_phases", mode="before")(_coerce_to_string_list)
    _coerce_deliverables = field_validator("deliverables", mode="before")(_coerce_to_string_list)
    _coerce_timeline = field_validator("timeline", mode="before")(_coerce_to_string_list)
    _coerce_questions = field_validator("questions", mode="before")(_coerce_to_string_list)


class CoverLetter(BaseModel):
    """The 35-word Discord/Upwork cover letter that links to the Doc."""
    body: str = Field(description="The full cover letter text including the Doc URL placeholder {{doc_url}}")


class ScreeningAnswer(BaseModel):
    answer: str = Field(description="A direct, conversational answer to a screening question")


class PanelExtraction(BaseModel):
    """Structured fields extracted from the raw text dump of an Upwork job-detail panel.

    The LLM sees the whole panel as plain text and pulls out every field. No
    regex maintenance, no per-layout brittleness. Missing fields stay None.
    """
    title: Optional[str] = Field(default=None, description="The job title (the canonical post title, not the section header).")
    posted_text: Optional[str] = Field(default=None, description="Human-readable 'posted N units ago' string, e.g. '17 minutes ago' or '2 days ago'.")

    budget_kind: Optional[Literal["fixed", "hourly"]] = Field(default=None, description="'fixed' for fixed-price jobs, 'hourly' for hourly jobs. None if neither is clearly stated.")
    budget_min_usd: Optional[float] = Field(default=None, description="For fixed-price: the budget. For hourly: the lower bound of the rate range. Numeric USD only, strip $ and commas.")
    budget_max_usd: Optional[float] = Field(default=None, description="For hourly with a range: the upper bound. For fixed-price with no range: leave None.")

    duration: Optional[str] = Field(default=None, description="Project duration phrase, e.g. '1 to 3 months', 'Less than 1 month', 'More than 6 months'.")
    experience_level: Optional[Literal["Entry level", "Intermediate", "Expert"]] = Field(default=None)
    hours_per_week: Optional[str] = Field(default=None, description="Hours-per-week phrase, e.g. 'Less than 30 hrs/week', '30+ hrs/week'.")

    skills: list[str] = Field(default_factory=list, description="Skill chips listed under 'Skills and Expertise' or similar. One skill per item, no duplicates, max 15.")
    description: Optional[str] = Field(default=None, description="The full job description text, including Summary and any sub-headings, up to 5000 chars.")

    client_country: Optional[str] = Field(default=None, description="The client's country, e.g. 'United States'.")
    client_payment_verified: Optional[bool] = Field(default=None, description="True if the page explicitly says 'Payment method verified' or similar.")
    client_rating: Optional[float] = Field(default=None, description="Client rating 0-5, e.g. 4.97. None if not shown.")
    client_total_spent_usd: Optional[float] = Field(default=None, description="Total amount the client has spent on Upwork in USD, e.g. 14000 for '$14K total spent'. Numeric only.")
    client_hires: Optional[int] = Field(default=None, description="Number of hires the client has made on Upwork, e.g. 58.")
    proposals_count: Optional[int] = Field(default=None, description="If the panel shows 'Proposals: 5 to 10' use the lower bound (5). If 'less than 5' use 5. None if not shown.")


class JobForensicFinding(BaseModel):
    """A specific actionable finding the Researcher surfaces from job descriptions.

    The whole point of this schema is to enforce specificity. min_length on the
    WHY fields prevents the LLM from returning shallow filler like "AI is hot".
    Empty evidence_job_ids is rejected (single-job 'patterns' aren't patterns).
    Hallucinated job_ids (not in input batch) are rejected at the call site.

    See docs/superpowers/specs/2026-05-05-researcher-design.md §5.5.
    """

    finding_type: Literal[
        "emerging_template",       # near-identical job descriptions
        "failure_mode_pattern",    # multiple jobs cite the same failed thing
        "tech_combo_emergence",    # specific tech combination spiking
        "specific_stack_demand",   # explicit ask for a specific stack
        "budget_anomaly",          # budget shift in a niche worth noting
        "geographic_cluster",      # geographic concentration worth noting
    ] = Field(description="Which kind of pattern this finding represents.")

    headline: str = Field(
        min_length=20,
        description=(
            "One-line specific summary. Right shape: '5 jobs this week want "
            "Ragas + LangSmith integration'. Wrong shape: 'AI jobs trending up'."
        ),
    )

    why_specific: str = Field(
        min_length=40,
        description=(
            "Cite job evidence: which specific jobs this is based on, what "
            "they say. Quote phrases when useful. Numbers help "
            "('3 of 5 jobs explicitly mention Vapi'). NEVER 'multiple jobs' "
            "or 'many clients' — be specific."
        ),
    )

    portfolio_tie: str = Field(
        min_length=30,
        description=(
            "Cite portfolio.json items by name. Either: (a) gap — operator "
            "doesn't have this; building it would unlock these jobs. Or (b) "
            "strength — operator's <named-project> matches; lead pitches with "
            "it. Or (c) 'no portfolio fit' — surface only if urgency is high."
        ),
    )

    suggested_action: str = Field(
        min_length=30,
        description=(
            "Specific next step. Not 'consider RAG' — 'build a Ragas eval "
            "demo this week, push to GitHub, add 1-paragraph case study to "
            "portfolio before bidding any of these'."
        ),
    )

    urgency: Literal["this_week", "this_month", "monitor"] = Field(
        description=(
            "this_week = act in <7 days or window closes (template emerging, "
            "tactical opportunity). this_month = important but not closing "
            "(persistent demand worth chasing). monitor = interesting but "
            "not actionable yet (early signal, watch for development)."
        ),
    )

    evidence_job_ids: list[str] = Field(
        min_length=2,
        description=(
            "List of job_ids from the input batch this finding is based on. "
            "MUST be at least 2 — single-job patterns are not patterns. "
            "MUST be IDs from the actual input batch — fabricated IDs are "
            "rejected by the call site."
        ),
    )


class ProjectGapBrief(BaseModel):
    """Operator-pulled brief: 'what should I build next?'

    Same WHY-enforcement philosophy as JobForensicFinding. The schema makes
    the LLM cite demand evidence + portfolio gap + goal fit; if it can't,
    pydantic rejects the response and the tool surfaces the error.
    """

    title: str = Field(
        min_length=10,
        description="Short name for the project to build. Concrete, not generic.",
    )
    one_line_pitch: str = Field(
        min_length=30,
        description="One-sentence summary of what this project IS.",
    )
    why_demand: str = Field(
        min_length=40,
        description=(
            "Cite corpus evidence: how many jobs / what budget range / what "
            "skills are repeatedly asked for. Must include numbers from the "
            "analyze_corpus snapshot."
        ),
    )
    why_gap: str = Field(
        min_length=30,
        description=(
            "Cite portfolio.json items by name. What's missing that this "
            "project would fill?"
        ),
    )
    why_goal_fit: str = Field(
        min_length=30,
        description=(
            "Cite the active goal (min_hourly / min_budget / preferred_country / "
            "prose). How does building this unlock the goal?"
        ),
    )
    relevance_tags: list[str] = Field(
        min_length=1,
        description=(
            "Tags to add to portfolio.json once the project ships. These "
            "will become the matching surface for future BA gap analysis."
        ),
    )


class SetupProposal(BaseModel):
    """Operator-pulled proposal: 'should we have a setup for X?'

    The proposer runs backtest_setup INTERNALLY before returning so
    backtest_count is grounded in real corpus matches. Refuses zero-match
    proposals — see assistant.ba_tools.propose_setup_from_corpus.
    """

    name: str = Field(
        min_length=5,
        description="Short setup name. Will become setups.name if approved.",
    )
    tier: Literal["quiet", "normal", "critical"] = Field(
        description=(
            "How loud this setup should be. quiet = whisper-only digests, "
            "normal = standard signal + DM, critical = high-priority + escalation."
        ),
    )
    filter_dsl: dict = Field(
        description=(
            "filter_dsl in the same shape as setups.filter_dsl. Will be "
            "validated through create_setup if operator approves."
        ),
    )
    prose: str = Field(
        min_length=30,
        description=(
            "Prose definition fed to the per-job relevance LLM. Specific, "
            "not generic — describes what makes a job actually fit this setup."
        ),
    )
    backtest_count: int = Field(
        ge=0,
        description=(
            "Count of corpus jobs in last 30 days that would have matched "
            "this filter_dsl. Set by the proposer (NOT the LLM); embedded "
            "for operator review."
        ),
    )
    why_demand: str = Field(
        min_length=40,
        description="Cite corpus evidence justifying this setup's existence.",
    )
    why_gap: str = Field(
        min_length=30,
        description=(
            "Cite portfolio.json items. Why does the operator have an angle "
            "on this niche specifically?"
        ),
    )
    why_goal_fit: str = Field(
        min_length=30,
        description="Cite the active goal. How does this setup advance it?",
    )
