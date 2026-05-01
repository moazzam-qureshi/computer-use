"""
Per-job JSON queue under jobs/<status>/<job-id>.json.

Pure-function module: no UIA, no LLM, no network. Just filesystem and JSON.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
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


def pick_newest_pending_today(
    now: datetime | None = None,
    root: Path = DEFAULT_ROOT,
) -> Path | None:
    """Return the pending JSON path with the latest found_at falling on `now`'s
    local date. Returns None if no pending jobs match today."""
    now = now or datetime.now()
    today_date = now.date()
    pending_dir = root / "pending"
    if not pending_dir.exists():
        return None
    candidates: list[tuple[str, Path]] = []
    for p in pending_dir.glob("*.json"):
        try:
            data = read_job(p)
            found_at = datetime.fromisoformat(data["found_at"])
        except Exception:
            continue
        if found_at.date() == today_date:
            candidates.append((data["found_at"], p))
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0], reverse=True)
    return candidates[0][1]


def find_pending_by_id(job_id: str, root: Path = DEFAULT_ROOT) -> Path | None:
    """Return the path to jobs/pending/<id>.json or None if not present."""
    p = root / "pending" / f"{job_id}.json"
    return p if p.exists() else None


def move_to_status(path: Path, new_status: str, root: Path = DEFAULT_ROOT) -> Path:
    """Move a job JSON into jobs/<new_status>/, append status_history, return new path."""
    if new_status not in STATUSES:
        raise ValueError(f"Unknown status {new_status!r}; must be one of {STATUSES}")
    data = read_job(path)
    data.setdefault("status_history", []).append({
        "status": new_status,
        "at": datetime.now().isoformat(timespec="seconds"),
    })
    job_id = data["job_id"]
    new_path = root / new_status / f"{job_id}.json"
    new_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    if path.resolve() != new_path.resolve():
        path.unlink()
    return new_path
