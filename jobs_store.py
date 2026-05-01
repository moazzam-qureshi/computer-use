"""
Per-job JSON queue under jobs/<status>/<job-id>.json.

Pure-function module: no UIA, no LLM, no network. Just filesystem and JSON.
"""
from __future__ import annotations

import json
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


def write_pending(job_data: dict, root: Path = DEFAULT_ROOT) -> Path:
    """Write the job JSON into jobs/pending/<id>.json. Returns the file path.

    `job_data` must include 'url' and 'found_at'. job_id and apply_url are
    derived; status_history is initialized.
    """
    job_id = extract_job_id(job_data["url"])
    apply_url = build_apply_url(job_data["url"])
    payload = {
        "job_id": job_id,
        "apply_url": apply_url,
        **job_data,
        "status_history": [
            {"status": "pending", "at": job_data["found_at"]},
        ],
    }
    path = root / "pending" / f"{job_id}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def read_job(path: Path) -> dict:
    """Load a job JSON file."""
    return json.loads(path.read_text(encoding="utf-8"))


def is_known_job_id(job_id: str, root: Path = DEFAULT_ROOT) -> bool:
    """Return True if <job_id>.json exists under any status directory."""
    for status in STATUSES:
        if (root / status / f"{job_id}.json").exists():
            return True
    return False
