"""Tools the assistant agent calls. Read tools + write tools + revert_last_change.
Each write tool wraps its mutation in _audited_write, which captures before/after
state and writes an audit-log row."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional, List

from langchain_core.tools import tool, BaseTool
from psycopg.types.json import Json
from pydantic import BaseModel, ValidationError

from storage.connection import Database
from storage.setups import SetupStore
from storage.portfolio import PortfolioStore
from storage.conversations import (
    ConversationStore, AuditStore, SystemConfigStore,
)
from storage.connects_ledger import ConnectsLedgerStore
from storage.goals import GoalStore, Goal
from storage.scan_briefs import BriefStore
from storage.bidder_state import BidderStateStore


class FiltersPatch(BaseModel):
    """Allowed filter-patch keys. Validated before merging into filter_dsl."""
    min_budget: Optional[float] = None
    max_budget: Optional[float] = None
    exclude_fixed_under: Optional[float] = None
    min_hourly: Optional[float] = None
    max_hourly: Optional[float] = None
    required_skills: Optional[list[str]] = None
    excluded_skills: Optional[list[str]] = None
    min_client_spend: Optional[float] = None
    payment_verified_required: Optional[bool] = None
    excluded_durations: Optional[list[str]] = None
    max_post_age_minutes: Optional[int] = None


def _patch_to_filter_rules(patch: FiltersPatch) -> list[dict]:
    """Convert a patch into a list of filter_dsl rules."""
    rules: list[dict] = []
    if patch.min_budget is not None:
        rules.append({"budget_min_at_least": patch.min_budget})
    if patch.required_skills:
        rules.append({"skill_in": patch.required_skills})
    if patch.payment_verified_required is True:
        rules.append({"client_payment_verified": True})
    if patch.max_budget is not None:
        rules.append({"budget_max_at_most": patch.max_budget})
    if patch.exclude_fixed_under is not None:
        rules.append({"exclude_fixed_under": patch.exclude_fixed_under})
    if patch.min_hourly is not None:
        rules.append({"min_hourly": patch.min_hourly})
    if patch.max_hourly is not None:
        rules.append({"max_hourly": patch.max_hourly})
    if patch.excluded_skills:
        rules.append({"excluded_skills": patch.excluded_skills})
    if patch.min_client_spend is not None:
        rules.append({"min_client_spend": patch.min_client_spend})
    if patch.excluded_durations:
        rules.append({"excluded_durations": patch.excluded_durations})
    if patch.max_post_age_minutes is not None:
        rules.append({"posted_within_minutes": int(patch.max_post_age_minutes)})
    return rules


def _merge_filter_rules(existing_spec: dict, new_rules: list[dict]) -> dict:
    """Merge new rules into an existing all_of spec. New rules with the same
    top-level key replace the old ones."""
    if not existing_spec:
        return {"all_of": new_rules}
    if "all_of" in existing_spec:
        old_rules = existing_spec["all_of"]
    elif "any_of" in existing_spec:
        old_rules = existing_spec["any_of"]
    else:
        old_rules = [existing_spec]
    new_keys = {list(r.keys())[0] for r in new_rules}
    kept = [r for r in old_rules if list(r.keys())[0] not in new_keys]
    return {"all_of": kept + new_rules}


@dataclass
class ToolContext:
    db: Database
    conversation_id: int


# ---------------------------------------------------------------------------
# _audited_write template: every write tool routes through this.
# ---------------------------------------------------------------------------

def _audited_write(
    ctx: ToolContext,
    *,
    tool_name: str,
    arguments: dict,
    capture_before: Callable[[], Optional[dict]],
    apply_mutation: Callable[[], dict],
    capture_after: Callable[[], Optional[dict]],
) -> dict:
    """Run a write mutation with before/after capture and audit logging.
    Errors raised by apply_mutation are caught and returned as {"error": ...}."""
    audit = AuditStore(ctx.db)
    try:
        before = capture_before()
        result = apply_mutation()
        after = capture_after()
    except Exception as e:  # noqa: BLE001 - we want to surface to the agent
        audit.record(
            conversation_id=ctx.conversation_id,
            tool_name=tool_name,
            arguments=arguments,
            result={"error": str(e)},
            before_state=None,
            after_state=None,
        )
        return {"error": str(e)}

    audit.record(
        conversation_id=ctx.conversation_id,
        tool_name=tool_name,
        arguments=arguments,
        result=result,
        before_state=before,
        after_state=after,
    )
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_to_dict(s) -> dict:
    return {
        "setup_id": s.setup_id,
        "name": s.name,
        "status": s.status,
        "tier": s.tier,
        "filter_dsl": s.filter_dsl.spec,
        "prose_definition": s.prose_definition,
        "auto_apply_enabled": s.auto_apply_enabled,
        "escalation_config": s.escalation_config,
        "ignored_clients": list(s.ignored_clients or []),
        "tone_override": s.tone_override,
    }


def _setup_summary(s) -> dict:
    spec = s.filter_dsl.spec or {}
    rules = spec.get("all_of") or spec.get("any_of") or [spec]
    rule_names = [list(r.keys())[0] for r in rules if isinstance(r, dict) and r]
    return {
        "setup_id": s.setup_id,
        "name": s.name,
        "status": s.status,
        "tier": s.tier,
        "auto_apply": s.auto_apply_enabled,
        "ignored_clients_count": len(s.ignored_clients or []),
        "filter_rule_keys": rule_names,
    }


def _goal_to_dict(g: Goal) -> dict:
    return {
        "goal_id": g.goal_id,
        "prose": g.prose,
        "target_metric": g.target_metric,
        "target_value": float(g.target_value) if g.target_value is not None else None,
        "horizon": g.horizon,
        "min_hourly": float(g.min_hourly) if g.min_hourly is not None else None,
        "min_budget": float(g.min_budget) if g.min_budget is not None else None,
        "preferred_country": g.preferred_country,
        "notes": g.notes,
        "created_at": g.created_at.isoformat() if g.created_at else None,
    }


def _today_window():
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, now


def _this_week_window():
    """Monday 00:00 UTC to now."""
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, now


# ---------------------------------------------------------------------------
# Tool builder. Tools need ctx, so we close over it.
# ---------------------------------------------------------------------------

def build_tools(ctx: ToolContext) -> list[BaseTool]:
    setups = SetupStore(ctx.db)
    portfolio = PortfolioStore(ctx.db)
    connects = ConnectsLedgerStore(ctx.db)
    sysconfig = SystemConfigStore(ctx.db)
    goals = GoalStore(ctx.db)
    briefs = BriefStore(ctx.db)
    bidder_state = BidderStateStore(ctx.db)

    @tool
    def list_setups() -> list[dict]:
        """List all setups (any status) with a compact summary."""
        from domain.types import Setup, FilterDsl
        with ctx.db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT setup_id, name, status, tier, filter_dsl, prose_definition,
                           pitch_template_id, cover_letter_template_id,
                           auto_apply_enabled, escalation_config,
                           ignored_clients, tone_override
                    FROM setups ORDER BY setup_id
                """)
                rows = cur.fetchall()
        out = []
        for r in rows:
            s = Setup(
                setup_id=r[0], name=r[1], status=r[2], tier=r[3],
                filter_dsl=FilterDsl(r[4]), prose_definition=r[5],
                pitch_template_id=r[6], cover_letter_template_id=r[7],
                auto_apply_enabled=r[8], escalation_config=r[9],
                ignored_clients=list(r[10] or []), tone_override=r[11],
            )
            out.append(_setup_summary(s))
        return out

    @tool
    def get_setup(setup_id: int) -> dict:
        """Return the full configuration of one setup, including filters,
        prose definition, ignored clients, and tone override."""
        s = setups.get(setup_id)
        if s is None:
            return {"error": f"setup_id {setup_id} not found"}
        return _setup_to_dict(s)

    @tool
    def list_orders(status: str = "awaiting_approval", limit: int = 20) -> list[dict]:
        """List orders by status. Default: awaiting_approval (drafted, not yet
        applied). Other statuses: drafting, approved, submitted, cancelled, failed."""
        with ctx.db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT o.order_id, o.job_id, o.setup_id, o.status, o.drafted_at,
                           j.title, j.url
                    FROM orders o
                    LEFT JOIN jobs j ON j.job_id = o.job_id
                    WHERE o.status = %s
                    ORDER BY o.drafted_at DESC NULLS LAST
                    LIMIT %s
                """, (status, limit))
                rows = cur.fetchall()
        return [
            {
                "order_id": r[0], "job_id": r[1], "setup_id": r[2],
                "status": r[3], "drafted_at": r[4].isoformat() if r[4] else None,
                "job_title": r[5], "job_url": r[6],
            }
            for r in rows
        ]

    @tool
    def get_job(job_id: str) -> dict:
        """Return job detail plus the most recent signal's match info if any.
        job_id is the URL slug used as the primary key."""
        with ctx.db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT job_id, url, title, description, budget_kind,
                           budget_min_usd, budget_max_usd,
                           client_country, client_payment_verified,
                           posted_at, posted_text
                    FROM jobs WHERE job_id = %s
                """, (job_id,))
                row = cur.fetchone()
                if row is None:
                    return {"error": f"job {job_id} not found"}
                cur.execute("""
                    SELECT primary_setup_id, matched_setups, fired_at
                    FROM signals WHERE job_id = %s
                    ORDER BY signal_id DESC LIMIT 1
                """, (job_id,))
                sig = cur.fetchone()
        return {
            "job_id": row[0], "url": row[1], "title": row[2],
            "description": (row[3] or "")[:1500],
            "budget_kind": row[4],
            "budget_min_usd": float(row[5]) if row[5] is not None else None,
            "budget_max_usd": float(row[6]) if row[6] is not None else None,
            "client_country": row[7], "client_payment_verified": row[8],
            "posted_at": row[9].isoformat() if row[9] else None,
            "posted_text": row[10],
            "latest_signal": (
                {
                    "primary_setup_id": sig[0],
                    "matched_setups": sig[1],
                    "fired_at": sig[2].isoformat() if sig[2] else None,
                } if sig else None
            ),
        }

    @tool
    def recent_activity(hours: int = 24) -> dict:
        """Aggregate counters from the last N hours: scrape cycles, jobs scanned,
        signals fired, orders drafted, applies, total LLM cost."""
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        with ctx.db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM scrape_runs WHERE started_at >= %s", (since,))
                cycles_run = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM jobs WHERE scraped_first_at >= %s", (since,))
                jobs_scanned = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM signals WHERE fired_at >= %s", (since,))
                signals_fired = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM orders WHERE drafted_at >= %s", (since,))
                drafts_created = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM orders WHERE submitted_at >= %s", (since,))
                applies = cur.fetchone()[0]
                cur.execute(
                    "SELECT coalesce(sum(total_cost_usd), 0) FROM agent_runs WHERE started_at >= %s",
                    (since,),
                )
                total_cost = cur.fetchone()[0]
        return {
            "hours": hours,
            "cycles_run": cycles_run,
            "jobs_scanned": jobs_scanned,
            "signals_fired": signals_fired,
            "drafts_created": drafts_created,
            "applies": applies,
            "total_llm_cost_usd": float(total_cost),
        }

    @tool
    def connects_status() -> dict:
        """Current connects spending state: daily/weekly cap, spent so far,
        and bidder-paused flag. Cap values come from system_config if set,
        else env defaults."""
        import os
        daily_cap = sysconfig.get("connects_daily_cap")
        if daily_cap is None:
            daily_cap = int(os.environ.get("CONNECTS_DAILY_CAP", "3"))
        weekly_cap = sysconfig.get("connects_weekly_cap")
        if weekly_cap is None:
            weekly_cap = int(os.environ.get("CONNECTS_WEEKLY_CAP", "10"))
        d_start, d_end = _today_window()
        w_start, w_end = _this_week_window()
        daily_spent = connects.spent_in_window(d_start, d_end)
        weekly_spent = connects.spent_in_window(w_start, w_end)
        return {
            "daily_cap": int(daily_cap),
            "weekly_cap": int(weekly_cap),
            "daily_spent": daily_spent,
            "weekly_spent": weekly_spent,
            "bidder_paused": bool(sysconfig.get("bidder_paused") or False),
        }

    @tool
    def list_portfolio_items() -> list[dict]:
        """Return the full portfolio (list of past projects). Each item has
        name, summary, client_context, outcome, tech (list), relevance_tags
        (list), year_completed."""
        return portfolio.list_all()

    @tool
    def search_jobs(query: str, status: Optional[str] = None) -> list[dict]:
        """Find jobs matching a substring in title or skills. status is
        optional and filters orders.status if provided."""
        like = f"%{query}%"
        with ctx.db.connection() as conn:
            with conn.cursor() as cur:
                if status:
                    cur.execute("""
                        SELECT j.job_id, j.title, j.client_country, j.posted_at, o.status
                        FROM jobs j
                        JOIN orders o ON o.job_id = j.job_id
                        WHERE (j.title ILIKE %s)
                          AND o.status = %s
                        ORDER BY j.posted_at DESC NULLS LAST
                        LIMIT 30
                    """, (like, status))
                else:
                    cur.execute("""
                        SELECT j.job_id, j.title, j.client_country, j.posted_at, NULL
                        FROM jobs j
                        WHERE j.title ILIKE %s
                        ORDER BY j.posted_at DESC NULLS LAST
                        LIMIT 30
                    """, (like,))
                rows = cur.fetchall()
        return [
            {
                "job_id": r[0], "title": r[1], "client_country": r[2],
                "posted_at": r[3].isoformat() if r[3] else None,
                "order_status": r[4],
            }
            for r in rows
        ]

    @tool
    def get_goal() -> dict:
        """Return the operator's current active goal as a dict, or {error: ...} if none set."""
        g = goals.get_active()
        if g is None:
            return {"error": "no active goal"}
        return _goal_to_dict(g)

    # ---- write tools ----

    def _apply_filter_patch(setup_id: int, patch: dict, *, audit_tool_name: str) -> dict:
        """Shared body: validate patch dict, merge into setup.filter_dsl,
        audit as `audit_tool_name`. Used by update_setup_filters AND the
        granular set_setup_<key> tools below — they all funnel through
        the same merge + audit so revert_last_change works identically."""
        try:
            patch_obj = FiltersPatch(**patch)
        except ValidationError as e:
            return {"error": f"validation: {e}"}
        new_rules = _patch_to_filter_rules(patch_obj)
        if not new_rules:
            return {"error": "patch contained no recognized keys"}

        def before():
            s = setups.get(setup_id)
            return None if s is None else {"filter_dsl": s.filter_dsl.spec}

        def apply():
            s = setups.get(setup_id)
            if s is None:
                raise ValueError(f"setup {setup_id} not found")
            merged = _merge_filter_rules(s.filter_dsl.spec, new_rules)
            with ctx.db.transaction() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE setups SET filter_dsl = %s WHERE setup_id = %s",
                        (Json(merged), setup_id),
                    )
            return {"setup_id": setup_id, "filter_dsl": merged}

        def after():
            s = setups.get(setup_id)
            return None if s is None else {"filter_dsl": s.filter_dsl.spec}

        return _audited_write(
            ctx, tool_name=audit_tool_name,
            arguments={"setup_id": setup_id, "patch": patch},
            capture_before=before, apply_mutation=apply, capture_after=after,
        )

    @tool
    def update_setup_filters(setup_id: int, patch: dict) -> dict:
        """Batch-merge filter rules into a setup. Prefer the single-purpose
        set_setup_* tools (set_setup_min_budget, set_setup_max_post_age_minutes,
        etc.) — use this only when you need to set 3+ filters at once.

        patch keys: min_budget, max_budget, exclude_fixed_under, min_hourly,
        max_hourly, required_skills, excluded_skills, min_client_spend,
        payment_verified_required, excluded_durations, max_post_age_minutes.
        Existing rules with the same key are replaced."""
        return _apply_filter_patch(setup_id, patch, audit_tool_name="update_setup_filters")

    @tool
    def set_setup_min_budget(setup_id: int, amount: float) -> dict:
        """Set the minimum budget filter (USD) on a setup. Replaces any
        existing min-budget rule. Jobs below this are hard-excluded."""
        return _apply_filter_patch(setup_id, {"min_budget": amount},
                                   audit_tool_name="update_setup_filters")

    @tool
    def set_setup_max_budget(setup_id: int, amount: float) -> dict:
        """Set the maximum budget filter (USD) on a setup. Replaces any
        existing max-budget rule."""
        return _apply_filter_patch(setup_id, {"max_budget": amount},
                                   audit_tool_name="update_setup_filters")

    @tool
    def set_setup_min_hourly(setup_id: int, amount: float) -> dict:
        """Set the minimum hourly rate filter (USD/hr) on a setup. Replaces
        any existing min-hourly rule."""
        return _apply_filter_patch(setup_id, {"min_hourly": amount},
                                   audit_tool_name="update_setup_filters")

    @tool
    def set_setup_max_hourly(setup_id: int, amount: float) -> dict:
        """Set the maximum hourly rate filter (USD/hr) on a setup."""
        return _apply_filter_patch(setup_id, {"max_hourly": amount},
                                   audit_tool_name="update_setup_filters")

    @tool
    def set_setup_exclude_fixed_under(setup_id: int, amount: float) -> dict:
        """Hard-exclude fixed-price jobs whose budget_max < amount (USD).
        Hourly jobs are unaffected."""
        return _apply_filter_patch(setup_id, {"exclude_fixed_under": amount},
                                   audit_tool_name="update_setup_filters")

    @tool
    def set_setup_required_skills(setup_id: int, skills: List[str]) -> dict:
        """Set the required-skills filter on a setup. Replaces any existing
        list. A job matches if it has at least one of these skills (case-
        insensitive)."""
        return _apply_filter_patch(setup_id, {"required_skills": skills},
                                   audit_tool_name="update_setup_filters")

    @tool
    def set_setup_excluded_skills(setup_id: int, skills: List[str]) -> dict:
        """Set the excluded-skills filter on a setup. Replaces any existing
        list. Jobs with any of these skills are filtered out."""
        return _apply_filter_patch(setup_id, {"excluded_skills": skills},
                                   audit_tool_name="update_setup_filters")

    @tool
    def set_setup_min_client_spend(setup_id: int, amount: float) -> dict:
        """Set the minimum total-client-spend filter (USD) on a setup."""
        return _apply_filter_patch(setup_id, {"min_client_spend": amount},
                                   audit_tool_name="update_setup_filters")

    @tool
    def set_setup_payment_verified_required(setup_id: int, required: bool) -> dict:
        """Toggle whether a setup requires the client's payment method to
        be verified. True = exclude unverified clients."""
        return _apply_filter_patch(setup_id, {"payment_verified_required": required},
                                   audit_tool_name="update_setup_filters")

    @tool
    def set_setup_excluded_durations(setup_id: int, durations: List[str]) -> dict:
        """Set the excluded-durations filter (e.g. ['less than 1 month',
        'less than 1 week']). Jobs whose duration matches any entry are
        filtered out."""
        return _apply_filter_patch(setup_id, {"excluded_durations": durations},
                                   audit_tool_name="update_setup_filters")

    @tool
    def set_setup_max_post_age_minutes(setup_id: int, minutes: int) -> dict:
        """Hard-exclude jobs whose posted_at is older than `minutes` from
        now. Jobs whose posted_at couldn't be parsed are also excluded
        (fail-closed). Replaces any existing freshness rule on this setup."""
        return _apply_filter_patch(setup_id, {"max_post_age_minutes": minutes},
                                   audit_tool_name="update_setup_filters")

    @tool
    def clear_setup_filter(setup_id: int, rule_key: str) -> dict:
        """Remove a single filter rule from a setup by its rule key
        (e.g. 'posted_within_minutes', 'budget_min_at_least', 'skill_in',
        'min_hourly', 'exclude_fixed_under'). Use list_setups or get_setup
        to see the current rule keys on a setup."""
        def before():
            s = setups.get(setup_id)
            return None if s is None else {"filter_dsl": s.filter_dsl.spec}

        def apply():
            s = setups.get(setup_id)
            if s is None:
                raise ValueError(f"setup {setup_id} not found")
            spec = s.filter_dsl.spec or {}
            if "all_of" in spec:
                old = spec["all_of"]
                container = "all_of"
            elif "any_of" in spec:
                old = spec["any_of"]
                container = "any_of"
            else:
                # single-rule spec; if its key matches, drop it entirely.
                if isinstance(spec, dict) and rule_key in spec:
                    new_spec = {"all_of": []}
                else:
                    return {"setup_id": setup_id, "removed": False,
                            "filter_dsl": spec}
                with ctx.db.transaction() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE setups SET filter_dsl = %s WHERE setup_id = %s",
                            (Json(new_spec), setup_id),
                        )
                return {"setup_id": setup_id, "removed": True,
                        "filter_dsl": new_spec}
            kept = [r for r in old
                    if not (isinstance(r, dict) and len(r) == 1
                            and list(r.keys())[0] == rule_key)]
            removed = len(kept) != len(old)
            new_spec = {container: kept}
            with ctx.db.transaction() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE setups SET filter_dsl = %s WHERE setup_id = %s",
                        (Json(new_spec), setup_id),
                    )
            return {"setup_id": setup_id, "removed": removed,
                    "filter_dsl": new_spec}

        def after():
            s = setups.get(setup_id)
            return None if s is None else {"filter_dsl": s.filter_dsl.spec}

        # Audit under update_setup_filters so revert_last_change restores
        # the entire filter_dsl snapshot (the existing revert handler).
        return _audited_write(
            ctx, tool_name="update_setup_filters",
            arguments={"setup_id": setup_id, "clear_rule_key": rule_key},
            capture_before=before, apply_mutation=apply, capture_after=after,
        )

    @tool
    def add_ignored_client(setup_id: int, client_name: str) -> dict:
        """Add a client name to a setup's ignore list. Idempotent."""
        def before():
            s = setups.get(setup_id)
            return None if s is None else {"ignored_clients": list(s.ignored_clients)}

        def apply():
            with ctx.db.transaction() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE setups
                           SET ignored_clients = (
                               SELECT array_agg(DISTINCT x)
                               FROM unnest(ignored_clients || ARRAY[%s]) x
                           )
                           WHERE setup_id = %s""",
                        (client_name, setup_id),
                    )
            return {"setup_id": setup_id, "added": client_name}

        def after():
            s = setups.get(setup_id)
            return None if s is None else {"ignored_clients": list(s.ignored_clients)}

        return _audited_write(
            ctx, tool_name="add_ignored_client",
            arguments={"setup_id": setup_id, "client_name": client_name},
            capture_before=before, apply_mutation=apply, capture_after=after,
        )

    @tool
    def remove_ignored_client(setup_id: int, client_name: str) -> dict:
        """Remove a client name from a setup's ignore list. No-op if absent."""
        def before():
            s = setups.get(setup_id)
            return None if s is None else {"ignored_clients": list(s.ignored_clients)}

        def apply():
            with ctx.db.transaction() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE setups SET ignored_clients = array_remove(ignored_clients, %s) WHERE setup_id = %s",
                        (client_name, setup_id),
                    )
            return {"setup_id": setup_id, "removed": client_name}

        def after():
            s = setups.get(setup_id)
            return None if s is None else {"ignored_clients": list(s.ignored_clients)}

        return _audited_write(
            ctx, tool_name="remove_ignored_client",
            arguments={"setup_id": setup_id, "client_name": client_name},
            capture_before=before, apply_mutation=apply, capture_after=after,
        )

    @tool
    def set_setup_tier(setup_id: int, tier: str) -> dict:
        """Set a setup's tier. Allowed values: quiet, normal, critical."""
        if tier not in ("quiet", "normal", "critical"):
            return {"error": f"validation: tier must be quiet|normal|critical, got {tier!r}"}

        def before():
            s = setups.get(setup_id)
            return None if s is None else {"tier": s.tier}

        def apply():
            with ctx.db.transaction() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE setups SET tier = %s WHERE setup_id = %s", (tier, setup_id))
            return {"setup_id": setup_id, "tier": tier}

        def after():
            s = setups.get(setup_id)
            return None if s is None else {"tier": s.tier}

        return _audited_write(
            ctx, tool_name="set_setup_tier",
            arguments={"setup_id": setup_id, "tier": tier},
            capture_before=before, apply_mutation=apply, capture_after=after,
        )

    @tool
    def set_auto_apply(setup_id: int, enabled: bool) -> dict:
        """Toggle a setup's auto_apply_enabled flag."""
        def before():
            s = setups.get(setup_id)
            return None if s is None else {"auto_apply_enabled": s.auto_apply_enabled}

        def apply():
            with ctx.db.transaction() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE setups SET auto_apply_enabled = %s WHERE setup_id = %s",
                        (enabled, setup_id),
                    )
            return {"setup_id": setup_id, "auto_apply_enabled": enabled}

        def after():
            s = setups.get(setup_id)
            return None if s is None else {"auto_apply_enabled": s.auto_apply_enabled}

        return _audited_write(
            ctx, tool_name="set_auto_apply",
            arguments={"setup_id": setup_id, "enabled": enabled},
            capture_before=before, apply_mutation=apply, capture_after=after,
        )

    def _make_status_tool(name: str, target_status: str, narrative: str):
        @tool(name, description=narrative)
        def _f(setup_id: int) -> dict:
            def before():
                s = setups.get(setup_id)
                return None if s is None else {"status": s.status}

            def apply():
                setups.update_status(setup_id, target_status)
                return {"setup_id": setup_id, "status": target_status}

            def after():
                s = setups.get(setup_id)
                return None if s is None else {"status": s.status}

            return _audited_write(
                ctx, tool_name=name,
                arguments={"setup_id": setup_id},
                capture_before=before, apply_mutation=apply, capture_after=after,
            )
        return _f

    pause_setup = _make_status_tool(
        "pause_setup", "disabled",
        "Pause a setup. Sets status='disabled'. Bidder will skip it on the next cycle.",
    )
    resume_setup = _make_status_tool(
        "resume_setup", "active",
        "Resume a setup. Sets status='active'.",
    )
    archive_setup = _make_status_tool(
        "archive_setup", "retired",
        "Archive a setup. Sets status='retired' (soft-delete; row stays for audit).",
    )

    @tool
    def create_setup(name: str, tier: str, filters: dict, prose: str) -> dict:
        """Create a new Setup. tier: quiet|normal|critical. filters is a patch
        in the same shape update_setup_filters accepts. prose is a free-text
        description (used by the relevance tie-break LLM)."""
        if tier not in ("quiet", "normal", "critical"):
            return {"error": f"validation: tier must be quiet|normal|critical, got {tier!r}"}
        try:
            patch_obj = FiltersPatch(**filters)
        except ValidationError as e:
            return {"error": f"validation: {e}"}
        rules = _patch_to_filter_rules(patch_obj)
        spec = {"all_of": rules}

        def apply():
            from domain.types import Setup, FilterDsl
            new_id = setups.create(Setup(
                setup_id=0, name=name, status="active", tier=tier,
                filter_dsl=FilterDsl(spec), prose_definition=prose,
                pitch_template_id=None, cover_letter_template_id=None,
                auto_apply_enabled=False, escalation_config={},
            ))
            return {"setup_id": new_id, "name": name, "tier": tier}

        return _audited_write(
            ctx, tool_name="create_setup",
            arguments={"name": name, "tier": tier, "filters": filters, "prose": prose},
            capture_before=lambda: None, apply_mutation=apply,
            capture_after=lambda: None,
        )

    @tool
    def set_connects_cap(daily: Optional[int] = None, weekly: Optional[int] = None) -> dict:
        """Override connects caps via system_config. Pass daily and/or weekly.
        Bidder reads these on next cycle, falls back to env if unset."""
        if daily is None and weekly is None:
            return {"error": "validation: must provide daily and/or weekly"}

        def before():
            return {
                "daily": sysconfig.get("connects_daily_cap"),
                "weekly": sysconfig.get("connects_weekly_cap"),
            }

        def apply():
            if daily is not None:
                sysconfig.set("connects_daily_cap", int(daily))
            if weekly is not None:
                sysconfig.set("connects_weekly_cap", int(weekly))
            return {"daily": daily, "weekly": weekly}

        def after():
            return {
                "daily": sysconfig.get("connects_daily_cap"),
                "weekly": sysconfig.get("connects_weekly_cap"),
            }

        return _audited_write(
            ctx, tool_name="set_connects_cap",
            arguments={"daily": daily, "weekly": weekly},
            capture_before=before, apply_mutation=apply, capture_after=after,
        )

    def _make_bidder_pause_tool(name: str, target: bool, narrative: str):
        @tool(name, description=narrative)
        def _f() -> dict:
            def before():
                return {"bidder_paused": bool(sysconfig.get("bidder_paused") or False)}

            def apply():
                sysconfig.set("bidder_paused", target)
                return {"bidder_paused": target}

            def after():
                return {"bidder_paused": bool(sysconfig.get("bidder_paused") or False)}

            return _audited_write(
                ctx, tool_name=name, arguments={},
                capture_before=before, apply_mutation=apply, capture_after=after,
            )
        return _f

    pause_bidder = _make_bidder_pause_tool(
        "pause_bidder", True,
        "Pause the bidder loop. Next cycle exits early without scanning.",
    )
    resume_bidder = _make_bidder_pause_tool(
        "resume_bidder", False, "Resume the bidder loop.",
    )

    @tool
    def update_pitch_tone(setup_id: int, tone_notes: str) -> dict:
        """Set a per-setup tone override. The proposal generator prepends this
        to its prompt as an 'Operator note on tone' section. Empty string clears it."""
        normalized = tone_notes.strip() or None

        def before():
            s = setups.get(setup_id)
            return None if s is None else {"tone_override": s.tone_override}

        def apply():
            with ctx.db.transaction() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE setups SET tone_override = %s WHERE setup_id = %s",
                        (normalized, setup_id),
                    )
            return {"setup_id": setup_id, "tone_override": normalized}

        def after():
            s = setups.get(setup_id)
            return None if s is None else {"tone_override": s.tone_override}

        return _audited_write(
            ctx, tool_name="update_pitch_tone",
            arguments={"setup_id": setup_id, "tone_notes": tone_notes},
            capture_before=before, apply_mutation=apply, capture_after=after,
        )

    @tool
    def set_goal(
        prose: str,
        target_metric: Optional[str] = None,
        target_value: Optional[float] = None,
        horizon: Optional[str] = None,
        min_hourly: Optional[float] = None,
        min_budget: Optional[float] = None,
        preferred_country: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> dict:
        """Set or replace the operator's active goal. Free-text prose is required;
        structured targets are optional. The new goal becomes the agent's north
        star and is loaded into the system prompt every turn."""
        if not prose or not prose.strip():
            return {"error": "validation: prose is required"}

        def before():
            g = goals.get_active()
            return None if g is None else _goal_to_dict(g)

        def apply():
            new_id = goals.create(Goal(
                goal_id=None, prose=prose.strip(), target_metric=target_metric,
                target_value=target_value, horizon=horizon, min_hourly=min_hourly,
                min_budget=min_budget, preferred_country=preferred_country,
                notes=notes,
            ))
            return {"goal_id": new_id, "prose": prose.strip()}

        def after():
            g = goals.get_active()
            return None if g is None else _goal_to_dict(g)

        return _audited_write(
            ctx, tool_name="set_goal",
            arguments={
                "prose": prose, "target_metric": target_metric,
                "target_value": target_value, "horizon": horizon,
                "min_hourly": min_hourly, "min_budget": min_budget,
                "preferred_country": preferred_country, "notes": notes,
            },
            capture_before=before, apply_mutation=apply, capture_after=after,
        )

    @tool
    def clear_goal() -> dict:
        """Deactivate the active goal. After this, there is no active goal."""
        def before():
            g = goals.get_active()
            return None if g is None else _goal_to_dict(g)

        def apply():
            goals.clear_active()
            return {"cleared": True}

        def after():
            return None  # no active goal after clear

        return _audited_write(
            ctx, tool_name="clear_goal", arguments={},
            capture_before=before, apply_mutation=apply, capture_after=after,
        )

    @tool
    def trigger_bidder_scan() -> dict:
        """Status check on the goal-driven bidder. Phase 2.B: detection runs
        every 60s automatically against the operator's active goal, so a
        manual 'force run' is a no-op. This returns the most recent cycle
        status instead. For ad-hoc 'find me X jobs' requests, use
        trigger_briefed_scan."""
        s = bidder_state.get()
        return {
            "info": (
                "Detection runs every 60s against the active goal — no force-run "
                "needed. For ad-hoc hunts, use trigger_briefed_scan."
            ),
            "last_cycle_started_at": s.last_cycle_started_at.isoformat() if s.last_cycle_started_at else None,
            "last_cycle_finished_at": s.last_cycle_finished_at.isoformat() if s.last_cycle_finished_at else None,
            "last_cycle_status": s.last_cycle_status,
            "paused": s.paused,
        }

    @tool
    def trigger_briefed_scan(prose: str, filter_patch: Optional[dict] = None) -> dict:
        """Queue a one-shot briefed scan. The bidder will pick it up async,
        run a single ephemeral cycle against the brief, and the brief-watcher
        will DM the operator a natural-language summary when it finishes.

        prose: a sentence describing what we're hunting for (will be used
               as the synthetic setup's prose_definition, fed to the LLM
               relevance check).
        filter_patch: optional dict with the same keys update_setup_filters
                      accepts (min_budget, required_skills,
                      max_post_age_minutes, etc.). Omit or pass {} for an
                      LLM-only brief with no hard filters."""
        if not prose or not prose.strip():
            return {"error": "validation: prose is required"}
        try:
            patch_obj = FiltersPatch(**(filter_patch or {}))
        except ValidationError as e:
            return {"error": f"validation: {e}"}
        rules = _patch_to_filter_rules(patch_obj)
        filter_dsl = {"all_of": rules} if rules else {"all_of": []}

        def apply():
            brief_id = briefs.request(
                prose=prose.strip(),
                filter_dsl=filter_dsl,
                requested_by_conversation_id=ctx.conversation_id,
            )
            return {"brief_id": brief_id, "status": "pending",
                    "expected_within_seconds": 30}

        return _audited_write(
            ctx, tool_name="trigger_briefed_scan",
            arguments={"prose": prose, "filter_patch": filter_patch or {}},
            capture_before=lambda: None, apply_mutation=apply,
            capture_after=lambda: None,
        )

    @tool
    def revert_last_change() -> dict:
        """Revert the most recent write tool call in this conversation by
        applying its before_state. If the most recent entry was itself a
        revert, walks back one more."""
        audit = AuditStore(ctx.db)
        last = audit.last_for_conversation(ctx.conversation_id)
        if last is None:
            return {"error": "nothing to revert"}
        if last["tool_name"] == "revert_last_change":
            with ctx.db.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT audit_id, tool_name, arguments, before_state
                           FROM assistant_audit_log
                           WHERE conversation_id = %s AND tool_name <> 'revert_last_change'
                           ORDER BY audit_id DESC LIMIT 1""",
                        (ctx.conversation_id,),
                    )
                    row = cur.fetchone()
            if row is None:
                return {"error": "nothing to revert"}
            tool_name, arguments, before_state = row[1], row[2], row[3]
        else:
            tool_name = last["tool_name"]
            arguments = last["arguments"]
            before_state = last["before_state"]
        if before_state is None:
            return {"error": f"audit row for {tool_name} has no before_state; cannot revert"}

        try:
            _apply_revert(ctx, tool_name, arguments, before_state)
        except Exception as e:  # noqa: BLE001
            return {"error": f"revert failed: {e}"}

        audit.record(
            conversation_id=ctx.conversation_id,
            tool_name="revert_last_change",
            arguments={"reverted_tool": tool_name, "reverted_arguments": arguments},
            result={"restored_state": before_state},
            before_state=None,
            after_state=before_state,
        )
        return {"reverted_tool": tool_name, "restored_state": before_state}

    from assistant.ba_tools import build_ba_tools
    ba_tools = build_ba_tools(ctx)

    return [
        list_setups, get_setup, list_orders, get_job,
        recent_activity, connects_status, list_portfolio_items, search_jobs,
        get_goal,
        update_setup_filters,
        set_setup_min_budget, set_setup_max_budget,
        set_setup_min_hourly, set_setup_max_hourly,
        set_setup_exclude_fixed_under,
        set_setup_required_skills, set_setup_excluded_skills,
        set_setup_min_client_spend, set_setup_payment_verified_required,
        set_setup_excluded_durations, set_setup_max_post_age_minutes,
        clear_setup_filter,
        add_ignored_client, remove_ignored_client,
        set_setup_tier, set_auto_apply,
        pause_setup, resume_setup, archive_setup,
        create_setup, set_connects_cap,
        pause_bidder, resume_bidder,
        update_pitch_tone,
        set_goal, clear_goal,
        trigger_bidder_scan, trigger_briefed_scan,
        *ba_tools,
        revert_last_change,
    ]


def _apply_revert(ctx: ToolContext, tool_name: str, arguments: dict, before_state: dict) -> None:
    """Inverse mutations keyed by tool_name. Each branch knows how to write
    before_state back to the affected row(s)."""
    setups_store = SetupStore(ctx.db)
    sysconfig = SystemConfigStore(ctx.db)
    if tool_name in ("pause_setup", "resume_setup", "archive_setup"):
        setups_store.update_status(arguments["setup_id"], before_state["status"])
    elif tool_name == "set_setup_tier":
        with ctx.db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE setups SET tier = %s WHERE setup_id = %s",
                    (before_state["tier"], arguments["setup_id"]),
                )
    elif tool_name == "set_auto_apply":
        with ctx.db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE setups SET auto_apply_enabled = %s WHERE setup_id = %s",
                    (before_state["auto_apply_enabled"], arguments["setup_id"]),
                )
    elif tool_name == "update_setup_filters":
        with ctx.db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE setups SET filter_dsl = %s WHERE setup_id = %s",
                    (Json(before_state["filter_dsl"]), arguments["setup_id"]),
                )
    elif tool_name in ("add_ignored_client", "remove_ignored_client"):
        with ctx.db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE setups SET ignored_clients = %s WHERE setup_id = %s",
                    (list(before_state["ignored_clients"]), arguments["setup_id"]),
                )
    elif tool_name == "update_pitch_tone":
        with ctx.db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE setups SET tone_override = %s WHERE setup_id = %s",
                    (before_state["tone_override"], arguments["setup_id"]),
                )
    elif tool_name == "set_connects_cap":
        if before_state.get("daily") is None:
            with ctx.db.transaction() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM system_config WHERE key = 'connects_daily_cap'")
        else:
            sysconfig.set("connects_daily_cap", before_state["daily"])
        if before_state.get("weekly") is None:
            with ctx.db.transaction() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM system_config WHERE key = 'connects_weekly_cap'")
        else:
            sysconfig.set("connects_weekly_cap", before_state["weekly"])
    elif tool_name in ("pause_bidder", "resume_bidder"):
        sysconfig.set("bidder_paused", before_state["bidder_paused"])
    elif tool_name == "create_setup":
        # before_state is None for create; nothing to invert.
        pass
    elif tool_name in ("trigger_bidder_scan", "trigger_briefed_scan"):
        # No meaningful inverse; you cannot un-scan. Defensive no-op so
        # revert_last_change does not error if these are the most recent action.
        pass
    elif tool_name == "set_goal":
        # before_state is either None (no prior goal) or the previous goal dict.
        # Restore: clear current active, then if before_state is not None, recreate.
        goals_store = GoalStore(ctx.db)
        goals_store.clear_active()
        if before_state is not None:
            goals_store.create(Goal(
                goal_id=None,
                prose=before_state["prose"],
                target_metric=before_state.get("target_metric"),
                target_value=before_state.get("target_value"),
                horizon=before_state.get("horizon"),
                min_hourly=before_state.get("min_hourly"),
                min_budget=before_state.get("min_budget"),
                preferred_country=before_state.get("preferred_country"),
                notes=before_state.get("notes"),
            ))
    elif tool_name == "clear_goal":
        # before_state is either None (clearing was a no-op) or the prior goal dict.
        if before_state is not None:
            goals_store = GoalStore(ctx.db)
            goals_store.create(Goal(
                goal_id=None,
                prose=before_state["prose"],
                target_metric=before_state.get("target_metric"),
                target_value=before_state.get("target_value"),
                horizon=before_state.get("horizon"),
                min_hourly=before_state.get("min_hourly"),
                min_budget=before_state.get("min_budget"),
                preferred_country=before_state.get("preferred_country"),
                notes=before_state.get("notes"),
            ))
    else:
        raise ValueError(f"no revert handler for tool {tool_name!r}")
