"""MarketCorpus: idempotent ingestion of card-level search results.

Card-level scans don't have real Upwork URLs (the search-results UIA tree
doesn't expose hrefs reliably), so each card is keyed by a deterministic
hash of (title, posted_text, source). The synthetic url 'ba-corpus://{hash}'
satisfies the jobs.url NOT NULL UNIQUE constraint without colliding with
real Upwork URLs (which start with https://).

Re-running the same query within a window is a no-op: each card hashes to
the same job_id, so JobStore.upsert's ON CONFLICT (job_id) DO UPDATE
handles dedup naturally.
"""
from __future__ import annotations

import hashlib
from typing import Iterable

from domain.types import Job
from storage.connection import Database
from storage.jobs import JobStore
from upwork.search_driver import CardResult


_SYNTHETIC_URL_SCHEME = "ba-corpus://"


def _synthetic_id(card: CardResult, source: str) -> tuple[str, str]:
    """Deterministic (job_id, url) for a card without a real URL.

    Hash inputs:
      - title (canonical text)
      - posted_text (rough timestamp; collision across days is what we want
        — same listing scanned today vs tomorrow gets the same id, recorded
        as the same row, scrape_events records the re-touch)
      - source (so the same listing surfaced under two different BA queries
        is deduplicated *within* a query but visible *across* queries)

    Note: when a future feature adds real-URL capture (e.g. a deeper-parse
    pass) we should switch the row to its real Upwork URL via a one-shot
    migration helper. For v1, synthetic IDs are the corpus identity.
    """
    h = hashlib.sha256()
    h.update((card.title or "").strip().lower().encode("utf-8"))
    h.update(b"|")
    h.update((card.posted_text or "").strip().lower().encode("utf-8"))
    h.update(b"|")
    h.update(source.encode("utf-8"))
    digest = h.hexdigest()[:16]  # 64 bits is plenty for our scale
    return f"ba-{digest}", f"{_SYNTHETIC_URL_SCHEME}{digest}"


def _card_to_job(card: CardResult, source: str) -> Job:
    job_id, url = _synthetic_id(card, source)
    return Job(
        job_id=job_id,
        url=url,
        title=card.title,
        description=card.snippet,
        budget_kind=card.budget_kind if card.budget_kind != "unknown" else None,
        budget_min_usd=card.budget_min_usd,
        budget_max_usd=card.budget_max_usd,
        skills=list(card.skills),
        client_country=card.client_country,
        client_payment_verified=card.payment_verified,
        posted_at=card.posted_at,
        posted_text=card.posted_text,
    )


class MarketCorpusStore:
    """Thin wrapper that converts CardResult lists into Job rows and persists
    them via JobStore. No new tables — corpus rows live in `jobs` with
    source='ba:<query>'.
    """

    def __init__(self, db: Database):
        self._db = db
        self._jobs = JobStore(db)

    def ingest_cards(self, cards: Iterable[CardResult], source: str) -> dict:
        """Write cards to the corpus. Idempotent: re-running the same query
        does not produce duplicate rows.

        Args:
            cards: parsed cards from upwork.search_driver.search()
            source: source tag, e.g. 'ba:RAG engineer|payment_verified=1'.
                    Convention is the BA prefix + query string + filter
                    serialization, but any non-empty string is accepted.

        Returns:
            {"inserted": N, "updated_existing": M}.

            "inserted" counts cards that became NEW rows. "updated_existing"
            counts cards whose synthetic_id was already present (the upsert
            ran but the row was already there). The two together equal the
            input card count modulo titleless cards which are dropped.
        """
        if not source:
            raise ValueError("source must be a non-empty string")
        inserted = 0
        updated = 0
        for card in cards:
            if not card.title:
                continue  # defensive: lift already drops these
            job = _card_to_job(card, source)
            already_present = self._jobs.is_known(job.job_id)
            self._jobs.upsert(job, source=source, raw_panel={"_ba_card": True})
            if already_present:
                updated += 1
            else:
                inserted += 1
        return {"inserted": inserted, "updated_existing": updated}

    def ingest_jobs(self, jobs: Iterable, source: str) -> dict:
        """Write deep-scanned Job objects to the corpus. Idempotent.

        Distinct from ingest_cards: deep-scanned jobs have REAL Upwork URLs
        (captured via clipboard click during the panel walk). Dedup is via
        the canonical job_id parsed from the URL, which JobStore.upsert's
        ON CONFLICT (job_id) DO UPDATE handles natively.

        Same query re-scanned tomorrow may re-touch some of the same jobs;
        the upsert refreshes their fields without producing new rows.

        Args:
            jobs: domain.types.Job instances from
                  upwork.search_driver.deep_search()
            source: source tag, same convention as ingest_cards.

        Returns:
            {"inserted": N, "updated_existing": M}.
        """
        if not source:
            raise ValueError("source must be a non-empty string")
        inserted = 0
        updated = 0
        for job in jobs:
            if not getattr(job, "title", None):
                continue
            already_present = self._jobs.is_known(job.job_id)
            self._jobs.upsert(job, source=source, raw_panel={"_ba_deep": True})
            if already_present:
                updated += 1
            else:
                inserted += 1
        return {"inserted": inserted, "updated_existing": updated}
