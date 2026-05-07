"""Unit tests: pure-function helpers in ai/market_analysis.py.

The DB-backed analyze_corpus and backtest_filter_dsl have integration
tests in tests/integration/ai/. These cover the percentile math.
"""
from ai.market_analysis import _percentile


def test_percentile_empty():
    assert _percentile([], 0.5) is None


def test_percentile_single():
    assert _percentile([42.0], 0.5) == 42.0
    assert _percentile([42.0], 0.0) == 42.0
    assert _percentile([42.0], 1.0) == 42.0


def test_percentile_p50_odd():
    # Sorted [10, 20, 30] → median is the middle value.
    assert _percentile([10.0, 20.0, 30.0], 0.5) == 20.0


def test_percentile_p25_p50_p75():
    vals = sorted([10.0, 20.0, 30.0, 40.0, 50.0])
    # Linear-interp on 5 elements: p25 → rank 1.0 → 20.0, p50 → rank 2.0 → 30.0, p75 → rank 3.0 → 40.0
    assert _percentile(vals, 0.25) == 20.0
    assert _percentile(vals, 0.50) == 30.0
    assert _percentile(vals, 0.75) == 40.0


def test_percentile_interpolation():
    # Even-length list: p50 should fall between the two middle values.
    vals = [10.0, 20.0, 30.0, 40.0]
    # rank = 0.5 * 3 = 1.5 → between vals[1]=20 and vals[2]=30 → 25
    assert _percentile(vals, 0.5) == 25.0
