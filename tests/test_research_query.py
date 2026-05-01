"""Unit tests for upwork_research.parse_query_line + QuerySpec.url/source."""
import pytest

from upwork_research import QuerySpec, parse_query_line, ALLOWED_FILTER_KEYS


# ---------------------------------------------------------------------------
# parse_query_line
# ---------------------------------------------------------------------------

def test_parse_bare_query():
    spec = parse_query_line("LLM engineer")
    assert spec.query == "LLM engineer"
    assert spec.filters == {}


def test_parse_query_with_one_filter():
    spec = parse_query_line("LLM engineer | payment_verified=1")
    assert spec.query == "LLM engineer"
    assert spec.filters == {"payment_verified": "1"}


def test_parse_query_with_multiple_filters():
    spec = parse_query_line("ai agent developer | payment_verified=1, t=0, hourly_rate=25-35")
    assert spec.query == "ai agent developer"
    assert spec.filters == {
        "payment_verified": "1",
        "t": "0",
        "hourly_rate": "25-35",
    }


def test_parse_strips_whitespace_around_query_and_pairs():
    spec = parse_query_line("  voice agent   |   duration_v3=ongoing  ,  t=0   ")
    assert spec.query == "voice agent"
    assert spec.filters == {"duration_v3": "ongoing", "t": "0"}


def test_parse_rejects_unknown_filter_key():
    with pytest.raises(ValueError, match="Unknown filter key"):
        parse_query_line("foo | unknown_filter=42")


def test_parse_rejects_filter_without_equals():
    with pytest.raises(ValueError, match="Malformed filter"):
        parse_query_line("foo | payment_verified")


def test_parse_skips_empty_filter_pairs():
    # Trailing comma or empty entries between commas should not crash
    spec = parse_query_line("foo | t=0, ")
    assert spec.filters == {"t": "0"}


def test_parse_open_ended_hourly_range():
    spec = parse_query_line("foo | hourly_rate=50-")
    assert spec.filters == {"hourly_rate": "50-"}


def test_allowed_filter_keys_match_documented_set():
    # Sanity that the constant matches what we documented.
    assert ALLOWED_FILTER_KEYS == frozenset({
        "payment_verified", "t", "hourly_rate", "amount", "proposals", "duration_v3",
    })


# ---------------------------------------------------------------------------
# QuerySpec.source — stable attribution string
# ---------------------------------------------------------------------------

def test_source_for_bare_query():
    assert QuerySpec(query="LLM engineer").source == "search:LLM engineer"


def test_source_sorts_filters_alphabetically():
    # File ordering shouldn't matter; the source should be deterministic.
    a = parse_query_line("foo | t=0, payment_verified=1, hourly_rate=25-35").source
    b = parse_query_line("foo | hourly_rate=25-35, t=0, payment_verified=1").source
    assert a == b == "search:foo|hourly_rate=25-35,payment_verified=1,t=0"


# ---------------------------------------------------------------------------
# QuerySpec.url
# ---------------------------------------------------------------------------

def test_url_bare_query():
    spec = QuerySpec(query="LLM engineer")
    url = spec.url
    assert url == (
        "https://www.upwork.com/nx/search/jobs/"
        "?from_recent_search=true&q=LLM%20engineer&sort=relevance%2Bdesc"
    )


def test_url_with_full_filters():
    spec = parse_query_line("ai agent developer | payment_verified=1, t=1, amount=500-999, proposals=0-4")
    url = spec.url
    # Filters in alphabetical order after the standard prefix
    assert url == (
        "https://www.upwork.com/nx/search/jobs/"
        "?from_recent_search=true&q=ai%20agent%20developer&sort=relevance%2Bdesc"
        "&amount=500-999&payment_verified=1&proposals=0-4&t=1"
    )


def test_url_encodes_spaces_as_pct20():
    spec = QuerySpec(query="multi word query")
    assert "q=multi%20word%20query" in spec.url


def test_url_preserves_dashes_in_ranges():
    spec = parse_query_line("foo | hourly_rate=25-35")
    assert "hourly_rate=25-35" in spec.url
    spec2 = parse_query_line("foo | hourly_rate=50-")
    assert "hourly_rate=50-" in spec2.url
