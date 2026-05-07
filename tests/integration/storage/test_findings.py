"""Integration test: FindingStore — persistence + dedup + status FSM."""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from storage.findings import (
    FindingStore, ResearcherFinding, compute_dedup_key,
)
from storage.migrate import apply_migrations


MIG = Path(__file__).parents[3] / "storage" / "migrations"


@pytest.fixture
def fresh_db(db):
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        conn.commit()
    apply_migrations(db, MIG)
    return db


def _finding(**overrides) -> ResearcherFinding:
    base = dict(
        finding_id=None,
        detected_at=None,
        finding_type="emerging_template",
        headline="5 jobs this week want Ragas + LangSmith integration",
        why_specific=(
            "Three jobs explicitly mention Ragas; two more mention LangSmith "
            "as a hard requirement. All five are $80-120/hr."
        ),
        portfolio_tie=(
            "Your portfolio mentions LangSmith but not Ragas. Building a "
            "Ragas demo this week would credibly close the gap."
        ),
        suggested_action=(
            "Build a small Ragas eval demo, push to GitHub, add to portfolio "
            "with a 1-paragraph case study before bidding any of these."
        ),
        urgency="this_week",
        evidence_job_ids=["job-001", "job-002", "job-003"],
        raw_llm_response={"model": "gpt-5-mini", "tokens": 1234},
    )
    base.update(overrides)
    return ResearcherFinding(**base)


# ----- compute_dedup_key -----

def test_dedup_key_is_order_insensitive():
    a = compute_dedup_key("emerging_template", ["j1", "j2", "j3"])
    b = compute_dedup_key("emerging_template", ["j3", "j1", "j2"])
    assert a == b


def test_dedup_key_changes_with_finding_type():
    a = compute_dedup_key("emerging_template", ["j1", "j2"])
    b = compute_dedup_key("budget_anomaly", ["j1", "j2"])
    assert a != b


def test_dedup_key_changes_with_evidence():
    a = compute_dedup_key("emerging_template", ["j1", "j2"])
    b = compute_dedup_key("emerging_template", ["j1", "j2", "j3"])
    assert a != b


# ----- insert + idempotent dedup -----

def test_insert_round_trip(fresh_db):
    store = FindingStore(fresh_db)
    fid = store.insert(_finding())
    assert isinstance(fid, int) and fid > 0

    fetched = store.get(fid)
    assert fetched is not None
    assert fetched.finding_type == "emerging_template"
    assert fetched.headline == _finding().headline
    assert fetched.evidence_job_ids == ["job-001", "job-002", "job-003"]
    assert fetched.status == "new"
    assert fetched.detected_at is not None
    assert fetched.dedup_key is not None  # auto-computed at insert


def test_insert_dedup_returns_existing_id(fresh_db):
    store = FindingStore(fresh_db)
    fid_first = store.insert(_finding())

    # Same finding, different headline (immaterial to dedup) — still dedups.
    fid_second = store.insert(_finding(headline="DIFFERENT HEADLINE"))
    assert fid_second == fid_first

    # Verify only one row exists.
    with fresh_db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM researcher_findings")
            assert cur.fetchone()[0] == 1


def test_insert_rejects_empty_evidence(fresh_db):
    store = FindingStore(fresh_db)
    with pytest.raises(ValueError):
        store.insert(_finding(evidence_job_ids=[]))


def test_different_evidence_creates_new_row(fresh_db):
    store = FindingStore(fresh_db)
    fid_a = store.insert(_finding(evidence_job_ids=["j1", "j2"]))
    fid_b = store.insert(_finding(evidence_job_ids=["j3", "j4"]))
    assert fid_a != fid_b


# ----- list_by_status -----

def test_list_by_status_filters(fresh_db):
    store = FindingStore(fresh_db)
    f1 = store.insert(_finding(evidence_job_ids=["j1", "j2"]))
    f2 = store.insert(_finding(evidence_job_ids=["j3", "j4"]))
    f3 = store.insert(_finding(evidence_job_ids=["j5", "j6"]))

    store.mark_nudged(f1)
    store.mark_dismissed(f2, reason="not relevant")

    new_rows = store.list_by_status("new")
    assert [r.finding_id for r in new_rows] == [f3]

    dismissed_rows = store.list_by_status("dismissed")
    assert [r.finding_id for r in dismissed_rows] == [f2]
    assert dismissed_rows[0].dismissed_reason == "not relevant"


def test_list_by_status_urgency_filter(fresh_db):
    store = FindingStore(fresh_db)
    f_week = store.insert(_finding(urgency="this_week",
                                    evidence_job_ids=["j1", "j2"]))
    f_month = store.insert(_finding(urgency="this_month",
                                     evidence_job_ids=["j3", "j4"]))
    f_monitor = store.insert(_finding(urgency="monitor",
                                       evidence_job_ids=["j5", "j6"]))

    week_only = store.list_by_status("new", urgency="this_week")
    assert {r.finding_id for r in week_only} == {f_week}


# ----- status FSM -----

def test_mark_nudged_sets_timestamp(fresh_db):
    store = FindingStore(fresh_db)
    fid = store.insert(_finding())
    store.mark_nudged(fid)
    f = store.get(fid)
    assert f.status == "nudged"
    assert f.nudged_at is not None


def test_mark_dismissed_with_reason(fresh_db):
    store = FindingStore(fresh_db)
    fid = store.insert(_finding())
    store.mark_dismissed(fid, reason="already chasing this lead")
    f = store.get(fid)
    assert f.status == "dismissed"
    assert f.dismissed_at is not None
    assert f.dismissed_reason == "already chasing this lead"


def test_mark_snoozed_sets_future_date(fresh_db):
    store = FindingStore(fresh_db)
    fid = store.insert(_finding())
    store.mark_snoozed(fid, days=7)
    f = store.get(fid)
    assert f.status == "snoozed"
    assert f.snoozed_until is not None
    delta = f.snoozed_until - datetime.now(timezone.utc)
    assert 6.5 < delta.days + (delta.seconds / 86400) < 7.5


def test_mark_snoozed_rejects_zero_or_negative(fresh_db):
    store = FindingStore(fresh_db)
    fid = store.insert(_finding())
    with pytest.raises(ValueError):
        store.mark_snoozed(fid, days=0)
    with pytest.raises(ValueError):
        store.mark_snoozed(fid, days=-1)


def test_promote_due_snoozes(fresh_db):
    store = FindingStore(fresh_db)
    f_due = store.insert(_finding(evidence_job_ids=["j1", "j2"]))
    f_future = store.insert(_finding(evidence_job_ids=["j3", "j4"]))
    store.mark_snoozed(f_due, days=1)
    store.mark_snoozed(f_future, days=30)

    # Backdate f_due's snoozed_until so it's in the past
    with fresh_db.transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE researcher_findings SET snoozed_until = now() - interval '1 hour' "
                "WHERE finding_id = %s",
                (f_due,),
            )

    promoted = store.promote_due_snoozes()
    assert promoted == 1
    assert store.get(f_due).status == "new"
    assert store.get(f_due).snoozed_until is None
    assert store.get(f_future).status == "snoozed"
