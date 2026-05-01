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
