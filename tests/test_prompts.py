from assistant.prompts import SYSTEM_PROMPT, truncate_for_discord


def test_system_prompt_includes_status_mapping():
    assert "pause_setup" in SYSTEM_PROMPT
    assert "disabled" in SYSTEM_PROMPT


def test_truncate_short_passthrough():
    assert truncate_for_discord("hello") == ["hello"]


def test_truncate_splits_at_paragraph():
    long = ("a" * 1500) + "\n\n" + ("b" * 1500)
    out = truncate_for_discord(long, limit=2000)
    assert len(out) == 2
    assert out[0].startswith("a")
    assert out[1].startswith("b")


def test_truncate_falls_back_to_hard_cut():
    long = "a" * 5000
    out = truncate_for_discord(long, limit=2000)
    assert len(out) == 3
    assert all(len(c) <= 2000 for c in out)
