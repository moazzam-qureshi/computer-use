"""Integration test: nudge engine end-to-end against real DB.

Mocks the LLM (write_nudge / write_digest) and the Discord sender so we
can assert the FSM transitions, status updates, and digest cooldown
without external services.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from researcher.nudge import process_new_findings
from storage.findings import FindingStore, ResearcherFinding
from storage.migrate import apply_migrations
from storage.conversations import SystemConfigStore


MIG = Path(__file__).parents[3] / "storage" / "migrations"


@pytest.fixture
def fresh_db(db):
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        conn.commit()
    apply_migrations(db, MIG)
    return db


def _finding(urgency: str, evidence: list[str]) -> ResearcherFinding:
    return ResearcherFinding(
        finding_id=None, detected_at=None,
        finding_type="emerging_template",
        headline=f"Headline for {urgency} finding {','.join(evidence)}",
        why_specific=(
            "Specific evidence cited including concrete numbers and "
            "named tools that justify this finding existing."
        ),
        portfolio_tie=(
            "Operator portfolio mentions named-thing-A but not named-thing-B."
        ),
        suggested_action=(
            "Build a small demo of named-thing-B and add to portfolio."
        ),
        urgency=urgency,
        evidence_job_ids=evidence,
    )


class _SentDMs:
    """Captures Discord DMs the engine would send."""

    def __init__(self):
        self.messages: list[str] = []

    async def send(self, text: str) -> None:
        self.messages.append(text)


def _patch_llm(monkeypatch, *, nudge_text="MOCK_NUDGE", digest_text="MOCK_DIGEST"):
    """Patch the LLM-backed compressors so tests don't call OpenAI."""
    import researcher.nudge as nudge_mod

    monkeypatch.setattr(
        nudge_mod, "write_nudge",
        lambda finding, **kw: f"{nudge_text} #{finding.finding_id}",
    )
    monkeypatch.setattr(
        nudge_mod, "write_digest",
        lambda findings, **kw: f"{digest_text} ({len(findings)} findings)",
    )


def test_immediate_dm_for_this_week(fresh_db, monkeypatch):
    _patch_llm(monkeypatch)
    store = FindingStore(fresh_db)
    fid_a = store.insert(_finding("this_week", ["~01a", "~01b"]))
    fid_b = store.insert(_finding("this_week", ["~01c", "~01d"]))

    dms = _SentDMs()
    result = asyncio.run(process_new_findings(fresh_db, dms.send))

    assert result.immediate_sent == 2
    assert len(dms.messages) == 2
    # Both findings should now be 'nudged'
    assert store.get(fid_a).status == "nudged"
    assert store.get(fid_b).status == "nudged"


def test_this_month_deferred_when_digest_recent(fresh_db, monkeypatch):
    _patch_llm(monkeypatch)
    store = FindingStore(fresh_db)
    fid = store.insert(_finding("this_month", ["~01a", "~01b"]))

    # Pretend we sent a digest 1 hour ago — cooldown not elapsed.
    sysconfig = SystemConfigStore(fresh_db)
    recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    sysconfig.set("researcher_last_digest_at", recent)

    dms = _SentDMs()
    result = asyncio.run(process_new_findings(fresh_db, dms.send))

    assert result.immediate_sent == 0
    assert result.digest_sent is False
    assert result.deferred_to_digest == 1
    assert dms.messages == []
    # Finding stays 'new' so tomorrow's digest can pick it up
    assert store.get(fid).status == "new"


def test_digest_fires_when_cooldown_elapsed(fresh_db, monkeypatch):
    _patch_llm(monkeypatch)
    store = FindingStore(fresh_db)
    f1 = store.insert(_finding("this_month", ["~01a", "~01b"]))
    f2 = store.insert(_finding("this_month", ["~01c", "~01d"]))

    # No prior digest at all → digest is due
    dms = _SentDMs()
    result = asyncio.run(process_new_findings(fresh_db, dms.send))

    assert result.digest_sent is True
    assert set(result.digest_finding_ids) == {f1, f2}
    assert len(dms.messages) == 1
    assert "MOCK_DIGEST" in dms.messages[0]
    assert store.get(f1).status == "nudged"
    assert store.get(f2).status == "nudged"

    # Last-digest timestamp got persisted
    sysconfig = SystemConfigStore(fresh_db)
    assert sysconfig.get("researcher_last_digest_at") is not None


def test_digest_caps_at_max_findings(fresh_db, monkeypatch):
    _patch_llm(monkeypatch)
    store = FindingStore(fresh_db)
    fids = [
        store.insert(_finding("this_month", [f"~01a{i}", f"~01b{i}"]))
        for i in range(8)
    ]

    dms = _SentDMs()
    result = asyncio.run(process_new_findings(
        fresh_db, dms.send, digest_max=3,
    ))

    assert result.digest_sent is True
    assert len(result.digest_finding_ids) == 3
    # Findings beyond cap stay 'new'
    nudged_count = sum(1 for fid in fids if store.get(fid).status == "nudged")
    assert nudged_count == 3
    new_count = sum(1 for fid in fids if store.get(fid).status == "new")
    assert new_count == 5
    assert result.deferred_to_digest == 5


def test_monitor_findings_skipped(fresh_db, monkeypatch):
    _patch_llm(monkeypatch)
    store = FindingStore(fresh_db)
    fid = store.insert(_finding("monitor", ["~01a", "~01b"]))

    dms = _SentDMs()
    result = asyncio.run(process_new_findings(fresh_db, dms.send))

    assert result.skipped_monitor == 1
    assert result.immediate_sent == 0
    assert result.digest_sent is False
    assert dms.messages == []
    # Stays 'new' — operator can list_findings to find it
    assert store.get(fid).status == "new"


def test_dm_send_failure_does_not_mark_nudged(fresh_db, monkeypatch):
    """If Discord send fails, the finding stays 'new' so the next pass
    retries it."""
    _patch_llm(monkeypatch)
    store = FindingStore(fresh_db)
    fid = store.insert(_finding("this_week", ["~01a", "~01b"]))

    async def boom(_text):
        raise RuntimeError("simulated discord outage")

    result = asyncio.run(process_new_findings(fresh_db, boom))

    assert result.immediate_sent == 0
    assert len(result.errors) == 1
    assert "simulated discord outage" in result.errors[0]
    assert store.get(fid).status == "new"


def test_mixed_urgencies_in_one_pass(fresh_db, monkeypatch):
    _patch_llm(monkeypatch)
    store = FindingStore(fresh_db)
    week = store.insert(_finding("this_week", ["~01a", "~01b"]))
    month = store.insert(_finding("this_month", ["~01c", "~01d"]))
    monitor = store.insert(_finding("monitor", ["~01e", "~01f"]))

    dms = _SentDMs()
    result = asyncio.run(process_new_findings(fresh_db, dms.send))

    assert result.immediate_sent == 1
    assert result.digest_sent is True   # no prior digest, so it fires
    assert result.skipped_monitor == 1
    assert store.get(week).status == "nudged"
    assert store.get(month).status == "nudged"
    assert store.get(monitor).status == "new"
    # 1 immediate DM + 1 digest DM = 2 messages
    assert len(dms.messages) == 2
