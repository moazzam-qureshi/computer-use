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


def capture_panel(window_title: str, max_scrolls: int = 60) -> tuple[list, str]:
    """Walk the open panel top-to-bottom, capturing every element along the way.

    Behavior:
      1. Observe the panel top.
      2. Scroll down one Down-arrow at a time (~40px), observing after each press.
      3. The MOMENT the Copy-to-clipboard button becomes visible, click it
         immediately and capture the resulting URL from the clipboard. This
         eliminates any stale-bounds risk: the button is clicked while its
         bounds are live in the current observation.
      4. Continue scrolling after URL capture until we hit panel-bottom
         (detected when an observation produces zero new elements). This
         surfaces description / budget chips / skills / client trust signals
         that may sit BELOW the Copy button on long panels.

    Returns:
        (merged_elements, captured_url_or_empty_string)
    """
    from substrate import act, observe
    from upwork import clipboard_url

    act.focus_window(window_title)
    obs_top = observe.observe(window_title=window_title, include_unnamed=False, include_text=True)

    panel_open = any(
        e.role == "button" and (e.name or "").strip() == "Apply now"
        for e in obs_top.elements
    )
    if not panel_open:
        return [], ""

    seen_keys = {(e.role, e.name, e.bounds) for e in obs_top.elements}
    merged = list(obs_top.elements)

    def _find_copy_button(elements: list):
        for e in elements:
            if e.role == "button" and (e.name or "").strip() == "Copy to clipboard":
                l, t, r, b = e.bounds
                if l >= 1200 and t >= 200 and r > l and b > t and r <= 5000 and b <= 5000:
                    return e
        return None

    captured_url = ""

    # If Copy is already visible at the top of the panel, click it immediately.
    initial_copy = _find_copy_button(obs_top.elements)
    if initial_copy is not None:
        print(f"[panel] Copy visible at top; clicking now bounds={initial_copy.bounds}", flush=True)
        url = clipboard_url.capture_url_from_button(window_title, initial_copy)
        if url:
            captured_url = url
            print(f"[panel] URL captured: {url}", flush=True)

    # Two-phase scroll cadence:
    #   Phase A (URL not yet captured): slow Down-arrow with 0.6s settle.
    #     Small steps so we observe Copy the instant it enters the viewport
    #     and never overshoot before we can click it.
    #   Phase B (URL already captured): mouse-WHEEL scroll at the panel-body
    #     coordinate. Why not PageDown: clicking Copy puts keyboard focus on
    #     the button (and triggers a tooltip), so subsequent PageDown keys
    #     either no-op or are caught by the tooltip/modal layer, causing the
    #     loop to detect zero progress and bail out — losing description /
    #     skills / client-trust elements that live BELOW the Copy button.
    #     Wheel events scroll whatever is under the cursor regardless of
    #     keyboard focus, so we explicitly aim them at the Copy button's
    #     last-known coordinate (which is inside the panel scroll area).
    import pyautogui  # local import; act.scroll wraps but doesn't accept coords
    consecutive_no_progress = 0
    # Snapshot the post-click anchor for wheel scrolling. captured_url path
    # only enters Phase B once, so we set it here when URL is captured below.
    wheel_anchor: Optional[tuple[int, int]] = None
    if captured_url and initial_copy is not None:
        wheel_anchor = initial_copy.center
    for i in range(max_scrolls):
        act.focus_window(window_title)
        if not captured_url:
            act.key("down")
            time.sleep(0.6)
        else:
            # Wheel-scroll at the panel-body anchor. Negative argument scrolls
            # content downward (page advances). Magnitude tuned so each call
            # advances roughly one viewport, matching the old PageDown cadence.
            if wheel_anchor is not None:
                pyautogui.moveTo(wheel_anchor[0], wheel_anchor[1])
            pyautogui.scroll(-600)
            time.sleep(0.35)
        obs_more = observe.observe(window_title=window_title, include_unnamed=False, include_text=True)
        new_count = 0
        for e in obs_more.elements:
            k = (e.role, e.name, e.bounds)
            if k not in seen_keys:
                merged.append(e)
                seen_keys.add(k)
                new_count += 1

        # Click Copy the instant it's in the live observation. Bounds are
        # guaranteed fresh because we observed them in this same iteration.
        if not captured_url:
            copy_now = _find_copy_button(obs_more.elements)
            if copy_now is not None:
                print(f"[panel] Copy found mid-scroll after {i+1} down(s); clicking now bounds={copy_now.bounds}", flush=True)
                url = clipboard_url.capture_url_from_button(window_title, copy_now)
                if url:
                    captured_url = url
                    # Anchor wheel-scroll at the just-clicked button. Cursor
                    # is already there from click_xy, but we re-snapshot the
                    # coordinate so subsequent moveTo calls are explicit.
                    wheel_anchor = copy_now.center
                    print(f"[panel] URL captured: {url}; wheel_anchor={wheel_anchor}", flush=True)

        if new_count == 0:
            consecutive_no_progress += 1
            if consecutive_no_progress >= 2:
                print(f"[panel] reached end of panel after {i+1} press(es); merged {len(merged)} elements; url={'YES' if captured_url else 'NO'}", flush=True)
                break
        else:
            consecutive_no_progress = 0

    return merged, captured_url

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


_CLIENT_HISTORY_ANCHORS = (
    "client's recent history",
    "client's history",
    "recent history",
    "jobs in progress",
)


def _truncate_at_client_history(elems: list) -> list:
    """Return elems sliced to drop everything from the client-history
    section onward.

    The Upwork panel renders the client's PRIOR JOB POSTINGS at the
    bottom of the panel — each one with its own budget chip ('Hourly',
    'Fixed-price'), skill chips, description preview, etc. If our
    parser walks past this anchor it picks up budget/skill/etc tokens
    from those prior jobs and overwrites the actual job's data with
    the client-history bleed. Truncating here is the cleanest fix —
    every legitimate field for THIS job appears before this section.

    No-op if no anchor found (some panels don't render the section,
    e.g. when the client has zero prior history).
    """
    for i, e in enumerate(elems):
        if getattr(e, "role", "") != "text":
            continue
        n = (e.name or "").strip().lower()
        if not n:
            continue
        for anchor in _CLIENT_HISTORY_ANCHORS:
            if n.startswith(anchor):
                return elems[:i]
    return elems


def parse_panel(elements: Iterable) -> PanelData:
    """Parse a panel's observed elements. Returns a PanelData with as many fields filled as possible."""
    elems = list(elements)
    # Drop everything from the client-history section onward — those are
    # the client's PRIOR job postings rendered with their own budget /
    # skill / description chips. Walking past this anchor causes the
    # parser to overwrite THIS job's fields with bleed from prior jobs.
    elems = _truncate_at_client_history(elems)

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
    budget_diag: List[str] = []  # diagnostic trail

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
            budget_diag.append(f"FIXED@{i} <- {n!r}")
            _add_bit("Fixed-price")
            continue
        if nl == "hourly" or nl.startswith("hourly:") or nl.startswith("hourly "):
            budget_kind = "hourly"
            budget_diag.append(f"HOURLY@{i} <- {n!r}")
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
            budget_diag.append(f"MONEY@{i} <- {n!r}")
            money_singletons.append((i, n))
            continue

    if budget_diag:
        print(f"[panel.parse] budget extraction trail: {budget_diag} -> kind={budget_kind!r}", flush=True)

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
