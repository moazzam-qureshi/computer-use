"""Quick test harness for the operator-voice cover letter.

Reads the synthetic JD in test_jd.md, builds a Job, calls
generate_cover_letter against it with a fake Doc URL, and prints the result.

Uses the real Database / AgentRunStore so the LLM call path is identical to
production. Postgres must be up (docker compose ps).

Run:
    uv run python -m bin.test_cover_letter
"""
from __future__ import annotations

import sys
from pathlib import Path

# Force UTF-8 on stdout so the LLM's occasional unicode (arrows, em-dashes
# the model snuck in, etc.) doesn't crash printing on Windows cp1252.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

from scheduler.config import Settings
from storage.connection import Database
from storage.agent_runs import AgentRunStore
from storage.setups import SetupStore
from ai.proposal_gen import generate_cover_letter
from ai.relevance import check_relevance
from domain.types import Job


# Load the JD from test_jd.md so we can swap which post we're calibrating
# against without touching code. The first non-empty line becomes the title;
# everything else becomes the description (truncated to 4000 chars by the
# generator anyway).
JD_PATH = Path(__file__).parent.parent / "test_jd.md"
_jd_text = JD_PATH.read_text(encoding="utf-8").strip()
_jd_lines = [l for l in _jd_text.splitlines() if l.strip()]
_jd_title = _jd_lines[0].strip() if _jd_lines else "Untitled JD"
_jd_description = "\n".join(_jd_lines[1:]) or "(no description)"

JOB = Job(
    job_id="test-jd-001",
    url="https://www.upwork.com/jobs/test/~test-jd-001",
    title=_jd_title,
    description=_jd_description,
    budget_kind="hourly",
    budget_min_usd=15.0,
    budget_max_usd=50.0,
    skills=[],
    client_country=None,
    client_payment_verified=None,
    client_rating=None,
    client_hires=None,
    client_total_spent_usd=None,
    posted_text="3 hours ago",
    proposals_count_at_first_scrape=None,
)

FAKE_DOC_URL = "https://docs.google.com/document/d/FAKE_TEST_DOC_ID/edit"


def main() -> int:
    settings = Settings.from_env()
    db = Database(settings.database_url)
    agent_runs = AgentRunStore(db)
    setups_store = SetupStore(db)

    print("=" * 72)
    print(f"Test JD: {_jd_title}")
    print("=" * 72)
    print()

    # 1. Relevance check first — this is the gate. If the post triggers a
    #    hard skip (Loom, paid trial, etc.) we should see relevant=False and
    #    the cover letter should NOT be generated downstream.
    setups = setups_store.list_active()
    if not setups:
        print("ERROR: no active setups in DB. Run bin/import_seed_setup.py first.")
        return 1
    setup = setups[0]
    print(f"--- Relevance check against setup: {setup.name} ---")
    relevance = check_relevance(JOB, setup, agent_run_store=agent_runs)
    print(f"   relevant: {relevance.relevant}")
    print(f"   score:    {relevance.score:.2f}")
    print(f"   reason:   {relevance.reasoning}")
    print(f"   flags:    {relevance.application_flags}")
    print()

    cover_letter_job = JOB
    if not relevance.relevant:
        print("Post rejected by relevance gate.")
        print("For the cover-letter A/C comparison below, re-running on a sanitized")
        print("copy of the JD (Loom requirement stripped) so we can exercise both")
        print("variants regardless of the live gate.")
        print()
        sanitized_desc = JOB.description.replace(
            "1. A Loom video (or detailed breakdown) of a system you've built\n",
            "",
        ).replace(
            "1. A Loom video (or detailed breakdown) of a system you’ve built\n",
            "",
        ).replace(
            "A Loom video (or detailed breakdown) of a system you’ve built",
            "A detailed breakdown of a system you've built",
        ).replace(
            "A Loom video (or detailed breakdown) of a system you've built",
            "A detailed breakdown of a system you've built",
        )
        cover_letter_job = Job(**{**JOB.__dict__, "description": sanitized_desc})

    # 2. Cover letter generation (production prompt, no variant override).
    print("=" * 72)
    print("Cover letter (production prompt)")
    print("=" * 72)
    cl = generate_cover_letter(
        cover_letter_job,
        detected_client_name=None,
        agent_run_store=agent_runs,
    )
    body = cl.body.replace("{{doc_url}}", FAKE_DOC_URL)
    print(body)
    print()
    print(f"   word count: {len(body.split())}")
    bad_phrases = [
        "leverage", "ensure ", "robust", "scalable", "passionate",
        "delve", "cutting-edge", "seamless", "innovative", "synergy",
        "years of experience", "I align with",
        "—", "–",
    ]
    hits = [p for p in bad_phrases if p.lower() in body.lower()]
    print(f"   banned-phrase hits: {hits if hits else 'none'}")
    print()

    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
