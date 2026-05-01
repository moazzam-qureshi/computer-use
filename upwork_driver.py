"""
Deterministic Upwork job-scanner.

The Python loop does navigation. The LLM is invoked exactly once per job —
only for the relevance judgment. No agent loop, no recursion limit, no
hallucinated URLs.

Verified primitives (proven manually with dump.py + poke.py):
  - Feed: each job is a hyperlink in [main]; nearby skill-tag hyperlinks.
  - Click title → detail panel opens (URL doesn't change).
  - Description: text element starting with "Description" or sometimes
    just the body. Full text is in the element's `name`.
  - Copy URL: button named exactly "Copy to clipboard". Click it → URL
    is in the system clipboard.
  - Close panel: press Escape (does NOT move cursor).
  - Panel scroll: PageDown works (panel takes focus on open).

Usage:
    uv run upwork_driver.py
    uv run upwork_driver.py --max-jobs 10 --output relevant_jobs.md
"""
from __future__ import annotations

import argparse
import io
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# UTF-8 console output (Upwork descriptions can have em-dashes etc.)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import pyperclip
from dotenv import load_dotenv
from openai import OpenAI

import act
import gdocs
import jobs_store
import notify
import observe
import pacing
import proposal as proposal_mod

WINDOW = "Upwork"
URL_RE = re.compile(r"https?://www\.upwork\.com/jobs/~[0-9a-f]+")

# Strings that identify the feed nav vs job titles
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

# ============================================================================
# CRITERIA — read by the LLM judge.
# ============================================================================
CRITERIA = """\
The user's Upwork feed is already pre-filtered by Upwork's relevance algorithm
to their skills. Almost everything in the feed is worth alerting on. Skip ONLY
the two hard exclusions below; otherwise mark RELEVANT.

SKIP only if BOTH of these conditions are true at the same time:
  1. The job is FIXED-PRICE, AND
  2. The job's duration is short — "Less than 1 month" / "Less than 1 week"
     / "1-2 weeks" / "quick task" / similar sub-month phrasing.

If only ONE of these is true, the job is still RELEVANT:
  - Fixed-price + 6-month duration  -> RELEVANT (long-term work, ok)
  - Hourly + Less than 1 month       -> RELEVANT (hourly is fine, ok)
  - Hourly + 6-month duration        -> RELEVANT (perfect)
  - Fixed-price + Less than 1 month  -> SKIP (both bad → skip)

If neither hard-skip applies, mark RELEVANT. Don't overthink it. Don't filter
on tech stack, hourly rate, client history, description quality, or how
"engineering-y" the job sounds. Just the AND-rule above."""


@dataclass
class JobInfo:
    title: str
    url: str = ""
    posted: str = ""
    budget: str = ""
    tags: list[str] | None = None
    description: str = ""
    client_summary: str = ""

    def to_markdown(self, why: str) -> str:
        tags_str = ", ".join(self.tags or [])
        return (
            f"## {self.title}\n"
            f"- **URL**: {self.url}\n"
            f"- **Posted**: {self.posted or 'n/a'}\n"
            f"- **Budget**: {self.budget or 'n/a'}\n"
            f"- **Tags**: {tags_str or 'n/a'}\n"
            f"- **Client**: {self.client_summary or 'n/a'}\n"
            f"- **Why relevant**: {why}\n\n"
            f"---\n\n"
        )


def log(msg: str) -> None:
    print(f"[driver] {msg}", flush=True)


# ----------------------------------------------------------------------------
# Feed scraping
# ----------------------------------------------------------------------------

def parse_feed_cards(window: str) -> list[tuple[JobInfo, observe.Element]]:
    """Parse the Upwork feed deterministically into a list of (JobInfo, title_element).

    Algorithm: every job card starts with a 'Posted' text label. We segment the
    element list into card slices delimited by these labels. Within each slice
    every field has a known anchor in the dump, e.g.:
      "Posted" → next element is the time-ago text
      "Hourly" / "Fixed-price" → budget type
      "Expert" / "Intermediate" → experience level
      "Est. Time:" / "Est. Budget:" → next element is the value
      "Proposals:" → next element is the range
      "$XK+" + "spent" → spend bucket
      "Rating is N out of 5." → rating
      "Save job <title>" button → canonical title

    Returns list of (parsed JobInfo, hyperlink to click on for that card).
    """
    obs = observe.observe(window_title=window, include_unnamed=False, include_text=True)
    elements = obs.elements

    posted_indices = [
        i for i, e in enumerate(elements)
        if e.role == "text" and e.name.strip() == "Posted"
    ]
    if not posted_indices:
        return []

    out: list[tuple[JobInfo, observe.Element]] = []
    for idx, start in enumerate(posted_indices):
        next_posted = posted_indices[idx + 1] if idx + 1 < len(posted_indices) else len(elements)
        # Trim slice when y jumps backwards — that means we crossed into the
        # right sidebar / chrome subtree, which has its own y origin.
        end = _find_card_end(elements, start, next_posted)
        info, title_el = _parse_one_card(elements[start:end])
        if info and title_el:
            out.append((info, title_el))
    return out


def _find_card_end(elements: list[observe.Element], start: int, hard_end: int) -> int:
    """Walk forward from `start`; truncate when y jumps backwards by >150px.

    Card elements have monotonically growing y. When y resets to a much smaller
    value, we've crossed into a sibling DOM subtree (sidebar) and need to stop.
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


def _parse_one_card(card: list[observe.Element]) -> tuple[JobInfo | None, observe.Element | None]:
    """Parse a slice of elements that belong to one job card."""
    info = JobInfo(title="", tags=[])
    title_el: observe.Element | None = None

    # Canonical title from "Save job <title>" button
    save_prefix = "save job "
    canonical_title = ""
    for e in card:
        if e.role == "button" and e.name.lower().startswith(save_prefix):
            canonical_title = e.name[len(save_prefix):].strip()
            break
    if not canonical_title:
        return None, None
    info.title = canonical_title

    # Title hyperlink (exact match, full or truncated prefix)
    for e in card:
        if e.role == "hyperlink" and e.name.strip() == canonical_title:
            title_el = e
            break
    if title_el is None:
        for e in card:
            if e.role == "hyperlink" and len(e.name) > 20 and canonical_title.startswith(e.name.strip()[:30]):
                title_el = e
                break

    # Walk the card collecting fields by anchor labels
    posted_time = ""
    budget_type = ""
    experience = ""
    est_label = ""
    est_value = ""
    proposals_value = ""
    spent = ""
    rating = ""
    country = ""
    payment_verified = False
    description = ""
    skills: list[str] = []
    last_label: str | None = None

    posted_re = re.compile(r"^\d+\s*(minute|hour|day|week|month)s?\s*ago$", re.I)
    proposals_re = re.compile(r"^(\d+\s*(to|-)\s*\d+|less than\s*\d+|more than\s*\d+)$", re.I)
    spent_re = re.compile(r"^\$\d+(\.\d+)?[KM]?\+?$")

    for e in card:
        n = e.name.strip()
        if not n:
            continue

        if e.role == "text":
            # Pure labels — set what's expected next
            if n == "Posted":
                last_label = "posted"
                continue
            # Budget type: 'Hourly', 'Fixed-price', or 'Hourly: $X-$Y' (with rate inline)
            if n == "Fixed-price" or n.startswith("Fixed-price"):
                budget_type = "Fixed-price"
                continue
            if n == "Hourly" or n.startswith("Hourly:") or n.startswith("Hourly "):
                budget_type = n  # keep the full string so we capture the rate
                continue
            if n in ("Expert", "Intermediate", "Entry level"):
                experience = n
                continue
            if n in ("Est. Time:", "Est. Budget:"):
                last_label = n
                continue
            if n == "Proposals:":
                last_label = "proposals"
                continue
            if n == "Verified":
                continue
            if n == "Payment verified":
                payment_verified = True
                continue
            if n == "spent":
                continue

            # Values that follow labels
            if last_label == "posted" and posted_re.match(n):
                posted_time = n
                last_label = None
                continue
            if last_label in ("Est. Time:", "Est. Budget:"):
                est_label = last_label
                est_value = n
                last_label = None
                continue
            if last_label == "proposals" and proposals_re.match(n):
                proposals_value = n
                last_label = None
                continue

            # Spent value
            if spent_re.match(n) and not spent:
                spent = n
                continue

            # Rating
            if n.lower().startswith("rating is"):
                m = re.search(r"(\d+(?:\.\d+)?)\s*out of\s*5", n)
                rating = m.group(1) if m else ""
                continue

            # Long description text — only one per card
            if len(n) >= 200 and not description:
                description = n[:5000]
                continue

            # Country (heuristic: short capitalized text after spent)
            if not country and spent and 3 <= len(n) <= 40 and n[0].isupper() and not n.startswith("$"):
                country = n
                continue

        elif e.role == "hyperlink":
            if (n != canonical_title and 2 <= len(n) <= 40
                    and not n.startswith(("View ", "more about ", "Save job ", "Job feedback "))
                    and n not in skills):
                skills.append(n)

    info.posted = posted_time

    bp: list[str] = []
    if budget_type:
        bp.append(budget_type)
    if est_label and est_value:
        bp.append(f"{est_label} {est_value}")
    if experience:
        bp.append(experience)
    info.budget = " | ".join(bp)

    cs: list[str] = []
    if payment_verified:
        cs.append("Payment verified")
    if rating:
        cs.append(f"Rating {rating}/5")
    if spent:
        cs.append(f"{spent} spent")
    if country:
        cs.append(country)
    if proposals_value:
        cs.append(f"Proposals: {proposals_value}")
    info.client_summary = " | ".join(cs)

    info.description = description
    info.tags = skills[:15]
    return info, title_el


def collect_panel_info(window: str, expected_title: str = "") -> JobInfo:
    """Read a job's detail panel (must be already open).

    The panel is taller than the viewport — its content is split across:
      Top:    Title, Posted, Worldwide, Summary, Description (start)
      Middle: Description (continued), Apply now, Budget chips
      Bottom: Skills, Activity, About the client, Job link + Copy

    We scroll the panel to the top first, observe, then scroll to the
    bottom and observe again, merging the elements seen across both views.
    Then we extract every field by structural anchor.
    """
    info = JobInfo(title=expected_title, tags=[])

    # Phase 1: observe immediately. The panel opens at its top by default
    # after the title click, so we don't need to scroll up first.
    # CRITICAL: Do NOT send Ctrl+Home here — that would scroll the FEED to
    # the top (the feed has keyboard focus after the click, not the panel),
    # which collapses the panel and we'd end up reading the feed twice.
    act.focus_window(window)
    obs_top = observe.observe(window_title=window, include_unnamed=False, include_text=True)

    # Verify the panel actually opened. The "Apply now" button only exists in
    # an open job-detail panel — it's a reliable panel-open signal.
    panel_open = any(
        e.role == "button" and e.name.strip() == "Apply now"
        for e in obs_top.elements
    )
    if not panel_open:
        # Panel didn't open — return empty info so the caller can skip this job
        # rather than reading the entire feed and mashing all cards together.
        info.description = ""
        info.budget = ""
        info.client_summary = ""
        info.tags = []
        return info

    # Phase 2: PageDown a few times to surface skills, client info, and Job link.
    seen_keys: set[tuple] = {(e.role, e.name, e.bounds) for e in obs_top.elements}
    merged: list[observe.Element] = list(obs_top.elements)
    for _ in range(4):
        act.focus_window(window)
        act.scroll(1, method="key")  # PageDown — focused panel scrolls
        time.sleep(0.7)
        obs_more = observe.observe(window_title=window, include_unnamed=False, include_text=True)
        for e in obs_more.elements:
            k = (e.role, e.name, e.bounds)
            if k not in seen_keys:
                merged.append(e)
                seen_keys.add(k)
        # Stop early if we've seen the Copy-to-clipboard button (panel bottom)
        if any(e.role == "button" and e.name.strip() == "Copy to clipboard" for e in obs_more.elements):
            break

    elements = merged

    # ---- Title: take from expected_title if provided, else the topmost wide
    # ---- hyperlink that contains a job-shaped string. expected_title is the
    # ---- canonical from the feed's "Save job ..." button — most reliable.
    if not info.title:
        candidates = [
            e for e in elements
            if e.role == "hyperlink"
            and len(e.name) >= 30
            and (e.bounds[2] - e.bounds[0]) >= 300
            and e.name.strip().lower() not in NON_TITLE_NAMES
        ]
        if candidates:
            info.title = sorted(candidates, key=lambda e: e.bounds[1])[0].name

    # ---- Description: panel renders it as a single text element starting
    # ---- with "Summary\nDescription:" or just the prose. The panel's
    # ---- description text is virtually always the longest text element.
    text_elems = [e for e in elements if e.role == "text" and e.name and len(e.name) >= 100]
    if text_elems:
        info.description = max(text_elems, key=lambda e: len(e.name)).name[:5000]

    # ---- Posted: regex match on time-ago format, anywhere in the panel.
    posted_re = re.compile(r"^\d+\s*(minute|hour|day|week|month)s?\s*ago$", re.I)
    for e in elements:
        if e.role == "text" and posted_re.match(e.name.strip()):
            info.posted = e.name.strip()
            break

    # ---- Budget: look for the budget chips. Money-shape values that are NOT
    # client-history ($X total spent, $X /hr avg) are job budget.
    # Layouts:
    #     Fixed-price: text "$XXX.XX" + text "Fixed-price"
    #     Hourly:      text "$X.XX-$Y.YY" + text "Hourly" + hrs/week + duration
    # Plus experience: "Expert" / "Intermediate" / "Entry level"
    # Plus project type: text "Project Type:" + "One-time project" / "Ongoing project"
    budget_bits: list[str] = []
    seen_budget: set[str] = set()
    job_money_re = re.compile(r"^\$\d[\d,]*(?:\.\d+)?(?:\s*-\s*\$\d[\d,]*(?:\.\d+)?)?$")  # "$500.00" or "$30.00-$60.00"

    def _add_budget(s: str) -> None:
        s = s.strip()
        if s and s not in seen_budget:
            budget_bits.append(s)
            seen_budget.add(s)

    # Track money values; we'll only keep those that appear part of a
    # range (X.XX adjacent to Y.YY) or labeled as Fixed-price/Hourly. Stray
    # singletons are likely client-history pollution.
    money_singletons: list[tuple[int, str]] = []  # (element index, value)
    next_after = ""
    last_label_was_money = False

    for i, e in enumerate(elements):
        if e.role not in ("text", "listitem"):
            continue
        n = e.name.strip()
        nl = n.lower()
        if not n or len(n) > 200:
            continue

        # Skip composite client-history strings outright
        if any(k in nl for k in ("total spent", "/hr avg", "hires,", "jobs posted", "hire rate", "of ")):
            last_label_was_money = False
            continue

        # Budget type
        if nl == "fixed-price" or nl.startswith("fixed-price"):
            _add_budget("Fixed-price")
            last_label_was_money = False
            continue
        if nl == "hourly" or nl.startswith("hourly:") or nl.startswith("hourly "):
            _add_budget(n if nl != "hourly" else "Hourly")
            last_label_was_money = False
            continue
        # Experience
        if nl in ("expert", "intermediate", "entry level"):
            _add_budget(n)
            last_label_was_money = False
            continue
        # Project-type label and value
        if nl in ("project type:", "est. time:", "est. budget:"):
            next_after = "type"
            last_label_was_money = False
            continue
        if next_after == "type":
            _add_budget(n)
            next_after = ""
            last_label_was_money = False
            continue
        # Money values: collect, decide later if they're job budget or noise
        if job_money_re.match(n):
            money_singletons.append((i, n))
            last_label_was_money = True
            continue
        # Duration / hours phrases
        if "hrs/week" in nl or "hours/week" in nl:
            _add_budget(n)
            last_label_was_money = False
            continue
        if nl.startswith(("less than", "more than")) and ("month" in nl or "week" in nl):
            _add_budget(n)
            last_label_was_money = False
            continue
        if "one-time project" in nl or "ongoing project" in nl:
            _add_budget(n)
            last_label_was_money = False
            continue
        last_label_was_money = False

    # Resolve money singletons: pair adjacent ones into a range "$X-$Y".
    # Drop standalone money values that aren't adjacent to another money value
    # AND aren't adjacent to a Fixed-price/Hourly label — those are client noise.
    if money_singletons:
        money_singletons.sort()
        used = set()
        for k in range(len(money_singletons)):
            if k in used:
                continue
            i, val = money_singletons[k]
            # Pair with next if adjacent (within 3 elements) — likely a range
            if k + 1 < len(money_singletons):
                j, val2 = money_singletons[k + 1]
                if j - i <= 3:
                    _add_budget(f"{val}-{val2}")
                    used.add(k)
                    used.add(k + 1)
                    continue
            # Solo money value: only keep if a Fixed-price or Hourly label is
            # within 3 elements (suggests this is the job amount, not client stat).
            window_lo = max(0, i - 3)
            window_hi = min(len(elements), i + 4)
            has_budget_label = any(
                (elements[m].name.strip().lower() == "fixed-price"
                 or elements[m].name.strip().lower().startswith("fixed-price")
                 or elements[m].name.strip().lower().startswith("hourly"))
                for m in range(window_lo, window_hi)
                if elements[m].role in ("text", "listitem")
            )
            if has_budget_label:
                _add_budget(val)
                used.add(k)
            # else: dropped as noise

    info.budget = " | ".join(budget_bits)[:300]

    # ---- Skills: hyperlinks that appear after "Skills and Expertise" label
    # ---- or, if that anchor isn't present, hyperlinks that aren't system-named.
    skills_anchor_idx = None
    for i, e in enumerate(elements):
        if e.role == "text" and e.name.strip().lower() in ("skills and expertise", "mandatory skills"):
            skills_anchor_idx = i
            break
    skill_candidates: list[str] = []
    if skills_anchor_idx is not None:
        for e in elements[skills_anchor_idx:]:
            if e.role == "hyperlink":
                n = e.name.strip()
                if (n and 2 <= len(n) <= 40
                        and n != info.title
                        and n.lower() not in NON_TITLE_NAMES
                        and not n.startswith(("View ", "more about ", "Save job ", "Job feedback "))):
                    if n not in skill_candidates:
                        skill_candidates.append(n)
                    if len(skill_candidates) >= 15:
                        break
    else:
        # Fallback: short hyperlinks anywhere in panel
        for e in elements:
            if e.role == "hyperlink":
                n = e.name.strip()
                if (n and 2 <= len(n) <= 40
                        and n != info.title
                        and n.lower() not in NON_TITLE_NAMES
                        and not n.startswith(("View ", "more about ", "Save job ", "Job feedback "))):
                    if n not in skill_candidates:
                        skill_candidates.append(n)
    info.tags = skill_candidates[:15]

    # ---- Client summary: anchored on "About the client" header. Capture the
    # ---- characteristic right-column lines.
    client_keywords = (
        "payment method verified", "phone number verified",
        "rating is", "of ", "reviews",
        "jobs posted", "hire rate", "open job",
        "total spent", "hires", "active",
        "/hr avg", "hours",
        "member since",
    )
    # Strings that match a keyword but aren't actually client info
    client_blocklist = {
        "open job in a new window",
        "open in a new window",
        "save job",
        "go back",
        "apply now",
    }
    # Drop time-ago strings — those are duplicates of `posted`
    posted_re_local = re.compile(r"^\d+\s*(minute|hour|day|week|month)s?\s*ago$", re.I)
    client_bits: list[str] = []
    for e in elements:
        if e.role not in ("text", "listitem"):
            continue
        n = e.name.strip()
        if not n or len(n) > 200:
            continue
        nl = n.lower()
        if nl in client_blocklist:
            continue
        if posted_re_local.match(n):
            continue
        if any(k in nl for k in client_keywords):
            # Avoid duplicate-shaped lines: skip if `n` is already a substring
            # of an existing entry, or vice versa
            n_lower = n.lower()
            duplicate = False
            for existing in list(client_bits):
                el = existing.lower()
                if n_lower == el or n_lower in el:
                    duplicate = True
                    break
                if el in n_lower and len(n_lower) > len(el):
                    # New line is the longer composite — replace the shorter
                    client_bits.remove(existing)
            if not duplicate:
                client_bits.append(n)
    # Also: country lines like "United States Orlando 6:47 PM"
    for e in elements:
        if e.role == "listitem":
            n = e.name.strip()
            if re.search(r"^[A-Z][A-Za-z .'-]+(?:[A-Z][a-z]+)?\s*\d", n) and n not in client_bits:
                client_bits.append(n)
    info.client_summary = " | ".join(client_bits[:8])[:400]

    return info


def capture_url_via_clipboard(window: str, max_scrolls: int = 6) -> str:
    """PageDown until Copy-to-clipboard button is in the tree, click it, read clipboard."""
    # Bump a sentinel into the clipboard so we can detect actual change
    sentinel = "__driver_sentinel_no_url_yet__"
    try:
        pyperclip.copy(sentinel)
    except Exception:
        pass

    for attempt in range(max_scrolls + 1):
        obs = observe.observe(window_title=window, include_unnamed=False, include_text=False)
        copy_btns = [e for e in obs.elements if e.role == "button" and e.name.strip() == "Copy to clipboard"]
        if copy_btns:
            btn = copy_btns[0]
            l, t, r, b = btn.bounds
            log(f"  Copy button found after {attempt} pagedown(s) at bounds={btn.bounds}")

            # Sanity checks before clicking:
            # 1. Bounds must be valid positive on-screen coordinates.
            # 2. Bounds must NOT be near the top of the panel (y < 200) — that's
            #    where "Open job in a new window" lives. If our Copy button
            #    bounds ended up there, something is wrong; abort to avoid
            #    accidentally clicking the new-window link.
            # 3. Bounds must be in the right column (x >= 1300) — the panel's
            #    right sidebar. The Copy button is always there.
            invalid = False
            reason = ""
            if l < 0 or t < 0 or r <= l or b <= t or r > 5000 or b > 5000:
                invalid = True
                reason = "off-screen / negative coords"
            elif t < 200:
                invalid = True
                reason = f"too close to panel top (y={t}) — risk of hitting 'Open job in a new window'"
            elif l < 1200:
                invalid = True
                reason = f"left edge (x={l}) not in expected right-column range"

            if invalid:
                log(f"  Copy button bounds look invalid: {reason}. Aborting URL capture.")
                return ""

            act.focus_window(window)
            time.sleep(0.2)
            # Click the exact center of the button — jitter would risk missing
            # this small target (~76x26 px) and hitting the textbox above.
            cx, cy = btn.center
            act.click_xy(cx, cy)
            time.sleep(0.6)
            try:
                url = pyperclip.paste().strip()
            except Exception:
                url = ""
            if url and url != sentinel and URL_RE.search(url):
                # Extract canonical URL portion
                m = URL_RE.search(url)
                return m.group(0)
            log(f"  clipboard didn't contain a job URL: {url[:80]!r}")
            return ""
        # not yet visible — pagedown
        if attempt < max_scrolls:
            act.focus_window(window)
            act.scroll(1, method="key")
            time.sleep(0.3)

    log(f"  Copy button not found after {max_scrolls} pagedowns")
    return ""


# ----------------------------------------------------------------------------
# LLM judge
# ----------------------------------------------------------------------------

def judge_relevance(client: OpenAI, info: JobInfo, model: str) -> tuple[bool, str]:
    """Ask the LLM whether a job is relevant. Returns (is_relevant, reason)."""
    prompt = f"""\
{CRITERIA}

JOB:
Title: {info.title}
Posted: {info.posted}
Budget: {info.budget}
Tags: {', '.join(info.tags or [])}
Client: {info.client_summary}
Description (truncated):
{info.description[:1800]}

Reply with EXACTLY two lines:
DECISION: RELEVANT or SKIP
REASON: <one short sentence>"""

    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=120,
    )
    text = (resp.choices[0].message.content or "").strip()
    decision_line = ""
    reason_line = ""
    for ln in text.splitlines():
        ln_s = ln.strip()
        if ln_s.upper().startswith("DECISION"):
            decision_line = ln_s
        elif ln_s.upper().startswith("REASON"):
            reason_line = ln_s

    is_relevant = "RELEVANT" in decision_line.upper() and "SKIP" not in decision_line.upper()
    reason = reason_line.split(":", 1)[-1].strip() if ":" in reason_line else reason_line
    return is_relevant, reason or text[:140]


# ----------------------------------------------------------------------------
# Main loop
# ----------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-jobs", type=int, default=10)
    ap.add_argument("--output", default="relevant_jobs.md")
    ap.add_argument("--model", default="gpt-4o-mini")
    ap.add_argument("--actions-per-hour", type=int, default=40)
    ap.add_argument("--reload-first", action="store_true",
                    help="Press Ctrl+R to refresh the feed before scanning")
    ap.add_argument("--seen-urls-file", default="seen_urls.txt",
                    help="Persisted set of URLs already alerted on")
    args = ap.parse_args()

    load_dotenv()
    if not os.getenv("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set in environment / .env", file=sys.stderr)
        sys.exit(1)

    pacing.configure(pacing.PacingConfig(max_actions_per_hour=args.actions_per_hour))
    jobs_store.ensure_dirs()
    llm = OpenAI()

    # Configure the target window once: every input primitive will now verify
    # focus against this name and raise act.FocusLost on miss.
    act.set_target_window(WINDOW)

    log(f"Focusing window: {WINDOW!r}")
    if not act.focus_window(WINDOW):
        print(f"Could not focus a window with title containing {WINDOW!r}.", file=sys.stderr)
        sys.exit(1)
    time.sleep(0.5)

    # Load persisted seen-URLs (alerts already sent)
    seen_urls_path = Path(args.seen_urls_file)
    seen_urls: set[str] = set()
    if seen_urls_path.exists():
        for line in seen_urls_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                seen_urls.add(line)
        log(f"Loaded {len(seen_urls)} previously-alerted URL(s) from {seen_urls_path.name}")

    if args.reload_first:
        log("Refreshing feed (Ctrl+R)")
        act.focus_window(WINDOW)
        act.key("ctrl+r")
        time.sleep(4.0)  # generous wait for full reload

        # After reload, the default tab is "Best Matches". Click "Most Recent".
        log("Switching to Most Recent tab")
        act.focus_window(WINDOW)
        obs = observe.observe(window_title=WINDOW, include_unnamed=False, include_text=False)
        most_recent = next(
            (e for e in obs.elements
             if e.role == "button" and e.name.strip() == "Most Recent"),
            None,
        )
        if most_recent is None:
            log("WARN: 'Most Recent' tab not found after reload — proceeding on whatever is shown")
        else:
            act.click(most_recent)
            time.sleep(2.0)  # let the tab content render

    # Output file header if new
    out_path = Path(args.output)
    if not out_path.exists() or out_path.stat().st_size == 0:
        out_path.write_text(
            f"# Upwork Relevant Jobs — scan {datetime.now().isoformat(timespec='minutes')}\n\n",
            encoding="utf-8",
        )

    seen_titles: set[str] = set()
    saved = 0
    evaluated = 0
    skipped = 0
    deduped = 0  # judged RELEVANT but URL already in seen_urls.txt
    no_progress_iters = 0
    pacing_stop = False

    # Start from top so the scan is deterministic
    log("Scrolling to top of feed (Ctrl+Home).")
    act.focus_window(WINDOW)
    act.key("ctrl+home")
    time.sleep(1.0)

    # Reliability mode: feed is just the queue of titles to click. Every
    # judgment is based on the panel, which has consistent structure.
    while evaluated < args.max_jobs:
        cards = parse_feed_cards(WINDOW)
        # We only need the title + the clickable element from the feed.
        new_cards = [(info, el) for info, el in cards if info.title not in seen_titles]

        log(f"-- observation: {len(cards)} cards in view, {len(new_cards)} new --")

        if not new_cards:
            no_progress_iters += 1
            if no_progress_iters >= 2:
                log("No new cards after 2 iterations — end of feed. Stopping.")
                break
            log(f"  No new cards; scrolling down (no_progress_iters={no_progress_iters}).")
            act.focus_window(WINDOW)
            act.scroll(3, method="wheel")
            time.sleep(1.0)
            continue

        no_progress_iters = 0

        for feed_info, title_el in new_cards:
            if evaluated >= args.max_jobs:
                break
            seen_titles.add(feed_info.title)
            evaluated += 1

            try:
                log(f"[{evaluated}/{args.max_jobs}] {feed_info.title[:80]}")

                # 1. Open the panel — single source of truth for data.
                # Slow connections render content gradually; give it a generous wait.
                act.focus_window(WINDOW)
                act.click(title_el)
                time.sleep(3.0)

                # 2. Read the panel
                try:
                    info = collect_panel_info(WINDOW, expected_title=feed_info.title)
                except Exception as ex:
                    log(f"  ERROR reading panel: {ex}")
                    act.focus_window(WINDOW)
                    act.key("escape")
                    time.sleep(0.5)
                    skipped += 1
                    continue

                log(f"  Posted:  {info.posted or '(none)'}")
                log(f"  Budget:  {info.budget or '(none)'}")
                log(f"  Client:  {info.client_summary or '(none)'}")
                log(f"  Tags:    {', '.join(info.tags or [])[:120]}")
                log(f"  Desc:    {len(info.description)} chars")

                # If panel data is empty, the click likely didn't open the panel.
                # Try a cheap, non-destructive retry first: just click the title
                # again and wait longer. Avoid reload — reload shuffles the feed
                # state and loses our position.
                if len(info.description) < 80 and not info.budget:
                    log(f"  -> Panel didn't open — retrying click (no reload)")
                    act.focus_window(WINDOW)
                    act.key("escape")
                    time.sleep(0.5)
                    act.click(title_el)
                    time.sleep(4.0)  # generous wait this time

                    try:
                        info = collect_panel_info(WINDOW, expected_title=feed_info.title)
                    except Exception as ex:
                        log(f"  -> SKIP (retry collect failed: {ex})")
                        act.focus_window(WINDOW)
                        act.key("escape")
                        time.sleep(0.5)
                        skipped += 1
                        continue

                    if len(info.description) < 80 and not info.budget:
                        log(f"  -> SKIP (panel still didn't open after retry)")
                        skipped += 1
                        act.focus_window(WINDOW)
                        act.key("escape")
                        time.sleep(0.5)
                        continue

                    log(f"  -> Retry succeeded")
                    log(f"  Posted:  {info.posted or '(none)'}")
                    log(f"  Budget:  {info.budget or '(none)'}")
                    log(f"  Client:  {info.client_summary or '(none)'}")
                    log(f"  Tags:    {', '.join(info.tags or [])[:120]}")
                    log(f"  Desc:    {len(info.description)} chars")

                # 3. Judge based on real panel data
                try:
                    is_relevant, reason = judge_relevance(llm, info, args.model)
                except Exception as ex:
                    log(f"  LLM error: {ex}")
                    act.focus_window(WINDOW)
                    act.key("escape")
                    time.sleep(0.5)
                    skipped += 1
                    continue

                if not is_relevant:
                    skipped += 1
                    log(f"  -> SKIP ({reason})")
                    act.focus_window(WINDOW)
                    act.key("escape")
                    time.sleep(0.5)
                    continue

                # 4. Relevant — capture URL via Copy-to-clipboard, then close
                log(f"  -> RELEVANT ({reason}) — capturing URL")
                url = capture_url_via_clipboard(WINDOW)
                info.url = url

                act.focus_window(WINDOW)
                act.key("escape")
                time.sleep(0.5)

                if not url:
                    log(f"  WARN: no URL captured. Pressing Esc to recover; continuing.")
                    act.focus_window(WINDOW)
                    act.key("escape")
                    time.sleep(0.5)

                # Dedup: skip alerts/proposals if we've already pinged this URL
                if url and url in seen_urls:
                    log(f"  -> ALREADY ALERTED (url in seen_urls.txt) — not re-pinging")
                    deduped += 1
                    continue

                # Cross-status dedup: skip if this job already exists in any
                # jobs/<status>/ directory (already queued or already applied).
                if url:
                    try:
                        job_id_check = jobs_store.extract_job_id(url)
                        if jobs_store.is_known_job_id(job_id_check):
                            log(f"  -> ALREADY IN QUEUE (job_id={job_id_check}) — not re-queuing")
                            deduped += 1
                            continue
                    except ValueError:
                        pass  # malformed URL — fall through and let later code handle it

                with open(out_path, "a", encoding="utf-8") as f:
                    f.write(info.to_markdown(reason))
                saved += 1
                log(f"  -> SAVED  url={url or 'MISSING'}")

                # Generate Doc-version proposal: structured markdown body + short
                # cover letter that links to the Doc. Two steps:
                #   1. LLM call to produce body + draft cover letter (with placeholder URL).
                #   2. Composio: create Google Doc from markdown, get share URL.
                #   3. Substitute URL into the cover letter.
                p = None
                try:
                    p = proposal_mod.generate_doc_proposal(
                        job_title=info.title,
                        description=info.description,
                        budget=info.budget,
                        posted=info.posted,
                        client_summary=info.client_summary,
                        model=args.model,
                    )
                    log(f"  -> Doc proposal drafted ({len(p.doc_markdown)} chars markdown)")

                    # Create the Google Doc + make it shareable + insert diagram
                    doc_url = gdocs.create_proposal_doc(
                        title=f"Proposal for {info.title[:120]}",
                        markdown=p.doc_markdown,
                        diagram_url=p.diagram_url or None,
                    )
                    if doc_url:
                        p.doc_url = doc_url
                        p.cover_letter = p.cover_letter.replace("{DOC_URL}", doc_url)
                        log(f"  -> Google Doc created: {doc_url}")
                    else:
                        p.cover_letter = p.cover_letter.replace("{DOC_URL}", "(doc creation failed)")
                        log(f"  WARN: Google Doc creation failed; cover letter has no URL")
                except Exception as ex:
                    log(f"  Proposal generation failed: {ex}")
                    p = None

                # Persist this job to jobs/pending/<id>.json BEFORE we notify, so
                # the apply queue is independent of Discord delivery.
                if p is not None and url:
                    try:
                        job_data = {
                            "url": url,
                            "title": info.title,
                            "found_at": datetime.now().isoformat(timespec="seconds"),
                            "budget": info.budget,
                            "client": {"summary": info.client_summary},
                            "skills": info.tags or [],
                            "description": info.description,
                            "doc_url": p.doc_url,
                            "cover_letter": p.cover_letter,
                            "why_relevant": reason,
                        }
                        queue_path = jobs_store.write_pending(job_data)
                        log(f"  -> Queued for apply: {queue_path}")
                    except Exception as ex:
                        log(f"  WARN: failed to queue job for apply: {ex}")

                # Send Discord alert + proposal
                try:
                    alert = notify.JobAlert(
                        title=info.title,
                        url=url,
                        posted=info.posted,
                        budget=info.budget,
                        client_summary=info.client_summary,
                        why_relevant=reason,
                    )
                    if notify.send_alert(alert):
                        log(f"  -> Discord alert sent")
                    if p and notify.send_proposal(p.cover_letter, p.full_proposal, doc_url=p.doc_url):
                        log(f"  -> Discord proposal sent")
                except Exception as ex:
                    log(f"  Notify failed: {ex}")

                # Persist URL so future cycles dedup correctly
                if url:
                    seen_urls.add(url)
                    with open(seen_urls_path, "a", encoding="utf-8") as f:
                        f.write(url + "\n")

                # Pacing check
                s = pacing.get_pacer().stats()
                if s["actions_last_hour"] >= 0.85 * s["budget"]:
                    log(f"Pacing budget at 85% ({s['actions_last_hour']}/{s['budget']}) — stopping.")
                    pacing_stop = True
                    break
            except (act.FocusLost, observe.WaitTimeout) as ex:
                log(f"  ERROR (recoverable): {type(ex).__name__}: {ex}")
                log(f"  -> SKIP: trying to escape and continue with next card")
                try:
                    act.focus_window(WINDOW)
                    act.key("escape")
                except Exception:
                    pass
                time.sleep(1.0)
                skipped += 1
                continue

        if pacing_stop:
            break

        # Scroll one card height for the next iteration
        if evaluated < args.max_jobs:
            act.focus_window(WINDOW)
            act.scroll(3, method="wheel")
            time.sleep(0.9)

    log(f"\nDone. Evaluated {evaluated}, saved {saved}, skipped {skipped}, "
        f"already-alerted (deduped) {deduped}. File: {args.output}")


if __name__ == "__main__":
    main()
