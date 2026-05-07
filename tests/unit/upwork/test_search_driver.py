"""Unit tests: card-level search driver parsing helpers.

These exercise the pure parsing logic (budget, relative-time, FeedCard ->
CardResult lift). The search() function itself drives a real Chrome window
and is verified manually before merge — see plan task 1 acceptance test.
"""
from datetime import datetime, timedelta, timezone

from upwork.feed_cards import FeedCard
from upwork.search_driver import (
    CardResult,
    _feed_card_to_card_result,
    _parse_budget,
    _parse_relative_time,
)


# ----- _parse_budget -----

def test_budget_hourly_range():
    kind, lo, hi = _parse_budget("Hourly: $25.00 - $50.00", None)
    assert kind == "hourly"
    assert lo == 25.0
    assert hi == 50.0


def test_budget_hourly_single():
    kind, lo, hi = _parse_budget("Hourly: $75.00", None)
    assert kind == "hourly"
    assert lo == 75.0
    assert hi == 75.0


def test_budget_hourly_no_rate_shown():
    # Card sometimes shows just 'Hourly' with no $ figure. Kind known, range unknown.
    kind, lo, hi = _parse_budget("Hourly", None)
    assert kind == "hourly"
    assert lo is None
    assert hi is None


def test_budget_fixed_with_est():
    kind, lo, hi = _parse_budget("Fixed-price", "$4,000.00")
    assert kind == "fixed"
    assert lo == 4000.0
    assert hi == 4000.0


def test_budget_fixed_without_est():
    kind, lo, hi = _parse_budget("Fixed-price", None)
    assert kind == "fixed"
    assert lo is None


def test_budget_unknown_returns_unknown():
    kind, lo, hi = _parse_budget(None, None)
    assert kind == "unknown"
    assert lo is None
    assert hi is None


# ----- _parse_relative_time -----

def test_parse_relative_minutes():
    dt = _parse_relative_time("12 minutes ago")
    assert dt is not None
    delta = datetime.now(timezone.utc) - dt
    # Allow a few seconds of clock skew between the function call and the assertion.
    assert timedelta(minutes=11, seconds=55) <= delta <= timedelta(minutes=12, seconds=5)


def test_parse_relative_singular():
    dt = _parse_relative_time("1 hour ago")
    assert dt is not None
    delta = datetime.now(timezone.utc) - dt
    assert timedelta(minutes=59, seconds=55) <= delta <= timedelta(hours=1, seconds=5)


def test_parse_relative_unparseable():
    assert _parse_relative_time("just now") is None
    assert _parse_relative_time(None) is None
    assert _parse_relative_time("") is None


# ----- FeedCard -> CardResult lift -----

def test_feed_card_lift_full():
    fc = FeedCard(
        title="Build a RAG eval pipeline",
        posted_text="20 minutes ago",
        budget_text="Hourly: $60.00 - $90.00",
        experience_level="Expert",
        description_preview="We need a senior LLM engineer to design a Ragas-based eval pipeline...",
        skills=["RAG", "Python", "LangChain"],
        payment_verified=True,
        country="United States",
    )
    cr = _feed_card_to_card_result(fc)
    assert isinstance(cr, CardResult)
    assert cr.title == "Build a RAG eval pipeline"
    assert cr.budget_kind == "hourly"
    assert cr.budget_min_usd == 60.0
    assert cr.budget_max_usd == 90.0
    assert cr.snippet and "Ragas" in cr.snippet
    assert cr.skills == ["RAG", "Python", "LangChain"]
    assert cr.payment_verified is True
    assert cr.client_country == "United States"
    assert cr.posted_at is not None  # parsed from "20 minutes ago"


def test_feed_card_lift_fixed_with_est():
    fc = FeedCard(
        title="One-off RAG prototype",
        budget_text="Fixed-price",
        est_value="$4,000.00",
        skills=["RAG"],
    )
    cr = _feed_card_to_card_result(fc)
    assert cr is not None
    assert cr.budget_kind == "fixed"
    assert cr.budget_min_usd == 4000.0
    assert cr.budget_max_usd == 4000.0


def test_feed_card_lift_drops_titleless():
    # Spurious 'Posted' anchors with no Save Job button yield titleless FeedCards
    # in the upstream parser. The lift must drop them so they don't pollute corpus.
    fc = FeedCard(title=None, budget_text="Hourly")
    assert _feed_card_to_card_result(fc) is None


def test_feed_card_lift_payment_verified_false_becomes_none():
    # FeedCard.payment_verified defaults to False meaning "not seen on card",
    # not "client is unverified". Lift to None so corpus rows don't lie.
    fc = FeedCard(title="t", payment_verified=False)
    cr = _feed_card_to_card_result(fc)
    assert cr is not None
    assert cr.payment_verified is None


# ----- _job_id_from_url + _panel_data_to_job (deep_search helpers) -----

def test_job_id_from_url_canonical():
    from upwork.search_driver import _job_id_from_url
    assert _job_id_from_url(
        "https://www.upwork.com/jobs/Build-RAG-eval-pipeline_~01abc123def456"
    ) == "~01abc123def456"


def test_job_id_from_url_no_match_returns_none():
    from upwork.search_driver import _job_id_from_url
    assert _job_id_from_url("https://www.example.com/foo/bar") is None
    assert _job_id_from_url("") is None


def test_panel_data_to_job_full():
    """A full PanelData yields a Job with all key fields populated."""
    from upwork.panel import PanelData
    from upwork.search_driver import _panel_data_to_job

    panel = PanelData(
        title="Build a RAG eval pipeline",
        posted_text="20 minutes ago",
        budget_kind="hourly",
        budget_min_usd=60.0,
        budget_max_usd=90.0,
        budget_raw_text="$60-$90/hr",
        duration=None,
        experience_level="Expert",
        hours_per_week=None,
        skills=["RAG", "LangChain", "Python"],
        description="Long description...",
        client_country="United States",
        client_payment_verified=True,
        client_rating=4.9,
        client_hires=12,
        client_total_spent_usd=50000.0,
        proposals_count=8,
    )
    url = "https://www.upwork.com/jobs/Build-a-RAG-eval-pipeline_~01abc123"
    job = _panel_data_to_job(panel, url)
    assert job.job_id == "~01abc123"
    assert job.url == url
    assert job.title == "Build a RAG eval pipeline"
    assert job.description == "Long description..."
    assert job.budget_kind == "hourly"
    assert job.budget_min_usd == 60.0
    assert job.skills == ["RAG", "LangChain", "Python"]
    assert job.client_country == "United States"
    assert job.client_payment_verified is True
    assert job.client_total_spent_usd == 50000.0
    assert job.proposals_count_at_first_scrape == 8
    assert job.posted_at is not None  # parsed from "20 minutes ago"


def test_panel_data_to_job_url_parse_failure_falls_back():
    """When URL doesn't match the canonical pattern, job_id falls back to
    the URL itself so the row can still be persisted."""
    from upwork.panel import PanelData
    from upwork.search_driver import _panel_data_to_job

    panel = PanelData(
        title="t", posted_text=None, budget_kind=None,
        budget_min_usd=None, budget_max_usd=None, budget_raw_text=None,
        duration=None, experience_level=None, hours_per_week=None,
        skills=[], description="",
    )
    job = _panel_data_to_job(panel, "")
    # url falls back to deep://<job_id> which equals the empty-string fallback
    # — the URL is still distinct (so jobs.url UNIQUE doesn't collide).
    assert job.title == "t"
    assert job.url.startswith("deep://")
