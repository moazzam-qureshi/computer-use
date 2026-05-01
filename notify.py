"""
Discord notifier — posts relevant Upwork jobs as alerts via webhook.

Two messages per relevant job:
  1. Alert — title, budget, client, why-relevant, URL. Mobile-friendly.
  2. Proposal — code-blocked cover letter + full proposal. Desktop-friendly
     for copy-paste.

Reads webhook URL from DISCORD_WEBHOOK_URL env var. Falls back to the URL
the user shared earlier if env is unset.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Optional

import requests


# Fallback webhook (user said they don't care about rotation for this one)
_FALLBACK_WEBHOOK = (
    "https://discord.com/api/webhooks/1499572657954357308/"
    "q2GGu4wXBdsQ1tqw7Ke2hCylxJdvHSlGtMD05Z3gFTz0x6c2ELV2e2_zofY_EWmPumry"
)

DISCORD_MAX_CONTENT = 1900  # leave headroom under 2000 limit


def _webhook_url() -> str:
    return os.getenv("DISCORD_WEBHOOK_URL") or _FALLBACK_WEBHOOK


def _post(content: str, *, retries: int = 2) -> bool:
    """Post to webhook. Truncates over Discord's 2000-char limit."""
    url = _webhook_url()
    if not url:
        print("[notify] no webhook configured", flush=True)
        return False

    if len(content) > DISCORD_MAX_CONTENT:
        content = content[:DISCORD_MAX_CONTENT - 30] + "\n…(truncated)"

    payload = {"content": content}
    for attempt in range(retries + 1):
        try:
            r = requests.post(url, json=payload, timeout=10)
            if r.status_code in (200, 204):
                return True
            # Discord rate limit — honor Retry-After
            if r.status_code == 429:
                retry_after = float(r.json().get("retry_after", 1.5))
                time.sleep(retry_after + 0.2)
                continue
            print(f"[notify] http {r.status_code}: {r.text[:200]}", flush=True)
        except Exception as ex:
            print(f"[notify] post failed: {ex}", flush=True)
            if attempt < retries:
                time.sleep(1.0)
                continue
        break
    return False


@dataclass
class JobAlert:
    title: str
    url: str
    posted: str = ""
    budget: str = ""
    client_summary: str = ""
    why_relevant: str = ""


def send_alert(alert: JobAlert) -> bool:
    """Send the terse alert message."""
    lines = [f"**{alert.title}**"]
    if alert.posted:
        lines.append(f"Posted: {alert.posted}")
    if alert.budget:
        lines.append(f"Budget: {alert.budget[:200]}")
    if alert.client_summary:
        lines.append(f"Client: {alert.client_summary[:200]}")
    if alert.why_relevant:
        lines.append(f"Why: {alert.why_relevant}")
    if alert.url:
        lines.append(alert.url)
    return _post("\n".join(lines))


def send_proposal(cover_letter: str, full_proposal: str, doc_url: str = "") -> bool:
    """Send the proposal as a Discord message.

    The cover letter is what the user copy-pastes into Upwork. If a Google Doc
    URL is provided, it's already inside the cover letter (locked formula).
    The full markdown body is sent as a code block fallback in case the Doc
    creation failed and the user needs the raw text.
    """
    parts = [
        "**Cover letter (paste into Upwork):**",
        f"```\n{cover_letter.strip()}\n```",
    ]
    if doc_url:
        parts.append(f"**Google Doc:** {doc_url}")
    else:
        # Doc didn't create — show the full markdown as fallback
        parts.append("**Full proposal markdown (Doc creation failed — paste manually):**")
        parts.append(f"```\n{full_proposal.strip()[:DISCORD_MAX_CONTENT - 200]}\n```")
    content = "\n".join(parts)

    if len(content) <= DISCORD_MAX_CONTENT:
        return _post(content)

    # Split if too long
    ok1 = _post("**Cover letter (paste into Upwork):**\n"
                f"```\n{cover_letter.strip()}\n```")
    if doc_url:
        ok2 = _post(f"**Google Doc:** {doc_url}")
    else:
        ok2 = _post("**Full proposal markdown:**\n"
                    f"```\n{full_proposal.strip()[:DISCORD_MAX_CONTENT - 30]}\n```")
    return ok1 and ok2


def send_review_needed(title: str, apply_url: str, questions_answered: int) -> bool:
    """Notify human that an apply form is filled and ready for review + Submit."""
    lines = [
        "**Apply form filled, review and submit:**",
        f"**{title}**",
        f"Questions answered: {questions_answered}",
        apply_url,
    ]
    return _post("\n".join(lines))


def send_login_needed(title: str, apply_url: str) -> bool:
    """Notify human that Upwork session expired — log in once, then re-run."""
    lines = [
        "**Upwork login required — apply flow paused:**",
        f"Job: **{title}**",
        f"Open Chrome, log in to Upwork, then re-run the apply driver. "
        f"This job is still in jobs/pending/ and will be retried.",
        apply_url,
    ]
    return _post("\n".join(lines))
