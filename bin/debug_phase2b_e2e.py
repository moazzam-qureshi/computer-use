"""Phase 2.B end-to-end proof v2 — two-pass architecture mirroring the
legacy bidder's working pattern.

The bug in v1 was that we triaged THEN tried to re-find cards by title in a
fresh feed walk. That's not how the legacy bidder/scan_cycle.py works.
Legacy works because it clicks element refs while they're still live in
the SAME observation that produced them.

v2 architecture:

  PASS 1 (extraction, no clicks):
    Refresh; zoom; Ctrl+Home
    Walk -> cards 1-2 (FeedCard data only, no element refs kept)
    Down x21; walk -> cards 3-6
    Down x21; walk -> cards 7-10
    LLM triage all extracted FeedCards -> matched_titles set

  PASS 2 (click only matches, in feed order):
    Refresh; zoom; Ctrl+Home
    Walk -> cards 1-2 with LIVE refs
       for each title in matched_titles: click immediately (ref is fresh),
                                          capture_panel, Esc
    Down x21; walk -> next viewport (refs fresh again)
       same thing
    Down x21; walk -> last viewport
       same thing

Pass 2 mirrors legacy bidder/scan_cycle.py:run_one_cycle exactly. Element
refs come from the SAME observation as the click. No re-finding across
scrolls. No staleness.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

import pyautogui
import uiautomation as ua
from pydantic import BaseModel, Field

from substrate import act, observe, pacing
from upwork.feed import refresh_feed, _parse_visible_cards
from upwork import panel as upwork_panel

pacing.configure(pacing.PacingConfig(max_actions_per_hour=2000))


WINDOW = "Upwork"

TEST_GOAL = (
    "Any job involving AI agents, LLMs, machine learning, RAG, or automation "
    "engineering. Skip generic web/mobile dev, marketing, content writing, "
    "graphic design, or roles that aren't fundamentally AI work."
)


# ============================================================================
# FeedCard extraction (pass 1)
# ============================================================================


@dataclass
class FeedCard:
    title: Optional[str] = None
    posted_text: Optional[str] = None
    budget_text: Optional[str] = None
    experience_level: Optional[str] = None
    est_label: Optional[str] = None
    est_value: Optional[str] = None
    description_preview: Optional[str] = None
    skills: list[str] = field(default_factory=list)
    payment_verified: bool = False
    rating: Optional[str] = None
    spent: Optional[str] = None
    country: Optional[str] = None
    proposals: Optional[str] = None

    def to_dict_for_llm(self) -> dict:
        return {
            "title": self.title,
            "posted": self.posted_text,
            "budget": self.budget_text,
            "experience": self.experience_level,
            "est": f"{self.est_label or ''} {self.est_value or ''}".strip(),
            "description": self.description_preview,
            "skills": self.skills,
            "payment_verified": self.payment_verified,
            "rating": self.rating,
            "spent": self.spent,
            "country": self.country,
            "proposals": self.proposals,
        }


def _find_card_end(elements: list, start: int, hard_end: int) -> int:
    if start >= hard_end:
        return start
    max_y = elements[start].bounds[1]
    for i in range(start + 1, hard_end):
        y = elements[i].bounds[1]
        if y < max_y - 150:
            return i
        if y > max_y:
            max_y = y
    return hard_end


_SAVE_PREFIX = "save job "
_RATING_RE = re.compile(r"Rating is\s+([\d.]+)\s+out of 5", re.IGNORECASE)


def _parse_card_slice(slice_elems: list) -> FeedCard:
    card = FeedCard()
    for e in slice_elems:
        if e.role == "button" and (e.name or "").lower().startswith(_SAVE_PREFIX):
            raw = e.name[len(_SAVE_PREFIX):]
            card.title = re.sub(r"<[^>]+>", "", raw).strip()
            break
    if len(slice_elems) >= 2 and (slice_elems[0].name or "").strip() == "Posted":
        card.posted_text = (slice_elems[1].name or "").strip()
    for i, e in enumerate(slice_elems):
        n = (e.name or "").strip()
        if e.role == "text" and (n.startswith("Hourly:") or n == "Hourly" or n == "Fixed-price"):
            card.budget_text = n
            for j in range(i + 1, min(i + 4, len(slice_elems))):
                nj = (slice_elems[j].name or "").strip()
                if slice_elems[j].role == "text" and nj in {"Entry level", "Intermediate", "Expert"}:
                    card.experience_level = nj
                    break
            for j in range(i + 1, min(i + 6, len(slice_elems))):
                nj = (slice_elems[j].name or "").strip()
                if slice_elems[j].role == "text" and nj.startswith("Est."):
                    card.est_label = nj
                    if j + 1 < len(slice_elems):
                        card.est_value = (slice_elems[j + 1].name or "").strip()
                    break
            break
    longest = ""
    for e in slice_elems:
        if e.role == "text":
            n = e.name or ""
            if len(n) > len(longest) and len(n) >= 80 and n.strip() != card.title:
                longest = n
    card.description_preview = longest.strip() or None
    seen_skills: set[str] = set()
    for e in slice_elems:
        if e.role == "hyperlink":
            n = (e.name or "").strip()
            if n and n != card.title and n not in seen_skills and len(n) <= 60:
                seen_skills.add(n)
                card.skills.append(n)
    for i, e in enumerate(slice_elems):
        n = (e.name or "").strip()
        if e.role == "text":
            if n == "Payment verified":
                card.payment_verified = True
            m = _RATING_RE.match(n)
            if m:
                card.rating = m.group(1)
            if n == "spent" and i > 0:
                card.spent = (slice_elems[i - 1].name or "").strip()
            if n == "Proposals:" and i + 1 < len(slice_elems):
                card.proposals = (slice_elems[i + 1].name or "").strip()
    if card.spent:
        for i, e in enumerate(slice_elems):
            if e.role == "text" and (e.name or "").strip() == "spent":
                if i + 1 < len(slice_elems):
                    candidate = (slice_elems[i + 1].name or "").strip()
                    if candidate and candidate != "Proposals:" and not candidate.startswith("$"):
                        card.country = candidate
                break
    return card


def _extract_cards_from_current_view() -> list[FeedCard]:
    obs = observe.observe(window_title=WINDOW, include_unnamed=False, include_text=True)
    elements = obs.elements
    posted_indices = [
        i for i, e in enumerate(elements)
        if e.role == "text" and (e.name or "").strip().startswith("Posted")
    ]
    cards = []
    for idx, start in enumerate(posted_indices):
        next_posted = posted_indices[idx + 1] if idx + 1 < len(posted_indices) else len(elements)
        end = _find_card_end(elements, start, next_posted)
        cards.append(_parse_card_slice(elements[start:end]))
    return cards


def _dedup_into(cards: list[FeedCard], dest: list[FeedCard], seen: set[str]) -> int:
    n_new = 0
    for c in cards:
        if c.title and c.title not in seen:
            seen.add(c.title)
            dest.append(c)
            n_new += 1
    return n_new


# ============================================================================
# LLM triage
# ============================================================================


class TriageMatch(BaseModel):
    title: str = Field(..., description="exact title text from the input list")
    reason: str = Field(..., description="one-sentence reason this card matches the goal")


class TriageResponse(BaseModel):
    matches: list[TriageMatch] = Field(default_factory=list)


def llm_triage(cards: list[FeedCard], goal: str) -> TriageResponse:
    from langchain.agents import create_agent

    sys_prompt = (
        "You are a strict job-feed triage filter. Given an operator's goal "
        "and a list of jobs from the Upwork feed, return ONLY the matches. "
        "Use the EXACT title text from the input. Be lenient on prose match "
        "(description preview suggests AI work) but strict on hard numeric "
        "targets if the goal specifies them."
    )
    user_payload = {
        "goal": goal,
        "jobs": [c.to_dict_for_llm() for c in cards],
    }
    user = json.dumps(user_payload, indent=2, ensure_ascii=False)
    agent = create_agent(model="gpt-5-mini", response_format=TriageResponse)
    result = agent.invoke({"messages": [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user},
    ]})
    return result["structured_response"]


# ============================================================================
# Pass 2 — click-and-capture using LIVE element refs in feed order
# ============================================================================


def _do_substrate_recipe_setup() -> None:
    """Refresh + Ctrl+Home + Ctrl+- x4. Used at start of both passes."""
    refresh_feed(WINDOW)
    act.focus_window(WINDOW)
    act.key("ctrl+home")
    time.sleep(0.6)
    for _ in range(4):
        pyautogui.hotkey("ctrl", "-")
        time.sleep(0.18)
    time.sleep(0.6)


def _click_and_capture_one(title: str, link_el) -> dict:
    """Click a LIVE link element, capture panel, Esc. The ref must be from
    a UIA observation that hasn't been invalidated by intervening scrolls."""
    print(f"\n  >>> clicking: {title[:80]!r}", flush=True)
    bounds = link_el.bounds
    mid_y = (bounds[1] + bounds[3]) // 2
    if mid_y > 1100:
        return {"title": title, "error": f"card y={mid_y} too low; would hit taskbar"}

    act.focus_window(WINDOW)
    try:
        act.click(link_el)
    except Exception as e:
        return {"title": title, "error": f"click failed: {e!r}"}
    time.sleep(2.5)

    try:
        elements, url = upwork_panel.capture_panel(WINDOW)
    except Exception as e:
        # Best-effort: close panel, continue
        try:
            act.focus_window(WINDOW)
            act.key("escape")
            time.sleep(0.5)
        except Exception:
            pass
        return {"title": title, "error": f"capture_panel failed: {e!r}"}

    panel_data = upwork_panel.parse_panel(elements)

    title_match = False
    if panel_data.title and title:
        title_match = (
            panel_data.title.startswith(title[:50])
            or title.startswith(panel_data.title[:50])
        )
    if not title_match:
        print(f"        WARNING: panel.title={panel_data.title[:60]!r} != card.title={title[:60]!r}", flush=True)

    act.focus_window(WINDOW)
    act.key("escape")
    time.sleep(0.6)

    return {
        "title": title,
        "url": url,
        "panel_title": panel_data.title,
        "panel_title_matches_card": title_match,
        "budget_kind": panel_data.budget_kind,
        "budget_min": panel_data.budget_min_usd,
        "budget_max": panel_data.budget_max_usd,
        "budget_raw": panel_data.budget_raw_text,
        "client_country": panel_data.client_country,
        "client_payment_verified": panel_data.client_payment_verified,
        "client_total_spent": panel_data.client_total_spent_usd,
        "skills_from_panel": panel_data.skills,
        "description_excerpt": (panel_data.description or "")[:500],
    }


def _process_visible_matches(matched_titles: set[str], already_clicked: set[str]) -> list[dict]:
    """Walk current viewport for visible cards; click any whose title is a
    match AND hasn't been clicked yet. Element refs come from THIS walk and
    are used immediately within the same iteration."""
    visible = _parse_visible_cards(WINDOW)
    print(f"      visible in this viewport: {len(visible)}", flush=True)
    results = []
    for title, link_el in visible:
        if title not in matched_titles:
            continue
        if title in already_clicked:
            continue
        already_clicked.add(title)
        r = _click_and_capture_one(title, link_el)
        results.append(r)
    return results


# ============================================================================
# Main
# ============================================================================


def main() -> int:
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set; aborting.")
        return 1

    print("\n=== Phase 2.B end-to-end proof (v2 two-pass) ===\n", flush=True)
    print(f"Goal: {TEST_GOAL!r}\n")

    with ua.UIAutomationInitializerInThread():
        act.set_target_window(("Upwork", "Google Chrome"))

        # ====================================================================
        # PASS 1 — extract cards, triage with LLM
        # ====================================================================
        print("=" * 60, flush=True)
        print("PASS 1: extract cards + LLM triage", flush=True)
        print("=" * 60, flush=True)

        print("\n[1.1] refresh + zoom out", flush=True)
        _do_substrate_recipe_setup()

        all_cards: list[FeedCard] = []
        seen: set[str] = set()

        print("\n[1.2] PASS A: top of feed", flush=True)
        n = _dedup_into(_extract_cards_from_current_view(), all_cards, seen)
        print(f"      +{n} new cards (total: {len(all_cards)})")

        print("\n[1.3] Down x21", flush=True)
        act.focus_window(WINDOW)
        for _ in range(21):
            act.key("down")
        time.sleep(0.8)

        print("\n[1.4] PASS B", flush=True)
        n = _dedup_into(_extract_cards_from_current_view(), all_cards, seen)
        print(f"      +{n} new cards (total: {len(all_cards)})")

        print("\n[1.5] Down x21", flush=True)
        act.focus_window(WINDOW)
        for _ in range(21):
            act.key("down")
        time.sleep(0.8)

        print("\n[1.6] PASS C", flush=True)
        n = _dedup_into(_extract_cards_from_current_view(), all_cards, seen)
        print(f"      +{n} new cards (total: {len(all_cards)})")

        print(f"\n=== Extracted {len(all_cards)} cards. Sending to LLM triage... ===", flush=True)
        t0 = time.time()
        triage = llm_triage(all_cards, TEST_GOAL)
        print(f"      triage took {time.time() - t0:.1f}s; matched {len(triage.matches)}/{len(all_cards)}")
        for m in triage.matches:
            print(f"        [MATCH] {m.title[:80]!r}")
            print(f"                {m.reason}")

        if not triage.matches:
            print("\n  No matches; done.")
            act.focus_window(WINDOW)
            pyautogui.hotkey("ctrl", "0")
            return 0

        matched_titles = {m.title for m in triage.matches}
        # Sanity: drop matches that don't correspond to a card we extracted
        # (defends against any residual hallucination).
        extracted_titles = {c.title for c in all_cards if c.title}
        kept = matched_titles & extracted_titles
        dropped = matched_titles - extracted_titles
        if dropped:
            print(f"\n      DROPPED hallucinated titles: {dropped}")
        matched_titles = kept

        # ====================================================================
        # PASS 2 — fresh feed walk; click matches in feed order using LIVE refs
        # ====================================================================
        print("\n" + "=" * 60, flush=True)
        print(f"PASS 2: click {len(matched_titles)} matches in feed order", flush=True)
        print("=" * 60, flush=True)

        print("\n[2.1] refresh + zoom out (fresh feed for live refs)", flush=True)
        _do_substrate_recipe_setup()

        results: list[dict] = []
        already_clicked: set[str] = set()

        print("\n[2.2] viewport A: top of feed", flush=True)
        results.extend(_process_visible_matches(matched_titles, already_clicked))

        if len(already_clicked) < len(matched_titles):
            print(f"\n[2.3] Down x21 (have {len(already_clicked)}/{len(matched_titles)} so far)", flush=True)
            act.focus_window(WINDOW)
            for _ in range(21):
                act.key("down")
            time.sleep(0.8)

            print("\n[2.4] viewport B", flush=True)
            results.extend(_process_visible_matches(matched_titles, already_clicked))

        if len(already_clicked) < len(matched_titles):
            print(f"\n[2.5] Down x21 (have {len(already_clicked)}/{len(matched_titles)} so far)", flush=True)
            act.focus_window(WINDOW)
            for _ in range(21):
                act.key("down")
            time.sleep(0.8)

            print("\n[2.6] viewport C", flush=True)
            results.extend(_process_visible_matches(matched_titles, already_clicked))

        # Reset zoom for cleanliness
        act.focus_window(WINDOW)
        pyautogui.hotkey("ctrl", "0")
        time.sleep(0.3)

        # ====================================================================
        # Report
        # ====================================================================
        print(f"\n" + "=" * 60)
        print(f"FINAL: {len(results)} cards processed; {len(matched_titles) - len(already_clicked)} matches not seen in any viewport")
        print("=" * 60)
        for r in results:
            print(f"\n--- {r['title'][:80]!r} ---")
            if "error" in r:
                print(f"  ERROR: {r['error']}")
                continue
            print(f"  URL:                    {r.get('url') or '(empty)'}")
            print(f"  panel_title:            {r.get('panel_title')!r}")
            print(f"  title_matches:          {r.get('panel_title_matches_card')!r}")
            print(f"  budget:                 {r.get('budget_raw')!r} kind={r.get('budget_kind')}")
            print(f"  client_country:         {r.get('client_country')!r}")
            print(f"  client_payment_verified:{r.get('client_payment_verified')!r}")
            print(f"  client_total_spent:     {r.get('client_total_spent')!r}")
            print(f"  panel_skills:           {r.get('skills_from_panel')!r}")
            print(f"  desc excerpt:           {r.get('description_excerpt', '')[:300]!r}")

        unseen = matched_titles - already_clicked
        if unseen:
            print(f"\n  Matches not surfaced in any viewport: {len(unseen)}")
            for t in unseen:
                print(f"    - {t[:80]!r}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
