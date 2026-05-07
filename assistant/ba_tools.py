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

    # ---- Researcher: operator surface (R-Task 8) ----

    from storage.findings import FindingStore
    from researcher.query_portfolio import (
        load as _portfolio_load,
        add as _portfolio_add,
        remove as _portfolio_remove,
    )
    from storage.conversations import SystemConfigStore
    from assistant.tools import _audited_write

    findings_store = FindingStore(ctx.db)
    sysconfig = SystemConfigStore(ctx.db)

    def _finding_to_dict(f) -> dict:
        return {
            "finding_id": f.finding_id,
            "detected_at": f.detected_at.isoformat() if f.detected_at else None,
            "finding_type": f.finding_type,
            "headline": f.headline,
            "urgency": f.urgency,
            "status": f.status,
            "evidence_job_count": len(f.evidence_job_ids),
        }

    def _finding_to_full_dict(f) -> dict:
        d = _finding_to_dict(f)
        d.update({
            "why_specific": f.why_specific,
            "portfolio_tie": f.portfolio_tie,
            "suggested_action": f.suggested_action,
            "evidence_job_ids": list(f.evidence_job_ids),
            "nudged_at": f.nudged_at.isoformat() if f.nudged_at else None,
            "dismissed_at": f.dismissed_at.isoformat() if f.dismissed_at else None,
            "dismissed_reason": f.dismissed_reason,
            "snoozed_until": f.snoozed_until.isoformat() if f.snoozed_until else None,
        })
        return d

    @tool
    def list_findings(
        status: str = "new",
        urgency: Optional[str] = None,
        days_back: int = 7,
        limit: int = 20,
    ) -> dict:
        """List Researcher findings, filtered by status (default 'new') and
        optional urgency. Returns compact summaries — use get_finding for
        the full WHY/portfolio/action prose.

        status: 'new' | 'nudged' | 'dismissed' | 'snoozed' (default 'new')
        urgency: 'this_week' | 'this_month' | 'monitor' (default any)
        days_back: window in days (default 7)
        limit: cap on rows returned (default 20)
        """
        if status not in ("new", "nudged", "dismissed", "snoozed"):
            return {"error": f"validation: status must be new|nudged|dismissed|snoozed, got {status!r}"}
        if urgency is not None and urgency not in ("this_week", "this_month", "monitor"):
            return {"error": f"validation: urgency must be this_week|this_month|monitor, got {urgency!r}"}
        rows = findings_store.list_by_status(
            status, urgency=urgency,
            days_back=int(days_back), limit=int(limit),
        )
        return {
            "status": status, "urgency": urgency, "days_back": days_back,
            "count": len(rows),
            "findings": [_finding_to_dict(f) for f in rows],
        }

    @tool
    def get_finding(finding_id: int) -> dict:
        """Return full detail for one finding: headline, WHY, portfolio
        tie, suggested action, all evidence job_ids, status timestamps.

        Use this when the operator asks 'tell me more about #N'."""
        f = findings_store.get(int(finding_id))
        if f is None:
            return {"error": f"finding {finding_id} not found"}
        return _finding_to_full_dict(f)

    @tool
    def dismiss_finding(finding_id: int, reason: Optional[str] = None) -> dict:
        """Dismiss a finding so it never re-surfaces. Optionally record a
        reason for the audit log. Reversible via revert_last_change."""
        def before():
            f = findings_store.get(int(finding_id))
            return None if f is None else {
                "status": f.status,
                "dismissed_at": f.dismissed_at.isoformat() if f.dismissed_at else None,
                "dismissed_reason": f.dismissed_reason,
            }

        def apply():
            f = findings_store.get(int(finding_id))
            if f is None:
                raise ValueError(f"finding {finding_id} not found")
            findings_store.mark_dismissed(int(finding_id), reason=reason)
            return {"finding_id": int(finding_id), "status": "dismissed",
                    "reason": reason}

        def after():
            f = findings_store.get(int(finding_id))
            return None if f is None else {
                "status": f.status,
                "dismissed_at": f.dismissed_at.isoformat() if f.dismissed_at else None,
                "dismissed_reason": f.dismissed_reason,
            }

        return _audited_write(
            ctx, tool_name="dismiss_finding",
            arguments={"finding_id": int(finding_id), "reason": reason},
            capture_before=before, apply_mutation=apply, capture_after=after,
        )

    @tool
    def snooze_finding(finding_id: int, days: int) -> dict:
        """Hide a finding for N days. After the date passes, the next
        Researcher pass auto-promotes it back to 'new'. Reversible via
        revert_last_change."""
        if int(days) <= 0:
            return {"error": "validation: days must be > 0"}

        def before():
            f = findings_store.get(int(finding_id))
            return None if f is None else {
                "status": f.status,
                "snoozed_until": f.snoozed_until.isoformat() if f.snoozed_until else None,
            }

        def apply():
            f = findings_store.get(int(finding_id))
            if f is None:
                raise ValueError(f"finding {finding_id} not found")
            findings_store.mark_snoozed(int(finding_id), days=int(days))
            return {"finding_id": int(finding_id), "status": "snoozed",
                    "days": int(days)}

        def after():
            f = findings_store.get(int(finding_id))
            return None if f is None else {
                "status": f.status,
                "snoozed_until": f.snoozed_until.isoformat() if f.snoozed_until else None,
            }

        return _audited_write(
            ctx, tool_name="snooze_finding",
            arguments={"finding_id": int(finding_id), "days": int(days)},
            capture_before=before, apply_mutation=apply, capture_after=after,
        )

    @tool
    def list_research_queries() -> dict:
        """Show the current Researcher query portfolio — what queries the
        autonomous pass scans every day."""
        portfolio = _portfolio_load(sysconfig)
        return {"count": len(portfolio), "queries": portfolio}

    @tool
    def add_research_query(
        query: str,
        payment_verified: Optional[str] = None,
        t: Optional[str] = None,
        hourly_rate: Optional[str] = None,
        amount: Optional[str] = None,
        proposals: Optional[str] = None,
        duration_v3: Optional[str] = None,
    ) -> dict:
        """Add a query to the Researcher portfolio. The autonomous pass
        will deep-scan this query every day. Same flat filter args as
        search_market. Returns {added: bool, query, filters}."""
        if not query or not query.strip():
            return {"error": "validation: query is required"}
        try:
            filters = _build_filters(
                payment_verified=payment_verified, t=t,
                hourly_rate=hourly_rate, amount=amount,
                proposals=proposals, duration_v3=duration_v3,
            )
        except ValueError as e:
            return {"error": f"validation: {e}"}

        def before():
            return {"portfolio": _portfolio_load(sysconfig)}

        def apply():
            added = _portfolio_add(
                sysconfig, query=query.strip(),
                filters=filters, added_by="operator",
            )
            return {"added": added, "query": query.strip(), "filters": filters}

        def after():
            return {"portfolio": _portfolio_load(sysconfig)}

        return _audited_write(
            ctx, tool_name="add_research_query",
            arguments={"query": query, "filters": filters},
            capture_before=before, apply_mutation=apply, capture_after=after,
        )

    @tool
    def remove_research_query(query: str) -> dict:
        """Remove all entries matching `query` (any filters) from the
        Researcher portfolio. Reversible via revert_last_change."""
        if not query or not query.strip():
            return {"error": "validation: query is required"}

        def before():
            return {"portfolio": _portfolio_load(sysconfig)}

        def apply():
            removed = _portfolio_remove(sysconfig, query=query.strip())
            return {"removed": removed, "query": query.strip()}

        def after():
            return {"portfolio": _portfolio_load(sysconfig)}

        return _audited_write(
            ctx, tool_name="remove_research_query",
            arguments={"query": query},
            capture_before=before, apply_mutation=apply, capture_after=after,
        )

    # ---- BA proposers (R-Task 11) — operator-pulled briefs ----

    from ai.ba_proposer import propose_project_brief, propose_setup
    from ai.market_analysis import backtest_filter_dsl
    from storage.goals import GoalStore
    from storage.portfolio import PortfolioStore as _PortfolioStore

    portfolio_store = _PortfolioStore(ctx.db)
    goals_store = GoalStore(ctx.db)

    @tool
    def propose_project(
        theme: str,
        window_days: int = 30,
        source_pattern: Optional[str] = None,
    ) -> dict:
        """Generate a project brief: 'what should I build next?'

        Reads corpus + portfolio + active goal. Returns a ProjectGapBrief
        with REQUIRED WHY fields (demand evidence + portfolio gap + goal
        fit). Use when the operator says 'what should I build?' or 'show
        me a project brief for X'.

        theme: free-text (e.g. 'voice AI agents', 'RAG eval pipelines')
        window_days: corpus lookback (default 30)
        source_pattern: optional SQL LIKE pattern to scope the corpus
                        (e.g. 'ba:%RAG%' to only consider BA-RAG-scanned
                        rows). None = all corpus.
        """
        if not theme or not theme.strip():
            return {"error": "validation: theme is required"}
        try:
            brief = propose_project_brief(
                ctx.db, theme=theme.strip(),
                portfolio=portfolio_store.list_all(),
                active_goal=goals_store.get_active(),
                window_days=int(window_days),
                source_pattern=source_pattern,
            )
        except Exception as e:  # noqa: BLE001
            return {"error": f"propose_project failed: {type(e).__name__}: {e}"}
        if brief is None:
            return {
                "error": "LLM produced output that didn't pass schema validation; "
                         "WHY fields likely too short. Retry or refine the theme."
            }
        return brief.model_dump()

    @tool
    def propose_setup_from_corpus(
        theme: str,
        # Filter-hint args mirror search_market — flat kwargs to avoid the
        # nested-dict trap. The proposer LLM uses these as a starting point
        # for filter_dsl but may refine.
        min_hourly: Optional[float] = None,
        max_hourly: Optional[float] = None,
        min_budget: Optional[float] = None,
        required_skills: Optional[list] = None,
        payment_verified_required: Optional[bool] = None,
        window_days: int = 30,
        source_pattern: Optional[str] = None,
    ) -> dict:
        """Generate a setup proposal: 'should we have a setup for X?'

        Returns a SetupProposal with REQUIRED WHY fields PLUS a
        backtest_count from running the proposed filter_dsl against the
        corpus. REFUSES proposals with backtest_count == 0 — if the
        corpus has no matches, suggests widening the theme or running
        more search_market scans first.

        After review, operator can call create_setup with the returned
        name/tier/filter_dsl/prose to actually activate the setup.

        theme: free-text describing the niche
        min_hourly / max_hourly / min_budget / required_skills /
            payment_verified_required: optional filter-hint kwargs
        window_days: corpus + backtest window (default 30)
        source_pattern: optional SQL LIKE to scope the corpus
        """
        if not theme or not theme.strip():
            return {"error": "validation: theme is required"}

        # Build filter_hint dict from the optional flat args
        hint = {}
        if min_hourly is not None:
            hint["min_hourly"] = float(min_hourly)
        if max_hourly is not None:
            hint["max_hourly"] = float(max_hourly)
        if min_budget is not None:
            hint["min_budget"] = float(min_budget)
        if required_skills:
            hint["required_skills"] = list(required_skills)
        if payment_verified_required is not None:
            hint["payment_verified_required"] = bool(payment_verified_required)

        try:
            proposal = propose_setup(
                ctx.db, theme=theme.strip(),
                portfolio=portfolio_store.list_all(),
                active_goal=goals_store.get_active(),
                filter_hint=hint,
                window_days=int(window_days),
                source_pattern=source_pattern,
            )
        except Exception as e:  # noqa: BLE001
            return {"error": f"propose_setup failed: {type(e).__name__}: {e}"}

        if proposal is None:
            return {
                "error": "LLM produced output that didn't pass schema validation; "
                         "WHY fields likely too short. Retry or refine the theme."
            }

        # Backtest the proposed filter_dsl against the corpus.
        try:
            backtest = backtest_filter_dsl(
                ctx.db,
                filter_dsl=proposal.filter_dsl,
                window_days=int(window_days),
            )
        except Exception as e:  # noqa: BLE001
            return {
                "error": f"proposal generated but backtest failed: "
                         f"{type(e).__name__}: {e}",
                "proposal": proposal.model_dump(),
            }

        if backtest.match_count == 0:
            return {
                "error": (
                    "proposed filter has 0 corpus matches in the last "
                    f"{window_days} days; widen the theme or run more "
                    "search_market scans to seed the corpus, then retry."
                ),
                "proposal": proposal.model_dump(),
                "backtest": backtest.to_dict(),
            }

        # Overwrite the LLM-supplied backtest_count placeholder with reality.
        out = proposal.model_dump()
        out["backtest_count"] = backtest.match_count
        out["backtest"] = backtest.to_dict()
        return out

    return [
        search_market, analyze_corpus, backtest_setup,
        list_findings, get_finding,
        dismiss_finding, snooze_finding,
        list_research_queries, add_research_query, remove_research_query,
        propose_project, propose_setup_from_corpus,
    ]
