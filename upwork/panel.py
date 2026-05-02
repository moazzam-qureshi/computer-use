"""Panel parser — converts observed elements from the right-side detail panel into PanelData."""
from __future__ import annotations

import re
import time
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
_JOB_MONEY_RE = re.compile(r"^\$\d[\d,]*(?:\.\d+)?(?:\s*-\s*\$\d[\d,]*(?:\.\d+)?)?$")
_RATING_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:out of\s*5|/\s*5)", re.I)
_SPENT_RE = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)\s*([KkMm])?\+?\s*(?:total\s*)?spent", re.I)
_HIRES_RE = re.compile(r"(\d+)\s*hires?\b", re.I)
_PROPOSALS_RE = re.compile(r"proposals?\s*[:\-]?\s*(less than\s*\d+|\d+\s*to\s*\d+|\d+\s*-\s*\d+|\d+\+?)", re.I)
_AVG_HOURLY_RE = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)\s*/\s*hr", re.I)


NON_TITLE_NAMES = {
    "best matches", "most recent", "saved jobs", "create contract",
    "learn how", "filters", "skip skills", "next skills. update list",
    "open job in a new window", "go back", "apply now", "save job",
    "flag as inappropriate", "more info about payment verification",
    "more info about proposals", "view all recommendations",
    "muhammad moazzam q.", "complete your profile", "promote with ads",
    "edit availability badge", "boost your profile off",
    "edit boost your profile", "consultations", "preferences",
    "open upagents (ctrl+shift+k)", "arrange split view",
    "search tabs", "new tab", "mute tab", "close",
    "ahrefs seo toolbar\nhas access to this site",
    "open site in new tab",
}


KNOWN_COUNTRIES = {
    "United States", "United Kingdom", "Canada", "Australia", "Germany", "India",
    "Pakistan", "France", "Spain", "Italy", "Netherlands", "Sweden", "Brazil",
    "Singapore", "Japan", "Saudi Arabia", "United Arab Emirates", "Israel",
    "Ireland", "New Zealand", "Switzerland", "Norway", "Denmark", "Finland",
    "Belgium", "Austria", "Portugal", "Mexico", "Argentina", "Chile", "Poland",
    "Ukraine", "Turkey", "Egypt", "South Africa", "Nigeria", "Kenya", "China",
    "Hong Kong", "South Korea", "Taiwan", "Thailand", "Vietnam", "Philippines",
    "Indonesia", "Malaysia", "Bangladesh", "Sri Lanka", "Nepal",
}


def capture_panel(window_title: str, max_scrolls: int = 60) -> tuple[list, object]:
    """Capture all elements across the open panel; return (elements, copy_btn).

    Approach: observe top-of-panel, then walk the panel down ONE Down-arrow
    press at a time, observing AFTER each press, stopping the moment the
    Copy-to-clipboard button enters the UIA tree.

    Cadence: ~0.6s between arrow presses so each step is visibly small and
    controlled. With `max_scrolls=60` that's a hard upper bound of ~36s per
    panel, but in practice the loop exits the moment Copy appears (usually
    within 10-20 presses for typical Upwork JDs).

    Returns:
        (merged_elements, copy_to_clipboard_button_element_or_None)
    """
    from substrate import act, observe

    act.focus_window(window_title)
    obs_top = observe.observe(window_title=window_title, include_unnamed=False, include_text=True)

    panel_open = any(
        e.role == "button" and (e.name or "").strip() == "Apply now"
        for e in obs_top.elements
    )
    if not panel_open:
        return [], None

    seen_keys = {(e.role, e.name, e.bounds) for e in obs_top.elements}
    merged = list(obs_top.elements)

    def _find_copy_button(elements: list):
        for e in elements:
            if e.role == "button" and (e.name or "").strip() == "Copy to clipboard":
                l, t, r, b = e.bounds
                if l >= 1200 and t >= 200 and r > l and b > t and r <= 5000 and b <= 5000:
                    return e
        return None

    copy_btn = _find_copy_button(obs_top.elements)
    if copy_btn is not None:
        print(f"[panel] Copy button visible at top; bounds={copy_btn.bounds}", flush=True)

    # Slow Down-arrow scroll until Copy enters the viewport. Stop the moment
    # it appears so we never scroll past it. Once the Copy button leaves the
    # viewport, its bounds become stale and clicking them hits empty space.
    # We accept that fields below Copy on long panels (extra client trust
    # signals, etc.) won't be captured; URL capture is the priority.
    for i in range(max_scrolls):
        if copy_btn is not None:
            break
        act.focus_window(window_title)
        act.key("down")
        time.sleep(0.6)
        obs_more = observe.observe(window_title=window_title, include_unnamed=False, include_text=True)
        for e in obs_more.elements:
            k = (e.role, e.name, e.bounds)
            if k not in seen_keys:
                merged.append(e)
                seen_keys.add(k)
        copy_btn = _find_copy_button(obs_more.elements)
        if copy_btn is not None:
            print(f"[panel] Copy button found after {i+1} arrow-down(s); bounds={copy_btn.bounds}", flush=True)

    if copy_btn is None:
        all_copy = [e for e in merged if e.role == "button" and (e.name or "").strip() == "Copy to clipboard"]
        print(f"[panel] panel ended without Copy button visible. Copy buttons ever seen: {len(all_copy)}; bounds: {[e.bounds for e in all_copy]}", flush=True)

    return merged, copy_btn


def _parse_money(token: str) -> Optional[float]:
    m = _MONEY_RE.search(token)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _parse_spent(text: str) -> Optional[float]:
    m = _SPENT_RE.search(text)
    if not m:
        return None
    try:
        v = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    suf = (m.group(2) or "").lower()
    if suf == "k":
        v *= 1_000
    elif suf == "m":
        v *= 1_000_000
    return v


def _parse_proposals(text: str) -> Optional[int]:
    m = _PROPOSALS_RE.search(text)
    if not m:
        return None
    s = m.group(1).strip().lower()
    if s.startswith("less than"):
        nums = re.findall(r"\d+", s)
        if nums:
            return int(nums[0])
        return None
    nums = re.findall(r"\d+", s)
    if nums:
        return int(nums[0])
    return None


def parse_panel(elements: Iterable) -> PanelData:
    """Parse a panel's observed elements. Returns a PanelData with as many fields filled as possible."""
    elems = list(elements)

    # ---- Title: prefer wide hyperlink near top; fall back to topmost text element.
    title = ""
    hl_candidates = [
        e for e in elems
        if getattr(e, "role", "") == "hyperlink"
        and e.name and len(e.name) >= 30
        and (e.bounds[2] - e.bounds[0]) >= 300
        and e.name.strip().lower() not in NON_TITLE_NAMES
    ]
    if hl_candidates:
        title = sorted(hl_candidates, key=lambda e: e.bounds[1])[0].name.strip()
    if not title:
        text_elems_for_title = [
            e for e in elems
            if getattr(e, "role", "") == "text"
            and e.name and e.name.strip()
            and e.name.strip().lower() not in NON_TITLE_NAMES
        ]
        title_candidates = [e for e in text_elems_for_title if e.bounds[1] < 200]
        if title_candidates:
            title = max(title_candidates, key=lambda e: len(e.name)).name.strip()
        elif text_elems_for_title:
            title = text_elems_for_title[0].name.strip()

    text_elems = [e for e in elems if getattr(e, "role", "") == "text" and e.name and e.name.strip()]

    # ---- Posted
    posted_text = None
    for e in elems:
        if getattr(e, "role", "") == "text" and _POSTED_RE.match((e.name or "").strip()):
            posted_text = e.name.strip()
            break

    # ---- Description: longest text element >= 200 chars below the top region.
    body_candidates = [e for e in text_elems if len(e.name) >= 100]
    description = ""
    if body_candidates:
        description = max(body_candidates, key=lambda e: len(e.name)).name[:5000]

    # ---- Budget chips. Walk text/listitem in document order; pair adjacent
    # money values as ranges; keep solo money only if near a Fixed/Hourly label.
    budget_kind: Optional[str] = None
    budget_raw_bits: List[str] = []
    seen_bits: set = set()
    duration: Optional[str] = None
    experience_level: Optional[str] = None
    hours_per_week: Optional[str] = None

    money_singletons: List[tuple] = []  # (idx, value-string)

    def _add_bit(s: str) -> None:
        s = s.strip()
        if s and s not in seen_bits:
            budget_raw_bits.append(s)
            seen_bits.add(s)

    for i, e in enumerate(elems):
        if getattr(e, "role", "") not in ("text", "listitem"):
            continue
        n = (e.name or "").strip()
        if not n or len(n) > 200:
            continue
        nl = n.lower()

        if any(k in nl for k in ("total spent", "/hr avg", "hires,", "jobs posted", "hire rate")):
            continue

        if nl == "fixed-price" or nl.startswith("fixed-price"):
            budget_kind = "fixed"
            _add_bit("Fixed-price")
            continue
        if nl == "hourly" or nl.startswith("hourly:") or nl.startswith("hourly "):
            budget_kind = "hourly"
            _add_bit(n if nl != "hourly" else "Hourly")
            continue
        if nl in ("expert", "intermediate", "entry level"):
            experience_level = n
            _add_bit(n)
            continue
        if "hrs/week" in nl or "hours/week" in nl:
            hours_per_week = n
            _add_bit(n)
            continue
        if nl.startswith(("less than", "more than")) and ("month" in nl or "week" in nl):
            duration = n
            _add_bit(n)
            continue
        if "one-time project" in nl or "ongoing project" in nl:
            _add_bit(n)
            continue
        if _JOB_MONEY_RE.match(n):
            money_singletons.append((i, n))
            continue

    # Resolve money tokens.
    money_values: List[float] = []
    if money_singletons:
        money_singletons.sort()
        used = set()
        for k in range(len(money_singletons)):
            if k in used:
                continue
            i, val = money_singletons[k]
            if k + 1 < len(money_singletons):
                j, val2 = money_singletons[k + 1]
                if j - i <= 3:
                    _add_bit(f"{val}-{val2}")
                    used.add(k)
                    used.add(k + 1)
                    v1 = _parse_money(val)
                    v2 = _parse_money(val2)
                    if v1 is not None:
                        money_values.append(v1)
                    if v2 is not None:
                        money_values.append(v2)
                    continue
            # Solo: keep only if near a budget label.
            window_lo = max(0, i - 3)
            window_hi = min(len(elems), i + 4)
            has_budget_label = False
            for m in range(window_lo, window_hi):
                el = elems[m]
                if getattr(el, "role", "") in ("text", "listitem"):
                    nm = (el.name or "").strip().lower()
                    if nm == "fixed-price" or nm.startswith("fixed-price") or nm.startswith("hourly"):
                        has_budget_label = True
                        break
            # Also handle range-form like "$30.00-$60.00" embedded in single token
            if "-" in val:
                parts = val.split("-")
                for p in parts:
                    pv = _parse_money(p)
                    if pv is not None:
                        money_values.append(pv)
                _add_bit(val)
                used.add(k)
                continue
            if has_budget_label:
                _add_bit(val)
                used.add(k)
                pv = _parse_money(val)
                if pv is not None:
                    money_values.append(pv)

    # Fallback: if no structured money values found but Fixed/Hourly was set,
    # take any money tokens we saw (legacy test compatibility).
    if not money_values and budget_kind:
        for i, val in money_singletons:
            pv = _parse_money(val)
            if pv is not None:
                money_values.append(pv)
                _add_bit(val)

    budget_min: Optional[float] = None
    budget_max: Optional[float] = None
    if money_values:
        budget_min = min(money_values)
        if len(set(money_values)) > 1:
            budget_max = max(money_values)

    budget_raw_text = " | ".join(budget_raw_bits)[:400] or None

    # ---- Skills: hyperlinks after "Skills and Expertise" anchor; fallback to
    # short hyperlinks anywhere.
    skills: List[str] = []
    skills_anchor_idx = None
    for i, e in enumerate(elems):
        if getattr(e, "role", "") == "text" and (e.name or "").strip().lower() in (
            "skills and expertise", "mandatory skills"
        ):
            skills_anchor_idx = i
            break

    def _is_skill_candidate(name: str) -> bool:
        return bool(
            name and 2 <= len(name) <= 40
            and name != title
            and name.lower() not in NON_TITLE_NAMES
            and not name.startswith(("View ", "more about ", "Save job ", "Job feedback "))
        )

    if skills_anchor_idx is not None:
        # Section ends at next major header or a long text block.
        END_HEADERS = {"about the client", "activity on this job", "job link"}
        for e in elems[skills_anchor_idx + 1:]:
            role = getattr(e, "role", "")
            n = (e.name or "").strip()
            nl = n.lower()
            if role == "text" and (nl in END_HEADERS or len(n) > 80):
                break
            if role == "hyperlink" and _is_skill_candidate(n):
                if n not in skills:
                    skills.append(n)
                if len(skills) >= 15:
                    break
            elif role == "text" and 1 <= len(n) <= 60 and nl not in END_HEADERS:
                # Skills sometimes render as text chips, not hyperlinks.
                if _is_skill_candidate(n) and n not in skills:
                    skills.append(n)
                if len(skills) >= 15:
                    break
    else:
        for e in elems:
            if getattr(e, "role", "") == "hyperlink":
                n = (e.name or "").strip()
                if _is_skill_candidate(n) and n not in skills:
                    skills.append(n)

    # ---- Client info: scan text/listitem elements for anchors.
    client_payment_verified: Optional[bool] = None
    client_rating: Optional[float] = None
    client_hires: Optional[int] = None
    client_total_spent_usd: Optional[float] = None
    client_avg_hourly_paid: Optional[float] = None
    client_country: Optional[str] = None
    proposals_count: Optional[int] = None

    for e in elems:
        if getattr(e, "role", "") not in ("text", "listitem"):
            continue
        n = (e.name or "").strip()
        if not n:
            continue
        nl = n.lower()

        if "payment verified" in nl or "payment method verified" in nl:
            client_payment_verified = True
        if "rating" in nl or "out of 5" in nl:
            mm = _RATING_RE.search(n)
            if mm and client_rating is None:
                try:
                    client_rating = float(mm.group(1))
                except ValueError:
                    pass
        if "spent" in nl and client_total_spent_usd is None:
            v = _parse_spent(n)
            if v is not None:
                client_total_spent_usd = v
        if "hires" in nl and client_hires is None:
            mh = _HIRES_RE.search(n)
            if mh:
                try:
                    client_hires = int(mh.group(1))
                except ValueError:
                    pass
        if "/hr" in nl and "avg" in nl and client_avg_hourly_paid is None:
            ma = _AVG_HOURLY_RE.search(n)
            if ma:
                try:
                    client_avg_hourly_paid = float(ma.group(1).replace(",", ""))
                except ValueError:
                    pass
        if "proposals" in nl and proposals_count is None:
            pv = _parse_proposals(n)
            if pv is not None:
                proposals_count = pv
        if client_country is None and n in KNOWN_COUNTRIES:
            client_country = n

    return PanelData(
        title=title,
        posted_text=posted_text,
        budget_kind=budget_kind,
        budget_min_usd=budget_min,
        budget_max_usd=budget_max,
        budget_raw_text=budget_raw_text,
        duration=duration,
        experience_level=experience_level,
        hours_per_week=hours_per_week,
        skills=skills,
        description=description,
        client_country=client_country,
        client_payment_verified=client_payment_verified,
        client_rating=client_rating,
        client_hires=client_hires,
        client_total_spent_usd=client_total_spent_usd,
        client_avg_hourly_paid=client_avg_hourly_paid,
        proposals_count=proposals_count,
    )
