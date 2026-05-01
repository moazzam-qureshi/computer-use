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
