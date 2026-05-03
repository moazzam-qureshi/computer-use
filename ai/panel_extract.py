"""LLM-based panel field extraction.

Replaces the regex-laden upwork.panel.parse_panel. Takes a raw text dump of
all observed elements in the open job-detail panel and returns a structured
PanelExtraction. The LLM sees what a human would see and pulls out every
field; we maintain zero per-layout regex.
"""
from __future__ import annotations

import json
from typing import Optional

from openai import OpenAI

from ai.schemas import PanelExtraction
from ai.cost_tracker import CostTracker
from storage.agent_runs import AgentRunStore


PANEL_EXTRACT_SYSTEM = """You extract structured fields from the raw text of an Upwork job-detail panel.

Input: a flat dump of every visible text element in the panel, one per line, in document order.
Output: a JSON object matching the PanelExtraction schema. Include EVERY field whose value is present in the dump, even when the value sits on a different line from its label.

Field-by-field rules:

- title: the canonical job title. Usually appears as the page heading (a long line near the top) and again inside the 'Save job <title>' button. Prefer the page-heading form.

- posted_text: the human-readable 'N units ago' phrase. The label 'Posted' is followed on the next line(s) by something like '17 minutes ago' or '2 days ago'. ALWAYS extract this when present.

- budget_kind: 'hourly' if you see 'Hourly', '/hr', or 'hrs/week'. 'fixed' if you see 'Fixed-price', 'Fixed price', or 'Project Type: Fixed'. If neither, null.

- budget_min_usd / budget_max_usd: numeric USD only. '$15.00-$20.00' -> min=15.0 max=20.0. '$1,500' -> min=1500.0 max=null. Strip currency symbols and commas. CRITICAL: only values immediately adjacent to 'Hourly' / 'Fixed-price' / 'Est. Budget' are the JOB budget. Values adjacent to 'total spent', '/hr avg', 'hires' are CLIENT stats and go in client_total_spent_usd / client_hires, NOT the budget fields.

- duration: the project-length phrase, e.g. '1 to 3 months', 'Less than 1 month', 'More than 6 months'. ALWAYS extract when present.

- experience_level: exactly one of 'Entry level', 'Intermediate', 'Expert'. ALWAYS extract when present.

- hours_per_week: phrases like 'Less than 30 hrs/week' or '30+ hrs/week'. ALWAYS extract when present.

- skills: only the chips under 'Skills and Expertise' or 'Mandatory skills'. Do NOT include 'Skills and Expertise' itself as a skill. Do NOT include nav items, buttons, or single-letter labels. Max 15.

- description: the longest contiguous block of prose. Include 'Summary', 'Description', 'What I Need Help With' content. Cap at 5000 chars.

- client_country: the country name from the About-the-client block (e.g. 'United States', 'USA', 'United Kingdom'). Map common abbreviations: 'USA' -> 'United States'. ALWAYS extract when present.

- client_payment_verified: true if you see 'Payment method verified' or 'Payment verified'. false if you see 'Payment method not verified'. null only if neither.

- client_rating: numeric 0-5 from 'Rating is X out of 5' or '5.00 of N reviews' or a standalone 'X.X' next to stars. ALWAYS extract when a rating is shown.

- client_total_spent_usd: '$14K total spent' -> 14000.0. '$1.2M' -> 1200000.0. '$500K' -> 500000.0.

- client_hires: '58 hires' -> 58. '58 hires, 5 active' -> 58.

- proposals_count: 'Proposals: 5 to 10' -> 5 (lower bound). 'less than 5' -> 5. 'more than 50' -> 50.

DO NOT skip a field that is clearly present in the dump just because the label is on a separate line from the value. The label-then-value pattern is normal in the panel rendering. If a value is genuinely absent, return null.

Return ONLY the JSON object. No preamble, no markdown fences."""


PANEL_EXTRACT_USER = """RAW PANEL TEXT (one element per line, in document order):

{raw_text}

Extract the structured fields now. Return JSON."""


def _elements_to_text(elements) -> str:
    """Flatten observed elements into a one-per-line text dump for the LLM.

    Skips empty / placeholder lines. Caps at 30k chars to stay well within
    gpt-4o-mini's 128k context (with room for prompt + response).
    """
    lines: list[str] = []
    for e in elements:
        n = (getattr(e, "name", None) or "").strip()
        if not n:
            continue
        # Skip absurdly long single elements (likely error blobs)
        if len(n) > 8000:
            n = n[:8000] + "..."
        lines.append(n)
    text = "\n".join(lines)
    if len(text) > 30000:
        text = text[:30000] + "\n... (truncated)"
    return text


def extract_panel(
    elements,
    *,
    job_id: str,
    agent_run_store: AgentRunStore,
    parent_run_id: Optional[int] = None,
    model: str = "gpt-4o-mini",
) -> PanelExtraction:
    """Single LLM call -> PanelExtraction. Coerces dict-shaped responses
    via Pydantic; on JSON parse failure returns an empty extraction so the
    pipeline never crashes here.
    """
    # Materialize once; callers may pass a generator that _elements_to_text would
    # exhaust before we get to log the count.
    elements = list(elements)
    raw_text = _elements_to_text(elements)
    user = PANEL_EXTRACT_USER.format(raw_text=raw_text)

    # DEBUG: dump what the LLM is being fed. Truncated to 3000 chars so the
    # console stays readable; len(raw_text) tells us if we're hitting the 30k cap.
    print(
        f"[panel_extract DEBUG] job_id={job_id} elements_in={len(elements)} "
        f"raw_text_len={len(raw_text)} raw_text_lines={raw_text.count(chr(10))+1}",
        flush=True,
    )
    print(
        f"[panel_extract DEBUG] raw_text[:3000]=\n--- BEGIN RAW TEXT ---\n{raw_text[:3000]}\n--- END RAW TEXT ---",
        flush=True,
    )

    client = OpenAI()
    with CostTracker(
        agent_run_store,
        agent_name="panel_extract",
        trigger="per_job",
        trigger_context={"job_id": job_id, "raw_text_len": len(raw_text)},
        parent_run_id=parent_run_id,
    ):
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": PANEL_EXTRACT_SYSTEM},
                {"role": "user", "content": user},
            ],
            temperature=0.0,
            response_format={"type": "json_object"},
            max_tokens=4000,
        )

    raw = (resp.choices[0].message.content or "").strip()
    print(
        f"[panel_extract DEBUG] job_id={job_id} llm_raw_response=\n--- BEGIN LLM JSON ---\n{raw}\n--- END LLM JSON ---",
        flush=True,
    )
    try:
        parsed = json.loads(raw)
    except Exception as ex:
        print(f"[panel_extract DEBUG] json.loads FAILED: {ex!r}; returning empty PanelExtraction", flush=True)
        return PanelExtraction()

    try:
        result = PanelExtraction(**parsed)
        print(
            f"[panel_extract DEBUG] job_id={job_id} parsed={result.model_dump()}",
            flush=True,
        )
        return result
    except Exception as ex:
        # If a single field fails validation (e.g. LLM put a string where we
        # asked for a float), drop the bad keys and retry. Never fail the
        # whole extraction over one mis-typed field.
        print(f"[panel_extract] PanelExtraction validation failed ({ex}); retrying with strict-coerce", flush=True)
        cleaned: dict = {}
        dropped: dict = {}
        for field_name in PanelExtraction.model_fields:
            if field_name not in parsed:
                continue
            v = parsed[field_name]
            try:
                # Construct a single-field instance to test that field's validator
                PanelExtraction(**{field_name: v})
                cleaned[field_name] = v
            except Exception as fx:
                dropped[field_name] = (repr(v)[:120], str(fx)[:120])
                continue
        print(f"[panel_extract DEBUG] strict-coerce kept={list(cleaned.keys())} dropped={dropped}", flush=True)
        result = PanelExtraction(**cleaned)
        print(f"[panel_extract DEBUG] job_id={job_id} parsed-after-coerce={result.model_dump()}", flush=True)
        return result
