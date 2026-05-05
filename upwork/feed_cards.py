"""FeedCard parser for Phase 2.B detection.

Lifted verbatim from bin/debug_phase2b_e2e.py:_parse_card_slice. The parser
walks UIA-observed elements between consecutive 'Posted' anchors and pulls
title, posted text, budget, experience level, est. label/value, description
preview, skills, payment-verified, rating, spent, country, proposals.

Validated against 9 live feed cards (see results_e2e.md). The 'Save job
<title>' button is the most reliable title source. Description preview is
the longest text element >= 80 chars in the slice that isn't the title.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from substrate import observe


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


_SAVE_PREFIX = "save job "
_RATING_RE = re.compile(r"Rating is\s+([\d.]+)\s+out of 5", re.IGNORECASE)


def _find_card_end(elements: list, start: int, hard_end: int) -> int:
    """Walk forward from `start`; truncate when y jumps backwards by >150px,
    which marks the boundary between sibling DOM subtrees.
    """
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


def parse_card_slice(slice_elems: list) -> FeedCard:
    """Parse one card's worth of UIA elements into a FeedCard."""
    card = FeedCard()
    # Title from 'Save job <title>' button — most reliable.
    for e in slice_elems:
        if e.role == "button" and (e.name or "").lower().startswith(_SAVE_PREFIX):
            raw = e.name[len(_SAVE_PREFIX):]
            card.title = re.sub(r"<[^>]+>", "", raw).strip()
            break

    # Posted text immediately after the 'Posted' anchor.
    if len(slice_elems) >= 2 and (slice_elems[0].name or "").strip() == "Posted":
        card.posted_text = (slice_elems[1].name or "").strip()

    # Budget chips + experience + est. label/value.
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

    # Description preview: longest text >= 80 chars, not equal to the title.
    longest = ""
    for e in slice_elems:
        if e.role == "text":
            n = e.name or ""
            if len(n) > len(longest) and len(n) >= 80 and n.strip() != card.title:
                longest = n
    card.description_preview = longest.strip() or None

    # Skills: unique short hyperlinks that aren't the title.
    seen_skills: set[str] = set()
    for e in slice_elems:
        if e.role == "hyperlink":
            n = (e.name or "").strip()
            if n and n != card.title and n not in seen_skills and len(n) <= 60:
                seen_skills.add(n)
                card.skills.append(n)

    # Client-trust fields, rating, spent, proposals.
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

    # Country sits right after the spent label.
    if card.spent:
        for i, e in enumerate(slice_elems):
            if e.role == "text" and (e.name or "").strip() == "spent":
                if i + 1 < len(slice_elems):
                    candidate = (slice_elems[i + 1].name or "").strip()
                    if candidate and candidate != "Proposals:" and not candidate.startswith("$"):
                        card.country = candidate
                break
    return card


def extract_cards_from_window(window_title: str) -> list[FeedCard]:
    """Walk the UIA tree once for the given window and return parsed FeedCards
    in feed order. Caller has already focused the window and applied the 33%
    zoom recipe; this just observes + parses.
    """
    obs = observe.observe(window_title=window_title, include_unnamed=False, include_text=True)
    elements = obs.elements
    posted_indices = [
        i for i, e in enumerate(elements)
        if e.role == "text" and (e.name or "").strip().startswith("Posted")
    ]
    cards: list[FeedCard] = []
    for idx, start in enumerate(posted_indices):
        next_posted = posted_indices[idx + 1] if idx + 1 < len(posted_indices) else len(elements)
        end = _find_card_end(elements, start, next_posted)
        card = parse_card_slice(elements[start:end])
        if card.title:
            cards.append(card)
    return cards
