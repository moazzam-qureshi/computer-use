"""Panel parser — converts observed elements from the right-side detail panel into PanelData."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional


@dataclass
class PanelData:
    title: str
    posted_text: Optional[str]
    budget_kind: Optional[str]            # 'fixed' | 'hourly'
    budget_min_usd: Optional[float]
    budget_max_usd: Optional[float]
    budget_raw_text: Optional[str]
    duration: Optional[str]
    experience_level: Optional[str]
    hours_per_week: Optional[str]
    skills: List[str] = field(default_factory=list)
    description: str = ""
    client_country: Optional[str] = None
    client_city: Optional[str] = None
    client_member_since: Optional[str] = None
    client_payment_verified: Optional[bool] = None
    client_rating: Optional[float] = None
    client_hires: Optional[int] = None
    client_total_spent_usd: Optional[float] = None
    client_avg_hourly_paid: Optional[float] = None
    proposals_count: Optional[int] = None


_POSTED_RE = re.compile(r"^\s*\d+\s*(minute|hour|day|week|month)s?\s*ago\s*$", re.I)
_MONEY_RE = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)")


def parse_panel(elements: Iterable) -> PanelData:
    """Parse a panel's observed elements. Returns a PanelData with as many fields filled as possible."""
    elems = list(elements)

    text_elems = [e for e in elems if getattr(e, "role", "") == "text" and e.name and e.name.strip()]
    title_candidates = [e for e in text_elems if e.bounds[1] < 200]
    if title_candidates:
        title = max(title_candidates, key=lambda e: len(e.name)).name.strip()
    else:
        title = (text_elems[0].name.strip() if text_elems else "")

    posted_text = next((e.name.strip() for e in text_elems if _POSTED_RE.match(e.name or "")), None)

    budget_kind = None
    budget_raw = None
    budget_min = budget_max = None
    for e in text_elems:
        n = e.name.strip()
        if n.lower().startswith("fixed-price"):
            budget_kind = "fixed"
            budget_raw = n
        elif n.lower().startswith("hourly"):
            budget_kind = "hourly"
            budget_raw = n
    money_values = []
    for e in text_elems:
        for m in _MONEY_RE.finditer(e.name or ""):
            money_values.append(float(m.group(1).replace(",", "")))
    if money_values:
        budget_min = min(money_values)
        budget_max = max(money_values) if len(set(money_values)) > 1 else None

    skills: List[str] = []
    in_skills = False
    SECTION_HEADERS = {"about the client", "activity on this job", "skills and expertise", "job link"}
    for e in text_elems:
        n = (e.name or "").strip().lower()
        if n == "skills and expertise":
            in_skills = True
            continue
        if in_skills:
            if n in SECTION_HEADERS or len(e.name) > 80:
                in_skills = False
                continue
            if 1 <= len(e.name) <= 60:
                skills.append(e.name.strip())

    body_candidates = [e for e in text_elems if len(e.name) >= 200 and e.bounds[1] > 200]
    description = max(body_candidates, key=lambda e: len(e.name)).name if body_candidates else ""

    client_payment_verified = any("payment verified" in (e.name or "").lower() for e in text_elems)

    KNOWN_COUNTRIES = {
        "United States", "United Kingdom", "Canada", "Australia", "Germany", "India",
        "Pakistan", "France", "Spain", "Italy", "Netherlands", "Sweden", "Brazil",
        "Singapore", "Japan", "Saudi Arabia", "United Arab Emirates", "Israel"
    }
    client_country = next((e.name.strip() for e in text_elems if e.name.strip() in KNOWN_COUNTRIES), None)

    return PanelData(
        title=title,
        posted_text=posted_text,
        budget_kind=budget_kind,
        budget_min_usd=budget_min,
        budget_max_usd=budget_max,
        budget_raw_text=budget_raw,
        duration=None,
        experience_level=None,
        hours_per_week=None,
        skills=skills,
        description=description,
        client_country=client_country,
        client_payment_verified=client_payment_verified or None,
    )
