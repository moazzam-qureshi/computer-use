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

    return [search_market]
