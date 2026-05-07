"""BA module tools registered on the assistant agent.

These are the operator-facing surface for market intelligence:
  - search_market: card-level UIA scan, writes to corpus
  - analyze_corpus: pure SQL aggregation (Task 4)
  - backtest_setup: corpus match count for a hypothetical filter (Task 4)
  - find_portfolio_gaps: clusters with high demand and low coverage (Task 6)
  - propose_project: generate a ProjectGapBrief (Task 7)
  - propose_setup_from_corpus: generate a SetupProposal (Task 7)

Filter args are FLAT, not nested. The agent passes individual filter keys as
direct kwargs. This avoids the gpt-5-mini nested-dict trap that bit the
update_setup_filters surface earlier.
"""
from __future__ import annotations

from typing import Optional

from langchain_core.tools import BaseTool, tool
from pydantic import ValidationError

from ai.market_analysis import (
    analyze_corpus as _analyze_corpus,
    backtest_filter_dsl,
)
from upwork.search import ALLOWED_FILTER_KEYS
from upwork.search_driver import search_and_ingest


def _build_filters(
    payment_verified: Optional[str],
    t: Optional[str],
    hourly_rate: Optional[str],
    amount: Optional[str],
    proposals: Optional[str],
    duration_v3: Optional[str],
) -> dict:
    """Assemble the filters dict from flat kwargs. Drops None values."""
    raw = {
        "payment_verified": payment_verified,
        "t": t,
        "hourly_rate": hourly_rate,
        "amount": amount,
        "proposals": proposals,
        "duration_v3": duration_v3,
    }
    out = {k: v for k, v in raw.items() if v is not None and v != ""}
    # Defense: ALLOWED_FILTER_KEYS is the source of truth; if someone adds a
    # key here that the URL builder doesn't accept, fail loud at construction.
    unknown = set(out) - ALLOWED_FILTER_KEYS
    if unknown:
        raise ValueError(f"Unknown filter keys: {sorted(unknown)}")
    return out


def build_ba_tools(ctx) -> list[BaseTool]:
    """Construct BA tools that close over the assistant's ToolContext.

    Same shape as assistant.tools.build_tools so the caller in
    assistant/tools.py can extend its return list with these.
    """

    @tool
    def search_market(
        query: str,
        payment_verified: Optional[str] = None,
        t: Optional[str] = None,
        hourly_rate: Optional[str] = None,
        amount: Optional[str] = None,
        proposals: Optional[str] = None,
        duration_v3: Optional[str] = None,
        max_cards: int = 30,
    ) -> dict:
        """Run a card-level Upwork search and write results to the corpus.

        query: free-text search term (will be URL-encoded).

        All filter args are optional and use Upwork's URL-param values directly:
          payment_verified='1'                    only verified clients
          t='0' (Hourly) or '1' (Fixed-price)
          hourly_rate='25-35' | '50-' | '60-90'   (USD/hr range, open-ended OK)
          amount='0-99' | '100-499' | '500-999' | '1000-4999' | '5000-'
                                                  (Fixed-price tier)
          proposals='0-4' | '5-9' | '10-14' | '15-19' | '20-49'
                                                  (competition bucket)
          duration_v3='week' | 'month' | 'semester' | 'ongoing'
                                                  (project length: <1mo / 1-3mo / 3-6mo / 6mo+)

        max_cards: cap on returned cards. Default 30. Card capture is single
                   walk at 33% zoom — typical yield 8-12 cards per walk; raise
                   max_cards to scroll-and-rewalk for more.

        Returns:
          {query, filters, source, scanned, inserted, updated_existing, sample}.
          'source' is the tag written to jobs.source so analyze_corpus can scope.
        """
        try:
            filters = _build_filters(
                payment_verified=payment_verified,
                t=t,
                hourly_rate=hourly_rate,
                amount=amount,
                proposals=proposals,
                duration_v3=duration_v3,
            )
        except ValueError as e:
            return {"error": f"validation: {e}"}
        if not query or not query.strip():
            return {"error": "validation: query is required"}
        try:
            return search_and_ingest(
                ctx.db,
                query=query.strip(),
                filters=filters,
                max_cards=int(max_cards),
            )
        except Exception as e:  # noqa: BLE001 — surface to operator via tool result
            return {"error": f"search_market failed: {type(e).__name__}: {e}"}

    @tool
    def analyze_corpus(
        window_days: int = 30,
        source_pattern: Optional[str] = None,
    ) -> dict:
        """Aggregate the jobs corpus over a recent window. Pure SQL, no LLM.

        window_days: lookback in days (default 30).
        source_pattern: optional SQL LIKE pattern to scope rows by source
                        (e.g. 'ba:%RAG%' for BA-RAG-scanned jobs only,
                        'feed' for bidder-scanned only, None for everything).

        Returns: dict with total_jobs, top_skills, budget percentiles,
        weekly_volume, client_country_breakdown, payment_verified_share.
        """
        try:
            snapshot = _analyze_corpus(
                ctx.db,
                window_days=int(window_days),
                source_pattern=source_pattern,
            )
        except Exception as e:  # noqa: BLE001
            return {"error": f"analyze_corpus failed: {type(e).__name__}: {e}"}
        return snapshot.to_dict()

    @tool
    def backtest_setup(
        min_budget: Optional[float] = None,
        max_budget: Optional[float] = None,
        min_hourly: Optional[float] = None,
        max_hourly: Optional[float] = None,
        exclude_fixed_under: Optional[float] = None,
        required_skills: Optional[list] = None,
        excluded_skills: Optional[list] = None,
        min_client_spend: Optional[float] = None,
        payment_verified_required: Optional[bool] = None,
        excluded_durations: Optional[list] = None,
        max_post_age_minutes: Optional[int] = None,
        window_days: int = 30,
    ) -> dict:
        """Count corpus jobs that would have matched a hypothetical filter.

        All filter args are FLAT and optional — same shape as the set_setup_*
        tools, no nested patch dict. Use this BEFORE proposing a setup so
        you can cite a real backtest count in the proposal.

        Returns: {match_count, total_in_window, sample: [up to 5 jobs]}.
        """
        # Lazy import to avoid circular: assistant/tools.py imports ba_tools.
        from assistant.tools import (
            FiltersPatch as _FiltersPatch,
            _patch_to_filter_rules as _patch_to_rules,
        )
        patch = {
            "min_budget": min_budget, "max_budget": max_budget,
            "min_hourly": min_hourly, "max_hourly": max_hourly,
            "exclude_fixed_under": exclude_fixed_under,
            "required_skills": required_skills,
            "excluded_skills": excluded_skills,
            "min_client_spend": min_client_spend,
            "payment_verified_required": payment_verified_required,
            "excluded_durations": excluded_durations,
            "max_post_age_minutes": max_post_age_minutes,
        }
        # Drop Nones so FiltersPatch validation gets only what was passed.
        patch = {k: v for k, v in patch.items() if v is not None}
        try:
            patch_obj = _FiltersPatch(**patch)
        except ValidationError as e:
            return {"error": f"validation: {e}"}
        rules = _patch_to_rules(patch_obj)
        if not rules:
            return {"error": "no filter args provided; pass at least one"}
        filter_dsl = {"all_of": rules}
        try:
            result = backtest_filter_dsl(
                ctx.db, filter_dsl=filter_dsl,
                window_days=int(window_days),
            )
        except Exception as e:  # noqa: BLE001
            return {"error": f"backtest failed: {type(e).__name__}: {e}"}
        return {**result.to_dict(), "filter_dsl": filter_dsl}

    return [search_market, analyze_corpus, backtest_setup]
