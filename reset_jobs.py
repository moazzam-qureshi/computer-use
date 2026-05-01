"""
Move every job JSON from jobs/{awaiting_review,applied,skipped,failed}/
back to jobs/pending/.

Usage:
    uv run reset_jobs.py
"""
from __future__ import annotations

from pathlib import Path

import jobs_store

OTHER_STATUSES = ("awaiting_review", "applied", "skipped", "failed")


def main() -> None:
    jobs_store.ensure_dirs()
    moved = 0
    for status in OTHER_STATUSES:
        src_dir = jobs_store.DEFAULT_ROOT / status
        for src in sorted(src_dir.glob("*.json")):
            dst = jobs_store.DEFAULT_ROOT / "pending" / src.name
            src.rename(dst)
            print(f"moved: {status}/{src.name} -> pending/")
            moved += 1
    pending = sorted((jobs_store.DEFAULT_ROOT / "pending").glob("*.json"))
    print(f"---\nmoved {moved} file(s); pending now has {len(pending)} job(s).")


if __name__ == "__main__":
    main()
