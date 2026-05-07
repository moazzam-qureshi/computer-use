"""Researcher pass: deep-scan -> ingest -> forensics -> persist findings.

run_researcher_pass() is the single entry point. The scheduler (R-Task 9)
will call it daily; the operator can also call it manually for verification.

Sequence per pass:
  1. Promote due snoozes (snoozed_until passed -> status='new')
  2. Load query portfolio
  3. For each query:
     a. deep_search_and_ingest (writes corpus rows, returns the Job batch
        we just ingested via the corpus's "inserted/updated" path)
     b. Pull the just-touched rows from the corpus to feed forensics with
        full Job objects
     c. find_specific_patterns -> list[JobForensicFinding]
     d. Convert each finding to ResearcherFinding, insert via FindingStore
        (idempotent on dedup_key)
  4. Return summary dict

Failures per query are isolated: one query's failure doesn't kill the
pass. Each query's exception is captured in the per-query result.
"""
from __future__ import annotations

import time
import traceback
from datetime import datetime, timezone
from typing import Optional

from ai.researcher import find_specific_patterns
from ai.schemas import JobForensicFinding
from domain.types import Job
from researcher.query_portfolio import load as load_portfolio
from storage.agent_runs import AgentRunStore
from storage.connection import Database
from storage.conversations import SystemConfigStore
from storage.findings import FindingStore, ResearcherFinding
from storage.goals import GoalStore
from storage.jobs import JobStore
from storage.portfolio import PortfolioStore
from upwork.search_driver import deep_search_and_ingest, source_tag


def _recent_jobs_for_source(
    db: Database, job_store: JobStore, source: str, limit: int,
) -> list[Job]:
    """Return the most-recently-scraped Job objects for the given source.

    Used to feed forensics with the corpus state for this query. We pull
    the most recent N (where N = max_jobs_per_query) so the LLM sees a
    fresh, bounded batch — older corpus rows from prior passes that
    weren't re-touched this pass are deprioritized but still visible if
    the corpus is small.

    Note: scraped_first_at doesn't update on JobStore.upsert's ON CONFLICT
    path, so "most-recently-scraped" means "first-seen-most-recently"
    rather than "last-touched". This is deliberate — we want the LLM
    looking at the same listings the bidder is freshest on.
    """
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT job_id FROM jobs
                WHERE source = %s
                ORDER BY scraped_first_at DESC
                LIMIT %s
                """,
                (source, int(limit)),
            )
            ids = [r[0] for r in cur.fetchall()]
    out: list[Job] = []
    for jid in ids:
        job = job_store.get(jid)
        if job is not None:
            out.append(job)
    return out


def _finding_to_row(f: JobForensicFinding, raw: Optional[dict] = None) -> ResearcherFinding:
    return ResearcherFinding(
        finding_id=None, detected_at=None,
        finding_type=f.finding_type,
        headline=f.headline,
        why_specific=f.why_specific,
        portfolio_tie=f.portfolio_tie,
        suggested_action=f.suggested_action,
        urgency=f.urgency,
        evidence_job_ids=list(f.evidence_job_ids),
        raw_llm_response=raw,
    )


def run_researcher_pass(
    db: Database,
    *,
    max_jobs_per_query: int = 30,
    window_title: str = "Upwork",
    portfolio_override: Optional[list[dict]] = None,
) -> dict:
    """Run one full Researcher pass.

    Args:
        db: live Database.
        max_jobs_per_query: cap per query (deep_search arg).
        window_title: substrate focus target.
        portfolio_override: optional, replaces the system_config portfolio
                            for this pass only — useful for one-off manual
                            verification with a custom query set.

    Returns:
        Summary dict {
            started_at, finished_at,
            queries_run: int,
            queries_failed: int,
            jobs_ingested: int,
            findings_persisted: int,
            findings_skipped_dedup: int,
            per_query: [
                {query, filters, status, scanned, inserted, n_findings, error?}
            ],
        }
    """
    sysconfig = SystemConfigStore(db)
    findings_store = FindingStore(db)
    job_store = JobStore(db)
    portfolio_store = PortfolioStore(db)
    goals_store = GoalStore(db)
    agent_runs = AgentRunStore(db)

    # Promote any snoozed findings whose timer has expired so the LLM can
    # re-evaluate them this pass (the dedup_key will recognize them as
    # already-known, so they won't double-create rows — they just become
    # eligible for nudging again).
    promoted = findings_store.promote_due_snoozes()

    portfolio_entries = (
        portfolio_override
        if portfolio_override is not None
        else load_portfolio(sysconfig)
    )
    portfolio_items = portfolio_store.list_all()
    active_goal = goals_store.get_active()

    started_at = datetime.now(timezone.utc)
    summary = {
        "started_at": started_at.isoformat(),
        "finished_at": None,
        "promoted_snoozes": promoted,
        "queries_run": 0,
        "queries_failed": 0,
        "jobs_ingested": 0,
        "findings_persisted": 0,
        "findings_skipped_dedup": 0,
        "per_query": [],
    }

    if not portfolio_entries:
        summary["finished_at"] = datetime.now(timezone.utc).isoformat()
        summary["info"] = "empty query portfolio; nothing to research"
        return summary

    for entry in portfolio_entries:
        query = entry.get("query", "")
        filters = entry.get("filters", {}) or {}
        per_q = {
            "query": query, "filters": filters,
            "status": "ok",
            "scanned": 0, "inserted": 0,
            "n_findings": 0,
        }
        if not query.strip():
            per_q["status"] = "skipped"
            per_q["error"] = "empty query"
            summary["per_query"].append(per_q)
            continue

        try:
            ingest_result = deep_search_and_ingest(
                db, query=query, filters=filters,
                max_jobs=max_jobs_per_query,
                window_title=window_title,
            )
            per_q["scanned"] = ingest_result.get("scanned", 0)
            per_q["inserted"] = ingest_result.get("inserted", 0)
            summary["jobs_ingested"] += per_q["scanned"]
        except Exception as e:  # noqa: BLE001 — isolate per-query failures
            per_q["status"] = "scan_failed"
            per_q["error"] = f"{type(e).__name__}: {e}"
            per_q["traceback"] = traceback.format_exc()
            summary["queries_failed"] += 1
            summary["per_query"].append(per_q)
            continue

        # Pull the most recent corpus rows for this source. Source tag
        # is deterministic: same query+filters always yields the same
        # source string, so we can read by source instead of by timestamp
        # (which is brittle when ON CONFLICT upserts don't bump
        # scraped_first_at).
        source = source_tag(query, filters)
        try:
            jobs_for_forensics = _recent_jobs_for_source(
                db, job_store, source=source, limit=max_jobs_per_query,
            )
        except Exception as e:  # noqa: BLE001
            per_q["status"] = "corpus_read_failed"
            per_q["error"] = f"{type(e).__name__}: {e}"
            summary["queries_failed"] += 1
            summary["per_query"].append(per_q)
            continue

        if not jobs_for_forensics:
            per_q["status"] = "no_jobs_to_analyze"
            summary["per_query"].append(per_q)
            summary["queries_run"] += 1
            continue

        try:
            findings = find_specific_patterns(
                jobs=jobs_for_forensics,
                portfolio=portfolio_items,
                active_goal=active_goal,
                agent_run_store=agent_runs,
            )
        except Exception as e:  # noqa: BLE001
            per_q["status"] = "forensics_failed"
            per_q["error"] = f"{type(e).__name__}: {e}"
            per_q["traceback"] = traceback.format_exc()
            summary["queries_failed"] += 1
            summary["per_query"].append(per_q)
            continue

        per_q["n_findings"] = len(findings)

        # Persist each finding. insert() is idempotent on dedup_key — the
        # pre-check via by_dedup_key tells us whether we're producing a
        # NEW row vs hitting an existing one (so the summary counts are
        # accurate without parsing return semantics).
        for f in findings:
            row = _finding_to_row(
                f,
                raw={"model": "gpt-5-mini", "query": query, "source": source},
            )
            already = findings_store.by_dedup_key(row.compute_dedup_key())
            findings_store.insert(row)  # idempotent
            if already is not None:
                summary["findings_skipped_dedup"] += 1
            else:
                summary["findings_persisted"] += 1

        summary["queries_run"] += 1
        summary["per_query"].append(per_q)

    summary["finished_at"] = datetime.now(timezone.utc).isoformat()
    return summary
