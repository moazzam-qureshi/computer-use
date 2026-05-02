"""Pydantic models used as `response_format` in create_agent calls. One source of truth."""
from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel, Field


class Enrichment(BaseModel):
    extracted_tech: list[str] = Field(default_factory=list, description="Technologies/tools mentioned, normalized lowercase")
    pain_points: list[str] = Field(default_factory=list, description="What the client is struggling with")
    red_flags: list[str] = Field(default_factory=list, description="vague-spec, unrealistic-budget, scope-creep, etc.")
    green_flags: list[str] = Field(default_factory=list, description="specific outcome, named tech, etc.")
    project_shape: Optional[Literal["greenfield-build", "fix-existing", "audit", "integration", "ongoing-retainer", "prototype", "mvp", "scale-up"]] = None
    buyer_sophistication: Optional[Literal["technical-founder", "nontechnical-founder", "agency", "enterprise", "recruiter"]] = None


class RelevanceCheck(BaseModel):
    """Tie-break for the rule pre-filter."""
    relevant: bool = Field(description="Is this job actually a fit for the setup, beyond the surface rule match?")
    score: float = Field(ge=0.0, le=1.0, description="Confidence 0..1")
    reasoning: str = Field(description="One or two sentences explaining the call")


class ProposalDraft(BaseModel):
    """Doc body for the Google Doc proposal."""
    title: str = Field(description="6-12 word outcome line; not the raw job title")
    opener: str = Field(description="2-3 sentence opener with sharp specific insight")
    approach_phases: list[str] = Field(description="3-5 phases with bold-name + 1-2 sentences each")
    deliverables: list[str] = Field(description="3-6 concrete deliverables")
    timeline: list[str] = Field(description="3-5 week-by-week or phase-by-phase bullets")
    questions: list[str] = Field(description="2-3 sharp clarifying questions")
    mermaid_diagram: str = Field(description="A complete mermaid graph definition; never empty")
    about_me: str = Field(description="100-150 words tailored to job, picking 2-3 most relevant past projects")


class CoverLetter(BaseModel):
    """The 35-word Discord/Upwork cover letter that links to the Doc."""
    body: str = Field(description="The full cover letter text including the Doc URL placeholder {{doc_url}}")


class ScreeningAnswer(BaseModel):
    answer: str = Field(description="A direct, conversational answer to a screening question")
