"""Unit test: _click_with_lift_retry recovers from taskbar-overlap clicks.

Real production failure (apply executor order #28):
  ClickOutOfViewport: click target y=1146 is below safe viewport
  (y_max=1136); element bounds=(346, 1048, 1559, 1274) likely overlaps
  Windows taskbar.

The cover-letter / screening-question / rate-frequency clicks land on
tall elements that PageDown often parks with their bottom edge clipped
behind the taskbar. The helper arrow-up's a few times to lift the
element above the safe viewport, then retries the click.
"""
from unittest.mock import patch

import pytest

from substrate.act import ClickOutOfViewport
from upwork.apply_form import _click_with_lift_retry


class _Counter:
    """Mutable counter the fake click_fn closes over."""

    def __init__(self):
        self.fail_count = 0
        self.success_after = 0


def _make_click_fn(counter: _Counter):
    """A fake click_fn that fails ClickOutOfViewport `success_after` times,
    then succeeds. Mirrors the real-world pattern where the first click
    is in the taskbar zone and lifting the page brings the element up."""

    def click_fn(*args, **kwargs):
        if counter.fail_count < counter.success_after:
            counter.fail_count += 1
            raise ClickOutOfViewport("simulated taskbar overlap")
        return None  # success

    return click_fn


def test_succeeds_immediately_when_click_works_first_try():
    counter = _Counter()
    counter.success_after = 0
    click_fn = _make_click_fn(counter)
    with patch("upwork.apply_form.act.scroll") as scroll_mock:
        _click_with_lift_retry(click_fn, "elem")
    assert counter.fail_count == 0
    scroll_mock.assert_not_called()


def test_recovers_after_one_lift():
    counter = _Counter()
    counter.success_after = 1
    click_fn = _make_click_fn(counter)
    with patch("upwork.apply_form.act.scroll") as scroll_mock, \
         patch("upwork.apply_form.time.sleep"):
        _click_with_lift_retry(click_fn, "elem")
    assert counter.fail_count == 1
    assert scroll_mock.call_count == 1


def test_recovers_after_three_lifts():
    counter = _Counter()
    counter.success_after = 3
    click_fn = _make_click_fn(counter)
    with patch("upwork.apply_form.act.scroll") as scroll_mock, \
         patch("upwork.apply_form.time.sleep"):
        _click_with_lift_retry(click_fn, "elem")
    assert counter.fail_count == 3
    assert scroll_mock.call_count == 3


def test_re_raises_after_max_lifts_exhausted():
    counter = _Counter()
    counter.success_after = 99  # never succeeds
    click_fn = _make_click_fn(counter)
    with patch("upwork.apply_form.act.scroll"), \
         patch("upwork.apply_form.time.sleep"):
        with pytest.raises(ClickOutOfViewport):
            _click_with_lift_retry(click_fn, "elem", max_lifts=4)
    # max_lifts=4 means: 1 initial attempt + 4 retries = 5 total attempts
    assert counter.fail_count == 5


def test_uses_arrow_method_for_scroll():
    """Arrow-scroll is fine-grained (~40px); PageDown would over-scroll
    and might bump the element off the visible area entirely."""
    counter = _Counter()
    counter.success_after = 1
    click_fn = _make_click_fn(counter)
    with patch("upwork.apply_form.act.scroll") as scroll_mock, \
         patch("upwork.apply_form.time.sleep"):
        _click_with_lift_retry(click_fn, "elem")
    args, kwargs = scroll_mock.call_args
    # First positional is amount, kw or positional is method='arrow'
    method_arg = kwargs.get("method") or (args[1] if len(args) > 1 else None)
    assert method_arg == "arrow"
