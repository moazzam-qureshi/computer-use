"""
Per-job JSON queue under jobs/<status>/<job-id>.json.

Pure-function module: no UIA, no LLM, no network. Just filesystem and JSON.
"""
from __future__ import annotations

from pathlib import Path

STATUSES = ("pending", "awaiting_review", "applied", "skipped", "failed")
DEFAULT_ROOT = Path("jobs")


def ensure_dirs(root: Path = DEFAULT_ROOT) -> None:
    """Create jobs/<status>/ directories if missing. Idempotent."""
    for status in STATUSES:
        (root / status).mkdir(parents=True, exist_ok=True)
