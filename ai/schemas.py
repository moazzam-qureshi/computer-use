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
