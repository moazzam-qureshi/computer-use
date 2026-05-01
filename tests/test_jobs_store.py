from pathlib import Path

import jobs_store


def test_ensure_dirs_creates_all_status_dirs(tmp_path: Path):
    jobs_store.ensure_dirs(tmp_path)
    for status in ("pending", "awaiting_review", "applied", "skipped", "failed"):
        assert (tmp_path / status).is_dir()


def test_ensure_dirs_is_idempotent(tmp_path: Path):
    jobs_store.ensure_dirs(tmp_path)
    jobs_store.ensure_dirs(tmp_path)  # second call must not raise
    assert (tmp_path / "pending").is_dir()


import pytest


def test_extract_job_id_from_jobs_url():
    url = "https://www.upwork.com/jobs/~022050144604938339008"
    assert jobs_store.extract_job_id(url) == "022050144604938339008"


def test_extract_job_id_from_apply_url():
    url = "https://www.upwork.com/nx/proposals/job/~01abc123def/apply/"
    assert jobs_store.extract_job_id(url) == "01abc123def"


def test_extract_job_id_with_trailing_query_or_fragment():
    url = "https://www.upwork.com/jobs/~022050144604938339008?ref=foo"
    assert jobs_store.extract_job_id(url) == "022050144604938339008"


def test_extract_job_id_raises_on_no_match():
    with pytest.raises(ValueError):
        jobs_store.extract_job_id("https://example.com/foo")


def test_build_apply_url():
    job_url = "https://www.upwork.com/jobs/~022050144604938339008"
    assert (
        jobs_store.build_apply_url(job_url)
        == "https://www.upwork.com/nx/proposals/job/~022050144604938339008/apply/"
    )


def test_is_safe_apply_url_accepts_well_formed():
    assert jobs_store.is_safe_apply_url(
        "https://www.upwork.com/nx/proposals/job/~01abc/apply/"
    )


def test_is_safe_apply_url_rejects_other_hosts():
    assert not jobs_store.is_safe_apply_url(
        "https://evil.com/nx/proposals/job/~01abc/apply/"
    )


def test_is_safe_apply_url_rejects_missing_trailing_slash():
    assert not jobs_store.is_safe_apply_url(
        "https://www.upwork.com/nx/proposals/job/~01abc/apply"
    )


def test_is_safe_apply_url_rejects_non_hex_id():
    assert not jobs_store.is_safe_apply_url(
        "https://www.upwork.com/nx/proposals/job/~XYZ/apply/"
    )


import json
from datetime import datetime


def _sample_job_data():
    return {
        "url": "https://www.upwork.com/jobs/~022050144604938339008",
        "title": "Senior AI Engineer",
        "found_at": "2026-05-01T14:32:00",
        "budget": "Fixed-price $1,750",
        "client": {"name": "Sarah", "country": "Lebanon"},
        "skills": ["Python", "LangChain"],
        "description": "We are looking for a senior AI engineer...",
        "doc_url": "https://docs.google.com/document/d/abc",
        "cover_letter": "Hey Sarah, I spent some time...",
    }


def test_write_pending_creates_file_with_id_and_apply_url(tmp_path):
    jobs_store.ensure_dirs(tmp_path)
    path = jobs_store.write_pending(_sample_job_data(), root=tmp_path)
    assert path == tmp_path / "pending" / "022050144604938339008.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["job_id"] == "022050144604938339008"
    assert data["apply_url"] == (
        "https://www.upwork.com/nx/proposals/job/~022050144604938339008/apply/"
    )
    assert data["status_history"] == [
        {"status": "pending", "at": "2026-05-01T14:32:00"}
    ]


def test_read_job_round_trips(tmp_path):
    jobs_store.ensure_dirs(tmp_path)
    path = jobs_store.write_pending(_sample_job_data(), root=tmp_path)
    data = jobs_store.read_job(path)
    assert data["title"] == "Senior AI Engineer"


def test_is_known_job_id_finds_in_any_status(tmp_path):
    jobs_store.ensure_dirs(tmp_path)
    (tmp_path / "applied" / "022050144604938339008.json").write_text("{}")
    assert jobs_store.is_known_job_id("022050144604938339008", root=tmp_path)
    assert not jobs_store.is_known_job_id("999999", root=tmp_path)


def test_move_to_status_renames_and_appends_history(tmp_path, monkeypatch):
    jobs_store.ensure_dirs(tmp_path)
    src = jobs_store.write_pending(_sample_job_data(), root=tmp_path)

    fixed_now = datetime(2026, 5, 1, 15, 0, 0)

    class FakeDateTime:
        @classmethod
        def now(cls):
            return fixed_now

    monkeypatch.setattr(jobs_store, "datetime", FakeDateTime)

    new_path = jobs_store.move_to_status(src, "awaiting_review", root=tmp_path)
    assert not src.exists()
    assert new_path == tmp_path / "awaiting_review" / "022050144604938339008.json"
    data = json.loads(new_path.read_text(encoding="utf-8"))
    assert data["status_history"][-1] == {
        "status": "awaiting_review",
        "at": "2026-05-01T15:00:00",
    }


def test_move_to_status_rejects_unknown_status(tmp_path):
    jobs_store.ensure_dirs(tmp_path)
    src = jobs_store.write_pending(_sample_job_data(), root=tmp_path)
    with pytest.raises(ValueError):
        jobs_store.move_to_status(src, "bogus", root=tmp_path)


def _write_pending_with_found_at(tmp_path, job_id, found_at_iso):
    (tmp_path / "pending" / f"{job_id}.json").write_text(json.dumps({
        "job_id": job_id,
        "url": f"https://www.upwork.com/jobs/~{job_id}",
        "apply_url": f"https://www.upwork.com/nx/proposals/job/~{job_id}/apply/",
        "title": f"Job {job_id}",
        "found_at": found_at_iso,
        "status_history": [{"status": "pending", "at": found_at_iso}],
    }, indent=2), encoding="utf-8")


def test_pick_newest_pending_today_returns_latest(tmp_path):
    jobs_store.ensure_dirs(tmp_path)
    today = datetime(2026, 5, 1, 12, 0, 0)
    _write_pending_with_found_at(tmp_path, "01aaa", "2026-05-01T09:00:00")
    _write_pending_with_found_at(tmp_path, "01bbb", "2026-05-01T14:00:00")
    _write_pending_with_found_at(tmp_path, "01ccc", "2026-05-01T11:30:00")

    path = jobs_store.pick_newest_pending_today(now=today, root=tmp_path)
    assert path is not None
    assert path.name == "01bbb.json"


def test_pick_newest_pending_today_ignores_other_days(tmp_path):
    jobs_store.ensure_dirs(tmp_path)
    today = datetime(2026, 5, 1, 12, 0, 0)
    _write_pending_with_found_at(tmp_path, "01yest", "2026-04-30T22:00:00")
    _write_pending_with_found_at(tmp_path, "01tom", "2026-05-02T01:00:00")

    assert jobs_store.pick_newest_pending_today(now=today, root=tmp_path) is None


def test_pick_newest_pending_today_returns_none_when_empty(tmp_path):
    jobs_store.ensure_dirs(tmp_path)
    today = datetime(2026, 5, 1, 12, 0, 0)
    assert jobs_store.pick_newest_pending_today(now=today, root=tmp_path) is None
