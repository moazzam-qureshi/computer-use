"""Market analysis: pure SQL + Python aggregations over the jobs corpus.

No LLM. Deterministic. Fast. The output of these functions feeds the
LLM-backed proposers in ai/agents/ba_proposer.py — but that boundary is
strict: this module produces numbers; the proposers add narrative.

All windows are anchored to scraped_first_at (when WE first saw the job),
not posted_at — posted_at is operator-facing time language, scraped_first_at
is when the row landed in our DB and is what determines whether a job is
"in our analysis window" semantically.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from domain.scoring import score_job_against_setup
from domain.types import FilterDsl, Job, Setup
from storage.connection import Database
from storage.jobs import JobStore


@dataclass
class CorpusSnapshot:
    """All facts about a window's corpus, in one shape. Returned by
    analyze_corpus — agent and proposer modules read from here."""

    window_days: int
    source_pattern: Optional[str]
    total_jobs: int

    top_skills: list[tuple[str, int, float]] = field(default_factory=list)
    """[(skill_name, job_count, share_of_corpus_0_to_1)] sorted desc, top 20."""

    budget_p25_usd: Optional[float] = None
    budget_p50_usd: Optional[float] = None
    budget_p75_usd: Optional[float] = None
    budget_kind_breakdown: dict[str, int] = field(default_factory=dict)
    """{'hourly': N, 'fixed': M, 'unknown': K}."""

    weekly_volume: list[tuple[str, int]] = field(default_factory=list)
    """[(week_start_iso_date, job_count)] for the last ceil(window_days/7) weeks."""

    client_country_breakdown: list[tuple[str, int, float]] = field(default_factory=list)
    """[(country, count, share)] top 10."""

    payment_verified_share: Optional[float] = None
    """Fraction of jobs in window where client_payment_verified=True. None when
    the corpus is empty (vs 0.0 which means 'all unverified')."""

    def to_dict(self) -> dict:
        return {
            "window_days": self.window_days,
            "source_pattern": self.source_pattern,
            "total_jobs": self.total_jobs,
            "top_skills": [
                {"skill": s, "count": c, "share": share}
                for s, c, share in self.top_skills
            ],
            "budget": {
                "p25_usd": self.budget_p25_usd,
                "p50_usd": self.budget_p50_usd,
                "p75_usd": self.budget_p75_usd,
                "kind_breakdown": dict(self.budget_kind_breakdown),
            },
            "weekly_volume": [
                {"week_start": w, "count": c} for w, c in self.weekly_volume
            ],
            "client_country_breakdown": [
                {"country": c, "count": n, "share": s}
                for c, n, s in self.client_country_breakdown
            ],
            "payment_verified_share": self.payment_verified_share,
        }


def _percentile(sorted_vals: list[float], pct: float) -> Optional[float]:
    """Linear-interp percentile on a pre-sorted list. None on empty."""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    rank = pct * (len(sorted_vals) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = rank - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def analyze_corpus(
    db: Database,
    window_days: int = 30,
    source_pattern: Optional[str] = None,
) -> CorpusSnapshot:
    """Aggregate the jobs corpus over a recent window.

    Args:
        db: live Database.
        window_days: lookback in days (default 30). Window is open-right:
                     [now - window, now].
        source_pattern: optional SQL LIKE pattern to scope rows by source
                        (e.g. 'ba:%RAG%' for BA-RAG-scanned jobs only,
                        'feed' for bidder-scanned only, None for everything).

    Returns:
        CorpusSnapshot — empty window yields total_jobs=0 with all
        breakdowns empty/None. Callers must handle the empty case
        (the propose_* tools refuse to run on empty corpora).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    pattern_clause = "AND source LIKE %s" if source_pattern else ""
    pattern_args: tuple = (source_pattern,) if source_pattern else ()

    with db.connection() as conn:
        with conn.cursor() as cur:
            # Pull all relevant columns in one pass; aggregate in Python.
            # Corpus is small (thousands of rows max in practice); a single
            # SELECT then in-process aggregation is simpler than 5 SQL queries.
            cur.execute(f"""
                SELECT job_id, scraped_first_at, budget_kind,
                       budget_min_usd, budget_max_usd,
                       client_country, client_payment_verified
                FROM jobs
                WHERE scraped_first_at >= %s {pattern_clause}
            """, (cutoff, *pattern_args))
            rows = cur.fetchall()

            if not rows:
                return CorpusSnapshot(
                    window_days=window_days, source_pattern=source_pattern,
                    total_jobs=0,
                )

            job_ids = [r[0] for r in rows]
            cur.execute(f"""
                SELECT s.name, count(*) AS n
                FROM job_skills js
                JOIN skills s ON s.skill_id = js.skill_id
                WHERE js.job_id = ANY(%s)
                GROUP BY s.name
                ORDER BY n DESC
                LIMIT 20
            """, (job_ids,))
            skill_rows = cur.fetchall()

    total = len(rows)

    # Budget percentiles: representative budget per row is min if present,
    # else max, else skip the row (we can't bucket "unknown budget").
    budget_vals: list[float] = []
    kind_counter: Counter[str] = Counter()
    country_counter: Counter[str] = Counter()
    verified_count = 0
    week_buckets: Counter[str] = Counter()

    for row in rows:
        scraped_at = row[1]
        kind = row[2] or "unknown"
        b_min = row[3]
        b_max = row[4]
        country = row[5]
        verified = row[6]

        kind_counter[kind] += 1
        if country:
            country_counter[country] += 1
        if verified:
            verified_count += 1

        # Pick a representative dollar value per row.
        rep = b_min if b_min is not None else b_max
        if rep is not None:
            budget_vals.append(float(rep))

        # Weekly volume: bucket by Monday-anchored ISO week start.
        if scraped_at:
            day = scraped_at.date()
            monday = day - timedelta(days=day.weekday())
            week_buckets[monday.isoformat()] += 1

    sorted_budgets = sorted(budget_vals)

    top_skills = [
        (name, count, count / total)
        for (name, count) in skill_rows
    ]
    top_countries = [
        (country, count, count / total)
        for country, count in country_counter.most_common(10)
    ]
    weekly_volume = sorted(week_buckets.items())

    return CorpusSnapshot(
        window_days=window_days,
        source_pattern=source_pattern,
        total_jobs=total,
        top_skills=top_skills,
        budget_p25_usd=_percentile(sorted_budgets, 0.25),
        budget_p50_usd=_percentile(sorted_budgets, 0.50),
        budget_p75_usd=_percentile(sorted_budgets, 0.75),
        budget_kind_breakdown=dict(kind_counter),
        weekly_volume=weekly_volume,
        client_country_breakdown=top_countries,
        payment_verified_share=verified_count / total,
    )


@dataclass
class BacktestResult:
    match_count: int
    total_in_window: int
    sample: list[dict] = field(default_factory=list)
    """Up to 5 matching jobs as small dicts (job_id, title, budget, source).
    Operator-readable; not the full Job objects."""

    def to_dict(self) -> dict:
        return {
            "match_count": self.match_count,
            "total_in_window": self.total_in_window,
            "sample": self.sample,
        }


def backtest_filter_dsl(
    db: Database,
    filter_dsl: dict,
    window_days: int = 30,
    sample_size: int = 5,
) -> BacktestResult:
    """Run a hypothetical filter_dsl against the corpus window. Returns the
    count of jobs that would have matched plus a small sample.

    Operates on filter_dsl (the merged spec form), not patch — patches are
    an assistant-layer concept. The assistant's tool wrapper does the
    patch→dsl conversion before calling here, so this function stays in
    the lower analytical layer.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)

    # Build a synthetic Setup just for scoring. Not persisted.
    synth_setup = Setup(
        setup_id=-1, name="__backtest__", status="active", tier="normal",
        filter_dsl=FilterDsl(filter_dsl or {}),
        prose_definition=None,
        pitch_template_id=None, cover_letter_template_id=None,
        auto_apply_enabled=False, escalation_config={},
    )

    job_store = JobStore(db)
    matches: list[Job] = []
    match_count = 0
    total = 0
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT job_id FROM jobs
                WHERE scraped_first_at >= %s
                ORDER BY scraped_first_at DESC
            """, (cutoff,))
            ids = [r[0] for r in cur.fetchall()]
    for job_id in ids:
        job = job_store.get(job_id)
        if job is None:
            continue
        total += 1
        if score_job_against_setup(job, synth_setup).matched:
            match_count += 1
            if len(matches) < sample_size:
                matches.append(job)

    sample = [
        {
            "job_id": j.job_id,
            "title": j.title,
            "budget_kind": j.budget_kind,
            "budget_min_usd": j.budget_min_usd,
            "budget_max_usd": j.budget_max_usd,
        }
        for j in matches
    ]
    return BacktestResult(
        match_count=match_count,
        total_in_window=total,
        sample=sample,
    )
