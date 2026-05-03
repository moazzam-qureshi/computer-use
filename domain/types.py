"""Domain types. Pure data, no I/O. These cross module boundaries."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Optional


JobId = str
SetupId = int
OrderId = int
SignalId = int


@dataclass
class FilterDsl:
    """Wraps a filter spec. The spec is a dict of the form:
        { "all_of": [rule, ...] } or { "any_of": [rule, ...] } or a single rule
    Each rule is a single-key dict like {"skill_in": ["rag"]} or {"budget_min_at_least": 1500}.
    Supported rule keys (Phase 1):
        skill_in: [str]                       — case-insensitive match against job.skills
        budget_min_at_least: number           — job.budget_min_usd or budget_max_usd >= n
        budget_kind_in: ["fixed","hourly"]
        client_payment_verified: bool
        client_country_in: [str]
        description_matches: str (regex, case-insensitive)
    """
    spec: dict


@dataclass
class Job:
    job_id: JobId
    url: str
    title: str
    description: Optional[str] = None
    budget_kind: Optional[str] = None
    budget_min_usd: Optional[float] = None
    budget_max_usd: Optional[float] = None
    skills: list[str] = field(default_factory=list)
    client_country: Optional[str] = None
    client_payment_verified: Optional[bool] = None
    client_rating: Optional[float] = None
    client_hires: Optional[int] = None
    client_total_spent_usd: Optional[float] = None
    posted_at: Optional[datetime] = None
    posted_text: Optional[str] = None  # human-readable, e.g. '17 minutes ago' (Upwork-rendered)
    proposals_count_at_first_scrape: Optional[int] = None


@dataclass
class Setup:
    setup_id: SetupId
    name: str
    status: Literal["proposed", "active", "disabled", "retired"]
    tier: Literal["quiet", "normal", "critical"]
    filter_dsl: FilterDsl
    prose_definition: Optional[str]
    pitch_template_id: Optional[int]
    cover_letter_template_id: Optional[int]
    auto_apply_enabled: bool
    escalation_config: dict


@dataclass
class MatchResult:
    matched: bool
    matched_rules: list[str] = field(default_factory=list)
    unmet_rules: list[str] = field(default_factory=list)
    score: float = 0.0          # 0..1; reserved for LLM tie-break in Phase 2


@dataclass
class Signal:
    signal_id: Optional[SignalId]
    job_id: JobId
    primary_setup_id: SetupId
    matched_setups: list[dict]  # [{setup_id, match_reason: 'rule', matched_rules: [...]}]
    fired_at: Optional[datetime]
    market_state: dict


@dataclass
class Order:
    order_id: Optional[OrderId]
    signal_id: SignalId
    job_id: JobId
    setup_id: SetupId
    status: Literal["drafting", "awaiting_approval", "approved", "staging", "attempting", "submitted", "cancelled", "failed"]
    bid_amount_usd: Optional[float]
    connects_spent: Optional[int]
    cover_letter_body: Optional[str]
    doc_url: Optional[str]
    screening_answers_json: Optional[dict]
    drafted_at: Optional[datetime]
    approved_at: Optional[datetime]
    submitted_at: Optional[datetime]
    failed_reason: Optional[str]
    idempotency_key: str


@dataclass
class OutcomeEvent:
    id: Optional[int]
    order_id: OrderId
    event_type: Literal["submitted", "viewed", "replied", "interviewed", "hired", "declined", "ghosted"]
    observed_at: datetime
    source: Literal["discord_manual", "upwork_my_proposals_scrape", "self_reported"]
    notes: Optional[str]
