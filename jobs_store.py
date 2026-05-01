"""
Per-job JSON queue under jobs/<status>/<job-id>.json.

Pure-function module: no UIA, no LLM, no network. Just filesystem and JSON.
"""
from __future__ import annotations

import re
from pathlib import Path

STATUSES = ("pending", "awaiting_review", "applied", "skipped", "failed")
DEFAULT_ROOT = Path("jobs")

_JOB_ID_RE = re.compile(r"~([0-9a-f]+)")


def ensure_dirs(root: Path = DEFAULT_ROOT) -> None:
    """Create jobs/<status>/ directories if missing. Idempotent."""
    for status in STATUSES:
        (root / status).mkdir(parents=True, exist_ok=True)


def extract_job_id(url: str) -> str:
    """Pull the hex job-id (without the leading ~) from any Upwork job URL."""
    m = _JOB_ID_RE.search(url)
    if not m:
        raise ValueError(f"No ~<hex> job-id found in URL: {url!r}")
    return m.group(1)


_APPLY_URL_RE = re.compile(
    r"^https://www\.upwork\.com/nx/proposals/job/~[0-9a-f]+/apply/$"
)


def build_apply_url(job_url: str) -> str:
    """Transform a job URL into the direct apply-page URL."""
    job_id = extract_job_id(job_url)
    return f"https://www.upwork.com/nx/proposals/job/~{job_id}/apply/"


def is_safe_apply_url(url: str) -> bool:
    """Strict allowlist match for the apply URL pattern."""
    return bool(_APPLY_URL_RE.match(url))
