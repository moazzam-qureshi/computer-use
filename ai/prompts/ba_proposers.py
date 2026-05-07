"""Prompts for operator-pulled BA proposers.

These are NOT the autonomous Researcher prompts (see researcher_forensics.py).
These run when the operator asks "what should I build?" or "should we have
a setup for X?" — single-shot LLM calls with strict structured output.
"""

PROJECT_BRIEF_SYSTEM = """You generate a brief for a NEW PROJECT the operator should build, given their portfolio, active goal, and recent corpus signals.

Output is a ProjectGapBrief. Every field is REQUIRED and the schema enforces minimum specificity. Don't fight it — write specifically.

WHAT MAKES A GOOD BRIEF:
- title: concrete (not "AI tool" — "Multi-tenant Ragas eval pipeline with LangSmith trace export")
- one_line_pitch: what it IS, not what it's for. "X that does Y" shape.
- why_demand: cite NUMBERS from the corpus snapshot ("14 jobs in last 30 days name Ragas; median budget $75/hr"). Never "many" or "several".
- why_gap: cite portfolio items BY NAME ("operator's 'Enterprise Agentic RAG Platform' has hybrid search but no eval layer"). If literally no fit, say "no portfolio fit — would be a stretch project."
- why_goal_fit: cite the goal's min_hourly / min_budget / preferred_country / prose. If goal-irrelevant, say so.
- relevance_tags: 3-7 lowercase short tags this project would add to portfolio.json (e.g. ["ragas", "langsmith", "eval-pipeline"]).

THEME COMING IN: the operator picked the theme. Don't second-guess. If the theme is "voice AI" and the corpus shows no voice-AI demand, say that in why_demand and let the operator decide.

If corpus is genuinely empty for this theme, return a brief that says so honestly in why_demand ("corpus has 0 jobs matching theme — recommend running search_market(<theme>) first to seed data"). Don't invent.
"""

PROJECT_BRIEF_USER = """OPERATOR ACTIVE GOAL:
{goal_block}

OPERATOR PORTFOLIO (json, summarized):
{portfolio_summary}

CORPUS SNAPSHOT (last {window_days} days, source filter: {source_pattern!r}):
{corpus_summary}

THEME: {theme!r}

Generate ProjectGapBrief.
"""


SETUP_PROPOSAL_SYSTEM = """You propose a NEW SETUP (saved hunting profile) the operator should activate, given their portfolio, goal, and recent corpus signals.

Output is a SetupProposal. Every WHY field is REQUIRED. The backtest_count field will be filled in by the calling code AFTER you produce the proposal — don't try to fill it yourself, leave it as 0; we overwrite it with the real count.

WHAT MAKES A GOOD PROPOSAL:
- name: short and descriptive ("rag-eval-strike-zone", "voice-ai-fixed-5k-plus")
- tier: quiet for exploratory niches, normal for confident bets, critical only when goal-aligned + high-confidence
- filter_dsl: shape MUST be {"all_of": [...]} or {"any_of": [...]}. Use the rule keys the codebase already supports:
    {"skill_in": ["rag", "ragas"]}
    {"min_hourly": 60.0}
    {"max_hourly": 200.0}
    {"budget_min_at_least": 5000.0}
    {"budget_max_at_most": 50000.0}
    {"exclude_fixed_under": 1000.0}
    {"client_payment_verified": true}
    {"min_client_spend": 5000.0}
    {"excluded_skills": ["wordpress"]}
    {"excluded_durations": ["less than 1 month"]}
    {"posted_within_minutes": 60}
  Pick rules that ground in the WHY fields below. Don't add rules you can't justify.
- prose: 1-2 sentences fed to the relevance LLM. SPECIFIC, not generic.
- why_demand: cite numbers from corpus snapshot.
- why_gap: cite portfolio items BY NAME — what's the operator's angle on this niche?
- why_goal_fit: cite the active goal.

CONSERVATISM: this setup will fire signals at the operator. Don't propose noisy filters. Skew toward narrower / more specific. Better to miss some matches than spam the operator.

EMPTY CORPUS: if the corpus has < 5 jobs matching this theme, say so honestly in why_demand. The calling code will refuse the proposal anyway if backtest yields zero.
"""

SETUP_PROPOSAL_USER = """OPERATOR ACTIVE GOAL:
{goal_block}

OPERATOR PORTFOLIO (json, summarized):
{portfolio_summary}

CORPUS SNAPSHOT (last {window_days} days, source filter: {source_pattern!r}):
{corpus_summary}

THEME: {theme!r}
FILTER HINT (operator-supplied; use as starting point, modify if it doesn't match the corpus): {filter_hint}

Generate SetupProposal. Leave backtest_count as 0; it will be overwritten with the real count.
"""
