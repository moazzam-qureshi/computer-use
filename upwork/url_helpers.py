"""URL helpers ported verbatim from legacy db.py.

Three primitives:
  - extract_job_id(url): pulls the hex job-id from any Upwork job URL.
  - build_apply_url(job_url): transforms a job URL -> direct apply-page URL.
  - is_safe_apply_url(url): strict allowlist match used as a navigation gate.

Identical behavior to legacy: only hex (lowercase a-f + digits) is accepted.
"""
from __future__ import annotations

import re


_JOB_ID_RE = re.compile(r"~([0-9a-f]+)")
_APPLY_URL_RE = re.compile(
    r"^https://www\.upwork\.com/nx/proposals/job/~[0-9a-f]+/apply/$"
)


def extract_job_id(url: str) -> str:
    """Pull the hex job-id (without the leading ~) from any Upwork job URL.
    Raises ValueError if no ~<hex> token found.
    """
    m = _JOB_ID_RE.search(url)
    if not m:
        raise ValueError(f"No ~<hex> job-id found in URL: {url!r}")
    return m.group(1)


def build_apply_url(job_url: str) -> str:
    """Transform a job URL into the direct apply-page URL."""
    job_id = extract_job_id(job_url)
    return f"https://www.upwork.com/nx/proposals/job/~{job_id}/apply/"


def is_safe_apply_url(url: str) -> bool:
    """Strict allowlist match for the apply URL pattern."""
    return bool(_APPLY_URL_RE.match(url))
