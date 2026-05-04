"""Tools the assistant agent calls. Read tools + write tools + revert_last_change.
Each write tool wraps its mutation in _audited_write, which captures before/after
state and writes an audit-log row."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional, List

from langchain_core.tools import tool, BaseTool
from psycopg.types.json import Json

from storage.connection import Database
from storage.setups import SetupStore
from storage.portfolio import PortfolioStore
from storage.conversations import (
    ConversationStore, AuditStore, SystemConfigStore,
)
from storage.connects_ledger import ConnectsLedgerStore


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

    return [
        list_setups, get_setup, list_orders, get_job,
        recent_activity, connects_status, list_portfolio_items, search_jobs,
    ]
