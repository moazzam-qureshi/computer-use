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
